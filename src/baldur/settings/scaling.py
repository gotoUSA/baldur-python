"""
Scaling Settings - Pydantic v2.

Single Source of Truth for scaling subsystem configuration.

Environment Variables:
    BALDUR_SCALING_LOAD_SHEDDING_ENABLED=false
"""

from pydantic import Field
from pydantic_settings import BaseSettings

from baldur.settings.base import make_settings_config


class ScalingSettings(BaseSettings):
    """
    Scaling subsystem configuration with validation.

    Controls rate limiting, load shedding, and HPA metrics at the
    scaling module level. Individual scaling sub-features (backpressure,
    throttle, etc.) have their own dedicated settings classes.

    All defaults are fail-safe (disabled).
    """

    model_config = make_settings_config("BALDUR_SCALING_")

    # ==========================================================================
    # Feature Flags
    # ==========================================================================
    load_shedding_enabled: bool = Field(
        default=False,
        description="Enable load shedding in the scaling subsystem",
    )


# =============================================================================
# Singleton Pattern (cached settings)
# =============================================================================


def get_scaling_settings() -> ScalingSettings:
    """Get scaling settings singleton."""
    return ScalingSettings()


def reset_scaling_settings() -> None:
    """Reset scaling settings (no-op for direct instantiation)."""
    pass


__all__ = [
    "ScalingSettings",
    "get_scaling_settings",
    "reset_scaling_settings",
]
