"""Unit tests for grid_entity_detector module."""

import pytest

from apps.v2g_liberty.grid_connection.grid_entity_detector import (
    detect_grid_entities,
    _find_power_sensors,
    _find_triplets,
    _match_consumption_production,
    _find_fuse_threshold,
)


def _make_state(device_class="", unit="", friendly_name="", state="0"):
    """Helper to create a mock state dict."""
    return {
        "state": state,
        "attributes": {
            "device_class": device_class,
            "unit_of_measurement": unit,
            "friendly_name": friendly_name,
        },
    }


class TestFindPowerSensors:
    def test_finds_by_device_class(self):
        states = {
            "sensor.grid_power_l1": _make_state(device_class="power"),
            "sensor.temperature": _make_state(device_class="temperature"),
        }
        result = _find_power_sensors(states)
        assert result == ["sensor.grid_power_l1"]

    def test_finds_by_unit(self):
        states = {
            "sensor.pv_watt": _make_state(unit="W"),
            "sensor.pv_kw": _make_state(unit="kW"),
            "sensor.voltage": _make_state(unit="V"),
        }
        result = _find_power_sensors(states)
        assert "sensor.pv_watt" in result
        assert "sensor.pv_kw" in result
        assert "sensor.voltage" not in result

    def test_ignores_non_sensor(self):
        states = {
            "input_number.power": _make_state(device_class="power"),
            "binary_sensor.power": _make_state(device_class="power"),
            "sensor.actual_power": _make_state(device_class="power"),
        }
        result = _find_power_sensors(states)
        assert result == ["sensor.actual_power"]


class TestFindTriplets:
    def test_finds_l1_l2_l3_triplet(self):
        entities = [
            "sensor.grid_consumption_l1",
            "sensor.grid_consumption_l2",
            "sensor.grid_consumption_l3",
        ]
        triplets = _find_triplets(entities)
        assert len(triplets) == 1
        assert triplets[0] == (
            "sensor.grid_consumption_l1",
            "sensor.grid_consumption_l2",
            "sensor.grid_consumption_l3",
        )

    def test_finds_phase_1_2_3_triplet(self):
        entities = [
            "sensor.power_phase_1",
            "sensor.power_phase_2",
            "sensor.power_phase_3",
        ]
        triplets = _find_triplets(entities)
        assert len(triplets) == 1

    def test_finds_multiple_triplets(self):
        entities = [
            "sensor.grid_consumption_l1",
            "sensor.grid_consumption_l2",
            "sensor.grid_consumption_l3",
            "sensor.grid_production_l1",
            "sensor.grid_production_l2",
            "sensor.grid_production_l3",
        ]
        triplets = _find_triplets(entities)
        assert len(triplets) == 2

    def test_no_triplet_with_only_two(self):
        entities = [
            "sensor.grid_consumption_l1",
            "sensor.grid_consumption_l2",
        ]
        triplets = _find_triplets(entities)
        assert len(triplets) == 0

    def test_no_triplet_without_pattern(self):
        entities = [
            "sensor.grid_power",
            "sensor.solar_power",
        ]
        triplets = _find_triplets(entities)
        assert len(triplets) == 0

    def test_prefix_only_pattern(self):
        """Pattern like sensor.power_l1 (digit at end with prefix)."""
        entities = [
            "sensor.power_l1",
            "sensor.power_l2",
            "sensor.power_l3",
        ]
        triplets = _find_triplets(entities)
        assert len(triplets) == 1

    def test_suffix_only_pattern(self):
        """Pattern like sensor.l1_power (digit near start with suffix)."""
        entities = [
            "sensor.l1_power",
            "sensor.l2_power",
            "sensor.l3_power",
        ]
        triplets = _find_triplets(entities)
        assert len(triplets) == 1

    def test_case_insensitive(self):
        """Mixed case entity IDs are matched case-insensitively."""
        entities = [
            "sensor.Grid_Consumption_L1",
            "sensor.Grid_Consumption_L2",
            "sensor.Grid_Consumption_L3",
        ]
        triplets = _find_triplets(entities)
        assert len(triplets) == 1

    def test_emulated_entities(self):
        """Our own emulated entities should be detected."""
        entities = [
            "sensor.emulated_grid_consumption_l1",
            "sensor.emulated_grid_consumption_l2",
            "sensor.emulated_grid_consumption_l3",
            "sensor.emulated_grid_production_l1",
            "sensor.emulated_grid_production_l2",
            "sensor.emulated_grid_production_l3",
        ]
        triplets = _find_triplets(entities)
        assert len(triplets) == 2


class TestMatchConsumptionProduction:
    def test_matches_by_keyword(self):
        triplets = [
            (
                "sensor.grid_consumption_l1",
                "sensor.grid_consumption_l2",
                "sensor.grid_consumption_l3",
            ),
            (
                "sensor.grid_production_l1",
                "sensor.grid_production_l2",
                "sensor.grid_production_l3",
            ),
        ]
        cons, prod = _match_consumption_production(triplets)
        assert cons == list(triplets[0])
        assert prod == list(triplets[1])

    def test_matches_dutch_keywords(self):
        triplets = [
            (
                "sensor.verbruik_l1",
                "sensor.verbruik_l2",
                "sensor.verbruik_l3",
            ),
            (
                "sensor.teruglevering_l1",
                "sensor.teruglevering_l2",
                "sensor.teruglevering_l3",
            ),
        ]
        cons, prod = _match_consumption_production(triplets)
        assert cons == list(triplets[0])
        assert prod == list(triplets[1])

    def test_two_triplets_no_keywords(self):
        """With 2 triplets but no keywords, returns both in order."""
        triplets = [
            ("sensor.power_a1", "sensor.power_a2", "sensor.power_a3"),
            ("sensor.power_b1", "sensor.power_b2", "sensor.power_b3"),
        ]
        cons, prod = _match_consumption_production(triplets)
        assert cons == list(triplets[0])
        assert prod == list(triplets[1])

    def test_single_triplet(self):
        """Single triplet is returned as consumption."""
        triplets = [
            ("sensor.power_l1", "sensor.power_l2", "sensor.power_l3"),
        ]
        cons, prod = _match_consumption_production(triplets)
        assert cons == list(triplets[0])
        assert prod == []

    def test_no_triplets(self):
        cons, prod = _match_consumption_production([])
        assert cons == []
        assert prod == []


class TestFindFuseThreshold:
    def test_finds_fuse_entity(self):
        states = {
            "sensor.electricity_meter_fuse_threshold_l1": _make_state(
                unit="A", state="25", friendly_name="Fuse threshold L1"
            ),
        }
        assert _find_fuse_threshold(states) == 25

    def test_finds_fuse_by_friendly_name(self):
        states = {
            "sensor.some_entity": _make_state(
                unit="A", state="35", friendly_name="Electricity meter fuse threshold"
            ),
        }
        assert _find_fuse_threshold(states) == 35

    def test_ignores_non_ampere(self):
        states = {
            "sensor.fuse_threshold": _make_state(unit="kW", state="25"),
        }
        assert _find_fuse_threshold(states) is None

    def test_ignores_out_of_range(self):
        states = {
            "sensor.fuse_threshold": _make_state(unit="A", state="200"),
        }
        assert _find_fuse_threshold(states) is None

    def test_handles_float_value(self):
        states = {
            "sensor.fuse_threshold_l1": _make_state(unit="A", state="25.0"),
        }
        assert _find_fuse_threshold(states) == 25

    def test_no_fuse_entity(self):
        states = {
            "sensor.grid_power": _make_state(device_class="power"),
        }
        assert _find_fuse_threshold(states) is None


class TestDetectGridEntities:
    def test_full_3_phase_detection(self):
        """Full detection with 3-phase consumption, production, and fuse."""
        states = {
            "sensor.grid_consumption_l1": _make_state(device_class="power"),
            "sensor.grid_consumption_l2": _make_state(device_class="power"),
            "sensor.grid_consumption_l3": _make_state(device_class="power"),
            "sensor.grid_production_l1": _make_state(device_class="power"),
            "sensor.grid_production_l2": _make_state(device_class="power"),
            "sensor.grid_production_l3": _make_state(device_class="power"),
            "sensor.fuse_threshold_l1": _make_state(unit="A", state="25"),
        }
        result = detect_grid_entities(states)
        assert result["phases"] == 3
        assert result["capacity_per_phase"] == 25
        assert len(result["consumption_entities"]) == 3
        assert len(result["production_entities"]) == 3

    def test_no_power_sensors(self):
        """No power sensors → no detection."""
        states = {
            "sensor.temperature": _make_state(device_class="temperature"),
        }
        result = detect_grid_entities(states)
        assert result["phases"] is None
        assert result["consumption_entities"] == []
        assert result["production_entities"] == []

    def test_emulated_entities(self):
        """Detects our emulated entities correctly."""
        states = {
            "sensor.emulated_grid_consumption_l1": _make_state(device_class="power"),
            "sensor.emulated_grid_consumption_l2": _make_state(device_class="power"),
            "sensor.emulated_grid_consumption_l3": _make_state(device_class="power"),
            "sensor.emulated_grid_production_l1": _make_state(device_class="power"),
            "sensor.emulated_grid_production_l2": _make_state(device_class="power"),
            "sensor.emulated_grid_production_l3": _make_state(device_class="power"),
            "sensor.emulated_pv_power_1": _make_state(device_class="power"),
            "sensor.emulated_pv_power_2": _make_state(device_class="power"),
        }
        result = detect_grid_entities(states)
        assert result["phases"] == 3
        assert len(result["consumption_entities"]) == 3
        assert "sensor.emulated_grid_consumption_l1" in result["consumption_entities"]
        assert len(result["production_entities"]) == 3

    def test_single_phase(self):
        """Single consumption entity without triplet → 1 phase not detected."""
        states = {
            "sensor.grid_power": _make_state(device_class="power"),
        }
        result = detect_grid_entities(states)
        # No triplet found, can't determine phases
        assert result["phases"] is None
        assert result["consumption_entities"] == []
