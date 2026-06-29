"""Idempotency Guard and Hook for PolicyComposer.

Two-phase idempotency enforcement:
- IdempotencyGuard (Phase 1): Pre-execution check+acquire via IdempotencyGate
- IdempotencyHook (Phase 2): Post-execution mark (completed/failed)

Key communication via context.extra["_idempotency_key"]; the guard also
threads the per-call retry count and dedup memory window
(context.extra["_idempotency_retry_count"] / ["_idempotency_ttl"]) so the
hook marks with the same window the caller requested.

Fail behavior:
- A gate *decision* of SKIP (already completed) or ABORT (a concurrent
  in-flight duplicate) is fail-CLOSED — the guard rejects so the side effect
  does not run twice, mirroring the ``@idempotent`` decorator's shared decision
  contract.
- A cache *I/O exception* during the check is fail-CLOSED by default (an
  explicit ``idempotency_key=`` is a "must not duplicate" signal, so a transient
  blip must not let a duplicate through); opt into fail-open via
  ``IdempotencySettings.fail_open_on_cache_error`` or the per-call
  ``fail_open`` override.
- The post-execution mark (hook) stays fail-open: a transient mark failure is
  logged but never blocks the already-completed call.

The cache-backed gate is resolved once (memoized) via the same ProviderRegistry
path the ``@idempotent`` decorator uses, so the guard/hook dedup against the
registered distributed cache (or a shared in-process fallback when none is
registered) instead of the bare ``cache=None`` singleton, which would never
block a duplicate.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import structlog

from baldur.adapters.cache.memory_adapter import InMemoryCacheAdapter
from baldur.interfaces.resilience_policy import GuardResult, PolicyResult

if TYPE_CHECKING:
    from baldur.core.idempotency_gate import IdempotencyGate
    from baldur.interfaces.resilience_policy import PolicyContext

logger = structlog.get_logger()

__all__ = ["IdempotencyGuard", "IdempotencyHook"]

# Single source for the guard's ``name`` (D8). The facade's reject-mapping
# (protect_facade._finalize_value) imports and compares against this constant
# instead of re-typing the bare literal, so a guard rename cannot silently route
# idempotency rejects to the defensive fallback.
_GUARD_NAME = "idempotency"


# Module-level fallback cache used when ProviderRegistry has no cache adapter
# registered (single-process / OSS deployments). Distinct ``key_prefix`` from
# the decorator's ``_FALLBACK_CACHE`` and the service layer's so the three
# layers cannot collide on keys when all run in-process in a single worker.
_POLICY_FALLBACK_CACHE = InMemoryCacheAdapter(key_prefix="idempotency_policy:")

# Lazily-built, memoized cache-backed gate shared by the guard's Phase-1
# acquire and the hook's Phase-2 mark so both observe one cache. Lock-free —
# mirrors the decorator's per-wrapper ``gate_state`` rationale (the race is
# benign; the same gate would be built twice at worst, never inconsistently).
_policy_gate_state: dict[str, Any] = {"initialized": False, "gate": None}


def _ensure_policy_gate() -> IdempotencyGate:
    """Return the memoized cache-backed ``IdempotencyGate`` for the policy layer.

    Builds the gate once from a ProviderRegistry-resolved cache (or the shared
    in-process fallback when no adapter is registered), reusing the decorator's
    proven resolver path. In production with no registered cache adapter and the
    escape hatch off, :func:`resolve_cache_via_registry` raises
    ``ConfigurationError`` (fail-closed).
    """
    if not _policy_gate_state["initialized"]:
        from baldur.core.idempotency_gate import IdempotencyGate
        from baldur.services.idempotency._cache_resolver import (
            resolve_cache_via_registry,
        )

        cache = resolve_cache_via_registry(
            layer="policy",
            fallback_cache=_POLICY_FALLBACK_CACHE,
            raise_on_prod_no_toggle=True,
        )
        _policy_gate_state["gate"] = IdempotencyGate(cache=cache)
        _policy_gate_state["initialized"] = True
    return _policy_gate_state["gate"]


def _reset_policy_gate() -> None:
    """Test helper — clear the memoized gate, replace the fallback cache, and
    clear the shared resolver's one-shot WARN guard.

    Replacing ``_POLICY_FALLBACK_CACHE`` (rather than only clearing the gate)
    ensures prior-test dedup state cannot leak into the next test — mirroring
    the decorator's ``_reset_fallback_cache``. Wired into
    ``reset_protect_caches()`` so settings/cache resets between tests invalidate
    the policy gate too.
    """
    from baldur.services.idempotency._cache_resolver import _reset_warned_layers

    global _POLICY_FALLBACK_CACHE
    _POLICY_FALLBACK_CACHE = InMemoryCacheAdapter(key_prefix="idempotency_policy:")
    _policy_gate_state["initialized"] = False
    _policy_gate_state["gate"] = None
    _reset_warned_layers()


class IdempotencyGuard:
    """Pre-execution idempotency check guard.

    Phase 1: Checks whether the operation is already completed (SKIP) or being
    executed concurrently (ABORT) via IdempotencyGate. On a CONTINUE decision it
    stores the acquired key in context.extra for IdempotencyHook to complete
    Phase 2; on SKIP/ABORT it rejects (fail-closed). A cache I/O error fails
    closed by default — opt into fail-open via ``fail_open`` /
    ``IdempotencySettings.fail_open_on_cache_error``.

    ``ttl`` is the dedup memory window (how long a completed/failed record
    blocks duplicates); the guard stores it in ``context.extra`` on CONTINUE
    so the hook's ``mark_*`` uses the same window — the guard is the single
    source, making a guard/hook window mismatch structurally impossible.
    ``execution_ttl`` is the in-flight execution window passed to
    ``check_and_acquire`` (claim TTL + stale-takeover bound). ``None`` for
    either defers to the gate defaults.
    """

    # Reference: docs/impl/595_IDEMPOTENT_DEDUP_CONTRACT.md D4 — same
    # threading channel as _idempotency_key / _idempotency_retry_count.

    def __init__(
        self,
        key_generator: Callable[[PolicyContext], str],
        fail_open: bool | None = None,
        ttl: timedelta | None = None,
        execution_ttl: timedelta | None = None,
    ) -> None:
        from baldur.settings.idempotency import get_idempotency_settings

        settings = get_idempotency_settings()
        self._globally_enabled = settings.enabled
        # Cache-error fail direction (D9). ``None`` consults the global posture;
        # an explicit per-call bool (threaded from the facade) overrides it.
        self._fail_open_on_cache_error = (
            settings.fail_open_on_cache_error if fail_open is None else fail_open
        )
        self._key_fn = key_generator
        self._ttl = ttl
        self._execution_ttl = execution_ttl
        # Resolve the cache-backed gate at construction so a production
        # misconfiguration (no registered cache adapter + escape hatch off)
        # surfaces loudly here — propagating out of the facade's composer
        # build — rather than being swallowed by the fail-open ``check()``.
        # Idempotency is a correctness gate, not a side-effect, so it is
        # fail-closed in prod (CROSS_SERVICE_STANDARDS §3). Gated on
        # ``enabled`` so a globally-disabled feature never raises.
        if self._globally_enabled:
            _ensure_policy_gate()

    @property
    def name(self) -> str:
        return _GUARD_NAME

    def check(self, context: PolicyContext | None = None) -> GuardResult:
        if context is None:
            return GuardResult(allowed=True)

        if not self._globally_enabled:
            return GuardResult(allowed=True)

        key = ""
        try:
            from baldur.core.idempotency_gate import IdempotencyDecision

            key = self._key_fn(context)
            gate = _ensure_policy_gate()
            result = gate.check_and_acquire(key, ttl=self._execution_ttl)
            if result.decision == IdempotencyDecision.SKIP:
                # Block: already completed. WARN reuses the decorator's exact
                # event name so one log query catches a block on either surface.
                logger.warning(
                    "idempotency.duplicate_blocked",
                    key=key,
                    decision="SKIP",
                )
                return GuardResult(
                    allowed=False,
                    reason=f"Already processed (idempotency key: {key})",
                    metadata={
                        "idempotency_decision": result.decision.name,
                        "idempotency_key": key,
                        "cached_result": result.cached_result,
                    },
                )
            if result.decision == IdempotencyDecision.ABORT:
                # Block: a concurrent process holds the key (in-doubt window).
                logger.warning(
                    "idempotency.execution_blocked",
                    key=key,
                    decision="ABORT",
                )
                return GuardResult(
                    allowed=False,
                    reason=f"Another process is executing (idempotency key: {key})",
                    metadata={
                        "idempotency_decision": result.decision.name,
                        "idempotency_key": key,
                    },
                )
            # CONTINUE — store key + retry_count + memory ttl for Hook to
            # forward on mark (the guard is the single window source).
            context.extra["_idempotency_key"] = key
            context.extra["_idempotency_retry_count"] = result.retry_count
            context.extra["_idempotency_ttl"] = self._ttl
            return GuardResult(allowed=True)
        except Exception as e:
            # Cache I/O fault (e.g. Redis down) or key-generation error. Log the
            # fail-open/closed decision so a silent degradation is observable
            # (LOGGING_STANDARDS §3.2). Fail CLOSED by default to prevent a
            # duplicate side effect on a blip; opt-in fail-open trades that
            # guarantee for availability.
            logger.warning(
                "idempotency.guard_check_failed",
                error=str(e),
                fail_open=self._fail_open_on_cache_error,
            )
            if self._fail_open_on_cache_error:
                return GuardResult(allowed=True)
            return GuardResult(
                allowed=False,
                reason="Idempotency check unavailable (cache error); failing closed.",
                metadata={
                    "idempotency_unavailable": True,
                    "idempotency_key": key,
                    "error": str(e),
                },
            )


class IdempotencyHook:
    """Post-execution idempotency mark hook (fail-open).

    Phase 2: On success, marks the key as completed via IdempotencyGate.
    On failure, marks as failed so the key can be retried.
    """

    def on_success(
        self,
        policy_name: str,
        result: PolicyResult,
        context: PolicyContext | None = None,
    ) -> None:
        key = self._get_key(context)
        if key:
            try:
                _ensure_policy_gate().mark_completed(
                    key,
                    retry_count=self._get_retry_count(context),
                    ttl=self._get_ttl(context),
                )
            except Exception as e:
                # Fail-open: the call already succeeded, so a mark failure must
                # never raise. Log so the silent degradation is observable.
                logger.warning(
                    "idempotency.mark_completed_failed",
                    key=key,
                    error=str(e),
                    fail_open=True,
                )

    def on_failure(
        self,
        policy_name: str,
        error: Exception,
        attempt: int,
        context: PolicyContext | None = None,
    ) -> None:
        key = self._get_key(context)
        if key:
            try:
                _ensure_policy_gate().mark_failed(
                    key,
                    error=str(error),
                    retry_count=self._get_retry_count(context),
                    ttl=self._get_ttl(context),
                )
            except Exception as e:
                # Fail-open: marking the failure is best-effort; the original
                # error has already propagated. Log the silent degradation.
                logger.warning(
                    "idempotency.mark_failed_failed",
                    key=key,
                    error=str(e),
                    fail_open=True,
                )

    def on_execute(
        self, policy_name: str, attempt: int, context: PolicyContext | None = None
    ) -> None:
        pass

    def on_retry(
        self,
        policy_name: str,
        attempt: int,
        delay: float,
        context: PolicyContext | None = None,
    ) -> None:
        pass

    def on_reject(
        self, guard_name: str, reason: str, context: PolicyContext | None = None
    ) -> None:
        pass

    @staticmethod
    def _get_key(context: PolicyContext | None) -> str | None:
        if context is None:
            return None
        return (context.extra or {}).get("_idempotency_key")

    @staticmethod
    def _get_retry_count(context: PolicyContext | None) -> int:
        if context is None:
            return 0
        return (context.extra or {}).get("_idempotency_retry_count", 0)

    @staticmethod
    def _get_ttl(context: PolicyContext | None) -> timedelta | None:
        """Memory window threaded from the guard; ``None`` → gate default."""
        if context is None:
            return None
        return (context.extra or {}).get("_idempotency_ttl")
