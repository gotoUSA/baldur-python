"""
Tests for CeleryTaskSettings new fields from 321 — Beat Internalization.

Covers:
- queue_prefix: str (default "", namespace isolation)
- queue_type: str (default "quorum", pattern-validated)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from baldur.settings.celery_task import CeleryTaskSettings

# =============================================================================
# Contract Tests — Design Document Defaults
# =============================================================================


class TestCeleryTaskSettings321Contract:
    """321 design doc contract values for new queue configuration fields."""

    def test_queue_prefix_default_empty(self):
        """queue_prefix defaults to empty string (no namespace isolation)."""
        settings = CeleryTaskSettings()
        assert settings.queue_prefix == ""

    def test_queue_type_default_quorum(self):
        """queue_type defaults to 'quorum' (Raft-based message safety)."""
        settings = CeleryTaskSettings()
        assert settings.queue_type == "quorum"


# =============================================================================
# Behavior Tests — Validation & Edge Cases
# =============================================================================


class TestCeleryTaskSettings321Behavior:
    """Validation behavior for 321 queue configuration fields."""

    def test_queue_prefix_accepts_custom_value(self):
        """queue_prefix accepts any string value."""
        settings = CeleryTaskSettings(queue_prefix="shopping")
        assert settings.queue_prefix == "shopping"

    def test_queue_type_accepts_classic(self):
        """queue_type accepts 'classic'."""
        settings = CeleryTaskSettings(queue_type="classic")
        assert settings.queue_type == "classic"

    def test_queue_type_accepts_stream(self):
        """queue_type accepts 'stream'."""
        settings = CeleryTaskSettings(queue_type="stream")
        assert settings.queue_type == "stream"

    def test_queue_type_rejects_invalid_value(self):
        """queue_type rejects values outside (classic|quorum|stream)."""
        with pytest.raises(ValidationError):
            CeleryTaskSettings(queue_type="invalid")

    def test_queue_type_rejects_empty_string(self):
        """queue_type rejects empty string."""
        with pytest.raises(ValidationError):
            CeleryTaskSettings(queue_type="")

    def test_queue_prefix_env_override(self, monkeypatch):
        """queue_prefix can be set via BALDUR_CELERY_TASK_QUEUE_PREFIX env var."""
        monkeypatch.setenv("BALDUR_CELERY_TASK_QUEUE_PREFIX", "payments")
        settings = CeleryTaskSettings()
        assert settings.queue_prefix == "payments"

    def test_queue_type_env_override(self, monkeypatch):
        """queue_type can be set via BALDUR_CELERY_TASK_QUEUE_TYPE env var."""
        monkeypatch.setenv("BALDUR_CELERY_TASK_QUEUE_TYPE", "classic")
        settings = CeleryTaskSettings()
        assert settings.queue_type == "classic"
