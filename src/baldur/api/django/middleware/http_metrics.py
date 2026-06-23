"""
HTTP RED Metrics Middleware.

Records Rate, Errors, and Duration for all HTTP requests.
Uses EndpointNormalizer (doc 332) for cardinality control.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import structlog

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

logger = structlog.get_logger()

__all__ = [
    "HttpMetricsMiddleware",
    "AsyncHttpMetricsMiddleware",
]


class HttpMetricsMixin:
    """
    Shared RED metrics recording logic.

    Pure synchronous methods — safe to use from both
    sync and async middleware classes.
    """

    _enabled: bool | None = None

    def _is_enabled(self) -> bool:
        """Lazy check for MetricsSettings.enabled."""
        if self._enabled is None:
            try:
                from baldur.settings.metrics import get_metrics_settings

                self._enabled = get_metrics_settings().enabled
            except Exception:
                logger.debug("http_metrics_middleware.settings_fallback")
                self._enabled = True
        return self._enabled

    def _normalize(self, request: HttpRequest) -> str:
        """Normalize endpoint with cardinality guard."""
        try:
            from baldur.metrics.endpoint_normalizer import normalize_endpoint

            return normalize_endpoint(request.path, request)
        except Exception:
            return "NORMALIZATION_ERROR"

    def _record_response(
        self,
        request: HttpRequest,
        response: HttpResponse,
        duration: float,
    ) -> None:
        """Record RED metrics for a completed response."""
        try:
            from baldur.metrics.prometheus import (
                record_http_error,
                record_http_request,
            )

            endpoint = self._normalize(request)
            method = request.method or "UNKNOWN"
            status_code = response.status_code

            # Rate + Duration
            record_http_request(method, endpoint, status_code, duration)

            # Error (5xx)
            if status_code >= 500:
                record_http_error(method, endpoint, f"HTTP_{status_code}")

        except Exception as e:
            logger.warning(
                "http_metrics_middleware.record_failed",
                error=e,
            )

    def _record_exception(
        self,
        request: HttpRequest,
        exc: Exception,
        duration: float,
    ) -> None:
        """Record RED metrics for an unhandled exception."""
        try:
            from baldur.metrics.prometheus import (
                record_http_error,
                record_http_request,
            )

            endpoint = self._normalize(request)
            method = request.method or "UNKNOWN"

            # Duration (status_code=500 assumed for unhandled exceptions)
            record_http_request(method, endpoint, 500, duration)

            # Error
            record_http_error(method, endpoint, type(exc).__name__)

        except Exception as e:
            logger.warning(
                "http_metrics_middleware.record_exception_failed",
                error=e,
            )


class HttpMetricsMiddleware(HttpMetricsMixin):
    """
    HTTP RED Metrics middleware (synchronous).

    Records Rate, Errors, Duration for every request using
    normalized endpoints for cardinality safety.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not self._is_enabled():
            return cast("HttpResponse", self.get_response(request))

        start = time.perf_counter()
        try:
            response: HttpResponse = self.get_response(request)
        except Exception as e:
            duration = time.perf_counter() - start
            self._record_exception(request, e, duration)
            raise

        duration = time.perf_counter() - start
        self._record_response(request, response, duration)
        return response


class AsyncHttpMetricsMiddleware(HttpMetricsMixin):
    """
    HTTP RED Metrics middleware (asynchronous).

    ASGI variant of HttpMetricsMiddleware.
    """

    async_capable = True
    sync_capable = False

    def __init__(self, get_response: Callable[[HttpRequest], Any]):
        self.get_response = get_response

    async def __call__(self, request: HttpRequest) -> HttpResponse:
        if not self._is_enabled():
            return cast("HttpResponse", await self.get_response(request))

        start = time.perf_counter()
        try:
            response: HttpResponse = await self.get_response(request)
        except Exception as e:
            duration = time.perf_counter() - start
            self._record_exception(request, e, duration)
            raise

        duration = time.perf_counter() - start
        self._record_response(request, response, duration)
        return response
