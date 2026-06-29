"""
Stage 25: TLS/Certificate Failure Tests

Scenarios:
1. Certificate expired
2. Certificate not yet valid
3. Hostname mismatch
4. Self-signed certificate
5. Handshake timeout (retryable)
6. Connection reset (retryable)
"""

from __future__ import annotations

import ssl
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest


class TestTLSErrorClassification:
    """Tests for TLS error classification."""

    def test_classify_certificate_expired(self):
        """Expired certificate error classification."""
        from baldur.core.tls_handler import (
            TLSErrorClassifier,
            TLSErrorSeverity,
            TLSErrorType,
        )

        error = ssl.SSLError("certificate has expired")
        info = TLSErrorClassifier.classify(error, "https://api.example.com")

        assert info.error_type == TLSErrorType.CERTIFICATE_EXPIRED
        assert info.severity == TLSErrorSeverity.CRITICAL
        assert info.is_retryable is False
        assert info.is_certificate_error is True
        assert info.requires_immediate_action is True
        assert info.endpoint == "https://api.example.com"

    def test_classify_certificate_not_yet_valid(self):
        """Not yet valid certificate classification."""
        from baldur.core.tls_handler import (
            TLSErrorClassifier,
            TLSErrorSeverity,
            TLSErrorType,
        )

        error = ssl.SSLError("certificate is not yet valid")
        info = TLSErrorClassifier.classify(error)

        assert info.error_type == TLSErrorType.CERTIFICATE_NOT_YET_VALID
        assert info.severity == TLSErrorSeverity.HIGH
        assert info.is_retryable is False

    def test_classify_certificate_revoked(self):
        """Revoked certificate classification."""
        from baldur.core.tls_handler import (
            TLSErrorClassifier,
            TLSErrorType,
        )

        error = ssl.SSLError("certificate revoked")
        info = TLSErrorClassifier.classify(error)

        assert info.error_type == TLSErrorType.CERTIFICATE_REVOKED
        assert info.is_retryable is False

    def test_classify_hostname_mismatch(self):
        """Hostname mismatch classification."""
        from baldur.core.tls_handler import (
            TLSErrorClassifier,
            TLSErrorType,
        )

        error = ssl.SSLError("hostname 'api.example.com' doesn't match")
        info = TLSErrorClassifier.classify(error)

        assert info.error_type == TLSErrorType.CERTIFICATE_HOSTNAME_MISMATCH
        assert info.is_retryable is False

    def test_classify_self_signed(self):
        """Self-signed certificate classification."""
        from baldur.core.tls_handler import (
            TLSErrorClassifier,
            TLSErrorSeverity,
            TLSErrorType,
        )

        error = ssl.SSLError("self signed certificate in certificate chain")
        info = TLSErrorClassifier.classify(error)

        assert info.error_type == TLSErrorType.CERTIFICATE_SELF_SIGNED
        assert info.severity == TLSErrorSeverity.MEDIUM

    def test_classify_chain_invalid(self):
        """Invalid certificate chain classification."""
        from baldur.core.tls_handler import (
            TLSErrorClassifier,
            TLSErrorType,
        )

        error = ssl.SSLError("unable to get local issuer certificate")
        info = TLSErrorClassifier.classify(error)

        assert info.error_type == TLSErrorType.CERTIFICATE_CHAIN_INVALID

    def test_classify_handshake_timeout_retryable(self):
        """Handshake timeout is retryable."""
        from baldur.core.tls_handler import (
            TLSErrorClassifier,
            TLSErrorType,
        )

        error = ssl.SSLError("handshake operation timed out")
        info = TLSErrorClassifier.classify(error)

        assert info.error_type == TLSErrorType.HANDSHAKE_TIMEOUT
        assert info.is_retryable is True

    def test_classify_connection_reset_retryable(self):
        """Connection reset is retryable."""
        from baldur.core.tls_handler import (
            TLSErrorClassifier,
            TLSErrorType,
        )

        error = Exception("Connection reset by peer")
        info = TLSErrorClassifier.classify(error)

        assert info.error_type == TLSErrorType.CONNECTION_RESET
        assert info.is_retryable is True

    def test_classify_protocol_mismatch(self):
        """Protocol version mismatch classification."""
        from baldur.core.tls_handler import (
            TLSErrorClassifier,
            TLSErrorType,
        )

        error = ssl.SSLError("unsupported protocol version")
        info = TLSErrorClassifier.classify(error)

        assert info.error_type == TLSErrorType.PROTOCOL_VERSION_MISMATCH
        assert info.is_retryable is False

    def test_classify_unknown(self):
        """Unknown error classification."""
        from baldur.core.tls_handler import (
            TLSErrorClassifier,
            TLSErrorType,
        )

        error = Exception("Some random error")
        info = TLSErrorClassifier.classify(error)

        assert info.error_type == TLSErrorType.UNKNOWN
        assert info.is_retryable is True  # Default to retryable


class TestCertificateExpiryMonitor:
    """Tests for certificate expiry monitoring."""

    def test_certificate_valid(self):
        """Certificate valid for more than 30 days."""
        from baldur.core.cert_monitor import (
            CertificateExpiryMonitor,
            CertificateStatus,
        )

        monitor = CertificateExpiryMonitor()
        future = datetime.now(UTC) + timedelta(days=90)

        info = monitor.check_expiry(future, "api.example.com")

        assert info.status == CertificateStatus.VALID
        assert info.days_remaining >= 89  # Allow for time calculation edge cases
        assert info.is_valid is True
        assert info.needs_attention is False

    def test_certificate_expiring_soon(self):
        """Certificate expiring in 15 days (warning)."""
        from baldur.core.cert_monitor import (
            CertificateExpiryMonitor,
            CertificateStatus,
        )

        monitor = CertificateExpiryMonitor()
        future = datetime.now(UTC) + timedelta(days=15)

        info = monitor.check_expiry(future)

        assert info.status == CertificateStatus.EXPIRING_SOON
        assert info.needs_attention is True
        assert info.is_urgent is False

    def test_certificate_critical(self):
        """Certificate expiring in 3 days (critical)."""
        from baldur.core.cert_monitor import (
            CertificateExpiryMonitor,
            CertificateStatus,
        )

        monitor = CertificateExpiryMonitor()
        future = datetime.now(UTC) + timedelta(days=3)

        info = monitor.check_expiry(future)

        assert info.status == CertificateStatus.CRITICAL
        assert info.is_urgent is True

    def test_certificate_expired(self):
        """Certificate already expired."""
        from baldur.core.cert_monitor import (
            CertificateExpiryMonitor,
            CertificateStatus,
        )

        monitor = CertificateExpiryMonitor()
        past = datetime.now(UTC) - timedelta(days=1)

        info = monitor.check_expiry(past)

        assert info.status == CertificateStatus.EXPIRED
        assert info.is_valid is False
        assert info.is_urgent is True
        assert info.days_remaining == 0

    def test_custom_thresholds(self):
        """Custom warning and critical thresholds."""
        from baldur.core.cert_monitor import (
            CertificateExpiryMonitor,
            CertificateStatus,
        )

        monitor = CertificateExpiryMonitor(warning_days=60, critical_days=14)

        # 40 days remaining - should be expiring soon with 60-day warning
        future = datetime.now(UTC) + timedelta(days=40)
        info = monitor.check_expiry(future)

        assert info.status == CertificateStatus.EXPIRING_SOON

    def test_get_status_message(self):
        """Status message generation."""
        from baldur.core.cert_monitor import (
            CertificateExpiryMonitor,
        )

        monitor = CertificateExpiryMonitor()

        # Expired
        past = datetime.now(UTC) - timedelta(days=1)
        info = monitor.check_expiry(past, "api.example.com")
        message = monitor.get_status_message(info)
        assert "EXPIRED" in message
        assert "api.example.com" in message

        # Valid
        future = datetime.now(UTC) + timedelta(days=100)
        info = monitor.check_expiry(future, "valid.example.com")
        message = monitor.get_status_message(info)
        assert "OK" in message

    def test_naive_datetime_handling(self):
        """Naive datetime is handled correctly."""
        from baldur.core.cert_monitor import (
            CertificateExpiryMonitor,
            CertificateStatus,
        )

        monitor = CertificateExpiryMonitor()

        # Naive datetime (no tzinfo)
        naive_future = datetime.now() + timedelta(days=50)
        info = monitor.check_expiry(naive_future)

        assert info.status == CertificateStatus.VALID

    def test_alert_callback_on_expiring(self):
        """Alert callback is called for expiring certificates."""
        from baldur.core.cert_monitor import CertificateExpiryMonitor

        alerts = []
        monitor = CertificateExpiryMonitor(
            alert_callback=lambda info: alerts.append(info)
        )

        # Expiring soon
        future = datetime.now(UTC) + timedelta(days=15)
        monitor.check_expiry(future, "expiring.example.com")

        assert len(alerts) == 1
        assert alerts[0].endpoint == "expiring.example.com"

    def test_no_alert_for_valid_cert(self):
        """No alert for valid certificates."""
        from baldur.core.cert_monitor import CertificateExpiryMonitor

        alerts = []
        monitor = CertificateExpiryMonitor(
            alert_callback=lambda info: alerts.append(info)
        )

        # Valid
        future = datetime.now(UTC) + timedelta(days=100)
        monitor.check_expiry(future, "valid.example.com")

        assert len(alerts) == 0

    def test_get_expiring_certificates(self):
        """Get all expiring certificates."""
        from baldur.core.cert_monitor import CertificateExpiryMonitor

        monitor = CertificateExpiryMonitor()

        # Add some certificates
        monitor.check_expiry(datetime.now(UTC) + timedelta(days=100), "valid.com")
        monitor.check_expiry(datetime.now(UTC) + timedelta(days=20), "expiring1.com")
        monitor.check_expiry(datetime.now(UTC) + timedelta(days=5), "critical.com")

        expiring = monitor.get_expiring_certificates()

        assert len(expiring) == 2
        endpoints = {c.endpoint for c in expiring}
        assert "expiring1.com" in endpoints
        assert "critical.com" in endpoints


class TestTLSResilientClient:
    """Tests for TLS-resilient client wrapper."""

    def test_successful_request(self):
        """Successful request without TLS errors."""
        from baldur.core.tls_handler import SimpleTLSResilientClient

        mock_client = Mock()
        mock_client.request.return_value = {"status": "ok"}

        client = SimpleTLSResilientClient(mock_client)
        result = client.request("GET", "https://api.example.com")

        assert result == {"status": "ok"}
        mock_client.request.assert_called_once()

    def test_retry_on_transient_error(self):
        """Retry on transient TLS error."""
        from baldur.core.tls_handler import SimpleTLSResilientClient

        mock_client = Mock()
        mock_client.request.side_effect = [
            ssl.SSLError("handshake operation timed out"),
            {"status": "ok"},
        ]

        error_callback = Mock()
        client = SimpleTLSResilientClient(mock_client, error_callback=error_callback)

        result = client.request("GET", "https://api.example.com")

        assert result == {"status": "ok"}
        assert mock_client.request.call_count == 2
        assert error_callback.called

    def test_no_retry_on_cert_expired(self):
        """No retry on non-retryable certificate error."""
        from baldur.core.tls_handler import SimpleTLSResilientClient

        mock_client = Mock()
        mock_client.request.side_effect = ssl.SSLError("certificate has expired")

        client = SimpleTLSResilientClient(mock_client)

        with pytest.raises(ssl.SSLError):
            client.request("GET", "https://api.example.com")

        # Only one call - no retry
        assert mock_client.request.call_count == 1

    def test_max_retries_exhausted(self):
        """Error raised after max retries exhausted."""
        from baldur.core.tls_handler import SimpleTLSResilientClient

        mock_client = Mock()
        mock_client.request.side_effect = ssl.SSLError("handshake operation timed out")

        client = SimpleTLSResilientClient(mock_client, max_retries=3)

        with pytest.raises(ssl.SSLError):
            client.request("GET", "https://api.example.com")

        assert mock_client.request.call_count == 3

    def test_error_callback_receives_info(self):
        """Error callback receives TLSErrorInfo."""
        from baldur.core.tls_handler import SimpleTLSResilientClient, TLSErrorType

        mock_client = Mock()
        mock_client.request.side_effect = ssl.SSLError("certificate has expired")

        received_infos = []
        client = SimpleTLSResilientClient(
            mock_client, error_callback=lambda info: received_infos.append(info)
        )

        with pytest.raises(ssl.SSLError):
            client.request("GET", "https://api.example.com")

        assert len(received_infos) == 1
        assert received_infos[0].error_type == TLSErrorType.CERTIFICATE_EXPIRED
        assert received_infos[0].endpoint == "https://api.example.com"

    def test_wrapped_ssl_error_detected(self):
        """Wrapped SSL errors are detected and handled."""
        from baldur.core.tls_handler import SimpleTLSResilientClient

        mock_client = Mock()
        # Some libraries wrap SSL errors
        mock_client.request.side_effect = Exception("SSL: certificate has expired")

        client = SimpleTLSResilientClient(mock_client)

        with pytest.raises(Exception):
            client.request("GET", "https://api.example.com")

        # Should detect it's an SSL error and not retry cert expiry
        assert mock_client.request.call_count == 1


class TestCertificateAlertManager:
    """Tests for certificate alert deduplication."""

    def test_first_alert_allowed(self):
        """First alert for endpoint is allowed."""
        from baldur.core.cert_monitor import CertificateAlertManager

        manager = CertificateAlertManager()

        assert manager.should_alert("api.example.com") is True

    def test_immediate_second_alert_blocked(self):
        """Immediate second alert is blocked."""
        from baldur.core.cert_monitor import CertificateAlertManager

        manager = CertificateAlertManager(alert_interval_hours=1)
        manager.record_alert("api.example.com")

        assert manager.should_alert("api.example.com") is False

    def test_different_endpoint_allowed(self):
        """Different endpoint can still alert."""
        from baldur.core.cert_monitor import CertificateAlertManager

        manager = CertificateAlertManager()
        manager.record_alert("api1.example.com")

        assert manager.should_alert("api2.example.com") is True

    def test_clear_alerts(self):
        """Clear alerts resets state."""
        from baldur.core.cert_monitor import CertificateAlertManager

        manager = CertificateAlertManager()
        manager.record_alert("api.example.com")
        manager.clear_alerts()

        assert manager.should_alert("api.example.com") is True
