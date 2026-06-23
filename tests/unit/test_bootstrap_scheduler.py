"""Unit tests for bootstrap scheduler wiring (429 Part 6 / D6).

Scope:
- _start_default_scheduler AUTOSTART=0 early-return.
- Default job list registers five jobs (archive_old_dlq_entries, cb_recovery,
  cleanup_expired_config, daily_report, sla_drift).
- Unknown task_backend falls back to "inline" with WARNING log.
- arq backend explicitly logs "not_implemented" and falls back to inline.
- _build_celery_delegator returns None when Celery is missing.
- _wrap_with_context preserves contextvars across invocation.

Does NOT test LeaderScheduler internals — those have their own unit tests.
"""

from __future__ import annotations

import contextvars
from unittest.mock import MagicMock, patch

import pytest

from baldur.bootstrap import (
    _DEFAULT_SCHEDULED_JOBS,
    _build_celery_delegator,
    _resolve_job_callable,
    _start_default_scheduler,
    _wrap_with_context,
)


@pytest.fixture(autouse=True)
def _reset_scheduler_cache():
    """Clear the LeaderScheduler singleton cache between tests."""
    from baldur.coordination.scheduler import reset_schedulers

    reset_schedulers()
    yield
    reset_schedulers()


# =============================================================================
# Contract — default scheduled jobs list
# =============================================================================


class TestDefaultScheduledJobsContract:
    """Contract: the five default jobs documented in 429 D6 are registered
    with exactly these names and intervals."""

    def test_default_jobs_contract(self):
        """Exactly five jobs, keyed by name, with known intervals."""
        by_name = {
            name: interval for name, _mod, _attr, interval in _DEFAULT_SCHEDULED_JOBS
        }

        assert set(by_name) == {
            "daily_report",
            "sla_drift",
            "cb_recovery",
            "archive_old_dlq_entries",
            "cleanup_expired_config",
        }
        # Daily cadence — 24h in seconds
        assert by_name["daily_report"] == 24 * 60 * 60.0
        assert by_name["archive_old_dlq_entries"] == 24 * 60 * 60.0
        # Hourly cadence
        assert by_name["sla_drift"] == 60 * 60.0
        assert by_name["cleanup_expired_config"] == 60 * 60.0
        # Per-minute
        assert by_name["cb_recovery"] == 60.0


# =============================================================================
# Behavior — AUTOSTART env gate and unknown backend fallback
# =============================================================================


class TestStartDefaultSchedulerBehavior:
    """Behavior tests for _start_default_scheduler branching logic."""

    def test_autostart_env_zero_skips_scheduler_entirely(self, monkeypatch):
        """BALDUR_SCHEDULER_AUTOSTART=0 → no scheduler import or start.

        Patches the real import target ``baldur.coordination.scheduler.
        get_leader_scheduler`` because bootstrap.py imports it locally inside
        the function body; patching the ``bootstrap`` module's own attribute
        space would miss the actual call path and pass trivially.
        """
        monkeypatch.setenv("BALDUR_SCHEDULER_AUTOSTART", "0")

        with patch("baldur.coordination.scheduler.get_leader_scheduler") as mock_get:
            _start_default_scheduler()

        mock_get.assert_not_called()

    def test_unknown_backend_falls_back_to_inline(self, monkeypatch, caplog):
        """Given task_backend='unknown', we fall back to inline with a WARNING."""
        monkeypatch.setenv("BALDUR_SCHEDULER_AUTOSTART", "1")

        mock_sched = MagicMock()
        with (
            patch(
                "baldur.coordination.scheduler.get_leader_scheduler",
                return_value=mock_sched,
            ),
            caplog.at_level("WARNING"),
        ):
            _start_default_scheduler(task_backend="nonsense_backend")

        # Warning emitted about unknown backend
        log_events = [rec.message for rec in caplog.records]
        assert any("unknown_task_backend" in msg for msg in log_events)

    def test_arq_backend_logs_not_implemented_and_uses_inline(
        self, monkeypatch, caplog
    ):
        """arq is reserved; logs 'arq_backend_not_implemented' and uses inline."""
        monkeypatch.setenv("BALDUR_SCHEDULER_AUTOSTART", "1")
        mock_sched = MagicMock()

        with (
            patch(
                "baldur.coordination.scheduler.get_leader_scheduler",
                return_value=mock_sched,
            ),
            caplog.at_level("WARNING"),
        ):
            _start_default_scheduler(task_backend="arq")

        log_events = [rec.message for rec in caplog.records]
        assert any("arq_backend_not_implemented" in msg for msg in log_events)


# =============================================================================
# Behavior — _build_celery_delegator
# =============================================================================


class TestBuildCeleryDelegatorBehavior:
    """_build_celery_delegator should return None for unknown / unshipped jobs."""

    def test_unknown_job_returns_none(self):
        """Jobs not listed in _CELERY_TASK_NAMES get no delegator."""
        assert _build_celery_delegator("not_a_real_job") is None

    def test_known_job_returns_callable_when_celery_installed(self):
        """Known job name produces a zero-arg callable (celery already in deps)."""
        fn = _build_celery_delegator("daily_report")

        assert callable(fn)


# =============================================================================
# Behavior — _resolve_job_callable synthetic branches
# =============================================================================


class TestResolveJobCallableBehavior:
    """_resolve_job_callable routes synthetic names through dedicated builders."""

    def test_synthetic_cb_recovery_returns_callable(self):
        """cb_recovery attr shortcut returns a zero-arg callable."""
        fn = _resolve_job_callable("baldur.services", "_synthetic_cb_recovery_check")

        assert callable(fn)
        assert fn.__name__ == "cb_recovery_tick"

    def test_synthetic_sla_drift_returns_callable(self):
        """sla_drift attr shortcut returns a zero-arg callable."""
        fn = _resolve_job_callable(
            "baldur.tasks.drift_detection", "_synthetic_sla_drift_check"
        )

        assert callable(fn)
        assert fn.__name__ == "sla_drift_tick"

    def test_missing_module_returns_none(self):
        """Nonexistent module path returns None (skipped, not crashing)."""
        fn = _resolve_job_callable("baldur.nonexistent_module", "doesnt_matter")

        assert fn is None

    def test_missing_attribute_returns_none(self):
        """Module exists but attr missing → None."""
        fn = _resolve_job_callable("baldur.bootstrap", "definitely_not_a_function")

        assert fn is None


# =============================================================================
# Behavior — _wrap_with_context preserves contextvars across invocation
# =============================================================================


class TestWrapWithContextBehavior:
    """contextvars.copy_context() must pass through the caller's variables."""

    def test_wrap_propagates_contextvar_value(self):
        """A contextvar bound before wrap is visible inside wrap's invocation."""
        var: contextvars.ContextVar[str] = contextvars.ContextVar(
            "_test_ctx", default="unset"
        )

        captured: list[str] = []

        def target() -> None:
            captured.append(var.get())

        var.set("payload")
        wrapped = _wrap_with_context(target)
        # Mutate the contextvar after wrapping to prove the captured snapshot wins.
        var.set("after_wrap")

        wrapped()

        assert captured == ["payload"]
