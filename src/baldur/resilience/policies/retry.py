"""
Async Retry Policy — async 함수 재시도 지원.

AsyncResiliencePolicy Protocol을 구현하여 async 정책 체인의 재시도 gap을 해결한다.

동기 RetryPolicy(services/retry_handler/policy.py)와 별도 클래스로 공존한다.
RetryPolicy는 RateLimitCoordinator, AdaptiveRetryBudget 등 인프라 협력 객체를 포함하지만,
AsyncRetryPolicy는 순수 비동기 재시도 로직만 담당한다.

변경하지 않는 것:
- Circuit Breaker — 나노초 레벨 메모리 조회이므로 async 불필요 (의도적 설계)
- BackoffStrategy — 순수 계산 함수, I/O 없음
- RetryPolicy (sync) — 기존 sync 사용자에게 영향 없음

Jitter 전략:
- Jitter는 BackoffStrategy 내부 책임으로 완전 위임한다.
- async_sleep_with_jitter()는 Thundering Herd 방지용이므로 여기서 사용하지 않는다.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from typing import Any, TypeVar

import structlog

from baldur.core.backoff import BackoffStrategy, ExponentialBackoff
from baldur.interfaces.resilience_policy import (
    PolicyContext,
    PolicyOutcome,
    PolicyResult,
)

logger = structlog.get_logger()

T = TypeVar("T")


class AsyncRetryPolicy:
    """
    비동기 재시도 정책.

    async 함수를 최대 max_retries까지 재시도한다.
    재시도 간격은 BackoffStrategy로 계산하며, asyncio.sleep()을 사용.
    sync 함수가 전달되면 asyncio.to_thread()로 래핑하여 실행.

    AsyncResiliencePolicy Protocol 구현.

    Note:
        Jitter는 BackoffStrategy 내부에서 처리한다 (jitter, jitter_factor 파라미터).
        AsyncRetryPolicy는 별도의 jitter 로직을 갖지 않는다.
        async_sleep_with_jitter()는 Thundering Herd 방지용이므로 여기서 사용하지 않는다.

    Note:
        sync 함수는 asyncio.to_thread()로 기본 스레드 풀에서 실행된다.
        무거운 동기 I/O 함수가 반복 재시도되면 스레드 풀이 고갈될 수 있다.
        이 경우 해당 함수를 async I/O 클라이언트로 마이그레이션하거나
        워커 노드를 스케일 아웃하는 것이 올바른 아키텍처적 해결책이다.
    """

    def __init__(
        self,
        max_retries: int = 3,
        backoff: BackoffStrategy | None = None,
        retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
        non_retryable_exceptions: tuple[type[Exception], ...] | None = None,
    ):
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {max_retries}")
        self._max_retries = max_retries
        self._backoff = backoff or ExponentialBackoff()
        self._retryable_exceptions = retryable_exceptions

        from baldur.core.exceptions import non_retryable_exceptions as _defaults

        self._non_retryable = (
            non_retryable_exceptions
            if non_retryable_exceptions is not None
            else _defaults()
        )

    @property
    def name(self) -> str:
        """Policy 식별자."""
        return "retry"

    async def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        func를 최대 max_retries까지 비동기 재시도.

        Args:
            func: 실행할 함수 (async def 또는 sync def)
            *args: 위치 인수
            context: 정책 컨텍스트 (재시도 시 extra에 attempt/last_error 전파)
            **kwargs: 키워드 인수

        Returns:
            PolicyResult with value or error
        """
        _unwrapped = func
        while isinstance(_unwrapped, functools.partial):
            _unwrapped = _unwrapped.func
        is_async = asyncio.iscoroutinefunction(_unwrapped)
        last_error: Exception | None = None
        func_name = getattr(func, "__qualname__", None) or getattr(
            func, "__name__", "unknown"
        )

        for attempt in range(self._max_retries + 1):
            try:
                if is_async:
                    result = await func(*args, **kwargs)  # type: ignore[misc]
                else:
                    result = await asyncio.to_thread(func, *args, **kwargs)

                return PolicyResult(
                    value=result,
                    outcome=PolicyOutcome.SUCCESS,
                    total_attempts=attempt + 1,
                    executed_policies=["retry"],
                )

            except asyncio.CancelledError:
                raise

            except Exception as e:
                last_error = e

                # Non-retryable check first (CB-open, etc.)
                if isinstance(e, self._non_retryable):
                    break
                if not isinstance(e, self._retryable_exceptions):
                    break

                if context is not None:
                    context = context.with_updates(
                        extra={
                            **context.extra,
                            "retry_attempt": attempt + 1,
                            "retry_last_error": str(e),
                        }
                    )

                if attempt < self._max_retries:
                    delay = self._backoff.calculate(attempt, context=context)

                    logger.debug(
                        "retry.async_attempt_failed",
                        func=func_name,
                        attempt=attempt + 1,
                        max_retries=self._max_retries,
                        delay=delay,
                        error=str(e),
                    )

                    await asyncio.sleep(delay)

        logger.warning(
            "retry.async_exhausted",
            func=func_name,
            max_retries=self._max_retries,
            error=str(last_error),
        )

        return PolicyResult(
            outcome=PolicyOutcome.FAILURE,
            error=last_error,
            total_attempts=attempt + 1,
            executed_policies=["retry"],
        )


def async_retry_policy(
    max_retries: int = 3,
    backoff: BackoffStrategy | None = None,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    non_retryable_exceptions: tuple[type[Exception], ...] | None = None,
) -> AsyncRetryPolicy:
    """AsyncRetryPolicy factory function."""
    return AsyncRetryPolicy(
        max_retries=max_retries,
        backoff=backoff,
        retryable_exceptions=retryable_exceptions,
        non_retryable_exceptions=non_retryable_exceptions,
    )


def retried_async(
    max_retries: int = 3,
    backoff: BackoffStrategy | None = None,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    non_retryable_exceptions: tuple[type[Exception], ...] | None = None,
):
    """
    Async function retry decorator.

    @retried_async(max_retries=3, retryable_exceptions=(ConnectionError,))
    async def fetch_data():
        ...
    """
    policy = AsyncRetryPolicy(
        max_retries=max_retries,
        backoff=backoff,
        retryable_exceptions=retryable_exceptions,
        non_retryable_exceptions=non_retryable_exceptions,
    )

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            result = await policy.execute(func, *args, **kwargs)
            if not result.success:
                if result.error is not None:
                    raise result.error
                raise RuntimeError(
                    f"AsyncRetryPolicy exhausted without captured error "
                    f"(outcome={result.outcome})"
                )
            return result.value

        return wrapper

    return decorator


__all__ = [
    "AsyncRetryPolicy",
    "async_retry_policy",
    "retried_async",
]
