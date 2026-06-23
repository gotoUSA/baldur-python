"""
Audit Reconciler - WAL vs 중앙 저장소 정합성 검증.

ADR-005 (Fail-Open + WAL 기반 누락 0 보장) 구현의 보조 컴포넌트.

동작 원리:
1. WAL과 중앙 저장소의 시퀀스/레코드 비교
2. 누락 감지 시 재전송
3. 불일치 알림 발생
4. 주기적 실행 (기본 5분)

Usage:
    from baldur.audit.reconciler import AuditReconciler, ReconcilerConfig

    reconciler = AuditReconciler(
        wal=wal_instance,
        central_adapter=adapter,
    )
    reconciler.start()

    # 종료 시
    reconciler.stop()
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from baldur.core.serializable import SerializableMixin

if TYPE_CHECKING:
    from baldur.settings.audit_reconciler import AuditReconcilerSettings

logger = structlog.get_logger()


@dataclass
class ReconcilerConfig:
    """Reconciler 설정."""

    # 검증 주기 (초) - 기본 5분
    check_interval_seconds: float = 300.0

    # 검증 범위 (초) - 최근 N초 내 엔트리만 검증
    check_window_seconds: float = 3600.0  # 1시간

    # 누락 재전송 시 배치 크기
    resend_batch_size: int = 50

    # 최대 재전송 시도 횟수
    max_resend_attempts: int = 3

    # 알림 임계값 - N개 이상 누락 시 알림
    alert_threshold: int = 10

    # confirmed record_id cache max size
    max_confirmed_ids: int = 10000

    @classmethod
    def from_settings(
        cls,
        settings: AuditReconcilerSettings | None = None,
        **overrides,
    ) -> ReconcilerConfig:
        """
        Settings에서 ReconcilerConfig 인스턴스 생성.

        Args:
            settings: AuditReconcilerSettings 인스턴스 (없으면 싱글톤 사용)
            **overrides: 개별 필드 오버라이드

        Returns:
            ReconcilerConfig: Settings 기반 인스턴스
        """
        from baldur.settings.audit_reconciler import (
            get_audit_reconciler_settings,
        )

        s = settings or get_audit_reconciler_settings()
        return cls(
            check_interval_seconds=overrides.get(
                "check_interval_seconds", s.check_interval_seconds
            ),
            check_window_seconds=overrides.get(
                "check_window_seconds", s.check_window_seconds
            ),
            resend_batch_size=overrides.get("resend_batch_size", s.resend_batch_size),
            max_resend_attempts=overrides.get(
                "max_resend_attempts", s.max_resend_attempts
            ),
            alert_threshold=overrides.get("alert_threshold", s.alert_threshold),
            max_confirmed_ids=overrides.get("max_confirmed_ids", s.max_confirmed_ids),
        )


@dataclass
class ReconcileResult:
    """정합성 검증 결과."""

    timestamp: float = field(default_factory=time.time)
    wal_entry_count: int = 0
    central_entry_count: int = 0
    missing_count: int = 0
    resent_count: int = 0
    resend_failed_count: int = 0
    duration_ms: float = 0.0
    error: str | None = None

    @property
    def is_consistent(self) -> bool:
        """정합성 일치 여부."""
        return self.missing_count == 0 and self.resend_failed_count == 0

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "timestamp": self.timestamp,
            "wal_entry_count": self.wal_entry_count,
            "central_entry_count": self.central_entry_count,
            "missing_count": self.missing_count,
            "resent_count": self.resent_count,
            "resend_failed_count": self.resend_failed_count,
            "duration_ms": round(self.duration_ms, 2),
            "is_consistent": self.is_consistent,
            "error": self.error,
        }


@dataclass
class ReconcilerStats(SerializableMixin):
    """Reconciler 통계."""

    total_checks: int = 0
    total_missing_found: int = 0
    total_resent: int = 0
    total_resend_failed: int = 0
    last_check_time: float | None = None
    last_result: ReconcileResult | None = None
    consecutive_failures: int = 0


class AuditReconciler:
    """
    Audit Reconciler.

    WAL과 중앙 저장소 간의 정합성을 주기적으로 검증하고,
    누락된 엔트리를 재전송합니다.

    Thread-safe하며, 단일 인스턴스로 운영.
    """

    _instance: AuditReconciler | None = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        wal: Any = None,
        central_adapter: Any = None,
        config: ReconcilerConfig | None = None,
        on_missing_found: Callable[[int], None] | None = None,
        on_alert: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        """
        Initialize Reconciler.

        Args:
            wal: WriteAheadLog 인스턴스
            central_adapter: 중앙 저장소 어댑터
            config: Reconciler 설정
            on_missing_found: 누락 발견 콜백 (missing_count)
            on_alert: 알림 콜백 (alert_type, details)
        """
        self._wal = wal
        self._central_adapter = central_adapter
        self._config = config or ReconcilerConfig.from_settings()
        self._on_missing_found = on_missing_found
        self._on_alert = on_alert

        self._stats = ReconcilerStats()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        self._handle: Any | None = None  # DaemonWorkerHandle (impl 489 D9)

        # 중앙 저장소에서 확인된 record_id 캐시
        self._confirmed_ids: set[str] = set()
        self._confirmed_ids_max_size = self._config.max_confirmed_ids

        logger.info(
            "audit_reconciler.initialized",
            check_interval_seconds=self._config.check_interval_seconds,
        )

    @classmethod
    def get_instance(
        cls,
        wal: Any = None,
        central_adapter: Any = None,
        config: ReconcilerConfig | None = None,
    ) -> AuditReconciler:
        """Get or create singleton instance."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(
                        wal=wal,
                        central_adapter=central_adapter,
                        config=config,
                    )
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (테스트용)."""
        with cls._instance_lock:
            if cls._instance:
                cls._instance.stop()
            cls._instance = None

    def _get_wal(self) -> Any:
        """WAL 인스턴스 가져오기."""
        if self._wal is not None:
            return self._wal

        try:
            from baldur_pro.services.audit import _get_wal

            return _get_wal()
        except Exception as e:
            logger.warning(
                "audit_reconciler.get_wal_failed",
                error=e,
            )
            return None

    def _get_adapter(self) -> Any:
        """중앙 저장소 어댑터 가져오기."""
        if self._central_adapter is not None:
            return self._central_adapter

        try:
            from baldur.factory import ProviderRegistry

            return ProviderRegistry.get_audit_adapter()
        except Exception as e:
            logger.debug(
                "audit_reconciler.adapter_available",
                error=e,
            )
            return None

    def start(self) -> bool:
        """
        Reconciler 시작.

        Returns:
            True: 시작 성공
            False: 이미 실행 중
        """
        from baldur.meta.daemon_worker import DaemonWorkerHandle
        from baldur.metrics.recorders.daemon_worker import register_daemon_worker

        with self._lock:
            if self._running:
                return False

            self._stop_event.clear()
            self._running = True
            self._spawn_thread()
            assert self._thread is not None  # _spawn_thread() invariant
            self._handle = DaemonWorkerHandle(
                thread=self._thread,
                tick_interval_seconds=self._config.check_interval_seconds,
                restart_callback=self._spawn_thread,
            )
            register_daemon_worker("AuditReconciler", self._handle)
            logger.info("reconciler.started")
            return True

    def _spawn_thread(self) -> None:
        """Construct + start a fresh reconciler thread (impl 489 D9)."""
        self._thread = threading.Thread(
            target=self._run_loop_with_crash_capture,
            name="AuditReconciler",
            daemon=True,
        )
        self._thread.start()
        if self._handle is not None:
            self._handle.thread = self._thread

    def _run_loop_with_crash_capture(self) -> None:
        try:
            self._run_loop()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as e:
            if self._handle is not None:
                self._handle.record_crash(e)
            raise

    def stop(self, timeout: float = 1.0) -> None:
        """
        Reconciler 중지.

        Args:
            timeout: 종료 대기 시간 (초)
        """
        from baldur.metrics.recorders.daemon_worker import unregister_daemon_worker

        with self._lock:
            if not self._running:
                return

            if self._handle is not None:
                self._handle.is_stopping = True

            self._stop_event.set()
            self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

        unregister_daemon_worker("AuditReconciler")
        if self._thread is not None and self._thread.is_alive():
            logger.critical(
                "daemon_worker.stop_join_timeout",
                worker_name="AuditReconciler",
                join_timeout_seconds=timeout,
            )

        logger.info("reconciler.stopped")

    def _run_loop(self) -> None:
        """메인 검증 루프."""
        while not self._stop_event.is_set():
            iter_start = time.monotonic()
            try:
                result = self._reconcile()

                with self._lock:
                    self._stats.total_checks += 1
                    self._stats.last_check_time = time.time()
                    self._stats.last_result = result

                    if result.error:
                        self._stats.consecutive_failures += 1
                    else:
                        self._stats.consecutive_failures = 0
                        self._stats.total_missing_found += result.missing_count
                        self._stats.total_resent += result.resent_count
                        self._stats.total_resend_failed += result.resend_failed_count

                # 알림 체크
                if result.missing_count >= self._config.alert_threshold:
                    self._send_alert("missing_threshold_exceeded", result.to_dict())

                logger.debug(
                    "audit_reconciler.check_completed",
                    missing_count=result.missing_count,
                    resent_count=result.resent_count,
                )

            except Exception as e:
                logger.exception(
                    "audit_reconciler.reconcile_loop_error",
                    error=e,
                )
                with self._lock:
                    self._stats.consecutive_failures += 1

            if self._handle is not None:
                self._handle.observe_iteration(time.monotonic() - iter_start)
                self._handle.heartbeat()

            # 다음 사이클까지 대기
            self._stop_event.wait(timeout=self._config.check_interval_seconds)

    def _reconcile(self) -> ReconcileResult:
        """
        정합성 검증 수행.

        Returns:
            ReconcileResult
        """
        result = ReconcileResult()
        start_time = time.time()

        wal = self._get_wal()
        if wal is None:
            result.error = "WAL not available"
            return result

        adapter = self._get_adapter()

        try:
            # 검증 범위 내 WAL 엔트리 조회
            recent_entries = self._get_recent_entries(wal)
            result.wal_entry_count = len(recent_entries)

            if not recent_entries:
                result.duration_ms = (time.time() - start_time) * 1000
                return result

            # 누락 엔트리 식별
            missing_entries = self._identify_missing_entries(
                recent_entries, adapter, result
            )
            result.missing_count = len(missing_entries)

            # 누락 콜백 호출
            self._notify_missing_found(missing_entries)

            # 누락 엔트리 재전송
            if missing_entries and adapter:
                resent, failed = self._resend_missing(adapter, missing_entries)
                result.resent_count = resent
                result.resend_failed_count = failed

        except Exception as e:
            result.error = str(e)
            logger.exception(
                "audit_reconciler.reconcile_error",
                error=e,
            )

        result.duration_ms = (time.time() - start_time) * 1000
        return result

    def _get_recent_entries(self, wal: Any) -> list[Any]:
        """시간 범위 내 최근 엔트리 조회."""
        cutoff_time = time.time() - self._config.check_window_seconds
        all_entries = wal.recover_unprocessed(0)
        return [e for e in all_entries if e.timestamp >= cutoff_time]

    def _identify_missing_entries(
        self,
        entries: list[Any],
        adapter: Any,
        result: ReconcileResult,
    ) -> list[Any]:
        """누락된 엔트리 식별."""
        missing_entries = []
        for entry in entries:
            record_id = entry.data.get("record_id")
            if not record_id or record_id in self._confirmed_ids:
                continue

            if self._check_central_storage(adapter, record_id, result):
                continue

            missing_entries.append(entry)
        return missing_entries

    def _check_central_storage(
        self,
        adapter: Any,
        record_id: str,
        result: ReconcileResult,
    ) -> bool:
        """중앙 저장소에서 엔트리 확인."""
        if adapter and hasattr(adapter, "exists"):
            try:
                if adapter.exists(record_id):
                    self._add_confirmed_id(record_id)
                    result.central_entry_count += 1
                    return True
            except Exception:
                pass
        return False

    def _notify_missing_found(self, missing_entries: list[Any]) -> None:
        """누락 엔트리 콜백 호출."""
        if missing_entries and self._on_missing_found:
            try:
                self._on_missing_found(len(missing_entries))
            except Exception:
                pass

    def _resend_missing(
        self,
        adapter: Any,
        entries: list[Any],
    ) -> tuple[int, int]:
        """
        누락 엔트리 재전송.

        Returns:
            (resent_count, failed_count)
        """
        resent = 0
        failed = 0

        # 배치 크기만큼만 처리
        batch = entries[: self._config.resend_batch_size]

        for entry in batch:
            for attempt in range(self._config.max_resend_attempts):
                try:
                    if hasattr(adapter, "write"):
                        adapter.write(entry.data)
                    elif hasattr(adapter, "log"):
                        adapter.log(entry.data)

                    # 성공 - confirmed 캐시에 추가
                    record_id = entry.data.get("record_id")
                    if record_id:
                        self._add_confirmed_id(record_id)

                    resent += 1
                    break

                except Exception as e:
                    if attempt == self._config.max_resend_attempts - 1:
                        failed += 1
                        logger.warning(
                            "audit_reconciler.resend_entry_failed",
                            error=e,
                        )
                    else:
                        time.sleep(0.1 * (attempt + 1))  # 간단한 백오프

        return resent, failed

    def _add_confirmed_id(self, record_id: str) -> None:
        """confirmed ID 캐시에 추가."""
        self._confirmed_ids.add(record_id)

        # 캐시 크기 제한
        if len(self._confirmed_ids) > self._confirmed_ids_max_size:
            # 오래된 항목 제거 (간단히 절반 제거)
            to_remove = list(self._confirmed_ids)[: self._confirmed_ids_max_size // 2]
            for rid in to_remove:
                self._confirmed_ids.discard(rid)

    def _send_alert(self, alert_type: str, details: dict[str, Any]) -> None:
        """알림 전송."""
        if self._on_alert:
            try:
                self._on_alert(alert_type, details)
            except Exception as e:
                logger.warning(
                    "audit_reconciler.send_alert_failed",
                    error=e,
                )

        # 메트릭에도 기록
        try:
            from baldur.audit.resilience import AuditMetrics

            metrics = AuditMetrics.get_instance()
            metrics.record_failure("reconciler", alert_type)
        except Exception:
            pass

        logger.warning(
            "audit_reconciler.alert",
            alert_type=alert_type,
            details=details,
        )

    def reconcile_now(self) -> ReconcileResult:
        """
        즉시 정합성 검증 수행 (테스트/디버그용).

        Returns:
            ReconcileResult
        """
        return self._reconcile()

    def get_stats(self) -> dict[str, Any]:
        """통계 조회."""
        with self._lock:
            return self._stats.to_dict()

    @property
    def is_running(self) -> bool:
        """실행 중 여부."""
        return self._running


# =============================================================================
# Convenience Functions
# =============================================================================


def start_reconciler(
    wal: Any = None,
    central_adapter: Any = None,
    config: ReconcilerConfig | None = None,
) -> AuditReconciler:
    """
    Reconciler 시작 헬퍼 함수.

    싱글톤 인스턴스를 가져오고 시작합니다.
    """
    reconciler = AuditReconciler.get_instance(
        wal=wal,
        central_adapter=central_adapter,
        config=config,
    )
    reconciler.start()
    return reconciler


def stop_reconciler() -> None:
    """Reconciler 중지 헬퍼 함수."""
    try:
        reconciler = AuditReconciler.get_instance()
        reconciler.stop()
    except Exception:
        pass


def get_reconciler_stats() -> dict[str, Any] | None:
    """Reconciler 통계 조회 헬퍼 함수."""
    try:
        reconciler = AuditReconciler.get_instance()
        return reconciler.get_stats()
    except Exception:
        return None
