"""
API Rate Limit Settings integration tests.

Verifies api/django/rate_limit.py and ApiRateLimitSettings integration
in a Django environment.
"""

import pytest


@pytest.mark.django_db
class TestApiRateLimitSettingsIntegration:
    """api/django/rate_limit.py and ApiRateLimitSettings integration tests."""

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        """Reset all singletons before/after each test."""
        from baldur.settings.api_rate_limit import reset_api_rate_limit_settings

        reset_api_rate_limit_settings()
        yield
        reset_api_rate_limit_settings()

    def test_get_rate_limit_config_uses_settings_when_runtime_config_fails(
        self, monkeypatch
    ):
        """get_rate_limit_config() uses settings as fallback when RuntimeConfigManager fails."""
        from baldur.settings.api_rate_limit import reset_api_rate_limit_settings

        monkeypatch.setenv("BALDUR_API_RATE_LIMIT_DEFAULT_LIMIT", "250")
        monkeypatch.setenv("BALDUR_API_RATE_LIMIT_EMERGENCY_LIMIT", "25")

        reset_api_rate_limit_settings()

        def mock_get_runtime_config_manager():
            raise ImportError("RuntimeConfigManager not available for test")

        original_import = (
            __builtins__.__import__
            if hasattr(__builtins__, "__import__")
            else __import__
        )

        def mock_import(name, *args, **kwargs):
            if name == "baldur_pro.services.runtime_config":
                raise ImportError("Mocked import failure")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", mock_import)

        from baldur.api.django.rate_limit import get_rate_limit_config

        config = get_rate_limit_config()

        assert config["control_api_rate_limit"] == 250
        assert config["emergency_rate_limit"] == 25

    def test_get_setting_helper_reads_from_settings(self, monkeypatch):
        """_get_setting helper reads from ApiRateLimitSettings correctly."""
        from baldur.settings.api_rate_limit import reset_api_rate_limit_settings

        monkeypatch.setenv("BALDUR_API_RATE_LIMIT_DEFAULT_LIMIT", "999")
        monkeypatch.setenv("BALDUR_API_RATE_LIMIT_EMERGENCY_LIMIT", "99")

        reset_api_rate_limit_settings()

        from baldur.api.django.rate_limit.config import (
            _FALLBACK_DEFAULT_RATE_LIMIT,
            _get_setting,
        )

        default_limit = _get_setting("default_limit", _FALLBACK_DEFAULT_RATE_LIMIT)
        emergency_limit = _get_setting("emergency_limit", 10)

        assert default_limit == 999
        assert emergency_limit == 99

    def test_get_local_limiter_uses_cleanup_interval_setting(self, monkeypatch):
        """get_local_limiter() passes cleanup_interval from settings to SlidingWindowLimiter."""
        from baldur.api.django.rate_limit.middleware import (
            get_local_limiter,
        )
        from baldur.services.rate_limit import SlidingWindowLimiter
        from baldur.settings.api_rate_limit import reset_api_rate_limit_settings

        monkeypatch.setenv("BALDUR_API_RATE_LIMIT_LOCAL_CLEANUP_INTERVAL", "120")
        reset_api_rate_limit_settings()

        # Force singleton recreation
        import baldur.api.django.rate_limit.middleware as mod

        mod._local_limiter = None

        limiter = get_local_limiter()
        assert isinstance(limiter, SlidingWindowLimiter)
        assert limiter._cleanup_interval == 120

    def test_redis_health_checker_uses_settings(self, monkeypatch):
        """RedisHealthChecker uses settings values."""
        from baldur.api.django.rate_limit import RedisHealthChecker
        from baldur.settings.api_rate_limit import reset_api_rate_limit_settings

        monkeypatch.setenv("BALDUR_API_RATE_LIMIT_REDIS_PING_INTERVAL", "15")
        monkeypatch.setenv("BALDUR_API_RATE_LIMIT_REDIS_FAILURE_THRESHOLD", "7")
        monkeypatch.setenv("BALDUR_API_RATE_LIMIT_REDIS_RECOVERY_JITTER_MAX", "30")

        reset_api_rate_limit_settings()

        checker = RedisHealthChecker()

        assert checker.ping_interval == 15
        assert checker.failure_threshold == 7
        assert checker.recovery_jitter_max == 30

    def test_backward_compatibility_constants(self):
        """Backward compatibility module-level constants exist."""
        from baldur.api.django.rate_limit import (
            CONTROL_API_PATH_PREFIX,
            DEFAULT_RATE_LIMIT,
            DEFAULT_WINDOW_SECONDS,
            EMERGENCY_RATE_LIMIT,
            EMERGENCY_WINDOW_SECONDS,
        )

        assert DEFAULT_RATE_LIMIT == 100
        assert DEFAULT_WINDOW_SECONDS == 60
        assert EMERGENCY_RATE_LIMIT == 10
        assert EMERGENCY_WINDOW_SECONDS == 60
        assert CONTROL_API_PATH_PREFIX == "/api/baldur/"

    def test_sliding_window_limiter_check_respects_per_call_params(self):
        """SlidingWindowLimiter.check() uses per-call max_requests/window_seconds."""
        from baldur.services.rate_limit import SlidingWindowLimiter

        limiter = SlidingWindowLimiter()

        state = limiter.check("test_key", max_requests=2, window_seconds=60)
        assert state.allowed is True
        assert state.remaining == 1

        state = limiter.check("test_key", max_requests=2, window_seconds=60)
        assert state.allowed is True
        assert state.remaining == 0

        state = limiter.check("test_key", max_requests=2, window_seconds=60)
        assert state.allowed is False
        assert state.remaining == 0
