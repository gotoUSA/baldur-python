"""
Rate Limit Configuration — Settings loader and Prometheus metrics.

Provides runtime config reading from RuntimeConfigManager / ApiRateLimitSettings,
and lazy Prometheus metric initialization.

Extracted from api/django/rate_limit.py as part of 358 rate_limit package split.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


# =============================================================================
# Configuration Constants (from Settings - env var based)
# =============================================================================


def _get_api_rate_limit_settings():
    """Get ApiRateLimitSettings instance (lazy import)."""
    try:
        from baldur.settings.api_rate_limit import get_api_rate_limit_settings

        return get_api_rate_limit_settings()
    except ImportError:
        return None


def _get_setting(attr: str, fallback: Any) -> Any:
    """Get value from Settings or return fallback."""
    settings = _get_api_rate_limit_settings()
    if settings is not None:
        return getattr(settings, attr, fallback)
    return fallback


# Fallback constants (used when Settings load fails)
_FALLBACK_DEFAULT_RATE_LIMIT = 100
_FALLBACK_DEFAULT_WINDOW_SECONDS = 60
_FALLBACK_EMERGENCY_RATE_LIMIT = 10
_FALLBACK_EMERGENCY_WINDOW_SECONDS = 60
_FALLBACK_CONTROL_API_PATH_PREFIX = "/api/baldur/"

# Fallback log path
FALLBACK_LOG_PATH = Path("logs/rate_limit_fallback.jsonl")


# =============================================================================
# Runtime Config Reader (API Control)
# =============================================================================


def get_rate_limit_config() -> dict:
    """
    Get rate limit configuration from RuntimeConfigManager or Settings.

    Priority:
    1. RuntimeConfigManager (runtime dynamic settings)
    2. ApiRateLimitSettings (environment variable based)
    3. Hardcoded fallback constants

    Returns:
        dict with keys:
        - control_api_rate_limit: int (requests/minute for normal mode)
        - control_api_window_seconds: int
        - emergency_rate_limit: int (requests/minute for emergency mode)
        - emergency_window_seconds: int
    """
    # Load defaults from Settings
    default_limit = _get_setting("default_limit", _FALLBACK_DEFAULT_RATE_LIMIT)
    default_window = _get_setting(
        "default_window_seconds", _FALLBACK_DEFAULT_WINDOW_SECONDS
    )
    emergency_limit = _get_setting("emergency_limit", _FALLBACK_EMERGENCY_RATE_LIMIT)
    emergency_window = _get_setting(
        "emergency_window_seconds", _FALLBACK_EMERGENCY_WINDOW_SECONDS
    )

    try:
        from baldur.factory.registry import ProviderRegistry

        manager = ProviderRegistry.runtime_config_manager.safe_get()
        if manager is None:
            raise RuntimeError("baldur_pro RuntimeConfigManager not registered")
        config = manager.get_rate_limit_config()

        return {
            "control_api_rate_limit": config.get(
                "control_api_rate_limit", default_limit
            ),
            "control_api_window_seconds": config.get(
                "control_api_window_seconds", default_window
            ),
            "emergency_rate_limit": config.get("emergency_rate_limit", emergency_limit),
            "emergency_window_seconds": config.get(
                "emergency_window_seconds", emergency_window
            ),
        }
    except Exception as e:
        # Fallback to Settings if RuntimeConfig fails
        logger.warning(
            "rate_limit.runtime_config_failed",
            error=e,
        )
        return {
            "control_api_rate_limit": default_limit,
            "control_api_window_seconds": default_window,
            "emergency_rate_limit": emergency_limit,
            "emergency_window_seconds": emergency_window,
        }


# =============================================================================
# Prometheus Metrics (Lazy Import)
# =============================================================================


def _get_metrics():
    """Get or create Prometheus metrics (lazy import to avoid circular deps)."""
    try:
        from prometheus_client import REGISTRY, Counter, Gauge

        # Check if already registered
        if "baldur_rate_limit_exceeded_total" in REGISTRY._names_to_collectors:
            exceeded_total = REGISTRY._names_to_collectors[
                "baldur_rate_limit_exceeded_total"
            ]
            degraded_mode = REGISTRY._names_to_collectors[
                "baldur_rate_limit_degraded_mode"
            ]
            failover_total = REGISTRY._names_to_collectors[
                "baldur_rate_limit_failover_total"
            ]
        else:
            exceeded_total = Counter(
                "baldur_rate_limit_exceeded_total",
                "Rate limit exceeded count",
                ["mode"],  # normal, emergency
            )
            degraded_mode = Gauge(
                "baldur_rate_limit_degraded_mode",
                "Rate limit operating in degraded mode (1=yes, 0=no)",
            )
            failover_total = Counter(
                "baldur_rate_limit_failover_total",
                "Number of times rate limit failed over to local memory",
            )

        return exceeded_total, degraded_mode, failover_total
    except ImportError:
        return None, None, None


__all__ = [
    "FALLBACK_LOG_PATH",
    "get_rate_limit_config",
]
