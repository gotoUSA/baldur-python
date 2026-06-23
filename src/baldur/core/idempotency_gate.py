"""Unified idempotency gate for step-level deduplication.

Provides check-and-acquire, mark-completed, and mark-failed operations
using CacheProviderInterface.setnx() for atomic acquisition.

Used by Saga, Runbook, and other step-based execution engines.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

from baldur.core.exceptions import ConfigurationError

if TYPE_CHECKING:
    from baldur.interfaces.cache_provider import CacheProviderInterface

__all__ = [
    "IdempotencyDecision",
    "IdempotencyCheckResult",
    "IdempotencyGate",
    "IDEMPOTENCY_DEFAULT_TTL_SECONDS",
]

logger = logging.getLogger(__name__)

IDEMPOTENCY_DEFAULT_TTL_SECONDS: int = 1800  # 30 minutes


class IdempotencyDecision(str, Enum):
    """Result of an idempotency check."""

    CONTINUE = "continue"  # Proceed with execution
    SKIP = "skip"  # Already completed, use cached result
    ABORT = "abort"  # Another process is executing (in-doubt)


@dataclass
class IdempotencyCheckResult:
    """Idempotency check result."""

    decision: IdempotencyDecision
    cached_result: dict[str, Any] | None = None
    retry_count: int = 0


class IdempotencyGate:
    """Step-level idempotency gate — unified model.

    Key generation is the caller's responsibility.
    The gate performs check + state transitions only.

    Uses CacheProviderInterface.setnx() for atomic check-and-acquire.

    A dedup record lives under two decoupled windows:

    - **Execution window** (``execution_ttl_seconds``) — how long an
      in-flight EXECUTING claim is honored before a competing process may
      stale-take it over. Sized to worst-case operation duration. Used by
      ``check_and_acquire`` when no per-call ``ttl`` is supplied.
    - **Memory window** (``memory_ttl_seconds``) — how long a
      completed/failed record is remembered for dedup. Used by
      ``mark_completed`` / ``mark_failed`` when no per-call ``ttl`` is
      supplied. ``None`` (default) resolves per use from
      ``IdempotencySettings.gate_memory_ttl_seconds`` so every construction
      site honors the operator-tunable setting without threading.
    """

    # Reference: docs/impl/595_IDEMPOTENT_DEDUP_CONTRACT.md D2/D3/D5.

    def __init__(
        self,
        cache: CacheProviderInterface | None = None,
        execution_ttl_seconds: int = IDEMPOTENCY_DEFAULT_TTL_SECONDS,
        memory_ttl_seconds: int | None = None,
    ) -> None:
        self._cache = cache
        self._execution_ttl_seconds = execution_ttl_seconds
        self._memory_ttl_seconds = memory_ttl_seconds
        if cache is not None:
            # Validate the concrete adapter, not the metrics decorator.
            # Registry-resolved caches arrive wrapped in
            # ``MetricsAwareCacheAdapter`` (which overrides setnx/cas_dict_field
            # to delegate), so an unwrapped check would always pass and silently
            # admit a non-atomic underlying adapter.
            concrete = self._unwrap_cache(cache)
            self._validate_atomic_setnx(concrete)
            self._validate_atomic_cas_dict_field(concrete)

    @staticmethod
    def _unwrap_cache(cache: CacheProviderInterface) -> CacheProviderInterface:
        """Walk decorator delegates to the concrete adapter for capability checks.

        The atomicity validators below must inspect the concrete adapter rather
        than a delegating decorator: ``MetricsAwareCacheAdapter`` overrides
        ``setnx`` / ``cas_dict_field`` to forward to its delegate, so
        ``type(decorator).setnx is not CacheProviderInterface.setnx`` always
        holds regardless of whether the underlying adapter is atomic. Duck-typed
        on ``_delegate`` so this core module stays decoupled from the adapters
        layer (the metrics decorator is the only delegate-bearing wrapper today).
        """
        seen: set[int] = set()
        while hasattr(cache, "_delegate") and id(cache) not in seen:
            seen.add(id(cache))
            cache = cache._delegate
        return cache

    @staticmethod
    def _validate_atomic_setnx(cache: CacheProviderInterface) -> None:
        """Verify that the cache provides an atomic setnx() implementation.

        The base CacheProviderInterface.setnx() is a non-atomic
        exists() -> set() two-step. All production implementations
        (Redis, Memory) override with atomic versions, but a new
        implementation could silently inherit the non-atomic default.
        """
        from baldur.interfaces.cache_provider import CacheProviderInterface

        if type(cache).setnx is CacheProviderInterface.setnx:
            raise ConfigurationError(
                "IdempotencyGate requires an atomic setnx() implementation. "
                f"{type(cache).__name__} uses the non-atomic default."
            )

    @staticmethod
    def _validate_atomic_cas_dict_field(cache: CacheProviderInterface) -> None:
        """Verify that the cache provides an atomic cas_dict_field() implementation.

        Symmetric to _validate_atomic_setnx — mark_completed / mark_failed
        rely on cas_dict_field for single-RTT, race-free EXECUTING ->
        COMPLETED / FAILED transitions. The base interface default is a
        non-atomic get -> check -> set; production adapters (Redis Lua,
        Memory lock-wrapped) override.
        """
        from baldur.interfaces.cache_provider import CacheProviderInterface

        if type(cache).cas_dict_field is CacheProviderInterface.cas_dict_field:
            raise ConfigurationError(
                "IdempotencyGate requires an atomic cas_dict_field() "
                "implementation. "
                f"{type(cache).__name__} uses the non-atomic default."
            )

    def _effective_memory_ttl(self) -> timedelta:
        """Resolve the dedup memory window for ``mark_*`` default paths.

        An explicit ``memory_ttl_seconds`` constructor override wins;
        otherwise the settings field is read per use (not at init) so
        operators can retune the window at runtime via env +
        ``reset_idempotency_settings()``.
        """
        # Per-use lazy core→settings import — the established precedent is
        # core/backoff.py; acyclic because this module has no module-level
        # settings import.
        if self._memory_ttl_seconds is not None:
            return timedelta(seconds=self._memory_ttl_seconds)
        from baldur.settings.idempotency import get_idempotency_settings

        return timedelta(seconds=get_idempotency_settings().gate_memory_ttl_seconds)

    def check_and_acquire(
        self,
        key: str,
        ttl: timedelta | None = None,
    ) -> IdempotencyCheckResult:
        """Check idempotency and acquire EXECUTING state.

        The initial acquisition uses atomic setnx(). Retry paths
        (failed / stale-executing) use delete() + setnx() so that
        exactly one competing process wins — losers receive ABORT.

        ``ttl`` bounds the EXECUTING claim (execution window): the claim's
        cache TTL and the stale-takeover threshold. ``None`` uses the gate's
        execution default. Size it to worst-case operation duration, not to
        the dedup horizon — the completed-record memory window is governed
        separately by ``mark_completed`` / ``mark_failed``.

        Returns:
            CONTINUE — execution may proceed (EXECUTING state acquired)
            SKIP — already completed (cached_result included)
            ABORT — another process is executing (in-doubt window)
        """
        if self._cache is None:
            # Unconfigured / test no-op path. Deliberately un-metered: a
            # ``record_gate_decision("continue")`` here would conflate "no gate
            # installed" with "a real gate said continue" in the decision
            # counter. Metering happens only on the real-cache path below.
            return IdempotencyCheckResult(decision=IdempotencyDecision.CONTINUE)

        result = self._check_and_acquire(self._cache, key, ttl)
        self._record_gate_decision(result.decision)
        return result

    def _check_and_acquire(  # noqa: C901
        self,
        cache: CacheProviderInterface,
        key: str,
        ttl: timedelta | None,
    ) -> IdempotencyCheckResult:
        """Real-cache check-and-acquire (``cache`` guaranteed non-None)."""
        effective_ttl = ttl or timedelta(seconds=self._execution_ttl_seconds)
        record_value: dict[str, Any] = {
            "status": "executing",
            "started_at": time.time(),
            "retry_count": 0,
        }

        acquired = cache.setnx(key, record_value, ttl=effective_ttl)
        if acquired:
            return IdempotencyCheckResult(decision=IdempotencyDecision.CONTINUE)

        # Key already exists — check its status
        existing = cache.get(key)
        if existing is None:
            # Race: key expired between setnx and get — treat as CONTINUE
            retry_acquired = cache.setnx(key, record_value, ttl=effective_ttl)
            if retry_acquired:
                return IdempotencyCheckResult(decision=IdempotencyDecision.CONTINUE)
            return IdempotencyCheckResult(decision=IdempotencyDecision.ABORT)

        if not isinstance(existing, dict):
            return IdempotencyCheckResult(decision=IdempotencyDecision.ABORT)

        status = existing.get("status", "")

        if status == "completed":
            return IdempotencyCheckResult(
                decision=IdempotencyDecision.SKIP,
                cached_result=existing.get("result"),
                retry_count=existing.get("retry_count", 0),
            )

        if status == "failed":
            # Previous attempt failed — delete + setnx for safe retry.
            # Only one competing process wins the setnx; losers ABORT.
            record_value["retry_count"] = existing.get("retry_count", 0) + 1
            cache.delete(key)
            if cache.setnx(key, record_value, effective_ttl):
                return IdempotencyCheckResult(
                    decision=IdempotencyDecision.CONTINUE,
                    retry_count=record_value["retry_count"],
                )
            return IdempotencyCheckResult(decision=IdempotencyDecision.ABORT)

        if status == "executing":
            # In-doubt: check if stale (TTL-based crash recovery)
            started_at = existing.get("started_at", 0)
            elapsed = time.time() - started_at
            if elapsed > effective_ttl.total_seconds():
                # Stale — delete + setnx for safe retry.
                record_value["retry_count"] = existing.get("retry_count", 0) + 1
                cache.delete(key)
                if cache.setnx(key, record_value, effective_ttl):
                    return IdempotencyCheckResult(
                        decision=IdempotencyDecision.CONTINUE,
                        retry_count=record_value["retry_count"],
                    )
                return IdempotencyCheckResult(decision=IdempotencyDecision.ABORT)
            return IdempotencyCheckResult(decision=IdempotencyDecision.ABORT)

        # Unknown status — abort defensively
        return IdempotencyCheckResult(decision=IdempotencyDecision.ABORT)

    @staticmethod
    def _record_gate_decision(decision: IdempotencyDecision) -> None:
        """Record the gate decision via the idempotency metric recorder.

        Lazy import + swallow-on-error, mirroring
        ``_cache_resolver._record_fallback_metric`` and the established
        core→metrics lazy-import precedent — the dedup hot path must never be
        broken by an observability failure.
        """
        try:
            from baldur.metrics.prometheus import get_metrics

            rec = getattr(get_metrics(), "idempotency", None)
            if rec is not None:
                rec.record_gate_decision(decision.value)
        except Exception:
            pass

    def mark_completed(
        self,
        key: str,
        result: dict[str, Any] | None = None,
        retry_count: int = 0,
        ttl: timedelta | None = None,
    ) -> None:
        """Transition EXECUTING -> COMPLETED. Cache the result.

        Atomically replaces the record only if its current status is
        ``executing``. ``retry_count`` is supplied by the caller (forwarded
        from ``IdempotencyCheckResult.retry_count``) so the success path
        does not re-read the record before writing.

        ``ttl`` bounds the dedup memory window — how long this completed
        record blocks duplicates. ``None`` uses the gate's memory default
        (``IdempotencySettings.gate_memory_ttl_seconds`` unless overridden
        at construction).
        """
        if self._cache is None:
            return
        effective_ttl = ttl or self._effective_memory_ttl()
        new_record = {
            "status": "completed",
            "completed_at": time.time(),
            "result": result or {},
            "retry_count": retry_count,
        }
        success = self._cache.cas_dict_field(
            key, "status", "executing", new_record, effective_ttl
        )
        if not success:
            logger.info(
                "idempotency_gate.mark_completed_cas_conflict",
                extra={"key": key},
            )

    def release(self, key: str) -> None:
        """Delete the record for ``key``, re-arming a future acquisition.

        Unlike :meth:`mark_completed` (which leaves a COMPLETED record that
        makes subsequent ``check_and_acquire`` calls SKIP), this clears the key
        entirely. Used when the same logical key must be re-acquirable later —
        e.g. recovery compensation that shares a ``trigger_id``-scoped key across
        resumed sessions and must re-run if a resumed session fails again.

        Idempotent and best-effort: a missing key or cache error is a no-op.
        """
        if self._cache is None:
            return
        try:
            self._cache.delete(key)
        except Exception:
            logger.info(
                "idempotency_gate.release_failed",
                extra={"key": key},
            )

    def mark_failed(
        self,
        key: str,
        error: str = "",
        retry_count: int = 0,
        ttl: timedelta | None = None,
    ) -> None:
        """Transition EXECUTING -> FAILED.

        Atomically replaces the record only if its current status is
        ``executing``. ``retry_count`` is supplied by the caller (forwarded
        from ``IdempotencyCheckResult.retry_count``) so the failure path
        does not re-read the record before writing.

        ``ttl`` bounds the dedup memory window for the failed record (the
        retryable-state retention). ``None`` uses the gate's memory default
        (``IdempotencySettings.gate_memory_ttl_seconds`` unless overridden
        at construction).
        """
        if self._cache is None:
            return
        effective_ttl = ttl or self._effective_memory_ttl()
        new_record = {
            "status": "failed",
            "failed_at": time.time(),
            "error": error,
            "retry_count": retry_count,
        }
        success = self._cache.cas_dict_field(
            key, "status", "executing", new_record, effective_ttl
        )
        if not success:
            logger.info(
                "idempotency_gate.mark_failed_cas_conflict",
                extra={"key": key},
            )


# ── Singleton ────────────────────────────────────────────────

from baldur.utils.singleton import make_singleton_factory

get_idempotency_gate, configure_idempotency_gate, reset_idempotency_gate = (
    make_singleton_factory("idempotency_gate", IdempotencyGate)
)
