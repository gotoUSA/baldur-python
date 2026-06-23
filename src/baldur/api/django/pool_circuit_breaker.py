"""
Pool-aware Circuit Breaker Middleware for Django.

Pool이 고갈되면 즉시 503을 반환하여 시스템 멈춤을 방지합니다.
Pool 대기(block) 대신 Fail Fast 전략 사용.

핵심 원리:
1. 요청 도착 시 Pool 상태 체크 (non-blocking, 캐시 기반)
2. Pool 고갈 시 즉시 503 반환 (Pool 대기하지 않음!)
3. Circuit Breaker 상태로 관리하여 자동 복구

v6.2.0 (2026-01-01): 블로킹 이슈 해결
- 매 요청마다 check_pool_status() 호출 → 캐시 기반 조회로 변경
- 백그라운드 스레드에서 주기적으로 Pool 상태 갱신 (기본: 100ms)
- Lock 경합 최소화를 위한 atomic read 패턴 적용

v6.2.1 (2026-01-02): 엔터프라이즈급 안정성 강화
- TTL(캐시 갱신 주기) 범위 검증: 50ms ~ 1000ms 자동 클램핑
- Stale 데이터 처리: 10배 Threshold 경고, 5초 이상 Safe Fallback
- 백그라운드 스레드 자동 재시작 감지 (is_alive 체크)
- Audit 통합: 거부 결정에 decision_source: "cached_pool_status" 기록
- 새로운 통계 카운터: stale_cache_fallbacks, stale_cache_warnings, background_thread_restarts
"""

import os
import threading
import time
from typing import Any

import structlog
from django.db import connections
from django.http import JsonResponse

# Drift Detection 메트릭
from baldur.metrics.drift_metrics import (
    record_pool_cb_background_restart,
    record_pool_cb_cache_age,
    record_pool_cb_stale,
    update_pool_cb_hit_rate,
)

logger = structlog.get_logger()


class PoolCircuitBreaker:
    """
    Pool 상태 기반 Circuit Breaker.

    상태:
    - CLOSED: 정상 - 모든 요청 허용
    - OPEN: 고갈 - 모든 요청 즉시 거부 (503)
    - HALF_OPEN: 복구 테스트 중 - 일부 요청만 허용
    """

    # 싱글톤 인스턴스
    _instance = None
    _lock = threading.Lock()

    # 상태 상수
    CLOSED = "CLOSED"  # 정상
    OPEN = "OPEN"  # 차단 중
    HALF_OPEN = "HALF_OPEN"  # 복구 테스트 중

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._state: str = self.CLOSED
        self._state_lock = threading.Lock()

        # 설정값 (환경 변수로 오버라이드 가능)
        self._failure_threshold = int(
            os.getenv("POOL_CB_FAILURE_THRESHOLD", "3")
        )  # 3회 실패 시 OPEN
        self._success_threshold = int(
            os.getenv("POOL_CB_SUCCESS_THRESHOLD", "2")
        )  # 2회 성공 시 CLOSED
        self._recovery_timeout = int(
            os.getenv("POOL_CB_RECOVERY_TIMEOUT", "10")
        )  # 10초 후 HALF_OPEN
        self._half_open_max_requests = int(os.getenv("POOL_CB_HALF_OPEN_MAX", "3"))

        # v6.2.0: 캐시 기반 Pool 상태 조회 설정
        # v6.2.1: TTL 범위 검증 및 Stale 처리 개선
        raw_cache_interval = int(os.getenv("POOL_CB_CACHE_INTERVAL_MS", "100"))
        # TTL 범위 검증: 50ms ~ 1000ms (너무 짧으면 경합, 너무 길면 감지 지연)
        self._cache_interval_ms = max(50, min(1000, raw_cache_interval))
        if raw_cache_interval != self._cache_interval_ms:
            logger.warning(
                "pool_circuit_breaker.cache_interval_clamped_ms",
                raw_cache_interval=raw_cache_interval,
                cache_interval_ms=self._cache_interval_ms,
            )

        # v6.2.1: Stale 캐시 임계값 설정
        self._stale_threshold_multiplier = int(
            os.getenv("POOL_CB_STALE_MULTIPLIER", "10")
        )  # 10배 = 1초 (100ms 기준)
        self._critical_stale_ms = int(
            os.getenv("POOL_CB_CRITICAL_STALE_MS", "5000")
        )  # 5초 이상이면 완전 stale

        self._cached_pool_status = {
            "available": False,
            "reason": "Not initialized yet",
            "is_exhausted": False,
            "is_near_exhaustion": False,
            "_cache_time": 0,
            "_is_stale": True,  # v6.2.1: 초기에는 stale
        }
        self._cache_lock = threading.Lock()  # 캐시 갱신용 락 (요청 처리와 분리)
        self._background_thread = None
        self._stop_background = threading.Event()
        self._handle = None  # DaemonWorkerHandle (impl 489 D9)
        self._last_successful_refresh = 0  # v6.2.1: 마지막 성공적 갱신 시간

        # 상태 추적
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = None
        self._open_time = None
        self._half_open_requests = 0

        # 통계
        self._stats = {
            "total_requests": 0,
            "rejected_requests": 0,
            "pool_exhaustion_count": 0,
            "recovery_count": 0,
            "state_changes": [],
            "cache_hits": 0,
            "cache_refreshes": 0,
            "stale_cache_fallbacks": 0,  # v6.2.1: Stale로 인한 안전 폴백 횟수
            "stale_cache_warnings": 0,  # v6.2.1: Stale 경고 횟수
            "background_thread_restarts": 0,  # v6.2.1: 스레드 재시작 횟수
        }

        # v6.2.0: 백그라운드 Pool 상태 갱신 스레드 시작
        self._start_background_refresh()

        logger.info(
            "pool_circuit_breaker.initialized_fail_fast_enabled",
            cache_interval_ms=self._cache_interval_ms,
        )

    @property
    def state(self) -> str:
        """현재 상태"""
        with self._state_lock:
            return self._state

    def _set_state(self, new_state: str):
        """상태 변경"""
        with self._state_lock:
            if self._state != new_state:
                old_state = self._state
                self._state = new_state

                timestamp = time.time()
                self._stats["state_changes"].append(
                    {
                        "from": old_state,
                        "to": new_state,
                        "time": timestamp,
                    }
                )

                logger.warning(
                    "pool_circuit_breaker.state",
                    old_state=old_state,
                    new_state=new_state,
                )

                if new_state == self.OPEN:
                    self._open_time = timestamp
                    self._stats["pool_exhaustion_count"] += 1
                elif new_state == self.CLOSED and old_state != self.CLOSED:
                    self._stats["recovery_count"] += 1

    # =========================================================================
    # v6.2.0: 캐시 기반 Pool 상태 조회 (Non-Blocking)
    # =========================================================================

    def _start_background_refresh(self):
        """백그라운드 Pool 상태 갱신 스레드 시작"""
        from baldur.meta.daemon_worker import DaemonWorkerHandle
        from baldur.metrics.recorders.daemon_worker import register_daemon_worker

        if self._background_thread and self._background_thread.is_alive():
            return  # 이미 실행 중

        self._stop_background.clear()
        self._spawn_background_thread()
        if self._handle is None:
            self._handle = DaemonWorkerHandle(
                thread=self._background_thread,
                tick_interval_seconds=self._cache_interval_ms / 1000.0,
                restart_callback=self._spawn_background_thread,
            )
            register_daemon_worker("PoolCB-Refresh", self._handle)
        else:
            self._handle.thread = self._background_thread
        logger.debug("pool_circuit_breaker.background_refresh_thread_started")

    def _spawn_background_thread(self) -> None:
        """Construct + start a fresh background refresh thread (impl 489 D9)."""
        self._background_thread = threading.Thread(
            target=self._background_refresh_loop_with_crash_capture,
            name="PoolCB-Refresh",
            daemon=True,
        )
        self._background_thread.start()
        if self._handle is not None:
            self._handle.thread = self._background_thread

    def _background_refresh_loop_with_crash_capture(self) -> None:
        try:
            self._background_refresh_loop()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as e:
            if self._handle is not None:
                self._handle.record_crash(e)
            raise

    def _stop_background_refresh(self):
        """백그라운드 갱신 스레드 중지"""
        from baldur.metrics.recorders.daemon_worker import unregister_daemon_worker

        if self._handle is not None:
            self._handle.is_stopping = True
        self._stop_background.set()
        if self._background_thread:
            self._background_thread.join(timeout=1.0)
            unregister_daemon_worker("PoolCB-Refresh")
            if self._background_thread.is_alive():
                logger.critical(
                    "daemon_worker.stop_join_timeout",
                    worker_name="PoolCB-Refresh",
                    join_timeout_seconds=1.0,
                )
            logger.debug("pool_circuit_breaker.background_refresh_thread_stopped")

    def _background_refresh_loop(self):
        """백그라운드에서 주기적으로 Pool 상태 갱신"""
        interval_sec = self._cache_interval_ms / 1000.0
        consecutive_failures = 0

        while not self._stop_background.is_set():
            iter_start = time.monotonic()
            try:
                # Pool 상태 조회 (이 부분만 잠재적 블로킹)
                new_status = self._fetch_pool_status_internal()
                current_time = time.time()
                new_status["_cache_time"] = current_time
                new_status["_is_stale"] = False  # v6.2.1: 신선한 데이터

                # 캐시 업데이트 (atomic swap에 가깝게)
                with self._cache_lock:
                    self._cached_pool_status = new_status
                    self._stats["cache_refreshes"] += 1
                    self._last_successful_refresh = current_time

                # 성공 시 연속 실패 카운터 리셋
                consecutive_failures = 0

            except Exception as e:
                # v6.2.1: 연속 실패 추적
                consecutive_failures += 1
                logger.debug(
                    "pool_circuit_breaker.background_refresh_failed",
                    consecutive_failures=consecutive_failures,
                    error=e,
                )

                # 5회 연속 실패 시 경고 (5 * 100ms = 500ms 이상 갱신 안됨)
                if consecutive_failures >= 5:
                    logger.warning(
                        "pool_circuit_breaker.background_refresh_failing_consecutively",
                        consecutive_failures=consecutive_failures,
                    )

            if self._handle is not None:
                self._handle.observe_iteration(time.monotonic() - iter_start)
                self._handle.heartbeat()

            # 다음 갱신까지 대기
            self._stop_background.wait(timeout=interval_sec)

    def get_cached_pool_status(self) -> dict:
        """
        캐시된 Pool 상태 반환 (Non-Blocking).

        v6.2.0: 매 요청에서 이 메서드를 호출하여 블로킹 방지.
        v6.2.1: Stale 캐시 감지 및 안전 폴백 처리 추가.
        v6.2.2: Prometheus 메트릭 연동

        Stale 처리 정책:
        - 경고 (stale_threshold_multiplier 초과): 로그 경고, 캐시 데이터 사용
        - 폴백 (critical_stale_ms 초과): 안전하게 CLOSED로 폴백 (요청 허용)
        """
        # 캐시에서 읽기 (매우 빠름)
        with self._cache_lock:
            status: dict[str, Any] = self._cached_pool_status.copy()
            self._stats["cache_hits"] += 1

        # v6.2.1: 백그라운드 스레드 상태 확인 및 자동 재시작
        if not self._background_thread or not self._background_thread.is_alive():
            logger.error("pool_circuit_breaker.background_thread_died_restarting")
            self._stats["background_thread_restarts"] += 1
            # v6.2.2: Prometheus 메트릭 기록
            record_pool_cb_background_restart()
            self._start_background_refresh()

        # v6.2.1: 캐시 유효성 검사 (단계별 처리)
        cache_age_ms = (time.time() - status.get("_cache_time", 0)) * 1000
        stale_warning_threshold = (
            self._cache_interval_ms * self._stale_threshold_multiplier
        )

        # v6.2.2: 캐시 age 히스토그램 기록
        record_pool_cb_cache_age(cache_age_ms)

        # v6.2.2: 캐시 히트율 업데이트
        total_accesses = self._stats.get("cache_hits", 0) + self._stats.get(
            "cache_misses", 0
        )
        if total_accesses > 0:
            hit_rate = self._stats.get("cache_hits", 0) / total_accesses
            update_pool_cb_hit_rate(hit_rate)

        if cache_age_ms > self._critical_stale_ms:
            # 🔴 Critical Stale: 완전히 오래된 캐시 → 안전하게 CLOSED로 폴백
            logger.error(
                "pool_circuit_breaker.critical_stale_cache_ms",
                cache_age_ms=cache_age_ms,
                critical_stale_ms=self._critical_stale_ms,
            )
            self._stats["stale_cache_fallbacks"] += 1
            # v6.2.2: Prometheus 메트릭 기록
            record_pool_cb_stale("critical")
            # 안전 폴백: Pool 정상으로 가정 (요청 허용)
            return {
                "available": True,
                "is_exhausted": False,
                "is_near_exhaustion": False,
                "_cache_time": status.get("_cache_time", 0),
                "_is_stale": True,
                "_stale_fallback": True,  # Audit용: 폴백으로 인한 결정임을 표시
                "_cache_age_ms": cache_age_ms,
            }

        if cache_age_ms > stale_warning_threshold:
            # 🟡 Warning Stale: 경고만 남기고 캐시 데이터 사용
            logger.warning(
                "pool_circuit_breaker.stale_cache_ms_old",
                cache_age_ms=cache_age_ms,
                stale_warning_threshold=stale_warning_threshold,
            )
            self._stats["stale_cache_warnings"] += 1
            # v6.2.2: Prometheus 메트릭 기록
            record_pool_cb_stale("warning")
            status["_is_stale"] = True
            status["_cache_age_ms"] = cache_age_ms

        return status

    def check_pool_status(self) -> dict:
        """
        Pool 상태 조회 (캐시 우선).

        v6.2.0: 기본적으로 캐시된 값 반환 (Non-Blocking).
        실시간 조회가 필요하면 _fetch_pool_status_internal() 사용.
        """
        return self.get_cached_pool_status()

    def _fetch_pool_status_internal(self) -> dict:
        """
        실제 Pool 상태 조회 (내부용, 잠재적 블로킹).

        백그라운드 스레드에서만 호출됨.
        """
        try:
            # django-db-connection-pool의 pool_container를 통해 접근
            try:
                from dj_db_conn_pool.core.mixins.core import pool_container

                has_pool = pool_container.has("default")

                if has_pool:
                    pool = pool_container.get("default")
                    pool_size = pool.size()
                    checkedout = pool.checkedout()
                    checkedin = pool.checkedin()
                    overflow = pool.overflow()
                    max_overflow = getattr(pool, "_max_overflow", 2)
                    total_capacity = pool_size + max_overflow

                    # Pool 고갈 판단:
                    # Pool 크기 3, max_overflow 0일 때:
                    # - checkedout >= 3 이면 완전 고갈
                    # - checkedin == 0 이면 사용 가능한 연결 없음
                    is_at_capacity = checkedout >= total_capacity
                    is_overflow_maxed = (
                        overflow >= max_overflow if max_overflow > 0 else True
                    )
                    is_no_available = checkedin == 0

                    # 더 적극적인 고갈 감지: 사용 가능 연결이 0이고 모든 연결이 사용 중
                    is_exhausted = is_no_available and checkedout >= pool_size

                    # 66% 이상 사용 시 near exhaustion (Pool 3개면 2개 사용 시)
                    is_near_exhaustion = checkedout >= total_capacity * 0.66

                    if is_exhausted:
                        logger.warning(
                            "pool_circuit_breaker.exhausted",
                            checkedout=checkedout,
                            checkedin=checkedin,
                            pool_size=pool_size,
                        )

                    return {
                        "available": True,
                        "pool_size": pool_size,
                        "checkedout": checkedout,
                        "checkedin": checkedin,
                        "overflow": overflow,
                        "max_overflow": max_overflow,
                        "total_capacity": total_capacity,
                        "usage_percent": (
                            (checkedout / total_capacity * 100)
                            if total_capacity > 0
                            else 0
                        ),
                        "is_exhausted": is_exhausted,
                        "is_near_exhaustion": is_near_exhaustion,
                        # 디버깅용
                        "_is_at_capacity": is_at_capacity,
                        "_is_overflow_maxed": is_overflow_maxed,
                        "_is_no_available": is_no_available,
                    }
                # pool_container가 비어있음 - Django connection에서 직접 접근 시도
                # 단, ensure_connection()은 호출하지 않음 (블로킹 방지)
                conn = connections["default"]
                # 이미 연결이 있는 경우에만 Pool 접근
                if hasattr(conn, "connection") and conn.connection is not None:
                    raw_conn = conn.connection
                    if hasattr(raw_conn, "_pool"):
                        pool = raw_conn._pool
                        pool_size = pool.size()
                        checkedout = pool.checkedout()
                        checkedin = pool.checkedin()
                        overflow = pool.overflow()
                        max_overflow = getattr(pool, "_max_overflow", 0)
                        total_capacity = pool_size + max_overflow

                        is_exhausted = checkedin == 0 and checkedout >= pool_size
                        is_near_exhaustion = checkedout >= total_capacity * 0.66

                        if is_exhausted:
                            logger.warning(
                                "pool_circuit_breaker.exhausted",
                                checkedout=checkedout,
                                checkedin=checkedin,
                                pool_size=pool_size,
                            )

                        return {
                            "available": True,
                            "pool_size": pool_size,
                            "checkedout": checkedout,
                            "checkedin": checkedin,
                            "overflow": overflow,
                            "max_overflow": max_overflow,
                            "total_capacity": total_capacity,
                            "usage_percent": (
                                (checkedout / total_capacity * 100)
                                if total_capacity > 0
                                else 0
                            ),
                            "is_exhausted": is_exhausted,
                            "is_near_exhaustion": is_near_exhaustion,
                        }

                # 연결이 아직 없음 - Pool도 없음 (정상, 첫 요청 전)
                return {
                    "available": False,
                    "reason": "Pool not initialized yet",
                    "is_exhausted": False,
                    "is_near_exhaustion": False,
                }
            except ImportError as e:
                # django-db-connection-pool 미설치
                logger.warning(
                    "pool_circuit_breaker.available",
                    error=e,
                )
                return {
                    "available": False,
                    "reason": "dj_db_conn_pool not installed",
                    "is_exhausted": False,
                    "is_near_exhaustion": False,
                }

            # Fallback: pool_container가 없고 import도 실패한 경우
            # 연결 시도 없이 바로 반환 (ensure_connection 제거!)
            return {
                "available": False,
                "reason": "No pool available",
                "is_exhausted": False,
                "is_near_exhaustion": False,
            }

        except Exception as e:
            logger.exception(
                "pool_circuit_breaker.pool_status_check_failed",
                error=e,
            )
            return {
                "available": False,
                "reason": str(e),
            }

    def should_allow_request(self) -> tuple[bool, str | None]:  # noqa: C901, PLR0912
        """
        요청 허용 여부 판단 (Non-Blocking).

        v6.2.0: 캐시된 Pool 상태를 사용하여 블로킹 방지.

        Returns:
            (allow: bool, reason: Optional[str])
        """
        self._stats["total_requests"] += 1

        with self._state_lock:
            current_state = self._state

            if current_state == self.CLOSED:
                # 정상 상태 - 캐시된 Pool 상태 체크 (Non-Blocking!)
                pool_status = self.get_cached_pool_status()

                if pool_status.get("is_exhausted"):
                    # Pool 고갈 감지! 즉시 OPEN으로 전환
                    logger.error(
                        "pool_circuit_breaker.pool_exhausted",
                        pool_status=pool_status.get("checkedout"),
                        total_capacity=pool_status.get("total_capacity"),
                    )
                    self._set_state(self.OPEN)
                    self._stats["rejected_requests"] += 1
                    self._stats["pool_exhaustion_count"] += 1
                    return (False, "Pool exhausted - Circuit OPEN")

                if pool_status.get("is_near_exhaustion"):
                    # 80% 이상 사용 중 - 경고 및 failure count 증가
                    self._failure_count += 1
                    usage = pool_status.get("usage_percent", 0)
                    logger.warning(
                        "pool_circuit_breaker.pool_usage_high_failures",
                        usage=usage,
                        failure_count=self._failure_count,
                        failure_threshold=self._failure_threshold,
                    )

                    # Threshold 도달 시 OPEN
                    if self._failure_count >= self._failure_threshold:
                        self._set_state(self.OPEN)
                        self._stats["rejected_requests"] += 1
                        return (
                            False,
                            f"Pool near exhaustion ({usage:.1f}%) - Circuit OPEN",
                        )

                    # 90% 이상이면 50% 확률로 거부 (부하 분산)
                    if usage >= 90:
                        import random

                        if random.random() < 0.5:
                            self._stats["rejected_requests"] += 1
                            return (
                                False,
                                f"Pool critical ({usage:.1f}%) - load shedding",
                            )

                    return (True, None)

                # 정상 - failure count 리셋
                self._failure_count = 0
                return (True, None)

            if current_state == self.OPEN:
                # 차단 상태 - 복구 timeout 체크
                if self._open_time:
                    elapsed = time.time() - self._open_time
                    if elapsed >= self._recovery_timeout:
                        # Recovery timeout 경과 - HALF_OPEN으로 전환
                        self._set_state(self.HALF_OPEN)
                        self._half_open_requests = 0
                        self._success_count = 0
                        # 첫 번째 요청 허용
                        self._half_open_requests += 1
                        return (True, "Testing recovery (HALF_OPEN)")

                # 여전히 차단
                self._stats["rejected_requests"] += 1
                remaining = self._recovery_timeout - (
                    time.time() - (self._open_time or 0)
                )
                return (False, f"Circuit OPEN - retry in {remaining:.1f}s")

            if current_state == self.HALF_OPEN:
                # 복구 테스트 중
                if self._half_open_requests < self._half_open_max_requests:
                    self._half_open_requests += 1
                    return (True, "Testing recovery (HALF_OPEN)")
                # 테스트 요청 수 초과 - 대기
                self._stats["rejected_requests"] += 1
                return (False, "HALF_OPEN test in progress - wait")

        return (True, None)

    def record_success(self):
        """요청 성공 기록"""
        with self._state_lock:
            if self._state == self.HALF_OPEN:
                self._success_count += 1
                logger.info(
                    "pool_circuit_breaker.success",
                    success_count=self._success_count,
                    success_threshold=self._success_threshold,
                )

                if self._success_count >= self._success_threshold:
                    # 충분히 성공 - 복구 완료!
                    self._set_state(self.CLOSED)
                    self._failure_count = 0
                    self._success_count = 0
                    logger.info("pool_circuit_breaker.recovered_circuit_closed")

            elif self._state == self.CLOSED:
                # 정상 상태에서 성공 - 실패 카운터 리셋
                self._failure_count = 0

    def record_failure(self):
        """요청 실패 기록"""
        with self._state_lock:
            self._last_failure_time = time.time()

            if self._state == self.HALF_OPEN:
                # 복구 테스트 실패 - 다시 OPEN
                self._set_state(self.OPEN)
                logger.warning("pool_circuit_breaker.recovery_failed")

            elif self._state == self.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self._failure_threshold:
                    self._set_state(self.OPEN)

    def get_stats(self) -> dict:
        """통계 반환 (캐시 통계 포함)"""
        return {
            "state": self._state,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "stats": self._stats.copy(),
            "pool_status": self.get_cached_pool_status(),  # v6.2.0: 캐시 사용
            "cache_interval_ms": self._cache_interval_ms,  # v6.2.0: 캐시 설정 정보
        }

    def reset(self):
        """상태 초기화 (테스트용)"""
        with self._state_lock:
            self._state = self.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._open_time = None
            self._half_open_requests = 0
            # v6.2.0: 캐시 통계도 리셋
            self._stats["cache_hits"] = 0
            self._stats["cache_refreshes"] = 0
            logger.info("pool_circuit_breaker.reset_closed")

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance for test isolation."""
        with cls._lock:
            instance = cls._instance
            if instance is not None:
                instance._stop_background_refresh()
            cls._instance = None


# 전역 인스턴스
pool_circuit_breaker = PoolCircuitBreaker()


class PoolCircuitBreakerMiddleware:
    """
    Django Middleware: Pool 고갈 시 즉시 503 반환.

    사용법:
    MIDDLEWARE = [
        ...
        'baldur.api.django.pool_circuit_breaker.PoolCircuitBreakerMiddleware',
        ...
    ]

    비활성화:
    BALDUR_POOL_CB_MIDDLEWARE_ENABLED = False (settings.py)
    또는
    BALDUR_POOL_CB_MIDDLEWARE_ENABLED=false (환경변수)
    """

    # Circuit Breaker 적용 제외 경로
    EXCLUDED_PATHS = [
        "/health/",
        "/api/baldur/health/",
        "/api/baldur/circuit-breaker/",  # CB 관리 API는 제외
        "/admin/",
        "/static/",
        "/media/",
    ]

    def __init__(self, get_response):
        self.get_response = get_response
        self._request_count = 0
        self._log_interval = 100  # v6.2.0: 100 요청마다 Pool 상태 로깅 (50 → 100)
        self._audit_enabled = self._check_audit_available()
        self._enabled = self._check_enabled()

        status = "enabled" if self._enabled else "DISABLED"
        logger.info(
            "pool_circuit_breaker_middleware.initialized_audit",
            cb_status=status,
            audit_enabled="enabled" if self._audit_enabled else "disabled",
        )

    def _check_enabled(self) -> bool:
        """미들웨어 활성화 여부 확인"""
        try:
            from django.conf import settings

            return getattr(settings, "BALDUR_POOL_CB_MIDDLEWARE_ENABLED", True)
        except Exception:
            # settings 접근 불가 시 환경변수 확인
            return os.getenv("BALDUR_POOL_CB_MIDDLEWARE_ENABLED", "true").lower() in (
                "true",
                "1",
                "yes",
            )

    def _check_audit_available(self) -> bool:
        """Audit 시스템 사용 가능 여부 확인"""
        try:
            from baldur.audit import ContinuousAuditRecorder  # noqa: F401

            return True
        except ImportError:
            return False

    def _record_rejection_audit(
        self,
        request,
        reason: str,
        circuit_state: str,
        pool_status: dict,
    ):
        """
        v6.2.1: 503 거부 시 Audit 로그 기록.

        캐시 기반 결정임을 명확히 기록하여 분석 시 혼선 방지.

        - RequestAuditBuffer 패턴 우선 사용 (AuditMiddleware에서 일괄 기록)
        - 버퍼 사용 불가 시 기존 ContinuousAuditRecorder 직접 호출로 fallback
        """
        if not self._audit_enabled:
            return

        # === Audit 버퍼 패턴 우선 ===
        try:
            from baldur.audit.event_buffer import (
                AuditEventType,
                RequestAuditBuffer,
            )

            # 캐시 메타데이터 추출
            cache_age_ms = pool_status.get("_cache_age_ms", 0)
            is_stale = pool_status.get("_is_stale", False)
            is_stale_fallback = pool_status.get("_stale_fallback", False)

            buffer = RequestAuditBuffer.get_or_create(request)
            buffer.add(
                event_type=AuditEventType.POOL_CB_REJECTION,
                source="PoolCircuitBreakerMiddleware",
                details={
                    "request_path": request.path,
                    "request_method": request.method,
                    "circuit_state": circuit_state,
                    "rejection_reason": reason,
                    "decision_source": "cached_pool_status",
                    "cache_age_ms": cache_age_ms,
                    "is_stale": is_stale,
                    "is_stale_fallback": is_stale_fallback,
                    "pool_checkedout": pool_status.get("checkedout"),
                    "pool_total_capacity": pool_status.get("total_capacity"),
                    "pool_usage_percent": pool_status.get("usage_percent"),
                    "pool_is_exhausted": pool_status.get("is_exhausted"),
                },
                success=False,
                error_message=reason,
            )
            return  # 버퍼에 추가됨 - AuditMiddleware에서 기록
        except ImportError:
            pass  # event_buffer 사용 불가 - fallback

        # === Fallback: structured warning log when event_buffer is unavailable ===
        # The previous fallback called ``ContinuousAuditRecorder.record(...)``
        # via an ``AuditActionType.CIRCUIT_BREAKER_TRIGGERED`` enum member that
        # never existed (recorder exposes ``record_compliance_check`` /
        # ``record_drift_detected`` etc., not a generic ``record``). Since this
        # branch is only hit when the primary buffer path is unavailable and
        # the prior try/except swallowed every error, the audit value here was
        # zero. Emit a structured WARNING so SRE still sees the rejection.
        cache_age_ms = pool_status.get("_cache_age_ms", 0)
        is_stale = pool_status.get("_is_stale", False)
        is_stale_fallback = pool_status.get("_stale_fallback", False)
        logger.warning(
            "pool_circuit_breaker_middleware.request_rejected_audit_fallback",
            request_path=request.path,
            request_method=request.method,
            circuit_state=circuit_state,
            rejection_reason=reason,
            decision_source="cached_pool_status",
            cache_age_ms=cache_age_ms,
            is_stale=is_stale,
            is_stale_fallback=is_stale_fallback,
            pool_checkedout=pool_status.get("checkedout"),
            pool_total_capacity=pool_status.get("total_capacity"),
            pool_usage_percent=pool_status.get("usage_percent"),
            pool_is_exhausted=pool_status.get("is_exhausted"),
        )

    def __call__(self, request):  # noqa: C901, PLR0912
        # 미들웨어 비활성화 시 바이패스
        if not self._enabled:
            return self.get_response(request)

        # 제외 경로 체크
        path = request.path
        for excluded in self.EXCLUDED_PATHS:
            if path.startswith(excluded):
                return self.get_response(request)

        # 주기적 Pool 상태 로깅 (캐시된 값 사용)
        self._request_count += 1
        if self._request_count % self._log_interval == 0:
            # v6.2.0: 캐시된 Pool 상태 사용 (Non-Blocking)
            pool_status = pool_circuit_breaker.get_cached_pool_status()
            cache_stats = pool_circuit_breaker._stats
            logger.info(
                "pool_circuit_breaker_middleware.pool_status_every_reqs",
                log_interval=self._log_interval,
                pool_status=pool_status.get("checkedout", "?"),
                total_capacity=pool_status.get("total_capacity", "?"),
                usage_percent=pool_status.get("usage_percent", 0),
                is_exhausted=pool_status.get("is_exhausted", False),
                cache_stats=cache_stats.get("cache_hits", 0),
            )

        cb = pool_circuit_breaker
        allow, reason = cb.should_allow_request()

        if not allow:
            # 즉시 거부! (Fail Fast)
            logger.warning(
                "pool_circuit_breaker_middleware.rejected",
                path=path,
                reason=reason,
            )

            # v6.2.1: Audit 연동 - 캐시 기반 결정임을 명시
            pool_status = cb.get_cached_pool_status()
            self._record_rejection_audit(
                request=request,
                reason=reason,
                circuit_state=cb.state,
                pool_status=pool_status,
            )

            return JsonResponse(
                {
                    "error": "Service temporarily unavailable",
                    "reason": reason,
                    "circuit_state": cb.state,
                    "retry_after": cb._recovery_timeout,
                    # v6.2.1: 캐시 메타데이터 (디버깅/분석용)
                    "_cache_based_decision": True,
                    "_cache_age_ms": pool_status.get("_cache_age_ms", 0),
                    "_is_stale": pool_status.get("_is_stale", False),
                },
                status=503,
                headers={"Retry-After": str(cb._recovery_timeout)},
            )

        # 요청 처리
        try:
            response = self.get_response(request)

            # 성공 여부 판단
            if response.status_code < 500:
                cb.record_success()
            else:
                # 500 에러 - Pool 고갈 여부 확인 (캐시된 값 사용)
                try:
                    # v6.2.0: 캐시된 Pool 상태 확인 (Non-Blocking)
                    pool_status = cb.get_cached_pool_status()
                    if pool_status.get("is_exhausted", False):
                        # Pool 고갈 상태! 즉시 OPEN
                        logger.error(
                            "pool_circuit_breaker_middleware.error_pool_exhaustion_path",
                            path=path,
                            pool_status=pool_status,
                        )
                        cb._failure_count = cb._failure_threshold  # 즉시 threshold 도달
                        cb.record_failure()
                    else:
                        cb.record_failure()
                except Exception:
                    cb.record_failure()

            return response

        except Exception as e:
            # Pool Timeout 또는 DB 연결 오류 감지
            error_str = str(e).lower()

            # Pool 고갈 관련 예외 패턴
            pool_exhaustion_patterns = [
                "timeout",
                "queuepool limit",
                "pool exhausted",
                "connection pool",
                "too many connections",
                "can't get connection",
                "no connections available",
            ]

            is_pool_exhaustion = any(p in error_str for p in pool_exhaustion_patterns)

            if is_pool_exhaustion:
                # Pool 고갈 예외! 즉시 OPEN 전환
                logger.exception(
                    "pool_circuit_breaker_middleware.pool_exhaustion_detected",
                    error=e,
                )
                cb._failure_count = cb._failure_threshold  # 즉시 threshold 도달
                cb.record_failure()

                # v6.2.0: 캐시된 Pool 상태 로깅 (Non-Blocking)
                pool_status = cb.get_cached_pool_status()
                logger.exception(
                    "pool_circuit_breaker_middleware.pool_status_exhaustion",
                    pool_status=pool_status.get("checkedout", "?"),
                    total_capacity=pool_status.get("total_capacity", "?"),
                    overflow=pool_status.get("overflow", "?"),
                )

                # 503 반환 (재시도 유도)
                return JsonResponse(
                    {
                        "error": "Database pool exhausted",
                        "reason": str(e),
                        "circuit_state": cb.state,
                        "retry_after": cb._recovery_timeout,
                    },
                    status=503,
                    headers={"Retry-After": str(cb._recovery_timeout)},
                )
            # 일반 오류
            cb.record_failure()
            logger.exception(
                "pool_circuit_breaker_middleware.request_failed",
                error=e,
            )
            raise


# Circuit Breaker 상태 조회 API
def circuit_breaker_status(request):
    """Circuit Breaker 상태 조회 API

    v6.1.0: BaldurMiddleware의 CircuitBreakerService 상태도 포함
    """
    cb = pool_circuit_breaker
    stats = cb.get_stats()

    # v6.1.0: CircuitBreakerService 상태도 조회 (BaldurMiddleware에서 사용)
    cb_service_state = "unknown"
    cb_service_failure_count = 0
    try:
        from baldur.services.circuit_breaker.convenience import (
            get_circuit_breaker_service,
        )

        cb_service = get_circuit_breaker_service()
        if cb_service and cb_service.is_enabled:
            state_data = cb_service.get_or_create_state("database")
            cb_service_state = (
                state_data.state if hasattr(state_data, "state") else str(state_data)
            )
            cb_service_failure_count = getattr(state_data, "failure_count", 0)
    except Exception as e:
        logger.debug(
            "circuit_breaker_status.cb_service_state_lookup",
            error=e,
        )

    # 두 CB 중 하나라도 OPEN이면 OPEN으로 표시
    combined_state = stats["state"]
    if cb_service_state in ("open", "OPEN", "half_open", "HALF_OPEN"):
        combined_state = cb_service_state.upper()

    return JsonResponse(
        {
            "circuit_breaker": {
                "state": combined_state,  # v6.1.0: 통합된 상태
                "failure_count": max(stats["failure_count"], cb_service_failure_count),
                "success_count": stats["success_count"],
                "failure_threshold": cb._failure_threshold,
                "success_threshold": cb._success_threshold,
                "recovery_timeout_seconds": cb._recovery_timeout,
            },
            "pool_circuit_breaker": {
                "state": stats["state"],
                "failure_count": stats["failure_count"],
            },
            "service_circuit_breaker": {
                "state": cb_service_state,
                "failure_count": cb_service_failure_count,
                "service_name": "database",
            },
            "pool": stats["pool_status"],
            "statistics": stats["stats"],
        }
    )


def circuit_breaker_reset(request):
    """Circuit Breaker 리셋 API (관리용)"""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    cb = pool_circuit_breaker
    cb.reset()

    return JsonResponse(
        {
            "message": "Circuit Breaker reset to CLOSED",
            "state": cb.state,
        }
    )
