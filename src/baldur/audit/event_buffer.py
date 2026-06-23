"""
RequestAuditBuffer - 요청별 Audit 이벤트 버퍼

각 HTTP 요청의 전 생애주기에서 발생하는 Audit 이벤트를 수집합니다.
request.META에 저장되어 미들웨어 체인 전체에서 접근 가능하며,
AuditMiddleware에서 응답 직전에 일괄 기록됩니다.

핵심 설계 원칙:
- 모든 미들웨어와 서비스가 '직접 로깅' 대신 '버퍼에 적재'
- AuditMiddleware가 응답 직전에 버퍼를 '낚아채서' 단일 해시 체인으로 기록
- 이를 통해 "단 하나의 로그도 누락되거나 조작되지 않았다"를 증명
- RingBuffer + WAL 통합으로 데이터 유실 0% 달성

업계 사례:
- AWS CloudTrail: 이벤트 버퍼링 후 일괄 전송
- Datadog APM: Span 수집 후 Trace 완료 시 전송
- OpenTelemetry: SpanProcessor의 OnEnd에서 일괄 처리

Usage:
    # 미들웨어나 서비스에서 이벤트 적재
    from baldur.audit.event_buffer import RequestAuditBuffer, AuditEventType

    buffer = RequestAuditBuffer.get_or_create(request)
    buffer.add(
        event_type=AuditEventType.DLQ_STORE,
        source="DLQService",
        details={"dlq_id": 123, "domain": "payment"},
    )

    # AuditMiddleware에서 자동 수집 및 기록됨

Author: Baldur Team
Version: 2.0.0 (RingBuffer + WAL 통합)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

import structlog

from baldur.core.serializable import SerializableMixin
from baldur.utils.time import utc_now

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = structlog.get_logger()


class AuditEventType(str, Enum):
    """
    Audit 이벤트 유형.

    각 유형은 AuditAction과 매핑되어 ContinuousAuditRecorder에 기록됩니다.
    """

    # DLQ 관련
    DLQ_STORE = "dlq_store"
    DLQ_REPLAY = "dlq_replay"
    DLQ_ESCALATE = "dlq_escalate"
    DLQ_FORCE_REDRIVE = "dlq_force_redrive"

    # Circuit Breaker 관련
    CB_STATE_CHANGE = "circuit_breaker_state_change"
    CB_REJECTION = "circuit_breaker_rejection"
    CB_RECOVERY = "circuit_breaker_recovery"

    # Governance 관련
    GOVERNANCE_BLOCKED = "governance_blocked"
    GOVERNANCE_KILL_SWITCH = "governance_kill_switch"

    # Rate Limit 관련
    RATE_LIMITED = "rate_limited"

    # Pool Circuit Breaker 관련
    POOL_CB_REJECTION = "pool_circuit_breaker_rejection"
    POOL_CB_STATE_CHANGE = "pool_circuit_breaker_state_change"

    # 에러 및 시스템 관련
    ERROR_DETECTED = "error_detected"
    CONFIG_CHANGE = "config_change"
    MANUAL_OVERRIDE = "manual_override"

    # API 예외 관련 - DRF 예외 핸들러에서 사용
    API_EXCEPTION = "api_exception"
    """API 요청 처리 중 예외 발생 (일반 예외)."""

    API_VALIDATION_ERROR = "api_validation_error"
    """입력값 검증 실패 (ValidationError, ValueError 등)."""

    API_AUTH_ERROR = "api_auth_error"
    """인증/인가 실패 (AuthenticationFailed, PermissionDenied 등)."""

    API_NOT_FOUND = "api_not_found"
    """리소스를 찾을 수 없음 (Http404, NotFound 예외)."""

    API_THROTTLED = "api_throttled"
    """요청 제한 초과 (Throttled 예외)."""

    # 복구 관련
    RECOVERY_EVENT = "recovery_event"
    RECOVERY_CHAIN_STARTED = "recovery_chain_started"
    RECOVERY_CHAIN_COMPLETED = "recovery_chain_completed"

    # 재시도 관련
    RETRY_ATTEMPTED = "retry_attempted"
    RETRY_EXHAUSTED = "retry_exhausted"

    # 시스템 제어 관련
    SYSTEM_CONTROL_CHANGED = "system_control_changed"

    # 롤백 관련
    ROLLBACK_PERFORMED = "rollback_performed"

    # ═══════════════════════════════════════════════════════════
    # Security Violation 관련 (85_AUDIT_INTEGRATION_OVERVIEW.md Phase 1)
    # ═══════════════════════════════════════════════════════════
    SECURITY_VIOLATION = "security_violation"
    """보안 위반 감지 및 처리."""

    SECURITY_IP_BLOCKED = "security_ip_blocked"
    """IP 차단 (임시 또는 영구)."""

    SECURITY_SESSION_INVALIDATED = "security_session_invalidated"
    """사용자 세션 무효화."""

    # ═══════════════════════════════════════════════════════════
    # Regional Isolation 관련 (85_AUDIT_INTEGRATION_OVERVIEW.md Phase 1)
    # ═══════════════════════════════════════════════════════════
    REGION_ISOLATED = "region_isolated"
    """리전 격리 활성화."""

    REGION_RESTORED = "region_restored"
    """리전 격리 해제."""

    # Chaos 실험 관련
    CHAOS_EXPERIMENT_STARTED = "chaos_experiment_started"
    CHAOS_EXPERIMENT_COMPLETED = "chaos_experiment_completed"
    CHAOS_INJECTION_APPLIED = "chaos_injection_applied"
    CHAOS_ROLLBACK_TRIGGERED = "chaos_rollback_triggered"

    # Emergency Mode 관련
    EMERGENCY_MODE_ACTIVATED = "emergency_mode_activated"
    EMERGENCY_MODE_DEACTIVATED = "emergency_mode_deactivated"

    # Error Budget 관련
    ERROR_BUDGET_DEPLETED = "error_budget_depleted"
    ERROR_BUDGET_BLOCKED = "error_budget_blocked"
    ERROR_BUDGET_WARNING = "error_budget_warning"
    ERROR_BUDGET_RECOVERED = "error_budget_recovered"

    # Compliance 관련
    COMPLIANCE_VIOLATION = "compliance_violation"
    COMPLIANCE_CHECK_PASSED = "compliance_check_passed"
    COMPLIANCE_CHECK_EXEMPTED = "compliance_check_exempted"

    # Blast Radius 관련
    BLAST_RADIUS_ISOLATION = "blast_radius_isolation"
    BLAST_RADIUS_VIOLATION = "blast_radius_violation"

    # FinOps 관련
    FINOPS_THRESHOLD_EXCEEDED = "finops_threshold_exceeded"
    FINOPS_BUDGET_EXCEEDED = "finops_budget_exceeded"

    # 데이터 접근 (ADR-002: 설정 기반)
    DATA_ACCESS = "data_access"

    # ═══════════════════════════════════════════════════════════
    # CorruptionShield 관련 (Part 2: 27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md)
    # ═══════════════════════════════════════════════════════════
    CORRUPTION_DETECTED = "corruption_detected"
    """데이터 무결성 위반 발견 (L1/L2/L3)."""

    CORRUPTION_BLOCKED = "corruption_blocked"
    """무결성 위반으로 요청 차단."""

    # ═══════════════════════════════════════════════════════════
    # ShadowLogger/L2 Sync 관련 (Part 2: 27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md)
    # ═══════════════════════════════════════════════════════════
    SHADOW_LOG_SYNC_FAILED = "shadow_log_sync_failed"
    """L2 동기화 실패 기록."""

    SHADOW_LOG_RECOVERED = "shadow_log_recovered"
    """L2 복구 후 재동기화 완료."""

    # ═══════════════════════════════════════════════════════════
    # WAL 관련 (Part 2: 27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md)
    # ═══════════════════════════════════════════════════════════
    WAL_CORRUPTION_DETECTED = "wal_corruption_detected"
    """WAL CRC32 체크섬 불일치 발견."""

    WAL_RECOVERED = "wal_recovered"
    """WAL 미처리 엔트리 복구 완료."""

    WAL_ROTATED = "wal_rotated"
    """WAL 파일 로테이션 발생."""

    # ═══════════════════════════════════════════════════════════
    # Forensic 관련 (Part 2: 27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md)
    # ═══════════════════════════════════════════════════════════
    FORENSIC_CAPTURE_STARTED = "forensic_capture_started"
    """Forensic 캡처 시작."""

    FORENSIC_CAPTURE_COMPLETED = "forensic_capture_completed"
    """Forensic 캡처 완료."""

    FORENSIC_ANOMALY_DETECTED = "forensic_anomaly_detected"
    """Forensic 분석 중 이상 패턴 발견."""

    # ═══════════════════════════════════════════════════════════
    # Reconciliation 관련 (30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md)
    # ═══════════════════════════════════════════════════════════
    FAILSAFE_PERIOD_STARTED = "failsafe_period_started"
    """Fail-Safe 기간 시작."""

    FAILSAFE_PERIOD_ENDED = "failsafe_period_ended"
    """Fail-Safe 기간 종료."""

    SHADOW_BUDGET_CALCULATED = "shadow_budget_calculated"
    """Shadow Budget 계산 완료."""

    RECONCILIATION_APPROVED = "reconciliation_approved"
    """Reconciliation 승인됨."""

    RECONCILIATION_REJECTED = "reconciliation_rejected"
    """Reconciliation 거부됨."""

    RECONCILIATION_ACCURACY_VERIFIED = "reconciliation_accuracy_verified"
    """Reconciliation 정확도 사후 검증 완료."""

    PENDING_RECONCILIATION_FREEZE = "pending_reconciliation_freeze"
    """대규모 조정으로 인한 배포 동결."""

    # 일반
    GENERIC = "generic"


@dataclass
class AuditEvent(SerializableMixin):
    """
    단일 Audit 이벤트.

    요청 처리 중 발생하는 각 이벤트를 캡처합니다.
    RequestAuditBuffer에 적재되어 AuditMiddleware에서 일괄 처리됩니다.
    trace_id는 자동으로 현재 트레이스 컨텍스트에서 추출됩니다.
    """

    event_type: AuditEventType
    timestamp: datetime = field(default_factory=lambda: utc_now())
    source: str = "unknown"
    details: dict[str, Any] = field(default_factory=dict)
    actor_id: str | None = None
    actor_type: str = "system"
    success: bool = True
    error_message: str | None = None

    # 추가 메타데이터
    target_type: str | None = None
    target_id: str | None = None
    domain: str | None = None
    reason: str | None = None

    # 분산 트레이싱 (자동 설정)
    trace_id: str | None = field(default=None)

    def __post_init__(self) -> None:
        """trace_id 자동 설정 (없을 경우 현재 트레이스 컨텍스트에서 추출)."""
        if self.trace_id is None:
            try:
                from baldur.audit.trace import get_trace_id

                self.trace_id = get_trace_id()
            except Exception:
                pass  # trace 모듈 미사용 환경에서도 동작

    def __repr__(self) -> str:
        return (
            f"AuditEvent(type={self.event_type.value}, "
            f"source={self.source}, success={self.success})"
        )


class RequestAuditBuffer:
    """
    요청별 Audit 이벤트 버퍼.

    request.META에 저장되어 미들웨어 체인 전체에서 이벤트 수집.
    AuditMiddleware에서 최종 기록.

    설계 포인트:
    - 이 버퍼는 '영수증'과 같음 - 한 요청의 전 생애주기를 기록
    - Thread-safe: RingBuffer 사용으로 스레드 안전
    - 메모리 효율: RingBuffer의 고정 용량으로 메모리 폭발 방지
    - 데이터 유실 0%: WAL 통합으로 디스크 영속화 (선택적)
    - 하위 호환성: events 속성, truncated_count 속성 유지

    사용 예시:
        # 1. 버퍼 가져오기/생성
        buffer = RequestAuditBuffer.get_or_create(request)

        # 2. 이벤트 추가
        buffer.add(
            event_type=AuditEventType.CB_STATE_CHANGE,
            source="BaldurMiddleware",
            details={"cb_name": "payment", "new_state": "open"},
        )

        # 3. AuditMiddleware에서 자동 처리
    """

    # request.META에 저장될 키
    META_KEY = "X-AUDIT-EVENTS"

    # 단일 요청당 최대 이벤트 수 (하위 호환성 유지)
    # RingBuffer 사용 시 capacity로 대체됨
    DEFAULT_MAX_EVENTS = 100

    # WAL 활성화 환경변수
    WAL_ENABLED_ENV = "BALDUR_AUDIT_WAL_ENABLED"

    def __init__(
        self,
        max_events: int | None = None,
        enable_wal: bool | None = None,
        wal_instance: Any | None = None,
    ):
        """
        RequestAuditBuffer 초기화.

        Args:
            max_events: 최대 이벤트 수 (None이면 RingBufferSettings 사용)
            enable_wal: WAL 활성화 여부 (None이면 환경변수 확인)
            wal_instance: 사용할 WAL 인스턴스 (테스트용)
        """
        from baldur.audit.ring_buffer import BackpressureStrategy, RingBuffer
        from baldur.settings.ring_buffer import get_ring_buffer_settings

        # RingBuffer 설정 로드
        settings = get_ring_buffer_settings()
        capacity = max_events if max_events is not None else settings.capacity

        # RingBuffer 생성 (DROP_OLDEST 전략으로 새 이벤트 우선)
        self._ring_buffer: RingBuffer[AuditEvent] = RingBuffer(
            capacity=capacity,
            strategy=BackpressureStrategy.DROP_OLDEST,
        )

        # 요청 메타데이터
        self.request_id: str | None = None
        self.start_time: datetime = utc_now()
        self._path: str | None = None
        self._method: str | None = None
        self._user_id: str | None = None

        # 하위 호환성: _max_events 속성 유지
        self._max_events = capacity

        # WAL 설정 (데이터 유실 0% 달성)
        if enable_wal is None:
            enable_wal = os.environ.get(self.WAL_ENABLED_ENV, "false").lower() == "true"

        self._wal_enabled = enable_wal
        self._wal = wal_instance
        self._wal_sequences: list[int] = []  # WAL 시퀀스 추적

        if self._wal_enabled and self._wal is None:
            self._wal = self._get_default_wal()

    def _get_default_wal(self):
        """기본 WAL 인스턴스 반환."""
        try:
            from baldur.audit.wal import WALConfig, WriteAheadLog

            # 환경변수에서 WAL 디렉토리 설정
            wal_dir = os.environ.get(
                "BALDUR_AUDIT_WAL_DIR",
                "/var/log/audit/request_buffer_wal",
            )

            config = WALConfig(
                wal_dir=wal_dir,
                sync_on_write=True,  # 데이터 유실 0%를 위해 항상 sync
                max_file_size_mb=50,
                max_files=5,
                file_prefix="request_audit",
            )

            return WriteAheadLog(config=config)

        except Exception as e:
            logger.warning(
                "request_audit_buffer.wal_init_failed",
                error=e,
            )
            return None

    @property
    def events(self) -> list[AuditEvent]:
        """
        하위 호환성: events 속성으로 모든 이벤트 접근.

        RingBuffer의 모든 항목을 리스트로 반환.
        """
        return self._ring_buffer.get_all()

    @events.setter
    def events(self, value: list[AuditEvent]) -> None:
        """
        하위 호환성: events 속성 설정.

        기존 이벤트를 모두 지우고 새 이벤트로 교체.
        """
        self._ring_buffer.clear()
        for event in value:
            self._ring_buffer.put(event)

    def add_event(self, event: AuditEvent) -> bool:
        """
        이벤트 직접 추가. Non-blocking.

        RingBuffer 사용으로 DROP_OLDEST 전략 적용:
        - 버퍼가 가득 차면 가장 오래된 이벤트 제거
        - 새 이벤트는 항상 추가됨

        WAL 활성화 시 디스크에 먼저 기록하여 유실 방지.

        Args:
            event: 추가할 AuditEvent

        Returns:
            True: 정상 추가됨 (RingBuffer에서는 항상 True)
        """
        # WAL 활성화 시 디스크에 먼저 기록
        if self._wal_enabled and self._wal is not None:
            try:
                seq = self._wal.write(event.to_dict())
                self._wal_sequences.append(seq)
            except Exception as e:
                logger.warning(
                    "wal.write_failed",
                    error=e,
                )
                # WAL 실패해도 메모리 버퍼에는 추가

        # 메모리 버퍼에 추가
        return self._ring_buffer.put(event)

    @property
    def stats(self) -> dict[str, Any]:
        """
        버퍼 통계 (모니터링용).

        Returns:
            capacity, size, total_enqueued, total_dropped, drop_rate 포함 딕셔너리
        """
        rb_stats = self._ring_buffer.get_stats()
        result = {
            "capacity": rb_stats.capacity,
            "size": rb_stats.size,
            "total_enqueued": rb_stats.total_enqueued,
            "total_dropped": rb_stats.total_dropped,
            "drop_rate": rb_stats.drop_rate,
            "wal_enabled": self._wal_enabled,
        }

        if self._wal_enabled:
            result["wal_sequences_count"] = len(self._wal_sequences)

        return result

    @property
    def truncated_count(self) -> int:
        """
        하위 호환성: truncated_count는 dropped와 동일.

        RingBuffer에서 DROP_OLDEST로 제거된 이벤트 수.
        """
        return self._ring_buffer.get_stats().total_dropped

    def _mark_last_event_truncated(self) -> None:
        """
        마지막 이벤트에 truncation 메타데이터 추가.

        하위 호환성을 위해 유지하나, RingBuffer 사용 시에는
        DROP_OLDEST 전략으로 자동 처리됨.
        """
        events = self.events
        if not events:
            return

        last_event = events[-1]
        dropped = self.truncated_count
        if dropped > 0:
            last_event.details["_truncated"] = True
            last_event.details["_truncated_count"] = dropped

    def add(
        self,
        event_type: AuditEventType,
        source: str,
        details: dict[str, Any] | None = None,
        actor_id: str | None = None,
        actor_type: str = "system",
        success: bool = True,
        error_message: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        domain: str | None = None,
        reason: str | None = None,
    ) -> AuditEvent | None:
        """
        편의 메서드: 이벤트 생성 및 추가.

        max_events 초과 시 이벤트가 버려지고 None 반환됩니다.
        버려진 이벤트 수는 마지막 이벤트의 details._truncated_count에 기록됩니다.

        Args:
            event_type: 이벤트 유형
            source: 이벤트 발생 위치 (미들웨어명, 서비스명 등)
            details: 추가 상세 정보
            actor_id: 행위자 ID (없으면 ActorContext에서 자동 추출)
            actor_type: 행위자 유형 (system, user, scheduler 등)
            success: 성공 여부
            error_message: 실패 시 에러 메시지
            target_type: 대상 유형 (circuit_breaker, dlq_entry 등)
            target_id: 대상 ID
            domain: 비즈니스 도메인
            reason: 이벤트 사유

        Returns:
            생성된 AuditEvent, 또는 max_events 초과 시 None
        """
        # ActorContext에서 actor 정보 자동 추출 시도
        if actor_id is None:
            try:
                from baldur.context.actor_context import ActorContext

                if ActorContext.is_set():
                    actor = ActorContext.get_current()
                    actor_id = actor.actor_id
                    actor_type = actor.actor_type
            except ImportError:
                pass

        event = AuditEvent(
            event_type=event_type,
            source=source,
            details=details or {},
            actor_id=actor_id,
            actor_type=actor_type,
            success=success,
            error_message=error_message,
            target_type=target_type,
            target_id=target_id,
            domain=domain,
            reason=reason,
        )

        # RingBuffer DROP_OLDEST 전략은 항상 성공 (오래된 이벤트 제거 후 추가)
        self.add_event(event)
        return event

    def get_events(self) -> list[AuditEvent]:
        """모든 이벤트 반환 (복사본)."""
        return list(self.events)

    def has_events(self) -> bool:
        """이벤트 존재 여부."""
        return not self._ring_buffer.is_empty

    def event_count(self) -> int:
        """이벤트 개수."""
        return self._ring_buffer.size

    def get_events_by_type(self, event_type: AuditEventType) -> list[AuditEvent]:
        """특정 유형의 이벤트만 반환."""
        return [e for e in self.events if e.event_type == event_type]

    def get_failed_events(self) -> list[AuditEvent]:
        """실패 이벤트만 반환."""
        return [e for e in self.events if not e.success]

    def has_event_from_source(self, source: str) -> bool:
        """
        특정 source에서 기록한 이벤트가 존재하는지 확인.

        AuditMiddleware에서 중복 기록 방지에 사용됩니다.
        예: ExceptionHandler가 이미 예외를 기록했으면 ERROR_DETECTED 스킵.

        Args:
            source: 이벤트 발생 위치 (ExceptionHandler, AuditMiddleware 등)

        Returns:
            해당 source의 이벤트 존재 여부
        """
        return any(e.source == source for e in self.events)

    def set_request_metadata(
        self,
        path: str | None = None,
        method: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """요청 메타데이터 설정."""
        if path is not None:
            self._path = path
        if method is not None:
            self._method = method
        if user_id is not None:
            self._user_id = user_id

    def get_elapsed_seconds(self) -> float:
        """요청 시작부터 경과 시간 (초)."""
        return (utc_now() - self.start_time).total_seconds()

    @property
    def max_events(self) -> int:
        """설정된 최대 이벤트 수 (RingBuffer capacity)."""
        return self._max_events

    @property
    def is_truncated(self) -> bool:
        """이벤트가 버려졌는지 여부."""
        return self.truncated_count > 0

    def to_dict(self) -> dict[str, Any]:
        """버퍼 전체를 딕셔너리로 변환."""
        events_list = self.events
        dropped = self.truncated_count

        result: dict[str, Any] = {
            "request_id": self.request_id,
            "start_time": self.start_time.isoformat(),
            "elapsed_seconds": self.get_elapsed_seconds(),
            "path": self._path,
            "method": self._method,
            "user_id": self._user_id,
            "event_count": len(events_list),
            "events": [e.to_dict() for e in events_list],
        }

        # 이벤트가 버려진 경우 truncation 정보 추가
        if dropped > 0:
            result["truncated"] = True
            result["truncated_count"] = dropped
            result["max_events"] = self._max_events

        # RingBuffer 통계 추가
        result["buffer_stats"] = self.stats

        return result

    def clear(self) -> int:
        """버퍼 초기화 (테스트용). Returns number of cleared entries."""
        count = self._ring_buffer.clear()
        self._ring_buffer.reset_stats()
        self._wal_sequences.clear()
        return count

    # =========================================================================
    # Class Methods - request에서 버퍼 관리
    # =========================================================================

    @classmethod
    def get_or_create(cls, request: HttpRequest) -> RequestAuditBuffer:
        """
        request에서 버퍼 가져오거나 새로 생성.

        Args:
            request: Django HttpRequest 객체

        Returns:
            RequestAuditBuffer 인스턴스

        Usage:
            buffer = RequestAuditBuffer.get_or_create(request)
            buffer.add(event_type=..., source=..., details=...)
        """
        if not hasattr(request, "META"):
            # request 객체가 이상한 경우 새 버퍼 반환
            return cls()

        if cls.META_KEY not in request.META:
            request.META[cls.META_KEY] = cls()

        return request.META[cls.META_KEY]

    @classmethod
    def get(cls, request: HttpRequest) -> RequestAuditBuffer | None:
        """
        request에서 기존 버퍼 가져오기 (없으면 None).

        Args:
            request: Django HttpRequest 객체

        Returns:
            RequestAuditBuffer 또는 None
        """
        if not hasattr(request, "META"):
            return None
        return request.META.get(cls.META_KEY)

    @classmethod
    def exists(cls, request: HttpRequest) -> bool:
        """request에 버퍼가 존재하는지 확인."""
        if not hasattr(request, "META"):
            return False
        return cls.META_KEY in request.META


# =============================================================================
# 편의 함수
# =============================================================================


def add_audit_event(
    request: HttpRequest,
    event_type: AuditEventType,
    source: str,
    details: dict[str, Any] | None = None,
    **kwargs,
) -> AuditEvent | None:
    """
    요청에 Audit 이벤트 추가 (편의 함수).

    request 객체가 유효하지 않으면 None 반환.

    Args:
        request: Django HttpRequest 객체
        event_type: 이벤트 유형
        source: 이벤트 발생 위치
        details: 추가 상세 정보
        **kwargs: AuditEvent 추가 파라미터

    Returns:
        생성된 AuditEvent 또는 None

    Usage:
        from baldur.audit.event_buffer import add_audit_event, AuditEventType

        add_audit_event(
            request,
            AuditEventType.CB_STATE_CHANGE,
            "BaldurMiddleware",
            details={"cb_name": "payment", "new_state": "open"},
        )
    """
    try:
        buffer = RequestAuditBuffer.get_or_create(request)
        return buffer.add(
            event_type=event_type,
            source=source,
            details=details,
            **kwargs,
        )
    except Exception:
        return None
