"""
Replay Automation Settings - Pydantic v2.

Single Source of Truth for replay automation configuration.
Replaces:
- core/config.py:ReplayAutomationConfig (lines 434-495)
"""

from pydantic import Field
from pydantic_settings import BaseSettings

from baldur.settings.base import make_settings_config
from baldur.settings.field_types import (
    LargeCount,
    MediumCount,
    Probability,
)


class ReplayAutomationSettings(BaseSettings):
    """
    DLQ Replay 자동화 설정.

    Track 1: CB 복구 시 이벤트 기반 자동 Replay
    Track 2: Scheduled Batch (기존 5분 주기)
    Track 3: Traffic-Aware Replay (향후 구현)
    도메인별 차등 정책

    Environment variables:
        BALDUR_REPLAY_AUTOMATION_TRACK1_ENABLED=true
        BALDUR_REPLAY_AUTOMATION_TRACK1_MAX_ITEMS=50
        ...
    """

    model_config = make_settings_config("BALDUR_REPLAY_AUTOMATION_")

    # =========================================================================
    # Track 1: Event-Driven Replay (CB CLOSED 이벤트 기반)
    # =========================================================================
    track1_enabled: bool = Field(
        default=True,
        description="Enable Track 1",
    )
    track1_max_items: LargeCount = Field(
        default=100,
        description="Maximum replay items on CB recovery",
    )

    # =========================================================================
    # Track 2: Scheduled Batch Replay (기존 5분 주기 Beat)
    # =========================================================================
    track2_max_items: LargeCount = Field(
        default=50,
        description="Maximum replay items per batch",
    )

    # =========================================================================
    # Track 3: Traffic-Aware Replay (향후 구현)
    # =========================================================================
    track3_enabled: bool = Field(
        default=False,
        description="Enable Track 3 (default: disabled)",
    )
    track3_max_items: LargeCount = Field(
        default=30,
        description="Maximum replay items on traffic normalization",
    )

    # =========================================================================
    # Adaptive Mode (동적 max_items 조정)
    # =========================================================================
    adaptive_enabled: bool = Field(
        default=False,
        description="Enable adaptive mode",
    )
    adaptive_min_items: MediumCount = Field(
        default=10,
        description="Minimum batch size",
    )
    adaptive_max_items: int = Field(
        default=100,
        ge=10,
        le=1000,
        description="Maximum batch size",
    )
    adaptive_failure_threshold: Probability = Field(
        default=0.2,
        description="Failure rate threshold (0.2 = 20%)",
    )

    # =========================================================================
    # Domain Priority Policy (도메인별 차등 정책)
    # =========================================================================
    priority_enabled: bool = Field(
        default=False,
        description="Enable priority-based batch processing",
    )
    domain_priorities: dict[str, str] = Field(
        default_factory=dict,
        description='Per-domain priority mapping {"payment": "critical", "notification": "low"}',
    )
    domain_max_retries: dict[str, int] = Field(
        default_factory=dict,
        description='Per-domain max_retries override {"payment": 10}',
    )

    # =========================================================================
    # Service → Failure Type Mapping (Track 1 dispatch)
    # =========================================================================
    # ReplayService.replay_on_circuit_close() uses this to translate
    # "service that recovered" into "failure_types whose DLQ entries are
    # now safe to retry". An empty/missing mapping causes the method to
    # early-return at service.py with no event emit — operators MUST
    # configure this for Track 1 to drain DLQ on CB recovery.
    service_failure_type_map: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Service→failure_types mapping consulted by "
            "replay_on_circuit_close(). Example: "
            '{"payment_api": ["TIMEOUT", "CONNECTION_ERROR"]}. '
            "Empty default — operator must configure for Track 1 to drain."
        ),
    )


# =============================================================================
# Singleton pattern
# =============================================================================


def get_replay_automation_settings() -> "ReplayAutomationSettings":
    """Get cached ReplayAutomationSettings instance."""
    from baldur.settings.root import get_config

    return get_config().services_group.replay_automation


def reset_replay_automation_settings() -> None:
    """Reset cached settings (for testing)."""
    from baldur.settings.root import get_config

    try:
        del get_config().services_group.__dict__["replay_automation"]
    except KeyError:
        pass
