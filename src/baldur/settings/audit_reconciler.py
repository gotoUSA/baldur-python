"""
Audit Reconciler Settings - Pydantic v2.

WAL vs 중앙 저장소 정합성 검증 설정.
ADR-005 (Fail-Open + WAL 기반 누락 0 보장) 구현의 보조 컴포넌트.

Source:
- audit/reconciler.py (ReconcilerConfig)

Environment Variables:
    BALDUR_AUDIT_RECONCILER_CHECK_INTERVAL_SECONDS=300.0
    BALDUR_AUDIT_RECONCILER_CHECK_WINDOW_SECONDS=3600.0
    BALDUR_AUDIT_RECONCILER_RESEND_BATCH_SIZE=50
    BALDUR_AUDIT_RECONCILER_MAX_RESEND_ATTEMPTS=3
    BALDUR_AUDIT_RECONCILER_ALERT_THRESHOLD=10
    BALDUR_AUDIT_RECONCILER_MAX_CONFIRMED_IDS=10000
"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

from baldur.settings.base import make_settings_config
from baldur.settings.field_types import STANDARD_RETRY_COUNT, LargeCount, TinyCount
from baldur.settings.validators import warn_below


class AuditReconcilerSettings(BaseSettings):
    """
    Audit Reconciler 설정.

    WAL과 중앙 저장소 간의 정합성 검증 주기, 재전송 설정 등을 정의합니다.
    """

    model_config = make_settings_config("BALDUR_AUDIT_RECONCILER_")

    # ==========================================================================
    # Check Interval & Window (from reconciler.py lines 41-44)
    # ==========================================================================
    check_interval_seconds: float = Field(
        default=300.0,
        ge=60.0,
        le=3600.0,
        description="Verification interval (seconds). Default 5 minutes.",
    )
    check_window_seconds: float = Field(
        default=3600.0,
        ge=300.0,
        le=86400.0,
        description="Verification window (seconds). Only verifies entries within last N seconds. Default 1 hour.",
    )

    # ==========================================================================
    # Resend Settings (from reconciler.py lines 47-50)
    # ==========================================================================
    resend_batch_size: LargeCount = Field(
        default=50,
        description="Batch size for missing entry resend",
    )
    max_resend_attempts: TinyCount = Field(
        default=STANDARD_RETRY_COUNT,
        description="Maximum number of resend attempts",
    )

    # ==========================================================================
    # Alert Settings (from reconciler.py line 53)
    # ==========================================================================
    alert_threshold: LargeCount = Field(
        default=10,
        description="Trigger alert when N or more entries are missing",
    )

    # ==========================================================================
    # Cache Settings (from reconciler.py line 170)
    # ==========================================================================
    max_confirmed_ids: int = Field(
        default=10000,
        ge=1000,
        le=1000000,
        description="Maximum cache size for confirmed record_ids from central store",
    )

    @field_validator("check_interval_seconds")
    @classmethod
    def _warn_check_interval_seconds(cls, v: float) -> float:
        """check_interval이 너무 짧으면 경고."""
        return warn_below(120, "audit_reconciler_settings.low_consider_using_reduce")(v)


# =============================================================================
# Singleton Pattern
# =============================================================================


def get_audit_reconciler_settings() -> "AuditReconcilerSettings":
    """
    캐시된 AuditReconcilerSettings 인스턴스 반환.

    Returns:
        AuditReconcilerSettings: 싱글톤 인스턴스
    """
    from baldur.settings.root import get_config

    return get_config().audit_group.audit_reconciler


def reset_audit_reconciler_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    from baldur.settings.root import get_config

    try:
        del get_config().audit_group.__dict__["audit_reconciler"]
    except KeyError:
        pass
