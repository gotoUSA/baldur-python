"""
Pending Configuration Change Service.

Manages scheduled/pending configuration changes that are waiting to be applied.

Features:
- Store pending changes with scheduled apply time
- Cancel pending changes before they's applied
- Apply changes when the scheduled time arrives
- Track change history

Audit:
- cancel_pending_change: log_config_apply_audit(status="cancelled")
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import structlog

from baldur.audit.helpers import log_config_apply_audit
from baldur.core.apply_strategy import ApplyOptions, ApplyStrategy
from baldur.core.serializable import SerializableMixin
from baldur.core.state_backend import get_state_backend
from baldur.settings.audit import get_audit_settings
from baldur.utils.time import utc_now

logger = structlog.get_logger()


class PendingStatus(str, Enum):
    """Status of a pending configuration change."""

    PENDING = "pending"  # Waiting to be applied
    APPLIED = "applied"  # Successfully applied
    CANCELLED = "cancelled"  # Cancelled by user
    FAILED = "failed"  # Failed to apply
    EXPIRED = "expired"  # Expired without being applied


@dataclass
class PendingConfigChange(SerializableMixin):
    """A pending configuration change."""

    id: str
    config_type: str
    changes: dict[str, Any]
    strategy: str  # ApplyStrategy value
    status: str = PendingStatus.PENDING.value
    created_at: str = ""
    scheduled_at: str = ""
    applied_at: str | None = None
    cancelled_at: str | None = None
    cancelled_by: str | None = None
    error_message: str | None = None
    previous_values: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = utc_now().isoformat()


# Singleton
_pending_config_service: PendingConfigService | None = None
_service_lock = threading.Lock()


class PendingConfigService:
    """
    Service for managing pending configuration changes.

    Thread-safe singleton that tracks scheduled config changes.

    Reference:
        See 92_CONFIG_IMPLEMENTATION_GUIDE.md Week 4 [20] AuditSettings.
    """

    STORAGE_KEY = "pending_config_changes"

    def __init__(self):
        """Initialize PendingConfigService."""
        self._lock = threading.RLock()
        self._backend = get_state_backend()
        self._pending: dict[str, PendingConfigChange] = {}
        self._history: list[PendingConfigChange] = []
        self._settings = get_audit_settings()
        self._load_state()

    @property
    def MAX_HISTORY(self) -> int:
        """Get max history from settings."""
        return self._settings.max_history

    def _load_state(self) -> None:
        """Load state from storage."""
        with self._lock:
            data = self._backend.get(self.STORAGE_KEY)
            if data:
                # Load pending
                for item in data.get("pending", []):
                    change = PendingConfigChange.from_dict(item)
                    self._pending[change.id] = change
                # Load history
                for item in data.get("history", []):
                    self._history.append(PendingConfigChange.from_dict(item))

    def _save_state(self) -> None:
        """Save state to storage."""
        data = {
            "pending": [c.to_dict() for c in self._pending.values()],
            "history": [c.to_dict() for c in self._history[-self.MAX_HISTORY :]],
        }
        self._backend.set(self.STORAGE_KEY, data)

    def _move_to_history(self, change: PendingConfigChange) -> None:
        """Move a change from pending to history."""
        if change.id in self._pending:
            del self._pending[change.id]
        self._history.append(change)
        # Trim history
        if len(self._history) > self.MAX_HISTORY:
            self._history = self._history[-self.MAX_HISTORY :]

    # =========================================================================
    # Public API
    # =========================================================================

    def create_pending_change(
        self,
        config_type: str,
        changes: dict[str, Any],
        apply_options: ApplyOptions,
        previous_values: dict[str, Any] | None = None,
    ) -> PendingConfigChange:
        """
        Create a new pending configuration change.

        Args:
            config_type: Type of configuration (e.g., "circuit_breaker")
            changes: The configuration changes to apply
            apply_options: How and when to apply the changes
            previous_values: Current values before change (for rollback)

        Returns:
            The created PendingConfigChange
        """
        with self._lock:
            change_id = str(uuid.uuid4())[:8]

            # Calculate scheduled time
            if apply_options.strategy == ApplyStrategy.DELAYED:
                scheduled_time = utc_now() + timedelta(
                    seconds=apply_options.delay_seconds
                )
            else:
                scheduled_time = utc_now()

            change = PendingConfigChange(
                id=change_id,
                config_type=config_type,
                changes=changes,
                strategy=apply_options.strategy.value,
                scheduled_at=scheduled_time.isoformat(),
                previous_values=previous_values or {},
            )

            self._pending[change_id] = change
            self._save_state()

            logger.info(
                "pending_config.created_pending_change_scheduled",
                change_id=change_id,
                config_type=config_type,
                scheduled_time=scheduled_time,
            )

            return change

    def get_pending_change(self, change_id: str) -> PendingConfigChange | None:
        """Get a pending change by ID."""
        with self._lock:
            return self._pending.get(change_id)

    def get_pending_changes_for_config(
        self, config_type: str
    ) -> list[PendingConfigChange]:
        """Get all pending changes for a config type."""
        with self._lock:
            return [
                c
                for c in self._pending.values()
                if c.config_type == config_type
                and c.status == PendingStatus.PENDING.value
            ]

    def get_all_pending_changes(self) -> list[PendingConfigChange]:
        """Get all pending changes."""
        with self._lock:
            return [
                c
                for c in self._pending.values()
                if c.status == PendingStatus.PENDING.value
            ]

    def get_due_changes(self) -> list[PendingConfigChange]:
        """Get all changes that are due to be applied."""
        with self._lock:
            now = utc_now()
            due = []
            for change in self._pending.values():
                if change.status != PendingStatus.PENDING.value:
                    continue
                scheduled = datetime.fromisoformat(change.scheduled_at)
                if scheduled <= now:
                    due.append(change)
            return due

    def cancel_pending_change(
        self,
        change_id: str,
        cancelled_by: str | None = None,
    ) -> PendingConfigChange | None:
        """
        Cancel a pending change.

        Args:
            change_id: ID of the change to cancel
            cancelled_by: Who cancelled (user/system)

        Returns:
            The cancelled change, or None if not found
        """
        with self._lock:
            change = self._pending.get(change_id)
            if not change:
                return None

            if change.status != PendingStatus.PENDING.value:
                logger.warning(
                    "pending_config.cannot_cancel_status",
                    change_id=change_id,
                    change=change.status,
                )
                return None

            change.status = PendingStatus.CANCELLED.value
            change.cancelled_at = utc_now().isoformat()
            change.cancelled_by = cancelled_by

            self._move_to_history(change)
            self._save_state()

            logger.info(
                "pending_config.cancelled_pending_change",
                change_id=change_id,
            )

            # === Audit record: scheduled config change cancelled ===
            log_config_apply_audit(
                pending_id=change_id,
                config_key=change.config_type,
                old_value=change.previous_values,
                new_value=change.changes,
                status="cancelled",
                details={
                    "cancelled_by": cancelled_by,
                    "scheduled_at": change.scheduled_at,
                    "strategy": change.strategy,
                },
            )

            return change

    def mark_applied(
        self,
        change_id: str,
    ) -> PendingConfigChange | None:
        """Mark a pending change as applied."""
        with self._lock:
            change = self._pending.get(change_id)
            if not change:
                return None

            change.status = PendingStatus.APPLIED.value
            change.applied_at = utc_now().isoformat()

            self._move_to_history(change)
            self._save_state()

            logger.info(
                "pending_config.applied_pending_change",
                change_id=change_id,
            )
            return change

    def mark_failed(
        self,
        change_id: str,
        error_message: str,
    ) -> PendingConfigChange | None:
        """Mark a pending change as failed."""
        with self._lock:
            change = self._pending.get(change_id)
            if not change:
                return None

            change.status = PendingStatus.FAILED.value
            change.error_message = error_message

            self._move_to_history(change)
            self._save_state()

            logger.error(
                "pending_config.apply_failed",
                change_id=change_id,
                error_message=error_message,
            )
            return change

    def get_history(
        self,
        config_type: str | None = None,
        limit: int = 50,
    ) -> list[PendingConfigChange]:
        """Get change history."""
        with self._lock:
            history = self._history
            if config_type:
                history = [c for c in history if c.config_type == config_type]
            return list(reversed(history[-limit:]))

    def cleanup_expired(self, max_age_hours: int = 24) -> int:
        """
        Cleanup old pending changes that were never applied.

        Returns:
            Number of expired changes cleaned up
        """
        with self._lock:
            now = utc_now()
            cutoff = now - timedelta(hours=max_age_hours)
            expired = []

            for _change_id, change in list(self._pending.items()):
                if change.status != PendingStatus.PENDING.value:
                    continue
                created = datetime.fromisoformat(change.created_at)
                if created < cutoff:
                    change.status = PendingStatus.EXPIRED.value
                    expired.append(change)
                    self._move_to_history(change)

            if expired:
                self._save_state()
                logger.info(
                    "pending_config.cleaned_up_expired_changes",
                    expired_count=len(expired),
                )

            return len(expired)


def get_pending_config_service() -> PendingConfigService:
    """Get singleton PendingConfigService instance."""
    global _pending_config_service

    if _pending_config_service is None:
        with _service_lock:
            if _pending_config_service is None:
                _pending_config_service = PendingConfigService()

    return _pending_config_service


def reset_pending_config_service() -> None:
    """Reset singleton instance (for testing)."""
    global _pending_config_service
    with _service_lock:
        _pending_config_service = None
