"""
Circuit Breaker Policy — function-wrapping-based Circuit Breaker.

Converts the existing condition-check approach based on
CircuitBreakerService.should_allow() into a function-wrapping approach
based on ResiliencePolicy.execute().

Internally it reuses the existing CircuitBreakerService and manages state
through automatic counting (record_failure/record_success).

Three circuit-control paths coexist independently:
- CircuitBreakerPolicy (record_failure): automatic counting based on generic Exceptions
- ProtectionMixin (record_rate_limit_response): force_open based on 429 traffic
- ManualControlMixin (force_open/force_close): manual operator control
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

import structlog

from baldur.core.execution_mode import get_execution_mode, intervention_suppressed
from baldur.interfaces.resilience_policy import (
    PolicyContext,
    PolicyOutcome,
    PolicyResult,
    ResiliencePolicy,
)

from .config import CircuitBreakerConfig, CircuitState
from .exceptions import CircuitBreakerOpenError
from .service import CircuitBreakerService

logger = structlog.get_logger()

T = TypeVar("T")


class CircuitBreakerPolicy(ResiliencePolicy[T]):
    """
    Circuit Breaker Policy — function-wrapping-based.

    Decides whether a request is allowed via should_allow() and automatically
    calls record_success() / record_failure() based on the execution result.

    - CB disabled: run the function directly and return SUCCESS
    - CB OPEN state: do not run the function and return REJECTED (CircuitBreakerOpenError)
    - Function succeeds: call record_success() then return SUCCESS
    - Function fails: after _is_failure() judgment, call record_failure(); the exception propagates upward

    Transition-only philosophy: per-reject hook bodies are NOT a default
    responsibility of this policy. State-transition events
    (closed→open, half_open→open) and the matching audit rows are emitted by
    ``CircuitBreakerService``; per-reject volume is observable via
    ``baldur_circuit_breaker_blocked_total{service, reason}``. External
    authors can still inject custom ``hooks=[…]`` for per-call
    instrumentation.
    Ref: docs/impl/494_CB_REJECT_HOOK_TRANSITION_ONLY.md

    Args:
        service_name: Identifier of the external service protected by the Circuit Breaker
        cb_service: Existing CircuitBreakerService instance (auto-created if None)
        config: CircuitBreakerConfig (used when cb_service is None)
        failure_exceptions: Tuple of exception types counted as failures
        ignore_exceptions: Tuple of exception types NOT counted as failures
        hooks: List of PolicyHooks (None means an empty list (transition-only); cycle-level
            events are emitted by ``CircuitBreakerService``, and per-reject counts are
            handled by the ``baldur_circuit_breaker_blocked_total{service, reason}``
            metric)
    """

    def __init__(
        self,
        service_name: str,
        cb_service: CircuitBreakerService | None = None,
        config: CircuitBreakerConfig | None = None,
        failure_exceptions: tuple[type[Exception], ...] = (Exception,),
        ignore_exceptions: tuple[type[Exception], ...] = (),
        hooks: list | None = None,
    ):
        self._service_name = service_name
        self._cb_service = cb_service or self._create_default_service(config)
        self._failure_exceptions = failure_exceptions
        self._ignore_exceptions = ignore_exceptions
        self._hooks = hooks if hooks is not None else []

    @staticmethod
    def _create_default_service(
        config: CircuitBreakerConfig | None = None,
    ) -> CircuitBreakerService:
        """
        Create the default CircuitBreakerService — uses LayeredRepository.

        If the "layered" key is registered in ProviderRegistry, use LayeredRepository.
        Otherwise, fall back to the ProviderRegistry default (redis).
        This removes Redis I/O from the hot path and guarantees L1 Memory decisions (#227 §7.4).
        """
        repository = None
        try:
            from baldur.factory import ProviderRegistry

            repository = ProviderRegistry.get_circuit_breaker_repo(name="layered")
        except (ValueError, ImportError, Exception):
            logger.debug("circuit_breaker_policy.layered_repo_available_falling")
        return CircuitBreakerService(config=config, repository=repository)

    @property
    def name(self) -> str:
        """Policy identifier."""
        return "circuit_breaker"

    @property
    def service_name(self) -> str:
        """Name of the protected service."""
        return self._service_name

    @property
    def cb_service(self) -> CircuitBreakerService:
        """Internal CircuitBreakerService instance."""
        return self._cb_service

    def _is_failure(self, error: Exception) -> bool:
        """
        Decide whether an exception should be counted as a failure.

        If it matches ignore_exceptions, it is not counted as a failure.
        If it matches failure_exceptions, it is counted as a failure.
        """
        if isinstance(error, self._ignore_exceptions):
            return False
        return isinstance(error, self._failure_exceptions)

    def _invoke_hooks(self, method: str, *args: Any) -> None:
        """Invoke all hooks fail-open."""
        for hook in self._hooks:
            try:
                getattr(hook, method)(*args)
            except Exception as e:
                logger.debug(
                    "circuit_breaker_policy.hook_failed",
                    adapter_type=type(hook).__name__,
                    method=method,
                    error=e,
                )

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        Execute the function based on the Circuit Breaker state.

        1. CB disabled → run directly
        2. should_allow() == False → return REJECTED (function not run) + hook.on_reject
        3. should_allow() == True → run the function
           - success → return SUCCESS after record_success() + hook.on_success
           - failure → after _is_failure() judgment, record_failure() + hook.on_failure, re-raise exception

        On rejection due to CB OPEN, no exception is thrown; a PolicyResult is returned instead.
        Exceptions raised during function execution are re-raised so an upper Policy (Retry, etc.) can handle them.
        """
        # Run directly when CB is disabled
        if not self._cb_service.is_enabled:
            result = func(*args, **kwargs)
            return PolicyResult(
                value=result,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["circuit_breaker"],
            )

        # Hook: execution start
        self._invoke_hooks("on_execute", self._service_name, 1)

        # Observe-only (dry-run / shadow / evaluation): resolve the mode BEFORE
        # the admission check. ``should_allow_with_state`` atomically advances
        # OPEN->HALF_OPEN (a real persisted state mutation + auto-recovery audit
        # row + CIRCUIT_BREAKER_HALF_OPENED event) once recovery_timeout has
        # elapsed, so it must NOT run under observe-only — that would leak an
        # automatic transition the mode promises to suppress. Peek the state
        # read-only for the would-have signal, run the business function exactly
        # once, and never reject or record; the business exception still
        # propagates. The active path keeps its single-fetch admission below.
        if not get_execution_mode().should_execute:
            peek = self._cb_service.get_or_create_state(self._service_name)
            would_reject = peek.state == CircuitState.OPEN
            intervention_suppressed(
                service_name=self._service_name,
                action=(
                    "circuit_breaker_reject"
                    if would_reject
                    else "circuit_breaker_record"
                ),
                would_reject=would_reject,
            )
            result = func(*args, **kwargs)
            return PolicyResult(
                value=result,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["circuit_breaker"],
            )

        # Check whether the request is allowed — single fetch via companion API (#485 D2/G1).
        # ``decision.state.state`` reuses the state object that
        # ``should_allow_with_state`` already loaded, eliminating the second
        # ``get_or_create_state`` call that the former ``get_state`` lookup
        # incurred on every reject.
        decision = self._cb_service.should_allow_with_state(self._service_name)

        if not decision.allowed:
            reject_result = PolicyResult(
                outcome=PolicyOutcome.REJECTED,
                error=CircuitBreakerOpenError(self._service_name),
                executed_policies=["circuit_breaker"],
                metadata={
                    "service_name": self._service_name,
                    "state": decision.state.state,
                },
            )
            # Hook: CB OPEN rejection (Audit + EventBus)
            self._invoke_hooks("on_reject", self._service_name, "circuit_open")
            return reject_result

        # Run the function — 490 D4: pass decision.state as hint_state to skip the
        # redundant ``get_or_create_state`` lookup that record_success /
        # record_failure would otherwise perform. The CLOSED steady-state
        # path becomes a full no-op (zero repository acquires).
        try:
            result = func(*args, **kwargs)
            self._cb_service.record_success(
                self._service_name,
                hint_state=decision.state,
            )
            success_result = PolicyResult(
                value=result,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["circuit_breaker"],
            )
            # Hook: execution success (Audit + EventBus)
            self._invoke_hooks("on_success", self._service_name, success_result)
            return success_result
        except Exception as e:
            if self._is_failure(e):
                self._cb_service.record_failure(
                    self._service_name,
                    error_context={"error": str(e), "type": type(e).__name__},
                    hint_state=decision.state,
                )
            # Hook: execution failure (Audit + EventBus)
            self._invoke_hooks("on_failure", self._service_name, e, 1)
            raise  # propagate so an upper Policy (Retry, etc.) can handle it


def circuit_breaker(
    service_name: str | None = None,
    cb_service: CircuitBreakerService | None = None,
    config: CircuitBreakerConfig | None = None,
    failure_exceptions: tuple[type[Exception], ...] = (Exception,),
    ignore_exceptions: tuple[type[Exception], ...] = (),
) -> Callable:
    """
    Circuit Breaker decorator.

    Applying it to a function automatically wraps it with a CircuitBreakerPolicy.
    If service_name is None, the function's __qualname__ is used as the default.

    Usage::

        @circuit_breaker("payment_api")
        def call_payment_api():
            ...

        @circuit_breaker()  # service_name = the function's __qualname__
        def call_external():
            ...
    """

    def decorator(func: Callable[..., T]) -> Callable[..., PolicyResult[T]]:
        name = service_name or func.__qualname__
        policy = CircuitBreakerPolicy(
            service_name=name,
            cb_service=cb_service,
            config=config,
            failure_exceptions=failure_exceptions,
            ignore_exceptions=ignore_exceptions,
        )

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> PolicyResult[T]:
            return policy.execute(func, *args, **kwargs)

        # Attach attribute so the Policy instance is accessible
        wrapper.policy = policy  # type: ignore[attr-defined]
        return wrapper

    return decorator
