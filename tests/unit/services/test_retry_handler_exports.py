"""
retry_handler 패키지 Re-export 및 with_retry 데코레이터 단위 테스트.

테스트 대상: services/retry_handler/__init__.py, services/retry_handler/decorators.py
- 패키지 레벨 re-export 검증 (RetryPolicy, Guards, Sinks)
- with_retry 데코레이터 내부 RetryPolicy 사용 검증
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from baldur.services import retry_handler as pkg

# =============================================================================
# 패키지 Re-export — 계약 검증
# =============================================================================


class TestRetryHandlerPackageExportsContract:
    """retry_handler 패키지에서 새 클래스들이 정상 re-export되는지 검증."""

    @pytest.mark.parametrize(
        "name",
        [
            "RetryPolicy",
            "RetryPolicyConfig",
            "KillSwitchGuard",
            "ErrorBudgetGuard",
            "DLQSink",
            "with_retry",
            "detect_rate_limit",
        ],
    )
    def test_new_symbol_importable(self, name: str):
        """새로 추가된 심볼이 패키지에서 import 가능하다."""
        assert hasattr(pkg, name), f"{name} is not exported from retry_handler"

    def test_all_new_symbols_in_dunder_all(self):
        """__all__에 새 심볼 7개가 포함되어 있다."""
        expected = {
            "RetryPolicy",
            "RetryPolicyConfig",
            "KillSwitchGuard",
            "ErrorBudgetGuard",
            "DLQSink",
            "with_retry",
            "detect_rate_limit",
        }
        assert expected.issubset(set(pkg.__all__))


# =============================================================================
# with_retry 데코레이터 — 동작 검증
# =============================================================================


class TestWithRetryDecoratorBehavior:
    """with_retry 데코레이터가 내부적으로 RetryPolicy를 사용하는지 검증."""

    def test_successful_call_returns_value(self):
        """성공 시 함수의 반환값이 그대로 반환된다."""
        from baldur.services.retry_handler.decorators import with_retry

        @with_retry(domain="test", max_attempts=2)
        def ok_func():
            return 42

        with patch(
            "baldur.services.retry_handler.decorators.RetryPolicy"
        ) as MockPolicy:
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.value = 42
            MockPolicy.return_value.execute.return_value = mock_result

            assert ok_func() == 42

    def test_failure_raises_max_retries_exceeded(self):
        """실패 시 MaxRetriesExceededError가 발생한다."""
        from baldur.services.retry_handler.decorators import with_retry
        from baldur.services.retry_handler.models import MaxRetriesExceededError

        @with_retry(domain="test", max_attempts=2)
        def fail_func():
            raise ConnectionError("down")

        with patch(
            "baldur.services.retry_handler.decorators.RetryPolicy"
        ) as MockPolicy:
            mock_result = MagicMock()
            mock_result.success = False
            mock_result.total_attempts = 2
            mock_result.error = ConnectionError("down")
            MockPolicy.return_value.execute.return_value = mock_result

            with pytest.raises(MaxRetriesExceededError):
                fail_func()

    def test_creates_retry_policy_internally(self):
        """with_retry는 내부적으로 RetryPolicy 인스턴스를 생성한다."""
        from baldur.services.retry_handler.decorators import with_retry

        @with_retry(domain="test", max_attempts=3)
        def dummy():
            return "ok"

        with patch(
            "baldur.services.retry_handler.decorators.RetryPolicy"
        ) as MockPolicy:
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.value = "ok"
            MockPolicy.return_value.execute.return_value = mock_result

            dummy()
            MockPolicy.assert_called_once()

    def test_custom_retryable_exceptions(self):
        """retryable_exceptions 파라미터가 RetryPolicyConfig에 적용된다."""
        from baldur.services.retry_handler.decorators import with_retry

        @with_retry(
            domain="test",
            max_attempts=2,
            retryable_exceptions=(ValueError,),
        )
        def dummy():
            return "ok"

        with patch(
            "baldur.services.retry_handler.decorators.RetryPolicy"
        ) as MockPolicy:
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.value = "ok"
            MockPolicy.return_value.execute.return_value = mock_result

            dummy()
            call_kwargs = MockPolicy.call_args
            config = (
                call_kwargs[1]["config"]
                if "config" in call_kwargs[1]
                else call_kwargs[0][0]
            )
            assert config.retryable_exceptions == (ValueError,)

    def test_uses_config_from_settings(self):
        """with_retry는 RetryPolicyConfig.from_settings()로 config를 생성한다."""
        from baldur.services.retry_handler.decorators import with_retry

        with (
            patch(
                "baldur.services.retry_handler.decorators.RetryPolicyConfig"
            ) as MockConfig,
            patch("baldur.services.retry_handler.decorators.RetryPolicy") as MockPolicy,
        ):
            mock_cfg = MagicMock()
            MockConfig.from_settings.return_value = mock_cfg

            mock_result = MagicMock()
            mock_result.success = True
            mock_result.value = "ok"
            MockPolicy.return_value.execute.return_value = mock_result

            @with_retry(domain="billing", max_attempts=5)
            def dummy():
                return "ok"

            dummy()
            MockConfig.from_settings.assert_called_once_with("billing")
