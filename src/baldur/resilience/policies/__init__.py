"""
Resilience Policies — 통합 Policy 패키지.

각 resilience 패턴의 순수 Policy 구현과 PolicyComposer 조합 엔진을 제공한다.

사용 예시::

    from baldur.resilience.policies import compose, FallbackPolicy
    result = compose(
        FallbackPolicy(default_value={"degraded": True}),
    ).execute(lambda: fetch_a())

Note:
    HedgingPolicy, AsyncHedgingPolicy, HedgingConfigUpdateHook은
    core.hedging과의 순환 참조로 인해 lazy import로 제공된다.
    from baldur.resilience.policies.hedging import HedgingPolicy 직접 사용 권장.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Core interfaces (re-export from interfaces)
from baldur.interfaces.resilience_policy import (
    AsyncResiliencePolicy,
    PolicyContext,
    PolicyOutcome,
    PolicyRejectedException,
    PolicyResult,
    ResiliencePolicy,
)

# Composer
from baldur.resilience.policies.composer import (
    AsyncPolicyComposer,
    PolicyComposer,
    compose,
    compose_async,
)

# Policies — Fallback (순환 참조 없음)
from baldur.resilience.policies.fallback import (
    AsyncFallbackPolicy,
    FallbackPolicy,
    partition_aware_chain,
)

# Guards
from baldur.resilience.policies.guards import (
    BackpressureGuard,
    ErrorBudgetGuard,
    FullStopGuard,
    KillSwitchGuard,
    LoadSheddingGuard,
    ThrottleGovernanceGuard,
    create_default_full_stop_guard,
)

# Hooks
from baldur.resilience.policies.hooks import (
    AuditHook,
    EventBusHook,
    MetricsHook,
)

# Presets
from baldur.resilience.policies.presets import ha_pipeline, standard_pipeline
from baldur.resilience.policies.retry import (
    AsyncRetryPolicy,
    async_retry_policy,
    retried_async,
)

# Sinks
from baldur.resilience.policies.sinks import DLQSink

# Policies — Timeout
from baldur.resilience.policies.timeout import AsyncTimeoutPolicy, TimeoutPolicy
from baldur.services.circuit_breaker.policy import CircuitBreakerPolicy
from baldur.services.retry_handler.policy import RetryPolicy

if TYPE_CHECKING:
    from baldur.resilience.policies.hedging import (
        AsyncHedgingPolicy,
        HedgingConfigUpdateHook,
        HedgingPolicy,
    )

# BulkheadPolicy and ThrottlePolicy are PRO-tier (Bulkhead 519 PR 3, Throttle
# inherited from before). They resolve via ``__getattr__`` below so the module
# does not carry a module-level OSS↔PRO import. IDE/mypy treat them as ``Any``
# at this re-export site; consumers needing precise typing can import the
# concrete class from its PRO submodule directly.


def __getattr__(name: str):
    """Lazy import for PRO-tier policies (BulkheadPolicy/ThrottlePolicy) and
    the in-tree Hedging policies (which use deferred imports to break the
    OSS↔PRO module-load cycle — see ``hedging.py`` for the rationale)."""
    _hedging_names = {"AsyncHedgingPolicy", "HedgingConfigUpdateHook", "HedgingPolicy"}
    if name in _hedging_names:
        try:
            from baldur.resilience.policies import hedging as _hedging_mod

            return getattr(_hedging_mod, name)
        except ImportError as e:
            raise AttributeError(
                f"Cannot import {name} from hedging module: {e}"
            ) from e
    if name == "BulkheadPolicy":
        try:
            from baldur_pro.services.bulkhead.policy import BulkheadPolicy

            return BulkheadPolicy
        except ImportError as e:
            raise AttributeError(f"Cannot import BulkheadPolicy (PRO tier): {e}") from e
    if name == "ThrottlePolicy":
        try:
            from baldur_pro.services.throttle.policy import ThrottlePolicy

            return ThrottlePolicy
        except ImportError as e:
            raise AttributeError(f"Cannot import ThrottlePolicy (PRO tier): {e}") from e
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Core interfaces
    "AsyncResiliencePolicy",
    "PolicyContext",
    "PolicyOutcome",
    "PolicyRejectedException",
    "PolicyResult",
    "ResiliencePolicy",
    # Composer
    "AsyncPolicyComposer",
    "PolicyComposer",
    "compose",
    "compose_async",
    # Policies
    "AsyncFallbackPolicy",
    "AsyncHedgingPolicy",
    "AsyncRetryPolicy",
    "AsyncTimeoutPolicy",
    "BulkheadPolicy",
    "CircuitBreakerPolicy",
    "FallbackPolicy",
    "HedgingConfigUpdateHook",
    "HedgingPolicy",
    "RetryPolicy",
    "ThrottlePolicy",
    "TimeoutPolicy",
    "async_retry_policy",
    "partition_aware_chain",
    "retried_async",
    # Guards
    "BackpressureGuard",
    "ErrorBudgetGuard",
    "FullStopGuard",
    "KillSwitchGuard",
    "LoadSheddingGuard",
    "ThrottleGovernanceGuard",
    "create_default_full_stop_guard",
    # Hooks
    "AuditHook",
    "EventBusHook",
    "MetricsHook",
    # Sinks
    "DLQSink",
    # Presets
    "ha_pipeline",
    "standard_pipeline",
]
