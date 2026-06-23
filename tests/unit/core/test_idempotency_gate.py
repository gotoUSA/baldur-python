"""Unit tests for core/idempotency_gate.py — IdempotencyGate.

Verification techniques applied:
- Contract: IdempotencyDecision enum values, IDEMPOTENCY_DEFAULT_TTL_SECONDS
- State transition: CONTINUE→SKIP (via mark_completed), CONTINUE→ABORT (concurrent)
- Idempotency: duplicate check returns SKIP
- Edge case: cache=None (no-op mode), non-dict existing value
- Singleton lifecycle: get_idempotency_gate / reset_idempotency_gate
- 595 D3/D5: ``mark_*`` optional ``ttl`` (None → settings memory default,
  explicit → forwarded to cas_dict_field); constructor window split
  (``execution_ttl_seconds`` / ``memory_ttl_seconds``); per-use settings
  resolution (env retune via reset_idempotency_settings).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest

from baldur.core.exceptions import ConfigurationError
from baldur.core.idempotency_gate import (
    IDEMPOTENCY_DEFAULT_TTL_SECONDS,
    IdempotencyDecision,
    IdempotencyGate,
    get_idempotency_gate,
    reset_idempotency_gate,
)


class _FakeAtomicCache:
    """Fake cache with atomic setnx + cas_dict_field for testing IdempotencyGate.

    Implements just enough to pass _validate_atomic_setnx /
    _validate_atomic_cas_dict_field and support test scenarios with
    controllable return values.
    """

    def __init__(self, setnx_return: bool = True):
        self._store: dict[str, Any] = {}
        self._setnx_return = setnx_return
        self._setnx_calls: list[tuple] = []
        self._set_calls: list[tuple] = []
        self._delete_calls: list[str] = []
        self._get_calls: list[str] = []
        self._cas_calls: list[tuple] = []

    def setnx(self, key: str, value: Any, ttl=None) -> bool:
        self._setnx_calls.append((key, value, ttl))
        if key not in self._store:
            if self._setnx_return:
                self._store[key] = value
                return True
            return False
        return False

    def get(self, key: str) -> Any:
        self._get_calls.append(key)
        return self._store.get(key)

    def set(self, key: str, value: Any, ttl=None) -> bool:
        self._set_calls.append((key, value, ttl))
        self._store[key] = value
        return True

    def delete(self, key: str) -> bool:
        self._delete_calls.append(key)
        return self._store.pop(key, None) is not None

    def exists(self, key: str) -> bool:
        return key in self._store

    def cas_dict_field(
        self,
        key: str,
        field: str,
        expected: Any,
        new_value: dict[str, Any],
        ttl=None,
    ) -> bool:
        self._cas_calls.append((key, field, expected, new_value, ttl))
        existing = self._store.get(key)
        if not isinstance(existing, dict):
            return False
        if existing.get(field) != expected:
            return False
        self._store[key] = new_value
        return True


def _make_mock_cache(setnx_return: bool = True) -> _FakeAtomicCache:
    """Create a fake cache with atomic setnx for IdempotencyGate tests."""
    return _FakeAtomicCache(setnx_return=setnx_return)


# ── Contract Tests ──────────────────────────────────────────


class TestIdempotencyGateContract:
    """Design contract values for IdempotencyGate."""

    def test_default_ttl_is_1800_seconds(self):
        """IDEMPOTENCY_DEFAULT_TTL_SECONDS design contract: 1800 (30 min)."""
        assert IDEMPOTENCY_DEFAULT_TTL_SECONDS == 1800

    def test_decision_continue_value(self):
        """IdempotencyDecision.CONTINUE == 'continue'."""
        assert IdempotencyDecision.CONTINUE == "continue"

    def test_decision_skip_value(self):
        """IdempotencyDecision.SKIP == 'skip'."""
        assert IdempotencyDecision.SKIP == "skip"

    def test_decision_abort_value(self):
        """IdempotencyDecision.ABORT == 'abort'."""
        assert IdempotencyDecision.ABORT == "abort"

    def test_decision_is_str_enum(self):
        """IdempotencyDecision values are JSON-serializable strings."""
        for member in IdempotencyDecision:
            assert isinstance(member.value, str)

    def test_ctor_execution_default_is_module_constant(self):
        """595 D5 ctor split: ``execution_ttl_seconds`` defaults to the plain
        module constant (1800 s), NOT a settings read."""
        gate = IdempotencyGate()
        assert gate._execution_ttl_seconds == IDEMPOTENCY_DEFAULT_TTL_SECONDS

    def test_ctor_memory_default_is_none_sentinel(self):
        """595 D5 ctor split: ``memory_ttl_seconds`` defaults to the ``None``
        sentinel (→ per-use settings resolution at mark time)."""
        gate = IdempotencyGate()
        assert gate._memory_ttl_seconds is None

    def test_check_and_acquire_none_ttl_uses_execution_default_not_memory_setting(
        self, monkeypatch
    ):
        """595 D2/D5: the acquire path's default window is the execution
        constant — tuning the memory setting must not change it."""
        from baldur.settings.idempotency import reset_idempotency_settings

        monkeypatch.setenv("BALDUR_IDEMPOTENCY_GATE_MEMORY_TTL_SECONDS", "120")
        reset_idempotency_settings()
        try:
            cache = _make_mock_cache(setnx_return=True)
            gate = IdempotencyGate(cache=cache)

            gate.check_and_acquire("key-exec-default")

            assert cache._setnx_calls[0][2] == timedelta(
                seconds=IDEMPOTENCY_DEFAULT_TTL_SECONDS
            )
        finally:
            reset_idempotency_settings()


# ── Behavior Tests ──────────────────────────────────────────


class TestIdempotencyGateNoCacheBehavior:
    """Behavior when cache is None (no-op mode)."""

    def test_no_cache_always_returns_continue(self):
        """cache=None always returns CONTINUE."""
        gate = IdempotencyGate(cache=None)
        result = gate.check_and_acquire("any-key")
        assert result.decision == IdempotencyDecision.CONTINUE

    def test_no_cache_mark_completed_is_noop(self):
        """mark_completed is a no-op when cache=None."""
        gate = IdempotencyGate(cache=None)
        gate.mark_completed("key", {"result": "ok"})  # Should not raise

    def test_no_cache_mark_failed_is_noop(self):
        """mark_failed is a no-op when cache=None."""
        gate = IdempotencyGate(cache=None)
        gate.mark_failed("key", "error")  # Should not raise


class TestIdempotencyGateCheckAndAcquireBehavior:
    """State transition behavior for check_and_acquire."""

    def test_first_check_returns_continue(self):
        """First check: setnx succeeds → CONTINUE."""
        cache = _make_mock_cache(setnx_return=True)
        gate = IdempotencyGate(cache=cache)
        result = gate.check_and_acquire("key-1")
        assert result.decision == IdempotencyDecision.CONTINUE

    def test_completed_status_returns_skip_with_cached_result(self):
        """Key in completed status → SKIP + cached_result."""
        cache = _make_mock_cache(setnx_return=False)
        # Pre-populate with completed record
        cache._store["key-done"] = {
            "status": "completed",
            "result": {"output": "success"},
            "retry_count": 1,
        }

        gate = IdempotencyGate(cache=cache)
        result = gate.check_and_acquire("key-done")

        assert result.decision == IdempotencyDecision.SKIP
        assert result.cached_result == {"output": "success"}
        assert result.retry_count == 1

    def test_executing_status_within_ttl_returns_abort(self):
        """executing status within TTL → ABORT."""
        import time

        cache = _make_mock_cache(setnx_return=False)
        cache._store["key-running"] = {
            "status": "executing",
            "started_at": time.time(),
            "retry_count": 0,
        }

        gate = IdempotencyGate(cache=cache)
        result = gate.check_and_acquire("key-running")
        assert result.decision == IdempotencyDecision.ABORT

    def test_failed_status_returns_continue_with_incremented_retry(self):
        """failed status → delete+setnx succeeds → CONTINUE, retry_count incremented."""
        cache = _make_mock_cache(setnx_return=True)
        cache._store["key-failed"] = {
            "status": "failed",
            "retry_count": 2,
        }

        gate = IdempotencyGate(cache=cache)
        result = gate.check_and_acquire("key-failed")

        assert result.decision == IdempotencyDecision.CONTINUE
        assert result.retry_count == 3
        # delete was called to remove the failed record
        assert "key-failed" in cache._delete_calls

    def test_failed_status_setnx_race_returns_abort(self):
        """failed status → another process wins the setnx after delete → ABORT."""
        cache = _make_mock_cache(setnx_return=False)
        cache._store["key-failed"] = {
            "status": "failed",
            "retry_count": 1,
        }

        gate = IdempotencyGate(cache=cache)
        result = gate.check_and_acquire("key-failed")

        # delete removes the key, but setnx_return=False simulates
        # another process winning the setnx race
        assert result.decision == IdempotencyDecision.ABORT

    def test_stale_executing_returns_continue_via_delete_setnx(self):
        """executing status past TTL → delete+setnx succeeds → CONTINUE."""
        import time

        cache = _make_mock_cache(setnx_return=True)
        cache._store["key-stale"] = {
            "status": "executing",
            "started_at": time.time() - 7200,  # 2 hours ago (TTL=1800s)
            "retry_count": 1,
        }

        gate = IdempotencyGate(cache=cache)
        result = gate.check_and_acquire("key-stale")

        assert result.decision == IdempotencyDecision.CONTINUE
        assert result.retry_count == 2
        assert "key-stale" in cache._delete_calls

    def test_stale_executing_setnx_race_returns_abort(self):
        """executing status past TTL → setnx fails after delete → ABORT."""
        import time

        cache = _make_mock_cache(setnx_return=False)
        cache._store["key-stale"] = {
            "status": "executing",
            "started_at": time.time() - 7200,
            "retry_count": 0,
        }

        gate = IdempotencyGate(cache=cache)
        result = gate.check_and_acquire("key-stale")

        assert result.decision == IdempotencyDecision.ABORT

    def test_unknown_status_returns_abort(self):
        """Unknown status → ABORT (defensive)."""
        cache = _make_mock_cache(setnx_return=False)
        cache._store["key-weird"] = {"status": "unknown_state"}

        gate = IdempotencyGate(cache=cache)
        result = gate.check_and_acquire("key-weird")
        assert result.decision == IdempotencyDecision.ABORT

    def test_non_dict_existing_value_returns_abort(self):
        """Non-dict existing value → ABORT."""
        cache = _make_mock_cache(setnx_return=False)
        cache._store["key-bad"] = "not-a-dict"

        gate = IdempotencyGate(cache=cache)
        result = gate.check_and_acquire("key-bad")
        assert result.decision == IdempotencyDecision.ABORT


class TestIdempotencyGateMarkBehavior:
    """mark_completed / mark_failed transition behavior."""

    def test_mark_completed_sets_completed_status(self):
        """mark_completed stores the record with status='completed'."""
        cache = _make_mock_cache()
        cache._store["key-1"] = {"status": "executing", "retry_count": 1}

        gate = IdempotencyGate(cache=cache)
        gate.mark_completed("key-1", result={"data": "ok"}, retry_count=1)

        saved = cache._store["key-1"]
        assert saved["status"] == "completed"
        assert saved["result"] == {"data": "ok"}
        assert saved["retry_count"] == 1

    def test_mark_failed_sets_failed_status(self):
        """mark_failed stores the record with status='failed'."""
        cache = _make_mock_cache()
        cache._store["key-1"] = {"status": "executing", "retry_count": 0}

        gate = IdempotencyGate(cache=cache)
        gate.mark_failed("key-1", error="step crashed")

        saved = cache._store["key-1"]
        assert saved["status"] == "failed"
        assert saved["error"] == "step crashed"

    def test_mark_completed_does_not_read_via_get(self):
        """mark_completed does not read via cache.get (G1 regression guard)."""
        cache = _make_mock_cache()
        cache._store["key-1"] = {"status": "executing", "retry_count": 0}

        gate = IdempotencyGate(cache=cache)
        gate.mark_completed("key-1", result={"data": "ok"})

        assert "key-1" not in cache._get_calls
        assert any(call[0] == "key-1" for call in cache._cas_calls)

    def test_mark_failed_does_not_read_via_get(self):
        """mark_failed does not read via cache.get (G2 regression guard)."""
        cache = _make_mock_cache()
        cache._store["key-1"] = {"status": "executing", "retry_count": 0}

        gate = IdempotencyGate(cache=cache)
        gate.mark_failed("key-1", error="boom")

        assert "key-1" not in cache._get_calls
        assert any(call[0] == "key-1" for call in cache._cas_calls)

    def test_mark_completed_cas_conflict_skips_write(self):
        """mark_completed does not overwrite the record when status is not executing."""
        cache = _make_mock_cache()
        cache._store["key-1"] = {"status": "completed", "retry_count": 0}

        gate = IdempotencyGate(cache=cache)
        gate.mark_completed("key-1", result={"data": "ok"})

        saved = cache._store["key-1"]
        assert saved["status"] == "completed"
        assert "result" not in saved or saved.get("result") != {"data": "ok"}


class TestIdempotencyGateValidationBehavior:
    """Atomic setnx + cas_dict_field validation behavior."""

    def test_non_atomic_setnx_raises_configuration_error(self):
        """A non-atomic setnx implementation raises ConfigurationError."""
        from baldur.interfaces.cache_provider import CacheProviderInterface

        class BadCache(CacheProviderInterface):
            """Cache that inherits non-atomic setnx from base."""

            @property
            def provider_name(self) -> str:
                return "bad_cache"

            def get(self, key): ...
            def set(self, key, value, ttl=None): ...
            def delete(self, key): ...
            def exists(self, key): ...
            def incr(self, key, amount=1): ...
            def decr(self, key, amount=1): ...
            def expire(self, key, ttl): ...
            def ttl(self, key): ...
            def get_lock(self, name, timeout=None, blocking_timeout=None): ...
            def mget(self, keys): ...
            def mset(self, mapping, ttl=None): ...
            def health_check(self): ...
            def flush_all(self): ...
            def ping(self): ...

        with pytest.raises(ConfigurationError, match="atomic setnx"):
            IdempotencyGate(cache=BadCache())

    def test_non_atomic_cas_dict_field_raises_configuration_error(self):
        """A non-atomic cas_dict_field implementation raises ConfigurationError."""
        from baldur.interfaces.cache_provider import CacheProviderInterface

        class BadCacheCAS(CacheProviderInterface):
            """Cache with atomic setnx but non-atomic cas_dict_field (base default)."""

            @property
            def provider_name(self) -> str:
                return "bad_cache_cas"

            def setnx(self, key, value, ttl=None):
                return True

            def get(self, key): ...
            def set(self, key, value, ttl=None): ...
            def delete(self, key): ...
            def exists(self, key): ...
            def incr(self, key, amount=1): ...
            def decr(self, key, amount=1): ...
            def expire(self, key, ttl): ...
            def ttl(self, key): ...
            def get_lock(self, name, timeout=None, blocking_timeout=None): ...
            def mget(self, keys): ...
            def mset(self, mapping, ttl=None): ...
            def health_check(self): ...
            def flush_all(self): ...
            def ping(self): ...

        with pytest.raises(ConfigurationError, match="atomic cas_dict_field"):
            IdempotencyGate(cache=BadCacheCAS())

    def test_metrics_wrapped_non_atomic_adapter_still_raises(self):
        """A non-atomic adapter is caught even behind the metrics decorator.

        Registry-resolved caches arrive wrapped in ``MetricsAwareCacheAdapter``,
        which overrides setnx/cas_dict_field to delegate — so an un-unwrapped
        validator would always pass and silently admit a non-atomic underlying
        adapter. The gate unwraps to the concrete adapter before validating, so
        the check still fires on the wrapped shape.
        """
        from baldur.adapters.cache.metrics_decorator import (
            MetricsAwareCacheAdapter,
        )
        from baldur.interfaces.cache_provider import CacheProviderInterface

        class NonAtomicAdapter(CacheProviderInterface):
            """Inherits the non-atomic setnx/cas_dict_field base defaults."""

            @property
            def provider_name(self) -> str:
                return "non_atomic"

            def get(self, key): ...
            def set(self, key, value, ttl=None): ...
            def delete(self, key): ...
            def exists(self, key): ...
            def incr(self, key, amount=1): ...
            def decr(self, key, amount=1): ...
            def expire(self, key, ttl): ...
            def ttl(self, key): ...
            def get_lock(self, name, timeout=None, blocking_timeout=None): ...
            def mget(self, keys): ...
            def mset(self, mapping, ttl=None): ...
            def health_check(self): ...
            def flush_all(self): ...
            def ping(self): ...

        wrapped = MetricsAwareCacheAdapter(NonAtomicAdapter())
        with pytest.raises(ConfigurationError, match="atomic setnx"):
            IdempotencyGate(cache=wrapped)

    def test_metrics_wrapped_atomic_adapter_passes_and_is_stored_verbatim(self):
        """A metrics-wrapped atomic adapter (the production shape) validates.

        Unwrapping is for validation only — ``_cache`` retains the wrapped
        instance so cache ops still flow through the metrics decorator.
        """
        from baldur.adapters.cache.memory_adapter import InMemoryCacheAdapter
        from baldur.adapters.cache.metrics_decorator import (
            MetricsAwareCacheAdapter,
        )

        wrapped = MetricsAwareCacheAdapter(InMemoryCacheAdapter(key_prefix="t:"))
        gate = IdempotencyGate(cache=wrapped)

        assert gate._cache is wrapped


class TestIdempotencyGateDecisionMetricBehavior:
    """566 D9 — ``check_and_acquire`` records the decision on the real-cache path.

    The decision counter (``baldur_idempotency_gate_decision_total{decision}``)
    is recorded once at the gate, the single choke point shared by every
    consumer. The ``cache=None`` no-op path is deliberately un-metered so "no
    gate installed" is not conflated with "a real gate said continue".
    """

    def test_real_cache_continue_records_continue_decision(self):
        """setnx success → CONTINUE → records ``continue`` (D9)."""
        cache = _make_mock_cache(setnx_return=True)
        gate = IdempotencyGate(cache=cache)

        with patch("baldur.metrics.prometheus.get_metrics") as mock_get:
            result = gate.check_and_acquire("key-1")

        assert result.decision == IdempotencyDecision.CONTINUE
        mock_get.return_value.idempotency.record_gate_decision.assert_called_once_with(
            "continue"
        )

    def test_real_cache_skip_records_skip_decision(self):
        """completed record → SKIP → records ``skip`` (D9)."""
        cache = _make_mock_cache(setnx_return=False)
        cache._store["key-done"] = {
            "status": "completed",
            "result": {"output": "ok"},
            "retry_count": 0,
        }
        gate = IdempotencyGate(cache=cache)

        with patch("baldur.metrics.prometheus.get_metrics") as mock_get:
            result = gate.check_and_acquire("key-done")

        assert result.decision == IdempotencyDecision.SKIP
        mock_get.return_value.idempotency.record_gate_decision.assert_called_once_with(
            "skip"
        )

    def test_real_cache_abort_records_abort_decision(self):
        """executing-within-TTL record → ABORT → records ``abort`` (D9)."""
        import time

        cache = _make_mock_cache(setnx_return=False)
        cache._store["key-run"] = {
            "status": "executing",
            "started_at": time.time(),
            "retry_count": 0,
        }
        gate = IdempotencyGate(cache=cache)

        with patch("baldur.metrics.prometheus.get_metrics") as mock_get:
            result = gate.check_and_acquire("key-run")

        assert result.decision == IdempotencyDecision.ABORT
        mock_get.return_value.idempotency.record_gate_decision.assert_called_once_with(
            "abort"
        )

    def test_no_cache_noop_does_not_record(self):
        """The ``cache=None`` no-op path never touches the metrics registry (D9)."""
        gate = IdempotencyGate(cache=None)

        with patch("baldur.metrics.prometheus.get_metrics") as mock_get:
            result = gate.check_and_acquire("any-key")

        assert result.decision == IdempotencyDecision.CONTINUE
        # The early return precedes any metrics import — get_metrics is untouched.
        mock_get.assert_not_called()

    def test_metrics_failure_does_not_break_dedup(self):
        """Best-effort recording: a metrics failure cannot break the dedup path (R5)."""
        cache = _make_mock_cache(setnx_return=True)
        gate = IdempotencyGate(cache=cache)

        with patch(
            "baldur.metrics.prometheus.get_metrics",
            side_effect=RuntimeError("metrics down"),
        ):
            # No raise — the gate swallows observability failures.
            result = gate.check_and_acquire("key-1")

        assert result.decision == IdempotencyDecision.CONTINUE


class TestGateReleaseBehavior:
    """621 D6 — ``release()`` deletes the record, re-arming a future acquisition.

    Unlike ``mark_completed`` (which leaves a COMPLETED record that makes the
    next ``check_and_acquire`` SKIP), ``release`` clears the key entirely so the
    same logical key is re-acquirable — the cross-session re-arm the recovery
    compensation gate relies on. Idempotent and best-effort."""

    def test_release_makes_completed_key_reacquirable(self):
        """A released completed key is re-acquirable (CONTINUE, not SKIP)."""
        cache = _make_mock_cache(setnx_return=True)
        gate = IdempotencyGate(cache=cache)

        # Given — an acquired, then completed, record (would SKIP on re-check).
        gate.check_and_acquire("key-rearm")
        gate.mark_completed("key-rearm", result={"ok": True})
        assert gate.check_and_acquire("key-rearm").decision == IdempotencyDecision.SKIP

        # When — the record is released.
        gate.release("key-rearm")

        # Then — the key is re-acquirable.
        assert (
            gate.check_and_acquire("key-rearm").decision == IdempotencyDecision.CONTINUE
        )

    def test_release_deletes_the_record_from_cache(self):
        """release issues a single delete for the key."""
        cache = _make_mock_cache(setnx_return=True)
        gate = IdempotencyGate(cache=cache)
        gate.check_and_acquire("key-del")

        gate.release("key-del")

        assert "key-del" in cache._delete_calls
        assert "key-del" not in cache._store

    def test_release_missing_key_is_noop(self):
        """Releasing an absent key does not raise (idempotent)."""
        cache = _make_mock_cache(setnx_return=True)
        gate = IdempotencyGate(cache=cache)

        gate.release("never-seen")  # Should not raise

        assert (
            gate.check_and_acquire("never-seen").decision
            == IdempotencyDecision.CONTINUE
        )

    def test_release_no_cache_is_noop(self):
        """release is a no-op when cache=None (unconfigured gate)."""
        gate = IdempotencyGate(cache=None)
        gate.release("any-key")  # Should not raise

    def test_release_swallows_cache_error(self):
        """A cache delete failure is swallowed (best-effort)."""

        class _RaisingDeleteCache(_FakeAtomicCache):
            def delete(self, key):
                raise RuntimeError("cache down")

        gate = IdempotencyGate(cache=_RaisingDeleteCache())

        gate.release("key-boom")  # Should not raise


class TestIdempotencyGateSingletonBehavior:
    """Singleton lifecycle behavior."""

    def setup_method(self):
        reset_idempotency_gate()

    def teardown_method(self):
        reset_idempotency_gate()

    def test_get_returns_same_instance(self):
        """get_idempotency_gate() returns the same instance."""
        first = get_idempotency_gate()
        second = get_idempotency_gate()
        assert first is second

    def test_reset_clears_cached_instance(self):
        """A new instance is created after reset."""
        first = get_idempotency_gate()
        reset_idempotency_gate()
        second = get_idempotency_gate()
        assert first is not second

    def test_default_singleton_has_no_cache(self):
        """The default singleton has cache=None (no-op mode)."""
        gate = get_idempotency_gate()
        assert gate._cache is None


# ── 595 D3 — mark_* optional ttl (memory window) ────────────


class TestIdempotencyGateMarkTtlBehavior:
    """595 D3: ``mark_completed`` / ``mark_failed`` accept an optional memory
    ``ttl`` — explicit values forward verbatim to ``cas_dict_field``; ``None``
    resolves to the settings-driven memory default per use."""

    @pytest.fixture(autouse=True)
    def _reset_settings(self):
        from baldur.settings.idempotency import reset_idempotency_settings

        reset_idempotency_settings()
        yield
        reset_idempotency_settings()

    @staticmethod
    def _gate_with_executing_record(
        key: str,
    ) -> tuple[IdempotencyGate, _FakeAtomicCache]:
        cache = _make_mock_cache()
        cache._store[key] = {"status": "executing", "retry_count": 0}
        return IdempotencyGate(cache=cache), cache

    @pytest.mark.parametrize(
        "mark", ["mark_completed", "mark_failed"], ids=["completed", "failed"]
    )
    def test_explicit_ttl_forwarded_to_cas_dict_field(self, mark):
        """An explicit memory ttl reaches cas_dict_field unchanged."""
        gate, cache = self._gate_with_executing_record("key-ttl")
        explicit = timedelta(hours=2)

        getattr(gate, mark)("key-ttl", ttl=explicit)

        assert cache._cas_calls[0][4] is explicit

    @pytest.mark.parametrize(
        "mark", ["mark_completed", "mark_failed"], ids=["completed", "failed"]
    )
    def test_none_ttl_resolves_to_settings_memory_default(self, mark):
        """ttl=None → ``IdempotencySettings.gate_memory_ttl_seconds``."""
        from baldur.settings.idempotency import get_idempotency_settings

        gate, cache = self._gate_with_executing_record("key-default")

        getattr(gate, mark)("key-default")

        expected = timedelta(seconds=get_idempotency_settings().gate_memory_ttl_seconds)
        assert cache._cas_calls[0][4] == expected


# ── 595 D5 — settings-driven memory default (per-use resolution) ──


class TestIdempotencyGateMemoryDefaultBehavior:
    """595 D5: the ``None``-sentinel memory window resolves from
    ``BALDUR_IDEMPOTENCY_GATE_MEMORY_TTL_SECONDS`` per use (runtime-retunable
    via ``reset_idempotency_settings``); an explicit constructor override
    bypasses the settings read entirely."""

    @pytest.fixture(autouse=True)
    def _reset_settings(self):
        from baldur.settings.idempotency import reset_idempotency_settings

        reset_idempotency_settings()
        yield
        reset_idempotency_settings()

    def test_env_var_tunes_memory_default(self, monkeypatch):
        """The env-tuned setting reaches cas_dict_field on a default mark."""
        from baldur.settings.idempotency import reset_idempotency_settings

        monkeypatch.setenv("BALDUR_IDEMPOTENCY_GATE_MEMORY_TTL_SECONDS", "120")
        reset_idempotency_settings()

        cache = _make_mock_cache()
        cache._store["k"] = {"status": "executing", "retry_count": 0}
        gate = IdempotencyGate(cache=cache)

        gate.mark_completed("k")

        assert cache._cas_calls[0][4] == timedelta(seconds=120)

    def test_runtime_retune_is_read_per_mark_on_same_gate_instance(self, monkeypatch):
        """Per-use (not init-time) resolution: retuning env + settings reset
        changes the window of the NEXT mark on the same gate instance."""
        from baldur.settings.idempotency import reset_idempotency_settings

        # Given — a gate constructed while the window is 120 s.
        monkeypatch.setenv("BALDUR_IDEMPOTENCY_GATE_MEMORY_TTL_SECONDS", "120")
        reset_idempotency_settings()
        cache = _make_mock_cache()
        cache._store["k1"] = {"status": "executing", "retry_count": 0}
        cache._store["k2"] = {"status": "executing", "retry_count": 0}
        gate = IdempotencyGate(cache=cache)
        gate.mark_completed("k1")

        # When — the operator retunes the window to 240 s at runtime.
        monkeypatch.setenv("BALDUR_IDEMPOTENCY_GATE_MEMORY_TTL_SECONDS", "240")
        reset_idempotency_settings()
        gate.mark_failed("k2")

        # Then — the first mark used 120 s, the second 240 s.
        assert cache._cas_calls[0][4] == timedelta(seconds=120)
        assert cache._cas_calls[1][4] == timedelta(seconds=240)

    def test_ctor_memory_override_bypasses_settings(self, monkeypatch):
        """An explicit ``memory_ttl_seconds=`` wins over the env setting."""
        from baldur.settings.idempotency import reset_idempotency_settings

        monkeypatch.setenv("BALDUR_IDEMPOTENCY_GATE_MEMORY_TTL_SECONDS", "120")
        reset_idempotency_settings()

        cache = _make_mock_cache()
        cache._store["k"] = {"status": "executing", "retry_count": 0}
        gate = IdempotencyGate(cache=cache, memory_ttl_seconds=900)

        gate.mark_completed("k")

        assert cache._cas_calls[0][4] == timedelta(seconds=900)
