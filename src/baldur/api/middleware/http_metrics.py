"""
HTTP RED metrics recorder — framework-free.

Centralizes the Rate/Errors/Duration recording logic that the Django
``HttpMetricsMixin`` (``api/django/middleware/http_metrics.py``) open-codes, so
the Flask and FastAPI adapters reach Django parity on the
``baldur_http_request_duration_seconds`` histogram (the OSS-overview "HTTP
Latency" panel source) without each adapter re-deriving the enabled-gate and
fail-open discipline.

``record_http_red`` takes the already-computed ``(method, endpoint,
status_code, duration_seconds)`` — the adapters hold all four at response time —
so the helper carries no framework coupling and no time dependency, mirroring
the ``record_rtt_sample`` helper next to it.

Backend-agnostic: the recording goes through ``baldur.metrics.prometheus``'s
convenience functions, which route to the active backend via ``get_metrics()``.
The OTel backend's ``infra`` recorder family carries ``record_http_request`` /
``record_http_error`` too, so the series populate under prometheus and OTel
alike.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


__all__ = ["record_http_red"]


def record_http_red(
    method: str,
    endpoint: str,
    status_code: int,
    duration_seconds: float,
    *,
    error_type: str | None = None,
) -> None:
    """Record the HTTP RED triplet (Rate + Errors + Duration) for one response.

    Steps (parity with Django's ``HttpMetricsMixin._record_response`` /
    ``_record_exception``):

    1. Enabled-gate — read ``get_metrics_settings().enabled``; default to
       ``True`` (fail-open) when the settings lookup itself fails, so a
       misconfigured settings layer never silences metrics.
    2. ``record_http_request(method, endpoint, status_code, duration_seconds)``
       — the Rate + Duration series.
    3. When ``status_code >= 500`` — ``record_http_error(method, endpoint,
       error_type or f"HTTP_{status_code}")`` — the Errors series. Callers on
       the unhandled-exception path pass ``error_type=type(exc).__name__`` to
       match Django; the ``HTTP_<code>`` default covers app-returned 5xx.

    Fail-open: any recording failure is swallowed with a ``_failed``-suffixed
    WARNING (``http_red.record_failed``). Metric recording must never break the
    request — precedent: ``record_rtt_sample``.
    """
    try:
        from baldur.settings.metrics import get_metrics_settings

        try:
            enabled = get_metrics_settings().enabled
        except Exception:
            logger.debug("http_red.settings_fallback")
            enabled = True
        if not enabled:
            return

        from baldur.metrics.prometheus import record_http_error, record_http_request

        record_http_request(method, endpoint, status_code, duration_seconds)
        if status_code >= 500:
            record_http_error(method, endpoint, error_type or f"HTTP_{status_code}")
    except Exception as e:
        logger.warning("http_red.record_failed", error=e)
