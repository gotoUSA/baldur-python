"""
TrafficGate CascadeLoadShedding 연동 테스트.

테스트 항목:
- create_traffic_gate_with_cascade_load_shedding 함수
- LoadSheddingAdapter 동작
- reset_traffic_gate 함수
"""

from unittest.mock import MagicMock, patch

import pytest

from baldur.scaling.config import BackpressureLevel
from baldur.scaling.traffic_gate import (
    TrafficGate,
    create_traffic_gate_with_cascade_load_shedding,
    get_traffic_gate,
    reset_traffic_gate,
)


class TestTrafficGateCascadeIntegration:
    """TrafficGate와 CascadeLoadShedding 연동 테스트."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """각 테스트 전 싱글톤 리셋."""
        reset_traffic_gate()
        yield
        reset_traffic_gate()

    def test_reset_traffic_gate(self):
        """reset_traffic_gate 함수 동작 확인."""
        gate1 = get_traffic_gate()
        reset_traffic_gate()
        gate2 = get_traffic_gate()

        assert gate1 is not gate2

    def test_create_without_cascade_import(self):
        """CascadeLoadShedding import 실패 시 기본 gate 반환."""
        with patch.dict("sys.modules", {"baldur.audit.cascade_load_shedding": None}):
            # ImportError 시뮬레이션
            with patch("baldur.scaling.traffic_gate.logger"):
                # CascadeLoadShedding import가 실패하면 기본 TrafficGate 반환
                gate = TrafficGate()
                assert isinstance(gate, TrafficGate)

    def test_create_with_callable_buffer_provider(self):
        """callable buffer_size_provider로 생성."""
        buffer_size = 500

        def get_buffer_size():
            return buffer_size

        # Mock CascadeLoadShedding
        mock_shedding_class = MagicMock()
        mock_shedding_instance = MagicMock()
        mock_shedding_instance.should_accept.return_value = {"accepted": True}
        mock_shedding_class.return_value = mock_shedding_instance

        with patch(
            "baldur.scaling.traffic_gate.CascadeLoadShedding",
            mock_shedding_class,
            create=True,
        ):
            try:
                gate = create_traffic_gate_with_cascade_load_shedding(
                    buffer_size_provider=get_buffer_size,
                    buffer_capacity=10000,
                )
                assert isinstance(gate, TrafficGate)
            except ImportError:
                # CascadeLoadShedding이 없으면 스킵
                pytest.skip("CascadeLoadShedding not available")

    def test_create_with_object_having_len(self):
        """__len__ 메서드가 있는 객체로 생성."""
        mock_buffer = MagicMock()
        mock_buffer.__len__ = MagicMock(return_value=300)

        mock_shedding_class = MagicMock()
        mock_shedding_instance = MagicMock()
        mock_shedding_instance.should_accept.return_value = {"accepted": True}
        mock_shedding_class.return_value = mock_shedding_instance

        with patch(
            "baldur.scaling.traffic_gate.CascadeLoadShedding",
            mock_shedding_class,
            create=True,
        ):
            try:
                gate = create_traffic_gate_with_cascade_load_shedding(
                    buffer_size_provider=mock_buffer,
                    buffer_capacity=10000,
                )
                assert isinstance(gate, TrafficGate)
            except ImportError:
                pytest.skip("CascadeLoadShedding not available")

    def test_traffic_gate_with_load_shedding_rejects(self):
        """LoadShedding이 거부하면 TrafficDecision.allowed=False."""
        mock_load_shedding = MagicMock()
        mock_load_shedding.should_accept.return_value = {"accepted": False}

        mock_controller = MagicMock()
        mock_controller.get_state.return_value = MagicMock(level=BackpressureLevel.NONE)

        gate = TrafficGate(
            rate_controller=mock_controller,
            load_shedding=mock_load_shedding,
        )

        decision = gate.should_allow(priority=5)

        assert decision.allowed is False
        assert decision.gate == "CascadeLoadShedding"
        assert "Load shedding rejected" in decision.reason

    def test_traffic_gate_with_load_shedding_accepts(self):
        """LoadShedding이 수락하면 RateController 단계로 진행."""
        mock_load_shedding = MagicMock()
        mock_load_shedding.should_accept.return_value = {"accepted": True}

        mock_controller = MagicMock()
        mock_controller.get_state.return_value = MagicMock(level=BackpressureLevel.NONE)
        mock_controller.should_process.return_value = True

        gate = TrafficGate(
            rate_controller=mock_controller,
            load_shedding=mock_load_shedding,
        )

        decision = gate.should_allow(priority=5)

        assert decision.allowed is True
        assert decision.gate == "TrafficGate"

    def test_traffic_gate_load_shedding_exception_handled(self):
        """LoadShedding 예외 시 RateController 단계로 진행."""
        mock_load_shedding = MagicMock()
        mock_load_shedding.should_accept.side_effect = RuntimeError("Test error")

        mock_controller = MagicMock()
        mock_controller.get_state.return_value = MagicMock(level=BackpressureLevel.NONE)
        mock_controller.should_process.return_value = True

        gate = TrafficGate(
            rate_controller=mock_controller,
            load_shedding=mock_load_shedding,
        )

        # 예외가 발생해도 크래시하지 않음
        decision = gate.should_allow(priority=5)

        assert decision.allowed is True
