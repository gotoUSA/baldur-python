"""
API Rate Limit Settings - Pydantic v2.

Django API Rate Limiting 미들웨어를 위한 설정 클래스.
api/django/rate_limit.py의 하드코딩된 상수들을 환경변수 기반으로 관리.

Environment Variables:
    BALDUR_API_RATE_LIMIT_DEFAULT_LIMIT=100
    BALDUR_API_RATE_LIMIT_DEFAULT_WINDOW_SECONDS=60
    BALDUR_API_RATE_LIMIT_EMERGENCY_LIMIT=10
    BALDUR_API_RATE_LIMIT_EMERGENCY_WINDOW_SECONDS=60
    BALDUR_API_RATE_LIMIT_CONTROL_API_PATH_PREFIX=/api/baldur/
    BALDUR_API_RATE_LIMIT_REDIS_PING_INTERVAL=5
    BALDUR_API_RATE_LIMIT_REDIS_FAILURE_THRESHOLD=3
    BALDUR_API_RATE_LIMIT_REDIS_RECOVERY_JITTER_MAX=10
"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

from baldur.settings.base import make_settings_config
from baldur.settings.field_types import (
    HugeCount,
    IntervalDuration,
    LargeCount,
    ShortInterval,
    SmallCount,
)
from baldur.settings.validators import warn_above


class ApiRateLimitSettings(BaseSettings):
    """
    API Rate Limiting 설정 (Django 미들웨어용).

    Normal Mode (Redis 가용):
        - default_limit: 분당 최대 요청 수
        - default_window_seconds: 윈도우 크기 (초)

    Emergency Mode (Redis 불가):
        - emergency_limit: 분당 최대 요청 수 (분산 미동기화로 10배 엄격)
        - emergency_window_seconds: 비상 윈도우 크기 (초)

    Redis Health Checker:
        - redis_ping_interval: 헬스체크 간격 (초)
        - redis_failure_threshold: UNHEALTHY 판정을 위한 연속 실패 횟수
        - redis_recovery_jitter_max: Thundering Herd 방지용 최대 지터 (초)
    """

    model_config = make_settings_config("BALDUR_API_RATE_LIMIT_")

    # =========================================================================
    # Normal Mode Settings (Redis 가용 시)
    # =========================================================================
    default_limit: HugeCount = Field(
        default=100,
        description="Maximum requests per minute (when Redis is healthy)",
    )
    default_window_seconds: IntervalDuration = Field(
        default=60,
        description="Rate limit window size (seconds)",
    )

    # =========================================================================
    # Emergency Mode Settings (Redis 장애 시)
    # =========================================================================
    emergency_limit: LargeCount = Field(
        default=10,
        description="Maximum requests per minute (when Redis is down, local memory-based)",
    )
    emergency_window_seconds: IntervalDuration = Field(
        default=60,
        description="Emergency mode window size (seconds)",
    )

    # =========================================================================
    # Control API Path Configuration
    # =========================================================================
    control_api_path_prefix: str = Field(
        default="/api/baldur/",
        description="API path prefix where rate limiting is applied",
    )

    # =========================================================================
    # Redis Health Checker Settings
    # =========================================================================
    redis_ping_interval: ShortInterval = Field(
        default=5,
        description="Redis health check interval (seconds)",
    )
    redis_failure_threshold: SmallCount = Field(
        default=3,
        description="Consecutive failure count to transition to UNHEALTHY state",
    )
    redis_recovery_jitter_max: ShortInterval = Field(
        default=10,
        description="Maximum jitter to prevent thundering herd on recovery (seconds)",
    )
    redis_ping_timeout_ms: int = Field(
        default=100,
        ge=10,
        le=1000,
        description="Health check ping timeout in milliseconds (dedicated low-timeout client)",
    )

    # =========================================================================
    # Local Memory Limiter Settings
    # =========================================================================
    local_cleanup_interval: int = Field(
        default=60,
        ge=10,
        le=300,
        description="Local memory rate limiter cleanup interval (seconds)",
    )

    @field_validator("emergency_limit")
    @classmethod
    def _warn_emergency_limit(cls, v: int) -> int:
        """
        Emergency limit은 default_limit보다 낮아야 합니다.
        분산 환경에서 동기화되지 않으므로 보수적인 값을 권장.
        """
        return warn_above(50, "api_rate_limit.high_consider_using_safety")(v)


def get_api_rate_limit_settings() -> "ApiRateLimitSettings":
    from baldur.settings.root import get_config

    return get_config().services_group.api_rate_limit


def reset_api_rate_limit_settings() -> None:
    from baldur.settings.root import get_config

    try:
        del get_config().services_group.__dict__["api_rate_limit"]
    except KeyError:
        pass
