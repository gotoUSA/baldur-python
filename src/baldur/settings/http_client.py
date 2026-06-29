"""HTTP Client Settings — Pydantic v2.

Outbound HTTP timeouts used by Baldur components.

Environment Variables:
    BALDUR_HTTP_CLIENT_WEBHOOK_TIMEOUT=10.0
"""

from pydantic import Field
from pydantic_settings import BaseSettings

from baldur.settings.base import make_settings_config


class HttpClientSettings(BaseSettings):
    """HTTP client timeout settings.

    Currently a single field for outbound webhook calls (e.g. PRO
    Postmortem notifier). The previous ``default_timeout`` field served
    only the deleted ``BaldurHttpClient`` and has been removed.
    """

    model_config = make_settings_config("BALDUR_HTTP_CLIENT_")

    webhook_timeout: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="Timeout for outbound webhook HTTP calls (notifier, etc.)",
    )


def get_http_client_settings() -> "HttpClientSettings":
    from baldur.settings.root import get_config

    return get_config().adapters.http_client


def reset_http_client_settings() -> None:
    from baldur.settings.root import get_config

    try:
        del get_config().adapters.__dict__["http_client"]
    except KeyError:
        pass
