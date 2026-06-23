"""
ApiRateLimitSettings 단위 테스트.

API Rate Limiting 미들웨어 설정 검증:
- 기본값 검증
- 환경변수 오버라이드 검증
- 필드 유효성 검증 (min/max 범위)
- 싱글톤 패턴 검증
"""

import pytest
from pydantic import ValidationError


class TestApiRateLimitSettings:
    """ApiRateLimitSettings 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """각 테스트 전후로 싱글톤 초기화."""
        from baldur.settings.api_rate_limit import reset_api_rate_limit_settings

        reset_api_rate_limit_settings()
        yield
        reset_api_rate_limit_settings()

    def test_default_values(self):
        """기본값이 api/django/rate_limit.py의 하드코딩 값과 일치하는지 검증."""
        from baldur.settings.api_rate_limit import ApiRateLimitSettings

        settings = ApiRateLimitSettings()

        # Normal Mode (Redis 정상)
        assert settings.default_limit == 100
        assert settings.default_window_seconds == 60

        # Emergency Mode (Redis 장애)
        assert settings.emergency_limit == 10
        assert settings.emergency_window_seconds == 60

        # Control API Path
        assert settings.control_api_path_prefix == "/api/baldur/"

        # Redis Health Checker
        assert settings.redis_ping_interval == 5
        assert settings.redis_failure_threshold == 3
        assert settings.redis_recovery_jitter_max == 10

        # Local Memory Limiter
        assert settings.local_cleanup_interval == 60

    def test_env_override_default_limit(self, monkeypatch):
        """환경변수로 default_limit 오버라이드."""
        from baldur.settings.api_rate_limit import ApiRateLimitSettings

        monkeypatch.setenv("BALDUR_API_RATE_LIMIT_DEFAULT_LIMIT", "200")

        settings = ApiRateLimitSettings()
        assert settings.default_limit == 200

    def test_env_override_emergency_settings(self, monkeypatch):
        """환경변수로 emergency 설정 오버라이드."""
        from baldur.settings.api_rate_limit import ApiRateLimitSettings

        monkeypatch.setenv("BALDUR_API_RATE_LIMIT_EMERGENCY_LIMIT", "20")
        monkeypatch.setenv("BALDUR_API_RATE_LIMIT_EMERGENCY_WINDOW_SECONDS", "120")

        settings = ApiRateLimitSettings()
        assert settings.emergency_limit == 20
        assert settings.emergency_window_seconds == 120

    def test_env_override_redis_health_settings(self, monkeypatch):
        """환경변수로 Redis 헬스체커 설정 오버라이드."""
        from baldur.settings.api_rate_limit import ApiRateLimitSettings

        monkeypatch.setenv("BALDUR_API_RATE_LIMIT_REDIS_PING_INTERVAL", "10")
        monkeypatch.setenv("BALDUR_API_RATE_LIMIT_REDIS_FAILURE_THRESHOLD", "5")
        monkeypatch.setenv("BALDUR_API_RATE_LIMIT_REDIS_RECOVERY_JITTER_MAX", "20")

        settings = ApiRateLimitSettings()
        assert settings.redis_ping_interval == 10
        assert settings.redis_failure_threshold == 5
        assert settings.redis_recovery_jitter_max == 20

    def test_env_override_control_api_path(self, monkeypatch):
        """환경변수로 Control API 경로 prefix 오버라이드."""
        from baldur.settings.api_rate_limit import ApiRateLimitSettings

        monkeypatch.setenv(
            "BALDUR_API_RATE_LIMIT_CONTROL_API_PATH_PREFIX", "/custom/api/"
        )

        settings = ApiRateLimitSettings()
        assert settings.control_api_path_prefix == "/custom/api/"

    def test_validation_default_limit_min(self):
        """default_limit 최소값(1) 검증."""
        from baldur.settings.api_rate_limit import ApiRateLimitSettings

        with pytest.raises(ValidationError) as exc_info:
            ApiRateLimitSettings(default_limit=0)

        assert "default_limit" in str(exc_info.value)

    def test_validation_default_limit_max(self):
        """default_limit 최대값(10000) 검증."""
        from baldur.settings.api_rate_limit import ApiRateLimitSettings

        with pytest.raises(ValidationError) as exc_info:
            ApiRateLimitSettings(default_limit=10001)

        assert "default_limit" in str(exc_info.value)

    def test_validation_emergency_limit_min(self):
        """emergency_limit 최소값(1) 검증."""
        from baldur.settings.api_rate_limit import ApiRateLimitSettings

        with pytest.raises(ValidationError) as exc_info:
            ApiRateLimitSettings(emergency_limit=0)

        assert "emergency_limit" in str(exc_info.value)

    def test_validation_window_seconds_range(self):
        """window_seconds 범위 (1-3600) 검증."""
        from baldur.settings.api_rate_limit import ApiRateLimitSettings

        # Too low
        with pytest.raises(ValidationError):
            ApiRateLimitSettings(default_window_seconds=0)

        # Too high
        with pytest.raises(ValidationError):
            ApiRateLimitSettings(default_window_seconds=3601)

        # Valid edge cases
        settings_min = ApiRateLimitSettings(default_window_seconds=1)
        assert settings_min.default_window_seconds == 1

        settings_max = ApiRateLimitSettings(default_window_seconds=3600)
        assert settings_max.default_window_seconds == 3600

    def test_validation_redis_ping_interval_range(self):
        """redis_ping_interval 범위 (1-60) 검증."""
        from baldur.settings.api_rate_limit import ApiRateLimitSettings

        # Too low
        with pytest.raises(ValidationError):
            ApiRateLimitSettings(redis_ping_interval=0)

        # Too high
        with pytest.raises(ValidationError):
            ApiRateLimitSettings(redis_ping_interval=61)

        # Valid edge case
        settings = ApiRateLimitSettings(redis_ping_interval=60)
        assert settings.redis_ping_interval == 60

    def test_validation_redis_failure_threshold_range(self):
        """redis_failure_threshold 범위 (1-20) 검증."""
        from baldur.settings.api_rate_limit import ApiRateLimitSettings

        # Too low
        with pytest.raises(ValidationError):
            ApiRateLimitSettings(redis_failure_threshold=0)

        # Too high
        with pytest.raises(ValidationError):
            ApiRateLimitSettings(redis_failure_threshold=21)

    def test_validation_recovery_jitter_max_range(self):
        """redis_recovery_jitter_max 범위 (1-60) 검증."""
        from baldur.settings.api_rate_limit import ApiRateLimitSettings

        # Too low
        with pytest.raises(ValidationError):
            ApiRateLimitSettings(redis_recovery_jitter_max=0)

        # Too high
        with pytest.raises(ValidationError):
            ApiRateLimitSettings(redis_recovery_jitter_max=61)

    def test_validation_local_cleanup_interval_range(self):
        """local_cleanup_interval 범위 (10-300) 검증."""
        from baldur.settings.api_rate_limit import ApiRateLimitSettings

        # Too low
        with pytest.raises(ValidationError):
            ApiRateLimitSettings(local_cleanup_interval=9)

        # Too high
        with pytest.raises(ValidationError):
            ApiRateLimitSettings(local_cleanup_interval=301)

    def test_emergency_limit_warning_for_high_value(self):
        """emergency_limit이 50 초과 시 경고 로그 출력 검증."""
        from unittest.mock import patch

        from baldur.settings.api_rate_limit import ApiRateLimitSettings

        # logger.warning이 호출되는지 mock으로 검증 (병렬 테스트 안정적)
        with patch("baldur.settings.validators.logger") as mock_logger:
            settings = ApiRateLimitSettings(emergency_limit=51)

            assert settings.emergency_limit == 51
            # validator에서 logger.warning이 호출되었는지 확인
            mock_logger.warning.assert_called_once()
            # 호출된 메시지에 핵심 내용 포함 확인
            call_args = mock_logger.warning.call_args[0][0]
            assert call_args == "api_rate_limit.high_consider_using_safety"

    def test_singleton_pattern(self, monkeypatch):
        """get_api_rate_limit_settings 싱글톤 패턴 검증."""
        from baldur.settings.api_rate_limit import (
            get_api_rate_limit_settings,
            reset_api_rate_limit_settings,
        )

        # 첫 호출
        settings1 = get_api_rate_limit_settings()

        # 두 번째 호출 - 동일 인스턴스
        settings2 = get_api_rate_limit_settings()
        assert settings1 is settings2

        # 리셋 후 새 인스턴스
        reset_api_rate_limit_settings()
        settings3 = get_api_rate_limit_settings()
        assert settings1 is not settings3

    def test_singleton_env_reload(self, monkeypatch):
        """환경변수 변경 후 reset 시 새 값 반영 검증."""
        from baldur.settings.api_rate_limit import (
            get_api_rate_limit_settings,
            reset_api_rate_limit_settings,
        )

        # 초기값 확인
        settings1 = get_api_rate_limit_settings()
        assert settings1.default_limit == 100

        # 환경변수 변경 후 reset
        monkeypatch.setenv("BALDUR_API_RATE_LIMIT_DEFAULT_LIMIT", "500")
        reset_api_rate_limit_settings()

        # 새 값 반영 확인
        settings2 = get_api_rate_limit_settings()
        assert settings2.default_limit == 500


# =============================================================================
# Django 통합 테스트 클래스는 전역 tests 폴더에 별도 파일로 분리
# (Django 설정이 필요하므로 순수 단위 테스트에서 제외)
# 참조: tests/integration/test_api_rate_limit_integration.py
# =============================================================================
