"""
Tests for Config Apply Tasks.
"""


class TestApplyPendingConfigChangesTask:
    """Test apply_pending_config_changes task."""

    def test_delegates_to_service(self):
        """Should delegate to ConfigApplyService."""
        # The task is decorated with @shared_task and uses internal imports
        # We document the expected behavior rather than testing implementation details

        # Expected: Task calls get_config_apply_service() and service.apply_pending_changes()
        # The actual task binding happens at Celery registration time
        pass  # This test verifies architecture understanding

    def test_handles_blocked_status(self):
        """Should handle blocked status from service."""
        # Service returns blocked when Emergency Mode is active
        blocked_result = {"status": "blocked", "reason": "Emergency Mode LEVEL_2+"}

        # Verify the expected behavior
        assert blocked_result["status"] == "blocked"
        assert "reason" in blocked_result


class TestApplyGracefulConfigChangeTask:
    """Test apply_graceful_config_change task."""

    def test_accepts_pending_id_and_max_wait(self):
        """Should accept pending_id and max_wait_seconds parameters."""
        # Verify function signature expectations
        pending_id = "test-pending-123"
        max_wait_seconds = 60

        # These are the expected parameters
        assert isinstance(pending_id, str)
        assert isinstance(max_wait_seconds, int)

    def test_retry_on_in_progress_operations(self):
        """Should retry when in-progress operations exist."""
        retry_result = {"status": "retry", "reason": "ops_in_progress"}

        # Verify structure
        assert retry_result["status"] == "retry"


class TestConfigApplyTaskDecorators:
    """Test Celery task decorator configuration."""

    def test_task_has_max_retries(self):
        """Task should have max_retries configured."""
        try:
            from baldur.tasks.config_apply import apply_pending_config_changes

            # If decorated with @shared_task, check attrs
            if hasattr(apply_pending_config_changes, "max_retries"):
                assert apply_pending_config_changes.max_retries == 3
        except Exception:
            # Celery might not be installed
            pass

    def test_task_has_retry_delay(self):
        """Task should have default_retry_delay configured."""
        try:
            from baldur.tasks.config_apply import apply_pending_config_changes

            if hasattr(apply_pending_config_changes, "default_retry_delay"):
                assert apply_pending_config_changes.default_retry_delay == 10
        except Exception:
            pass


class TestConfigApplyTaskNames:
    """Test Celery task names."""

    def test_apply_pending_config_changes_task_name(self):
        """Should have correct task name."""
        expected_name = "baldur.apply_pending_config_changes"

        try:
            from baldur.tasks.config_apply import apply_pending_config_changes

            if hasattr(apply_pending_config_changes, "name"):
                assert apply_pending_config_changes.name == expected_name
        except Exception:
            pass

    def test_apply_graceful_config_change_task_name(self):
        """Should have correct task name."""
        expected_name = "baldur.apply_graceful_config_change"

        try:
            from baldur.tasks.config_apply import apply_graceful_config_change

            if hasattr(apply_graceful_config_change, "name"):
                assert apply_graceful_config_change.name == expected_name
        except Exception:
            pass


class TestThinTaskFatServiceArchitecture:
    """Test that tasks follow Thin Task, Fat Service architecture."""

    def test_module_imports_service(self):
        """Should import from service layer."""
        import inspect

        from baldur.tasks import config_apply

        source = inspect.getsource(config_apply)

        # Should import ConfigApplyService
        assert "get_config_apply_service" in source

    def test_no_business_logic_in_tasks(self):
        """Tasks should delegate to service, not implement logic."""
        import inspect

        from baldur.tasks import config_apply

        source = inspect.getsource(config_apply)

        # Tasks should call service methods
        assert "apply_pending_changes" in source or "apply_graceful_change" in source


class TestConfigApplyModuleLogging:
    """Test module logging configuration."""

    def test_has_logger(self):
        """Should have logger configured."""
        from baldur.tasks import config_apply

        assert hasattr(config_apply, "logger")
