"""
Retry Handler Decorators

Decorator factory for adding retry logic to functions.
Internally uses RetryPolicy.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, cast

from .models import MaxRetriesExceededError, RetryPolicyConfig, T
from .policy import RetryPolicy


def with_retry(
    domain: str = "default",
    max_attempts: int | None = None,
    retryable_exceptions: tuple[type[Exception], ...] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator to add retry logic to a function.

    Args:
        domain: Domain for configuration
        max_attempts: Override max attempts
        retryable_exceptions: Exceptions that should trigger retry

    Returns:
        Decorated function

    Example:
        @with_retry(domain="payment", max_attempts=3)
        def call_external_api():
            return requests.post(...)
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            config = RetryPolicyConfig.from_settings(domain)
            if max_attempts is not None:
                config.max_attempts = max_attempts
            if retryable_exceptions is not None:
                config.retryable_exceptions = retryable_exceptions

            policy = RetryPolicy(config=config)
            result = policy.execute(func, *args, **kwargs)

            if result.success:
                # success branch guarantees value is set per PolicyResult contract
                return cast("T", result.value)
            raise MaxRetriesExceededError(
                f"Max retries exceeded for {func.__name__}",
                retry_count=result.total_attempts,
                max_retries=config.max_attempts,
                last_error=result.error,
            )

        return wrapper

    return decorator
