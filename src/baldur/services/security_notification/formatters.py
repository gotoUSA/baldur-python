"""
Security Notification Message Formatters.

Extracted from SecurityNotificationService to support the unified notification
pipeline. Provides channel-agnostic message formatting.

Reference:
    docs/impl/410_UNM_CONFIGURABLE_CHANNEL_ROUTING.md (ID-6)
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "truncate_with_ellipsis",
    "format_alert_message",
    "format_incident_message",
]


def truncate_with_ellipsis(text: str, max_length: int) -> str:
    """
    Truncate text with ellipsis if it exceeds max length.

    Args:
        text: Text to truncate
        max_length: Maximum allowed length

    Returns:
        Truncated text with ellipsis if needed
    """
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def format_alert_message(
    title: str,
    message: str,
    severity: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Generic alert message formatting.

    Produces a channel-agnostic message dict consumed by SNS.deliver()
    and handler mixins.

    Args:
        title: Alert title
        message: Alert description
        severity: Severity level string
        metadata: Additional context data

    Returns:
        Formatted message dictionary
    """
    from baldur.services.security_notification.models import (
        _get_notification_limits,
    )
    from baldur.utils.time import utc_now

    limits = _get_notification_limits()

    return {
        "title": truncate_with_ellipsis(title, limits.title_max_length),
        "severity": severity.upper(),
        "description": truncate_with_ellipsis(message, limits.description_max_length),
        "detected_at": utc_now().isoformat(),
        "metadata": metadata or {},
    }


def format_incident_message(
    incident_id: int,
    incident_type: str,
    severity: str,
    description: str = "",
    source_ip: str | None = None,
    user_id: int | None = None,
    action_taken: str = "",
) -> dict[str, Any]:
    """
    Security incident rich formatting.

    Produces a structured message dict for security incident notifications.
    Applies truncation to prevent exceeding API limits.

    Args:
        incident_id: The security incident ID
        incident_type: Type of incident (e.g., 'signature_invalid')
        severity: Severity level
        description: Description of the incident
        source_ip: Source IP address
        user_id: Associated user ID
        action_taken: Action taken in response

    Returns:
        Formatted message dictionary
    """
    from baldur.services.security_notification.models import (
        _get_notification_limits,
    )
    from baldur.settings import get_config
    from baldur.utils.time import utc_now

    limits = _get_notification_limits()
    config = get_config()
    admin_url = f"{config.site_url}/admin/security-incident/{incident_id}/"

    desc = truncate_with_ellipsis(description, limits.description_max_length)
    action = (
        truncate_with_ellipsis(action_taken, limits.action_taken_max_length)
        if action_taken
        else "N/A"
    )

    return {
        "title": f"\U0001f6a8 Security Incident: {incident_type}"[
            : limits.title_max_length
        ],
        "severity": severity.upper(),
        "incident_id": incident_id,
        "type": incident_type,
        "status": "open",
        "description": desc,
        "source_ip": source_ip or "N/A",
        "user_id": user_id if user_id else "N/A",
        "detected_at": utc_now().isoformat(),
        "action_taken": action,
        "admin_url": admin_url,
    }
