"""Unit tests for ChargerPhaseDetector."""

from unittest.mock import AsyncMock, Mock, patch
import pytest

from apps.v2g_liberty.grid_connection import charger_phase_detector
from apps.v2g_liberty.grid_connection.charger_phase_detector import ChargerPhaseDetector
from apps.v2g_liberty import constants as c


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Patch asyncio.sleep so tests run instantly."""
    monkeypatch.setattr(charger_phase_detector.asyncio, "sleep", AsyncMock())


@pytest.fixture(autouse=True)
def _set_discharge_power():
    """Default to bidirectional charger. Tests can override."""
    original = c.CHARGER_MAX_DISCHARGE_POWER
    c.CHARGER_MAX_DISCHARGE_POWER = 1380
    yield
    c.CHARGER_MAX_DISCHARGE_POWER = original


def make_grid_state_mock(readings: list[dict]) -> Mock:
    """Create a get_state mock that returns values from a readings list.

    Each dict in readings represents one measurement cycle (3 get_state calls,
    one per entity). The dict is consumed after all 3 entities have been read.

    Keys: "l1", "l2", "l3" → matched by entity_id substring.
    """
    it = iter(readings)
    state = {"current": next(it, {"l1": "0", "l2": "0", "l3": "0"}), "count": 0}

    def fake_get_state(entity_id):
        if "l1" in entity_id:
            val = state["current"]["l1"]
        elif "l2" in entity_id:
            val = state["current"]["l2"]
        else:
            val = state["current"]["l3"]
        state["count"] += 1
        if state["count"] >= 3:
            state["current"] = next(it, {"l1": "0", "l2": "0", "l3": "0"})
            state["count"] = 0
        return val

    return Mock(side_effect=fake_get_state)


@pytest.fixture
def hass_mock():
    mock = Mock()
    mock.fire_event = Mock()
    mock.get_state = Mock(return_value="500")
    return mock


@pytest.fixture
def evse_mock():
    mock = AsyncMock()
    mock.is_car_connected = AsyncMock(return_value=True)
    mock.start_charge_with_power = AsyncMock()
    mock.stop_charging = AsyncMock()
    return mock


@pytest.fixture
def log_mock():
    return Mock()


@pytest.fixture
def grid_entities():
    return [
        "sensor.grid_cons_l1",
        "sensor.grid_cons_l2",
        "sensor.grid_cons_l3",
    ]


@pytest.fixture
def detector(hass_mock, evse_mock, log_mock, grid_entities):
    return ChargerPhaseDetector(
        hass=hass_mock,
        evse_client=evse_mock,
        log=log_mock,
        grid_entities=grid_entities,
        charge_power_w=1380,
    )


class TestPreconditions:
    @pytest.mark.asyncio
    async def test_no_car_connected(self, detector, evse_mock):
        """Detection fails if no car is connected."""
        evse_mock.is_car_connected.return_value = False

        result = await detector.run()

        assert result["success"] is False
        assert "No car connected" in result["error"]

    @pytest.mark.asyncio
    async def test_wrong_entity_count(self, hass_mock, evse_mock, log_mock):
        """Detection fails if not exactly 3 grid entities."""
        detector = ChargerPhaseDetector(
            hass=hass_mock,
            evse_client=evse_mock,
            log=log_mock,
            grid_entities=["sensor.l1"],
            charge_power_w=1380,
        )

        result = await detector.run()

        assert result["success"] is False
        assert "Expected 3" in result["error"]


class TestSinglePhaseDetection:
    @pytest.mark.asyncio
    async def test_charger_on_l1(self, detector, hass_mock):
        """Detects charger on L1 when only L1 shows significant delta."""
        hass_mock.get_state = make_grid_state_mock(
            [
                # Baseline (instant snapshot)
                {"l1": "500", "l2": "300", "l3": "200"},
                # Charge test: L1 jumps, detected on first poll
                {"l1": "1900", "l2": "320", "l3": "195"},
                # Discharge test: L1 drops, detected on first poll
                {"l1": "-860", "l2": "290", "l3": "210"},
            ]
        )

        result = await detector.run()

        assert result["success"] is True
        assert result["connected_to_phase"] == 1
        assert result["detected_at"] is not None

    @pytest.mark.asyncio
    async def test_charger_on_l3(self, detector, hass_mock):
        """Detects charger on L3 (unidirectional, no discharge test)."""
        c.CHARGER_MAX_DISCHARGE_POWER = 0
        hass_mock.get_state = make_grid_state_mock(
            [
                # Baseline
                {"l1": "500", "l2": "300", "l3": "200"},
                # Charge test: L3 jumps
                {"l1": "520", "l2": "310", "l3": "1600"},
            ]
        )

        result = await detector.run()

        assert result["success"] is True
        assert result["connected_to_phase"] == 3

    @pytest.mark.asyncio
    async def test_ramp_up_detected_after_multiple_polls(self, detector, hass_mock):
        """Charger ramps up slowly — result found after a few polls."""
        c.CHARGER_MAX_DISCHARGE_POWER = 0
        hass_mock.get_state = make_grid_state_mock(
            [
                # Baseline
                {"l1": "500", "l2": "300", "l3": "200"},
                # Poll 1: charger ramping up, not yet clear
                {"l1": "600", "l2": "310", "l3": "205"},
                # Poll 2: still ramping
                {"l1": "800", "l2": "305", "l3": "195"},
                # Poll 3: clear result
                {"l1": "1900", "l2": "310", "l3": "200"},
            ]
        )

        result = await detector.run()

        assert result["success"] is True
        assert result["connected_to_phase"] == 1


class TestThreePhaseDetection:
    @pytest.mark.asyncio
    async def test_three_phase_charger(self, detector, hass_mock):
        """Detects 3-phase charger when all phases show ~460W delta."""
        c.CHARGER_MAX_DISCHARGE_POWER = 0
        hass_mock.get_state = make_grid_state_mock(
            [
                # Baseline
                {"l1": "500", "l2": "300", "l3": "200"},
                # Charge test: all phases jump by ~460W (1380/3)
                {"l1": "960", "l2": "760", "l3": "660"},
            ]
        )

        result = await detector.run()

        assert result["success"] is True
        assert result["connected_to_phase"] == [1, 2, 3]


class TestFailureCases:
    @pytest.mark.asyncio
    async def test_no_significant_delta(self, detector, hass_mock):
        """Detection fails when no phase reaches 50% threshold."""
        c.CHARGER_MAX_DISCHARGE_POWER = 0
        # All readings are the same — no delta
        hass_mock.get_state = Mock(return_value="500")

        result = await detector.run()

        assert result["success"] is False
        assert "timed out" in result["error"]

    @pytest.mark.asyncio
    async def test_inconsistent_charge_discharge(self, detector, hass_mock):
        """Detection fails when charge and discharge point to different phases."""
        hass_mock.get_state = make_grid_state_mock(
            [
                # Baseline
                {"l1": "500", "l2": "300", "l3": "200"},
                # Charge test: L1 jumps
                {"l1": "1900", "l2": "310", "l3": "195"},
                # Discharge test: L2 drops (inconsistent!)
                {"l1": "510", "l2": "-1100", "l3": "210"},
            ]
        )

        result = await detector.run()

        assert result["success"] is False
        assert (
            "inconsistent" in result["error"].lower() or "points to" in result["error"]
        )

    @pytest.mark.asyncio
    async def test_grid_entity_unavailable(self, detector, hass_mock):
        """Detection fails if a grid entity returns unavailable."""
        c.CHARGER_MAX_DISCHARGE_POWER = 0
        hass_mock.get_state = Mock(return_value="unavailable")

        result = await detector.run()

        assert result["success"] is False
        assert "Could not read" in result["error"]


class TestChargerSafety:
    @pytest.mark.asyncio
    async def test_charger_stopped_on_error(self, detector, hass_mock, evse_mock):
        """Charger is stopped if an error occurs during detection."""
        c.CHARGER_MAX_DISCHARGE_POWER = 0
        hass_mock.get_state = Mock(side_effect=Exception("HA down"))

        result = await detector.run()

        assert result["success"] is False
        evse_mock.stop_charging.assert_called()

    @pytest.mark.asyncio
    async def test_charger_stopped_after_charge_test(
        self, detector, hass_mock, evse_mock
    ):
        """Charger is stopped after each test step."""
        c.CHARGER_MAX_DISCHARGE_POWER = 0
        hass_mock.get_state = Mock(return_value="500")

        await detector.run()

        # stop_charging called at least once (after charge test)
        evse_mock.stop_charging.assert_called()


class TestProgressEvents:
    @pytest.mark.asyncio
    async def test_progress_events_fired(self, detector, hass_mock):
        """Progress events are fired for each step."""
        c.CHARGER_MAX_DISCHARGE_POWER = 0
        hass_mock.get_state = Mock(return_value="500")

        await detector.run()

        progress_calls = [
            call
            for call in hass_mock.fire_event.call_args_list
            if call.args[0] == "charger_phase_detection.progress"
        ]
        steps = [call.kwargs["step"] for call in progress_calls]
        assert "baseline" in steps
        assert "charge_test" in steps
