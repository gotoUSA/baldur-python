"""
DLQ Replay 자동화 Settings 테스트.

ReplayAutomationSettings:
- Track 1: CB 복구 시 이벤트 기반 자동 Replay
- Track 2: Scheduled Batch (5분 주기)
- 도메인별 차등 정책 지원
"""

import pytest
from pydantic import ValidationError


class TestReplayAutomationSettings:
    """Tests for ReplayAutomationSettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from baldur.settings.replay_automation import (
            reset_replay_automation_settings,
        )

        reset_replay_automation_settings()
        yield
        reset_replay_automation_settings()

    def test_default_values(self):
        """기본값 검증."""
        from baldur.settings.replay_automation import ReplayAutomationSettings

        settings = ReplayAutomationSettings()

        # Track 1 defaults
        assert settings.track1_enabled is True
        assert settings.track1_max_items == 100

        # Track 2 defaults
        assert settings.track2_max_items == 50

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from baldur.settings.replay_automation import ReplayAutomationSettings

        monkeypatch.setenv("BALDUR_REPLAY_AUTOMATION_TRACK1_MAX_ITEMS", "100")

        settings = ReplayAutomationSettings()

        assert settings.track1_max_items == 100

    def test_validation_max_items_range(self):
        """max_items 범위 검증."""
        from baldur.settings.replay_automation import ReplayAutomationSettings

        with pytest.raises(ValidationError):
            ReplayAutomationSettings(track1_max_items=0)  # < 1

        with pytest.raises(ValidationError):
            ReplayAutomationSettings(track2_max_items=2000)  # > 1000

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from baldur.settings.replay_automation import (
            get_replay_automation_settings,
        )

        settings1 = get_replay_automation_settings()
        settings2 = get_replay_automation_settings()

        assert settings1 is settings2
