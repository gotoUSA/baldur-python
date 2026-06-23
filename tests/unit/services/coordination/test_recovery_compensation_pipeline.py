"""
Unit tests for 383: RecoveryCoordinator Idempotency Hardening.

Tests:
- Default compensate handler registration (A-1)
- Compensate handler logic: budget_reset, canary_resume, governance_normal (A-1)
- IdempotencyGate integration in _attempt_compensation (A-2)
- DLQ failure_type differentiation (A-3)
- Compensation event emission (A-4)
- trigger_id propagation through start/resume_recovery (C-1)
- partial_execution propagation in execute_next_step (B-3)
- stop_event passthrough in _execute_with_timeout (B-2)

Reference:
    docs/impl/383_RECOVERY_IDEMPOTENCY_HARDENING.md
"""

import pytest

pytest.importorskip("baldur_pro")

pytestmark = pytest.mark.requires_pro


from unittest.mock import MagicMock, patch

from baldur.core.state_backend import MemoryStateBackend
from baldur_pro.services.canary import CanaryRolloutService
from baldur_pro.services.coordination.distributed_recovery_lock import (
    InMemoryRecoveryLock,
)
from baldur_pro.services.coordination.enums import (
    CompensationStatus,
    RecoveryStatus,
)
from baldur_pro.services.coordination.recovery_coordinator import (
    RecoveryCoordinator,
)
from baldur_pro.services.coordination.recovery_state import (
    RecoverySession,
    RecoveryStep,
    RecoveryStepType,
)

# =========================================================================
# Helper
# =========================================================================


def _make_coordinator(**kwargs):
    """Create coordinator with InMemory backend for testing."""
    defaults = {
        "backend": MemoryStateBackend(),
        "recovery_lock": InMemoryRecoveryLock(),
        "use_regional_policy": False,
    }
    defaults.update(kwargs)
    return RecoveryCoordinator(**defaults)


def _make_session_with_completed_steps(coordinator, step_results=None):
    """Create a session with completed steps for compensation testing."""
    session = coordinator.start_recovery(
        namespace="global", trigger_level="LEVEL_3", initiated_by="test"
    )
    # Mark steps as completed with result_data
    for _i, step in enumerate(session.steps):
        step.status = RecoveryStatus.COMPLETED
        step.compensation_status = CompensationStatus.PENDING
        if step_results and step.step_type in step_results:
            step.result_data = step_results[step.step_type]
        else:
            step.result_data = {"success": True}
    coordinator._save_session(session)
    return session


# =========================================================================
# A-1: Default Compensate Handler Registration
# =========================================================================


class TestDefaultCompensateHandlerRegistrationContract:
    """383 A-1: All 4 step types have registered compensate handlers."""

    def test_all_step_types_have_compensate_handlers(self):
        """Each RecoveryStepType has a registered compensate handler."""
        coord = _make_coordinator()
        for step_type in RecoveryStepType:
            assert step_type in coord._compensate_handlers, (
                f"Missing compensate handler for {step_type}"
            )

    def test_compensate_handler_count_matches_step_type_count(self):
        """Compensate handlers count == number of step types (4)."""
        coord = _make_coordinator()
        assert len(coord._compensate_handlers) == len(RecoveryStepType)


# =========================================================================
# A-1: Compensate Handler Logic
# =========================================================================


class TestCompensateBudgetResetBehavior:
    """383 A-1: _compensate_budget_reset restores old multipliers."""

    def test_restores_old_multipliers(self):
        """Compensate restores each level via set_multiplier_override."""
        session = RecoverySession(
            id="recovery-test", namespace="global", trigger_level="LEVEL_3"
        )
        step = RecoveryStep(
            step_type=RecoveryStepType.BUDGET_RESET,
            order=1,
            result_data={
                "success": True,
                "old_multipliers": {"NORMAL": 1.0, "LEVEL_3": 5.0},
            },
        )

        mock_provider = MagicMock()

        with patch(
            "baldur_pro.services.error_budget.multiplier."
            "get_crisis_multiplier_provider",
            return_value=mock_provider,
        ):
            result = RecoveryCoordinator._compensate_budget_reset(session, step)

        assert result["success"] is True
        assert "LEVEL_3" in result["restored_levels"]
        assert mock_provider.set_multiplier_override.call_count == 2
        mock_provider.invalidate_cache.assert_called_once()

    def test_skips_when_no_old_multipliers(self):
        """Returns skip when result_data has no old_multipliers."""
        session = RecoverySession(
            id="recovery-test", namespace="global", trigger_level="LEVEL_3"
        )
        step = RecoveryStep(
            step_type=RecoveryStepType.BUDGET_RESET,
            order=1,
            result_data={"success": True},
        )

        result = RecoveryCoordinator._compensate_budget_reset(session, step)
        assert result["success"] is True
        assert result["skipped"] is True


class TestCompensateHealthCheckBehavior:
    """383 A-1: _compensate_health_check is always a no-op."""

    def test_always_returns_skipped(self):
        """Health check compensation is a no-op."""
        session = RecoverySession(
            id="recovery-test", namespace="global", trigger_level="LEVEL_3"
        )
        step = RecoveryStep(
            step_type=RecoveryStepType.HEALTH_CHECK,
            order=2,
            result_data={"success": True},
        )

        result = RecoveryCoordinator._compensate_health_check(session, step)
        assert result["success"] is True
        assert result["skipped"] is True


class TestCompensateCanaryResumeBehavior:
    """383 A-1: _compensate_canary_resume re-pauses resumed rollouts."""

    def test_pauses_resumed_rollouts(self):
        """Compensate pauses each rollout from resumed_rollouts."""
        session = RecoverySession(
            id="recovery-test", namespace="global", trigger_level="LEVEL_3"
        )
        step = RecoveryStep(
            step_type=RecoveryStepType.CANARY_RESUME,
            order=3,
            result_data={
                "success": True,
                "resumed_rollouts": ["r-1", "r-2"],
            },
        )

        mock_service = MagicMock(spec=CanaryRolloutService)
        mock_service.pause.return_value = True

        with patch(
            "baldur_pro.services.canary.get_canary_rollout_service",
            return_value=mock_service,
        ):
            result = RecoveryCoordinator._compensate_canary_resume(session, step)

        assert result["success"] is True
        assert result["paused_rollouts"] == ["r-1", "r-2"]
        assert mock_service.pause.call_count == 2

    def test_skips_when_no_resumed_rollouts(self):
        """Returns skip when result_data has no resumed_rollouts."""
        session = RecoverySession(
            id="recovery-test", namespace="global", trigger_level="LEVEL_3"
        )
        step = RecoveryStep(
            step_type=RecoveryStepType.CANARY_RESUME,
            order=3,
            result_data={"success": True},
        )

        result = RecoveryCoordinator._compensate_canary_resume(session, step)
        assert result["success"] is True
        assert result["skipped"] is True


class TestCompensateGovernanceNormalBehavior:
    """383 A-1: _compensate_governance_normal re-activates emergency mode."""

    def test_restores_emergency_mode(self):
        """Compensate calls record_emergency_activation with old_mode."""
        session = RecoverySession(
            id="recovery-test", namespace="global", trigger_level="LEVEL_3"
        )
        step = RecoveryStep(
            step_type=RecoveryStepType.GOVERNANCE_NORMAL,
            order=4,
            result_data={"success": True, "old_mode": "STRICT"},
        )

        mock_tracker = MagicMock()

        with patch(
            "baldur_pro.services.governance.get_emergency_tracker",
            return_value=mock_tracker,
        ):
            result = RecoveryCoordinator._compensate_governance_normal(session, step)

        assert result["success"] is True
        assert result["restored_mode"] == "STRICT"
        mock_tracker.record_emergency_activation.assert_called_once()

    def test_skips_when_old_mode_was_normal(self):
        """Returns skip when old_mode was already NORMAL."""
        session = RecoverySession(
            id="recovery-test", namespace="global", trigger_level="LEVEL_3"
        )
        step = RecoveryStep(
            step_type=RecoveryStepType.GOVERNANCE_NORMAL,
            order=4,
            result_data={"success": True, "old_mode": "NORMAL"},
        )

        result = RecoveryCoordinator._compensate_governance_normal(session, step)
        assert result["success"] is True
        assert result["skipped"] is True


# =========================================================================
# C-1: trigger_id Propagation
# =========================================================================


class TestTriggerIdPropagationBehavior:
    """383 C-1: trigger_id generation and inheritance."""

    def test_start_recovery_generates_trigger_id(self):
        """start_recovery generates a trigger_id when None."""
        coord = _make_coordinator()
        session = coord.start_recovery(namespace="global", trigger_level="LEVEL_3")
        assert session.trigger_id is not None
        assert len(session.trigger_id) == 12  # uuid4().hex[:12]

    def test_start_recovery_uses_provided_trigger_id(self):
        """start_recovery uses provided trigger_id as-is."""
        coord = _make_coordinator()
        session = coord.start_recovery(
            namespace="global", trigger_level="LEVEL_3", trigger_id="custom-id"
        )
        assert session.trigger_id == "custom-id"

    def test_resume_recovery_inherits_trigger_id(self):
        """resume_recovery passes last_session.trigger_id to new session."""
        coord = _make_coordinator()

        # Given — first session with known trigger_id
        session1 = coord.start_recovery(
            namespace="global", trigger_level="LEVEL_3", trigger_id="original-trig"
        )
        original_trigger_id = session1.trigger_id

        # Simulate failure
        session1.status = RecoveryStatus.FAILED
        coord._save_session(session1)
        coord._recovery_lock.release("global", session1.id)

        # When — resume
        session2 = coord.resume_recovery(namespace="global", initiated_by="sre")

        # Then — trigger_id inherited
        assert session2.trigger_id == original_trigger_id
        assert session2.id != session1.id  # Different session ID


# =========================================================================
# A-3: DLQ failure_type Differentiation
# =========================================================================


class TestDlqFailureTypeBehavior:
    """383 A-3: failure_type branches on compensation failures."""

    def test_comp_failures_propagated_to_store_failure_to_dlq(self):
        """comp_result with failed_steps is passed to _store_failure_to_dlq."""
        coord = _make_coordinator()
        session = coord.start_recovery(namespace="global", trigger_level="LEVEL_1")
        # Mark step as completed with a failing compensate handler
        step = session.steps[0]
        step.status = RecoveryStatus.COMPLETED
        step.compensation_status = CompensationStatus.PENDING
        step.result_data = {"success": True}
        coord._save_session(session)

        # Register a failing compensate handler
        coord._compensate_handlers[RecoveryStepType.BUDGET_RESET] = lambda s, st: {
            "success": False,
            "error": "comp fail",
        }

        captured = {}

        def spy_store_dlq(session, error, comp_result=None):
            captured["has_comp_failures"] = (
                comp_result is not None and len(comp_result.failed_steps) > 0
            )

        with patch.object(coord, "_store_failure_to_dlq", side_effect=spy_store_dlq):
            coord._fail_session(session, "test error")

        assert captured["has_comp_failures"] is True

    def test_no_comp_failures_when_no_completed_steps(self):
        """No compensation failures when no completed steps exist."""
        coord = _make_coordinator()
        session = coord.start_recovery(namespace="global", trigger_level="LEVEL_1")
        coord._save_session(session)

        captured = {}

        def spy_store_dlq(session, error, comp_result=None):
            captured["has_comp_failures"] = (
                comp_result is not None and len(comp_result.failed_steps) > 0
            )

        with patch.object(coord, "_store_failure_to_dlq", side_effect=spy_store_dlq):
            coord._fail_session(session, "test error")

        assert captured["has_comp_failures"] is False


# =========================================================================
# A-2: IdempotencyGate ABORT Fail-Open in Compensation
# =========================================================================


class TestCompensationIdempotencyAbortFailOpenBehavior:
    """IdempotencyGate ABORT should not block compensation (fail-open).

    ABORT means "in-doubt" (cache failure or concurrent execution).
    Compensation is safety-critical: skipping is more dangerous than
    double-executing (handlers are idempotent by design).
    Only SKIP (confirmed completed) should bypass the handler.
    """

    def test_abort_does_not_skip_compensation_handler(self):
        """ABORT decision still executes the compensate handler."""
        from baldur.core.idempotency_gate import (
            IdempotencyCheckResult,
            IdempotencyDecision,
            IdempotencyGate,
        )

        # Given — gate that always returns ABORT (simulating cache failure)
        mock_gate = MagicMock(spec=IdempotencyGate)
        mock_gate.check_and_acquire.return_value = IdempotencyCheckResult(
            decision=IdempotencyDecision.ABORT,
        )

        coord = _make_coordinator(idempotency_gate=mock_gate)
        session = coord.start_recovery(
            namespace="global",
            trigger_level="LEVEL_1",
            initiated_by="test",
        )
        step = session.steps[0]
        step.status = RecoveryStatus.COMPLETED
        step.compensation_status = CompensationStatus.PENDING
        step.result_data = {"success": True}
        coord._save_session(session)

        handler_called = {"called": False}

        def failing_comp_handler(s, st):
            handler_called["called"] = True
            return {"success": False, "error": "comp fail"}

        coord._compensate_handlers[step.step_type] = failing_comp_handler

        # When
        result = coord._attempt_compensation(session)

        # Then — handler was called despite ABORT
        assert handler_called["called"] is True
        assert len(result.failed_steps) == 1

    def test_skip_still_bypasses_compensation_handler(self):
        """SKIP decision correctly bypasses the compensate handler."""
        from baldur.core.idempotency_gate import (
            IdempotencyCheckResult,
            IdempotencyDecision,
            IdempotencyGate,
        )

        # Given — gate that returns SKIP (already completed)
        mock_gate = MagicMock(spec=IdempotencyGate)
        mock_gate.check_and_acquire.return_value = IdempotencyCheckResult(
            decision=IdempotencyDecision.SKIP,
        )

        coord = _make_coordinator(idempotency_gate=mock_gate)
        session = coord.start_recovery(
            namespace="global",
            trigger_level="LEVEL_1",
            initiated_by="test",
        )
        step = session.steps[0]
        step.status = RecoveryStatus.COMPLETED
        step.compensation_status = CompensationStatus.PENDING
        step.result_data = {"success": True}
        coord._save_session(session)

        handler_called = {"called": False}

        def comp_handler(s, st):
            handler_called["called"] = True
            return {"success": True}

        coord._compensate_handlers[step.step_type] = comp_handler

        # When
        result = coord._attempt_compensation(session)

        # Then — handler was NOT called (SKIP = confirmed completed)
        assert handler_called["called"] is False
        assert len(result.compensated_steps) == 1

    def test_abort_does_not_mark_completed_on_gate(self):
        """ABORT path sets comp_key=None so gate.mark_completed is not called."""
        from baldur.core.idempotency_gate import (
            IdempotencyCheckResult,
            IdempotencyDecision,
            IdempotencyGate,
        )

        # Given — gate that returns ABORT
        mock_gate = MagicMock(spec=IdempotencyGate)
        mock_gate.check_and_acquire.return_value = IdempotencyCheckResult(
            decision=IdempotencyDecision.ABORT,
        )

        coord = _make_coordinator(idempotency_gate=mock_gate)
        session = coord.start_recovery(
            namespace="global",
            trigger_level="LEVEL_1",
            initiated_by="test",
        )
        step = session.steps[0]
        step.status = RecoveryStatus.COMPLETED
        step.compensation_status = CompensationStatus.PENDING
        step.result_data = {"success": True}
        coord._save_session(session)

        coord._compensate_handlers[step.step_type] = lambda s, st: {"success": True}

        # When
        coord._attempt_compensation(session)

        # Then — mark_completed was NOT called (comp_key=None after ABORT)
        mock_gate.mark_completed.assert_not_called()

    def test_abort_logs_warning(self):
        """ABORT path emits recovery.compensation_idempotency_abort_proceeding warning."""
        from baldur.core.idempotency_gate import (
            IdempotencyCheckResult,
            IdempotencyDecision,
            IdempotencyGate,
        )

        mock_gate = MagicMock(spec=IdempotencyGate)
        mock_gate.check_and_acquire.return_value = IdempotencyCheckResult(
            decision=IdempotencyDecision.ABORT,
        )

        coord = _make_coordinator(idempotency_gate=mock_gate)
        session = coord.start_recovery(
            namespace="global",
            trigger_level="LEVEL_1",
            initiated_by="test",
        )
        step = session.steps[0]
        step.status = RecoveryStatus.COMPLETED
        step.compensation_status = CompensationStatus.PENDING
        step.result_data = {"success": True}
        coord._save_session(session)

        coord._compensate_handlers[step.step_type] = lambda s, st: {"success": True}

        # When
        with patch(
            "baldur_pro.services.coordination.recovery_coordinator."
            "_session_persistence.logger"
        ) as mock_logger:
            coord._attempt_compensation(session)

        # Then — warning emitted for ABORT proceeding
        warning_calls = [
            c
            for c in mock_logger.warning.call_args_list
            if c.args
            and c.args[0] == "recovery.compensation_idempotency_abort_proceeding"
        ]
        assert len(warning_calls) == 1


# =========================================================================
# A-4: Compensation Events
# =========================================================================


class TestCompensationEventsBehavior:
    """383 A-4: compensation_started/completed events are emitted."""

    def test_compensation_started_event_emitted(self):
        """recovery.compensation_started is logged when compensation begins."""
        coord = _make_coordinator()
        session = coord.start_recovery(namespace="global", trigger_level="LEVEL_1")
        step = session.steps[0]
        step.status = RecoveryStatus.COMPLETED
        step.compensation_status = CompensationStatus.PENDING
        step.result_data = {"success": True}
        coord._save_session(session)

        with patch(
            "baldur_pro.services.coordination.recovery_coordinator."
            "_session_persistence.logger"
        ) as mock_logger:
            coord._attempt_compensation(session)

        # Find compensation_started call
        info_calls = [
            c
            for c in mock_logger.info.call_args_list
            if c.args and c.args[0] == "recovery.compensation_started"
        ]
        assert len(info_calls) == 1
        assert info_calls[0].kwargs["trigger_id"] == session.trigger_id


# =========================================================================
# B-3: Partial Execution Propagation
# =========================================================================


class TestPartialExecutionPropagationBehavior:
    """383 B-3: partial_execution fields propagated to RecoveryStep."""

    def test_partial_execution_set_on_timeout_with_sub_steps(self):
        """On timeout, step.partial_execution=True if completed_sub_steps non-empty."""
        coord = _make_coordinator()
        session = coord.start_recovery(namespace="global", trigger_level="LEVEL_3")

        # Pre-populate completed_sub_steps on the current step
        step = session.get_current_step()
        step.completed_sub_steps = ["svc-1", "svc-2"]
        coord._save_session(session)

        # Simulate timeout during execute_next_step
        from baldur_pro.services.coordination.recovery_coordinator.exceptions import (
            StepTimeoutError,
        )

        with patch.object(
            coord, "_execute_with_timeout", side_effect=StepTimeoutError("test", 30)
        ):
            with patch.object(coord, "_record_step_executed"):
                with patch.object(coord, "_fail_session"):
                    result_step = coord.execute_next_step("global")

        assert result_step.partial_execution is True
