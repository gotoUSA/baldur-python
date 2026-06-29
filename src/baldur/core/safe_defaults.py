"""
Safe Default Values for Baldur Configuration.

모든 설정에 대해 안전한 기본값 정의.
설정 오류 시 이 값으로 폴백.

Fail-Safe Default 강화

PARTIAL DEPRECATION NOTICE:
- SAFE_DEFAULTS: 유지됨 (레거시 호환성)
- VALIDATION_RULES: 부분 폐기됨 → Pydantic Settings의 Field 제약조건으로 대체
- FATAL_CONFIGS: 유지됨 (위험한 설정 변경 방지)

새 코드에서는 baldur.settings 모듈의 Pydantic Settings를 사용하세요.
"""

from typing import Any

import structlog

from baldur.core.exceptions import ConfigurationError

logger = structlog.get_logger()


# =============================================================================
# Safe Default Values
# =============================================================================

SAFE_DEFAULTS: dict[str, dict[str, Any]] = {
    # Circuit Breaker - 보수적 설정 (더 빨리 열림, 시스템 보호 우선)
    "circuit_breaker": {
        "enabled": True,  # 항상 활성화
        "failure_threshold": 5,  # 낮게 유지
        "recovery_timeout": 60,  # 1분
        "success_threshold": 2,
        "half_open_max_calls": 3,
        "rate_limit_cascade_threshold": 10,
        "rate_limit_cascade_window_seconds": 60,
        "rate_limit_cascade_rate": 10.0,
        "rate_limit_cascade_minimum_calls": 20,
        "self_ddos_protection_enabled": True,
        "self_ddos_rps_limit": 200,
        "self_ddos_window_seconds": 10,
        "self_ddos_backoff_multiplier": 2.0,
    },
    # DLQ - 보수적 설정 (더 오래 보관, 데이터 유실 방지)
    "dlq": {
        "enabled": True,
        "retry_delay": 60,
        "expiry_hours": 72,
        "retention_days": 30,
        "batch_size": 10,
        "max_replay_attempts": 2,
    },
    # Retry - 보수적 설정 (덜 공격적, 백엔드 보호)
    "retry": {
        "max_attempts": 3,
        "backoff_strategy": "exponential",
        "backoff_base": 4,
        "base_delay": 1.0,
        "max_delay": 300.0,
        "min_delay": 1,
        "jitter": True,
        "jitter_percent": 25,
    },
    # Rate Limit - 합리적 제한
    "rate_limit": {
        "base_delay": 1.0,
        "max_delay": 60.0,
        "jitter_percent": 30.0,
        "default_retry_after": 5.0,
        "backoff_multiplier": 2.0,
        "control_api_rate_limit": 100,
        "control_api_window_seconds": 60,
        "emergency_rate_limit": 10,
        "emergency_window_seconds": 60,
    },
    # SLA - 합리적 기본값
    "sla": {
        "default_hours": 24,
    },
    # SLO - Google SRE 권장값
    "slo": {
        "default_window_days": 30,
        "default_target": 0.999,
        "default_fast_burn_rate": 14.4,
        "default_slow_burn_rate": 3.0,
    },
    # Security - 엄격한 설정
    "security": {
        "rate_limit_window_seconds": 60,
        "rate_limit_max_requests": 100,
        "temporary_ban_hours": 1,
        "permanent_ban_threshold": 5,
        "suspicious_ip_cache_timeout": 86400,
        "injection_ban_hours": 24,
        "failed_login_threshold": 5,
    },
    # Forensic - 보수적 크기 제한 (메모리 보호)
    "forensic": {
        "error_message_max_length": 500,
        "response_body_max_length": 5000,
        "user_agent_max_length": 500,
        "max_stack_frames": 50,
        "max_context_size_bytes": 65536,  # 64KB
        "include_local_variables": False,  # 보안상 비활성화
        "sanitize_sensitive_data": True,
    },
    # Logging - 기본 INFO 레벨
    "logging": {
        "dlq_log_level": "INFO",
        "circuit_breaker_log_level": "INFO",
        "replay_log_level": "INFO",
        "sla_log_level": "INFO",
        "forensic_log_level": "DEBUG",
        "emergency_log_level": "WARNING",
        "chaos_log_level": "INFO",
        "l2_storage_log_level": "INFO",
        "include_timestamps": True,
        "include_request_id": True,
        "include_user_info": False,  # 보안상 비활성화
        "structured_json": True,
    },
    # Notification - 합리적 제한
    "notification": {
        "enabled": True,
        "critical_threshold": 10,
        "warning_threshold": 5,
        "slack_block_text_limit": 3000,
        "description_max_length": 500,
        "action_taken_max_length": 200,
        "title_max_length": 150,
        "notification_timeout_seconds": 10,
    },
    # Metrics - 기본 활성화
    "metrics": {
        "enabled": True,
        "prefix": "baldur",
        "jitter_enabled": True,
        "jitter_max_delay_seconds": 60.0,
    },
    # Error Budget - Google SRE 권장값
    "error_budget": {
        "threshold_healthy": 75.0,
        "threshold_caution": 50.0,
        "threshold_warning": 20.0,
        "threshold_critical": 0.0,
        "burn_rate_fast_critical": 14.4,
        "burn_rate_fast_warning": 6.0,
        "burn_rate_slow_warning": 3.0,
        "burn_rate_slow_info": 1.0,
        "failsafe_alert_enabled": True,
        "failsafe_cooldown_seconds": 300,
        "heartbeat_enabled": True,
        "heartbeat_interval_seconds": 60,
        "heartbeat_timeout_seconds": 120,
        "recovery_alert_enabled": True,
        "recovery_alert_include_downtime": True,
        "escalation_enabled": True,
    },
    # Idempotency - 적절한 TTL
    "idempotency": {
        "default_cache_ttl": 60,
        "extended_cache_ttl": 300,
        "clock_skew_tolerance_seconds": 5.0,
    },
    # Chaos - 보수적 설정 (안전 우선)
    "chaos": {
        "enabled": False,  # 기본 비활성화
        "max_blast_radius": 0.05,  # 5%로 제한
        "dry_run": True,  # 기본 Dry Run
        "failure_rate": 0.01,  # 1%
        "latency_max_ms": 1000,
    },
    # Emergency - 보수적 설정
    "emergency": {
        "auto_trigger_enabled": False,  # 수동 트리거만
        "auto_release_enabled": True,
        "gradual_recovery_steps": 5,
        "recovery_step_duration_seconds": 60,
    },
    # Governance - Phase C 추가: RBAC 및 승인 관련 보수적 설정
    "governance": {
        "approval_timeout_hours": 24,  # 승인 대기 시간
        "max_approval_retries": 3,  # 최대 재승인 요청 횟수
        "threshold_operator": 0.15,  # Operator 레벨 임계값 (15%)
        "threshold_admin": 0.30,  # Admin 레벨 임계값 (30%)
        "emergency_expiry_hours": 4,  # 비상 모드 기본 만료 시간
        "audit_log_retention_days": 90,  # 감사 로그 보관 기간
        "require_reason_for_changes": True,  # 변경 사유 필수
    },
    # L2 Storage - Phase C 추가: 외부 스토리지 연동 보수적 설정
    "l2_storage": {
        "enabled": False,  # 기본 비활성화 (명시적 활성화 필요)
        "redis_timeout_ms": 50,  # Redis timeout 50ms (L2 is supplementary; fail-fast)
        "reconciliation_interval_seconds": 300,  # 5분마다 정합성 검사
        "reconciliation_jitter_percent": 20,  # 정합성 검사 지터 20%
        "max_retry_on_failure": 3,  # 실패 시 최대 재시도
        "connection_pool_size": 10,  # 커넥션 풀 크기
    },
    # Drift Threshold - Phase C 추가: 메트릭 드리프트 감지 보수적 설정
    "drift_threshold": {
        "enabled": True,  # 기본 활성화 (이상 감지 필요)
        "warning_percent": 5.0,  # 5% 이상 변동 시 경고
        "critical_percent": 20.0,  # 20% 이상 변동 시 Critical
        "check_interval_seconds": 60,  # 60초마다 검사
        "window_size_seconds": 300,  # 5분 윈도우
        "min_samples_required": 10,  # 최소 10개 샘플 필요
        "suppress_duplicate_alerts_seconds": 300,  # 중복 알림 억제 5분
    },
}


# =============================================================================
# Validation Rules
# =============================================================================

# 설정별 유효성 검증 규칙
VALIDATION_RULES: dict[str, dict[str, tuple[Any, Any]]] = {
    "circuit_breaker": {
        "failure_threshold": (1, 100),
        "recovery_timeout": (1, 3600),
        "success_threshold": (1, 100),
        "half_open_max_calls": (1, 100),
        "rate_limit_cascade_rate": (0.0, 100.0),
        "rate_limit_cascade_minimum_calls": (1, 100),
        "self_ddos_rps_limit": (1, 10000),
        "self_ddos_window_seconds": (1, 300),
        "self_ddos_backoff_multiplier": (1.0, 10.0),
    },
    "dlq": {
        "retry_delay": (1, 3600),
        "expiry_hours": (1, 720),
        "retention_days": (1, 365),
        "batch_size": (1, 1000),
        "max_replay_attempts": (1, 10),
    },
    "retry": {
        "max_attempts": (1, 20),
        "backoff_base": (1, 10),
        "base_delay": (0.1, 60.0),
        "max_delay": (1.0, 3600.0),
        "min_delay": (1, 60),
        "jitter_percent": (0, 100),
    },
    "rate_limit": {
        "base_delay": (0.1, 60.0),
        "max_delay": (1.0, 300.0),
        "jitter_percent": (0.0, 100.0),
        "default_retry_after": (0.1, 60.0),
        "backoff_multiplier": (1.0, 10.0),
        "control_api_rate_limit": (1, 10000),
        "emergency_rate_limit": (1, 100),
    },
    "security": {
        "rate_limit_window_seconds": (1, 3600),
        "rate_limit_max_requests": (1, 10000),
        "temporary_ban_hours": (1, 168),
        "permanent_ban_threshold": (1, 100),
        "injection_ban_hours": (1, 720),
        "failed_login_threshold": (1, 100),
    },
    "forensic": {
        "error_message_max_length": (50, 5000),
        "response_body_max_length": (100, 100000),
        "user_agent_max_length": (50, 2000),
        "max_stack_frames": (10, 200),
        "max_context_size_bytes": (1024, 1048576),
    },
    "notification": {
        "critical_threshold": (1, 100),
        "warning_threshold": (1, 100),
        "slack_block_text_limit": (100, 10000),
        "description_max_length": (50, 5000),
        "action_taken_max_length": (50, 1000),
        "title_max_length": (20, 500),
        "notification_timeout_seconds": (1, 60),
    },
    "metrics": {
        "jitter_max_delay_seconds": (0.0, 300.0),
    },
    "error_budget": {
        "threshold_healthy": (50.0, 100.0),
        "threshold_caution": (20.0, 80.0),
        "threshold_warning": (5.0, 50.0),
        "threshold_critical": (0.0, 20.0),
        "burn_rate_fast_critical": (10.0, 50.0),
        "burn_rate_fast_warning": (3.0, 15.0),
        "burn_rate_slow_warning": (1.0, 10.0),
        "burn_rate_slow_info": (0.5, 3.0),
        "failsafe_cooldown_seconds": (60, 3600),
        "heartbeat_interval_seconds": (10, 300),
        "heartbeat_timeout_seconds": (30, 600),
    },
    "idempotency": {
        "default_cache_ttl": (1, 3600),
        "extended_cache_ttl": (1, 86400),
        "clock_skew_tolerance_seconds": (0.0, 60.0),
    },
    "chaos": {
        "max_blast_radius": (0.0, 0.5),  # 50% 초과 불가
        "failure_rate": (0.0, 0.5),  # 50% 초과 불가
        "latency_max_ms": (0, 10000),
    },
    "sla": {
        "default_hours": (1, 720),
    },
    "slo": {
        "default_window_days": (1, 365),
        "default_target": (0.9, 1.0),
        "default_fast_burn_rate": (1.0, 100.0),
        "default_slow_burn_rate": (0.5, 50.0),
    },
    # Phase C 추가: governance 검증 규칙
    "governance": {
        "approval_timeout_hours": (1, 168),  # 1시간 ~ 7일
        "max_approval_retries": (1, 10),
        "threshold_operator": (0.01, 1.0),  # 1% ~ 100% (백분율)
        "threshold_admin": (0.01, 1.0),  # 1% ~ 100% (백분율)
        "emergency_expiry_hours": (1, 48),  # 최대 48시간
        "audit_log_retention_days": (7, 365),  # 7일 ~ 1년
    },
    # Phase C 추가: l2_storage 검증 규칙
    "l2_storage": {
        "redis_timeout_ms": (10, 1000),  # 10ms ~ 1s (L2 is supplementary; fail-fast)
        "reconciliation_interval_seconds": (60, 3600),  # 1분 ~ 1시간
        "reconciliation_jitter_percent": (0, 50),  # 0% ~ 50%
        "max_retry_on_failure": (1, 10),
        "connection_pool_size": (1, 100),
    },
    # Phase C 추가: drift_threshold 검증 규칙
    "drift_threshold": {
        "warning_percent": (1.0, 50.0),  # 1% ~ 50%
        "critical_percent": (5.0, 100.0),  # 5% ~ 100%
        "check_interval_seconds": (10, 600),  # 10초 ~ 10분
        "window_size_seconds": (60, 3600),  # 1분 ~ 1시간
        "min_samples_required": (1, 100),
        "suppress_duplicate_alerts_seconds": (60, 3600),
    },
}

# 유효한 로그 레벨
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# 유효한 backoff 전략
VALID_BACKOFF_STRATEGIES = {"exponential", "linear", "constant", "decorrelated_jitter"}


# =============================================================================
# Fatal Config Classification (is_fatal)
# =============================================================================
#
# Fatal configs: 위반 시 시스템 시작을 차단하는 Critical 설정
# Non-fatal configs: 위반 시 Safe Default로 대체하고 경고만 출력
#
# 설계 원칙:
# - Security, Chaos, Error Budget 관련 설정은 Fatal (시스템 안정성 직결)
# - Circuit Breaker, DLQ 등 운영 설정은 Non-fatal (Safe Default 적용)
# =============================================================================

FATAL_CONFIGS: dict[str, set[str]] = {
    # Security: 보안 관련 핵심 설정
    "security": {
        "rate_limit_max_requests",  # Rate limit이 너무 높으면 DDoS에 취약
        "injection_ban_hours",  # SQL Injection 대응 필수
        "failed_login_threshold",  # Brute force 방지 필수
    },
    # Chaos: 프로덕션에서 잘못된 설정은 치명적
    "chaos": {
        "max_blast_radius",  # 50% 초과 시 시스템 장애
        "failure_rate",  # 50% 초과 시 서비스 불가
    },
    # Error Budget: 잘못된 임계값은 자동 복구 오작동 유발
    "error_budget": {
        "threshold_critical",  # 임계값 0 미만 불가
        "burn_rate_fast_critical",  # Burn rate 범위 초과 위험
    },
}

# Fatal 설정 위반 시 Quarantine Mode 활성화 여부 (True = LEVEL_3 격리)
ENABLE_QUARANTINE_ON_FATAL = True


def is_fatal_config(config_type: str, key: str) -> bool:
    """
    설정이 Fatal (필수) 설정인지 확인.

    Fatal 설정 위반은:
    - CI/CD에서 Hard Block (비정상 종료)
    - 런타임에서 Quarantine Mode (LEVEL_3) 활성화

    Args:
        config_type: 설정 유형 (security, chaos 등)
        key: 설정 키

    Returns:
        True if fatal config, False otherwise
    """
    fatal_keys = FATAL_CONFIGS.get(config_type, set())
    return key in fatal_keys


def get_all_fatal_configs() -> dict[str, set[str]]:
    """
    모든 Fatal 설정 목록 반환.

    Returns:
        {config_type: {key1, key2, ...}, ...} 형태
    """
    return {k: v.copy() for k, v in FATAL_CONFIGS.items()}


# =============================================================================
# Helper Functions
# =============================================================================


def get_safe_default(config_type: str, key: str) -> Any | None:
    """
    안전한 기본값 반환.

    Args:
        config_type: 설정 유형 (circuit_breaker, dlq, retry 등)
        key: 설정 키

    Returns:
        안전한 기본값 또는 None
    """
    defaults = SAFE_DEFAULTS.get(config_type, {})
    return defaults.get(key)


def get_safe_defaults_for_type(config_type: str) -> dict[str, Any]:
    """
    특정 설정 유형의 모든 안전한 기본값 반환.

    Args:
        config_type: 설정 유형

    Returns:
        해당 설정 유형의 안전한 기본값 딕셔너리
    """
    return SAFE_DEFAULTS.get(config_type, {}).copy()


def is_valid_value(config_type: str, key: str, value: Any) -> bool:  # noqa: C901
    """
    값 유효성 검증.

    Args:
        config_type: 설정 유형
        key: 설정 키
        value: 검증할 값

    Returns:
        유효하면 True, 아니면 False
    """
    # None 체크
    if value is None:
        return False

    # 범위 검증 규칙이 있는 경우
    rules = VALIDATION_RULES.get(config_type, {})
    if key in rules:
        min_val, max_val = rules[key]
        try:
            if value < min_val or value > max_val:
                return False
        except TypeError:
            # 비교 불가능한 타입
            return False

    # 로그 레벨 검증
    if key.endswith("_log_level") and value not in VALID_LOG_LEVELS:
        return False

    # backoff 전략 검증
    if key == "backoff_strategy" and value not in VALID_BACKOFF_STRATEGIES:
        return False

    # Boolean validation
    return not (
        (key.startswith("enabled") or key.endswith("_enabled"))
        and not isinstance(value, bool)
    )


def validate_with_safe_fallback(
    config_type: str, values: dict[str, Any], log_changes: bool = True
) -> dict[str, Any]:
    """
    설정값 검증 후 안전한 값으로 폴백.

    잘못된 값은 Safe Default로 대체됩니다.

    Args:
        config_type: 설정 유형
        values: 검증할 설정값들
        log_changes: 변경 사항 로깅 여부

    Returns:
        검증 및 폴백된 설정값 딕셔너리
    """
    result = {}
    defaults = SAFE_DEFAULTS.get(config_type, {})

    for key, value in values.items():
        if not is_valid_value(config_type, key, value):
            safe_value = defaults.get(key)
            if safe_value is not None:
                if log_changes:
                    logger.warning(
                        "safe_default.invalid_using_safe_default",
                        config_type=config_type,
                        config_key=key,
                        config_value=value,
                        safe_value=safe_value,
                    )
                result[key] = safe_value
            else:
                # Safe default가 없으면 원래 값 유지하되 경고
                if log_changes:
                    logger.warning(
                        "safe_default.invalid_no_safe_default",
                        config_type=config_type,
                        config_key=key,
                        config_value=value,
                    )
                result[key] = value
        else:
            result[key] = value

    return result


def validate_all_with_safe_fallback(
    config_dict: dict[str, dict[str, Any]], log_changes: bool = True
) -> dict[str, dict[str, Any]]:
    """
    전체 설정 딕셔너리 검증 후 안전한 값으로 폴백.

    Args:
        config_dict: {config_type: {key: value, ...}, ...} 형태의 설정
        log_changes: 변경 사항 로깅 여부

    Returns:
        검증 및 폴백된 전체 설정 딕셔너리
    """
    result = {}
    for config_type, values in config_dict.items():
        result[config_type] = validate_with_safe_fallback(
            config_type, values, log_changes
        )
    return result


def apply_safe_defaults_to_missing(
    config_type: str, values: dict[str, Any]
) -> dict[str, Any]:
    """
    누락된 설정에 Safe Default 적용.

    기존 값은 유지하고 누락된 키에만 Safe Default 추가.

    Args:
        config_type: 설정 유형
        values: 현재 설정값들

    Returns:
        Safe Default가 채워진 설정값 딕셔너리
    """
    defaults = SAFE_DEFAULTS.get(config_type, {})
    result = defaults.copy()
    result.update(values)  # 기존 값이 우선
    return result


def get_validation_errors(config_type: str, values: dict[str, Any]) -> dict[str, str]:
    """
    설정값 검증 후 오류 목록 반환.

    Args:
        config_type: 설정 유형
        values: 검증할 설정값들

    Returns:
        {key: error_message} 형태의 오류 딕셔너리
    """
    errors = {}
    rules = VALIDATION_RULES.get(config_type, {})

    for key, value in values.items():
        if value is None:
            errors[key] = "Value cannot be None"
            continue

        # 범위 검증
        if key in rules:
            min_val, max_val = rules[key]
            try:
                if value < min_val:
                    errors[key] = f"Value {value} is below minimum {min_val}"
                elif value > max_val:
                    errors[key] = f"Value {value} exceeds maximum {max_val}"
            except TypeError:
                errors[key] = f"Value {value!r} is not a valid number"

        # 로그 레벨 검증
        if key.endswith("_log_level") and value not in VALID_LOG_LEVELS:
            errors[key] = (
                f"Invalid log level: {value}. Must be one of {VALID_LOG_LEVELS}"
            )

        # backoff 전략 검증
        if key == "backoff_strategy" and value not in VALID_BACKOFF_STRATEGIES:
            errors[key] = (
                f"Invalid backoff strategy: {value}. Must be one of {VALID_BACKOFF_STRATEGIES}"
            )

    return errors


# =============================================================================
# Startup Validation
# =============================================================================


class FatalConfigError(ConfigurationError):
    """
    Fatal configuration violation.

    Raised when a setting with is_fatal=True is invalid.
    Used for CI/CD hard block and runtime quarantine mode.
    """

    def __init__(self, violations: dict[str, dict[str, str]]):
        self.violations = violations
        violation_list = [
            f"{config_type}.{key}: {msg}"
            for config_type, keys in violations.items()
            for key, msg in keys.items()
        ]
        super().__init__(
            "Fatal config violations detected:\n" + "\n".join(violation_list)
        )

    def extra_context(self) -> dict:
        ctx = super().extra_context()
        ctx["violations"] = self.violations
        return ctx


class ConfigValidationResult:
    """설정 검증 결과."""

    def __init__(self):
        self.changes_count: int = 0
        self.fatal_violations: dict[str, dict[str, str]] = {}
        self.non_fatal_warnings: dict[str, dict[str, str]] = {}

    @property
    def has_fatal_violations(self) -> bool:
        return len(self.fatal_violations) > 0

    @property
    def is_valid(self) -> bool:
        return not self.has_fatal_violations

    def add_fatal_violation(self, config_type: str, key: str, error_msg: str) -> None:
        """Fatal 설정 위반 기록."""
        if config_type not in self.fatal_violations:
            self.fatal_violations[config_type] = {}
        self.fatal_violations[config_type][key] = error_msg

    def add_non_fatal_warning(self, config_type: str, key: str, error_msg: str) -> None:
        """Non-fatal 경고 기록."""
        if config_type not in self.non_fatal_warnings:
            self.non_fatal_warnings[config_type] = {}
        self.non_fatal_warnings[config_type][key] = error_msg


# config_type -> config attribute 매핑 (상수)
_CONFIG_TYPE_MAPPING: dict[str, str] = {
    "circuit_breaker": "circuit_breaker",
    "dlq": "dlq",
    "retry": "retry",
    "sla": "sla",
    "security": "security",
    "forensic": "forensic",
    "metrics": "metrics",
    "notification": "notification",
    "rate_limit": "rate_limit",
    "idempotency": "idempotency",
    "chaos": "chaos",
    "error_budget": "error_budget",
}


def _handle_fatal_violation(
    result: ConfigValidationResult,
    config_type: str,
    key: str,
    current: Any,
    error_msg: str,
    log_changes: bool,
) -> None:
    """Fatal 설정 위반 처리."""
    result.add_fatal_violation(config_type, key, error_msg)
    if log_changes:
        logger.error(
            "fatal.invalid_critical_config_violation",
            config_type=config_type,
            config_key=key,
            current=current,
        )


def _handle_non_fatal_violation(
    result: ConfigValidationResult,
    sub_config: Any,
    config_type: str,
    key: str,
    current: Any,
    safe_value: Any,
    error_msg: str,
    log_changes: bool,
) -> None:
    """Non-fatal 설정 위반 처리 및 Safe Default 적용."""
    result.add_non_fatal_warning(config_type, key, error_msg)
    if log_changes:
        logger.warning(
            "startup.invalid_applying_safe_default",
            config_type=config_type,
            config_key=key,
            current=current,
            safe_value=safe_value,
        )
    try:
        setattr(sub_config, key, safe_value)
        result.changes_count += 1
    except AttributeError:
        # frozen dataclass의 경우
        if log_changes:
            logger.warning(
                "startup.cannot_modify_frozen",
                config_type=config_type,
                config_key=key,
            )


def _validate_single_config_value(
    result: ConfigValidationResult,
    sub_config: Any,
    config_type: str,
    key: str,
    safe_value: Any,
    log_changes: bool,
) -> None:
    """단일 설정 값 검증 및 처리."""
    current = getattr(sub_config, key, None)

    if is_valid_value(config_type, key, current):
        return  # 유효한 값이면 조기 반환

    error_msg = f"Invalid value {current!r}, expected safe default: {safe_value!r}"

    if is_fatal_config(config_type, key):
        _handle_fatal_violation(
            result, config_type, key, current, error_msg, log_changes
        )
    else:
        _handle_non_fatal_violation(
            result,
            sub_config,
            config_type,
            key,
            current,
            safe_value,
            error_msg,
            log_changes,
        )


def _finalize_validation(
    result: ConfigValidationResult,
    log_changes: bool,
    raise_on_fatal: bool,
) -> None:
    """검증 완료 후 처리 (로깅 및 예외 발생)."""
    if log_changes and result.changes_count > 0:
        logger.info(
            "startup.applied_safe_default",
            changes_count=result.changes_count,
        )

    if result.has_fatal_violations:
        if log_changes:
            logger.critical(
                "fatal.fatal_config_violations_detected",
                fatal_violations_count=len(result.fatal_violations),
                fatal_violation_keys=list(result.fatal_violations.keys()),
            )
        if raise_on_fatal:
            raise FatalConfigError(result.fatal_violations)


def validate_startup_config(
    config: Any, log_changes: bool = True, raise_on_fatal: bool = False
) -> int:
    """
    시작 시 설정 검증 + Safe Default 적용.

    BaldurConfig 인스턴스의 모든 설정을 검증하고
    잘못된 값은 Safe Default로 대체합니다.

    Fatal 설정(is_fatal=True)이 유효하지 않으면:
    - raise_on_fatal=True: FatalConfigError 발생 (CI/CD용)
    - raise_on_fatal=False: 경고 로그만 출력 (런타임 Best-effort)

    Args:
        config: BaldurConfig 인스턴스
        log_changes: 변경 사항 로깅 여부
        raise_on_fatal: Fatal 설정 위반 시 예외 발생 여부

    Returns:
        수정된 설정 수

    Raises:
        FatalConfigError: raise_on_fatal=True이고 Fatal 설정 위반 시
    """
    result = ConfigValidationResult()

    for config_type, attr_name in _CONFIG_TYPE_MAPPING.items():
        sub_config = getattr(config, attr_name, None)
        if sub_config is None:
            continue

        defaults = SAFE_DEFAULTS.get(config_type, {})
        for key, safe_value in defaults.items():
            _validate_single_config_value(
                result, sub_config, config_type, key, safe_value, log_changes
            )

    _finalize_validation(result, log_changes, raise_on_fatal)
    return result.changes_count


def validate_config_preflight(config: Any) -> ConfigValidationResult:
    """
    Pre-flight 설정 검증 (CI/CD용).

    모든 설정을 검증하고 결과를 반환합니다.
    실제 설정은 수정하지 않습니다.

    Args:
        config: BaldurConfig 인스턴스

    Returns:
        ConfigValidationResult 인스턴스
    """
    result = ConfigValidationResult()

    config_mapping = {
        "circuit_breaker": "circuit_breaker",
        "dlq": "dlq",
        "retry": "retry",
        "sla": "sla",
        "security": "security",
        "forensic": "forensic",
        "metrics": "metrics",
        "notification": "notification",
        "rate_limit": "rate_limit",
        "idempotency": "idempotency",
        "chaos": "chaos",
        "error_budget": "error_budget",
    }

    for config_type, attr_name in config_mapping.items():
        sub_config = getattr(config, attr_name, None)
        if sub_config is None:
            continue

        defaults = SAFE_DEFAULTS.get(config_type, {})

        for key, safe_value in defaults.items():
            current = getattr(sub_config, key, None)

            if not is_valid_value(config_type, key, current):
                is_fatal = is_fatal_config(config_type, key)
                error_msg = (
                    f"Value {current!r} is invalid (safe default: {safe_value!r})"
                )

                if is_fatal:
                    if config_type not in result.fatal_violations:
                        result.fatal_violations[config_type] = {}
                    result.fatal_violations[config_type][key] = error_msg
                else:
                    if config_type not in result.non_fatal_warnings:
                        result.non_fatal_warnings[config_type] = {}
                    result.non_fatal_warnings[config_type][key] = error_msg

    return result


# =============================================================================
# Chaos-Specific Safety Guards
# =============================================================================


def validate_chaos_config(values: dict[str, Any]) -> dict[str, Any]:
    """
    Chaos 설정 특별 검증.

    Chaos 엔지니어링은 특히 위험하므로 추가 안전 장치 적용.

    Args:
        values: Chaos 설정값들

    Returns:
        안전하게 검증된 설정값들
    """
    result = values.copy()

    # Blast Radius 강제 제한 (50% 초과 불가)
    if "max_blast_radius" in result:
        if result["max_blast_radius"] > 0.5:
            logger.warning(
                "safe_default.chaos_exceeds_clamping",
                max_blast_radius=result["max_blast_radius"],
            )
            result["max_blast_radius"] = 0.5
        if result["max_blast_radius"] < 0:
            result["max_blast_radius"] = 0.0

    # Failure Rate 강제 제한
    if "failure_rate" in result:
        if result["failure_rate"] > 0.5:
            logger.warning(
                "safe_default.chaos_exceeds_clamping",
                failure_rate=result["failure_rate"],
            )
            result["failure_rate"] = 0.5
        if result["failure_rate"] < 0:
            result["failure_rate"] = 0.0

    # Production 환경에서는 dry_run 강제
    import os

    if os.environ.get("DJANGO_SETTINGS_MODULE", "").endswith(
        "production"
    ) and not result.get("dry_run", True):
        logger.warning("safe_default.chaos_production_forcing_true")
        result["dry_run"] = True

    return result
