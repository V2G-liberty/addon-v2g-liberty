"""Unit tests for meter_register_detector module."""

from apps.v2g_liberty.grid_connection.meter_register_detector import (
    detect_meter_registers,
    _find_energy_registers,
    _classify,
    _prefer_total,
)


def _reg(
    unit="kWh", device_class="energy", state_class="total_increasing", friendly=""
):
    """Mock state for a cumulative energy register."""
    return {
        "state": "12345.6",
        "attributes": {
            "device_class": device_class,
            "state_class": state_class,
            "unit_of_measurement": unit,
            "friendly_name": friendly,
        },
    }


# Native HA DSMR entity ids (English-derived, language-independent).
DSMR = {
    "sensor.electricity_meter_energy_consumption_tarif_1": _reg(),
    "sensor.electricity_meter_energy_consumption_tarif_2": _reg(),
    "sensor.electricity_meter_energy_production_tarif_1": _reg(),
    "sensor.electricity_meter_energy_production_tarif_2": _reg(),
}


class TestFindEnergyRegisters:
    def test_finds_total_increasing_energy(self):
        states = dict(DSMR)
        assert set(_find_energy_registers(states)) == set(DSMR)

    def test_excludes_power_sensors(self):
        states = {
            "sensor.grid_power_l1": _reg(
                unit="kW", device_class="power", state_class="measurement"
            ),
        }
        assert _find_energy_registers(states) == []

    def test_excludes_non_total_increasing_energy(self):
        # A per-interval energy sensor is device_class energy but NOT
        # total_increasing — must be excluded.
        states = {
            "sensor.energy_this_hour": _reg(state_class="total"),
            "sensor.energy_now": _reg(state_class="measurement"),
        }
        assert _find_energy_registers(states) == []

    def test_excludes_wrong_unit(self):
        states = {"sensor.voltage": _reg(unit="V")}
        assert _find_energy_registers(states) == []

    def test_accepts_wh_and_mwh(self):
        states = {
            "sensor.a_energy_consumption_total": _reg(unit="Wh"),
            "sensor.b_energy_production_total": _reg(unit="MWh"),
        }
        assert len(_find_energy_registers(states)) == 2


class TestClassify:
    def test_dsmr_patterns(self):
        cons, prod = _classify(list(DSMR), DSMR)
        assert cons == [
            "sensor.electricity_meter_energy_consumption_tarif_1",
            "sensor.electricity_meter_energy_consumption_tarif_2",
        ]
        assert prod == [
            "sensor.electricity_meter_energy_production_tarif_1",
            "sensor.electricity_meter_energy_production_tarif_2",
        ]

    def test_pattern_wins_over_pv_keyword(self):
        # A SolarEdge PV energy sensor contains "production" but not the DSMR
        # pattern. With DSMR production registers present, the keyword tier is
        # skipped so the PV meter does NOT contaminate grid export.
        states = dict(DSMR)
        states["sensor.solaredge_energy_production"] = _reg(friendly="SolarEdge energy")
        cons, prod = _classify(list(states), states)
        assert "sensor.solaredge_energy_production" not in prod
        assert len(prod) == 2

    def test_keyword_fallback_non_dsmr(self):
        # HomeWizard-style: no DSMR pattern → keyword fallback on import/export.
        states = {
            "sensor.p1_meter_energy_import_t1": _reg(),
            "sensor.p1_meter_energy_export_t1": _reg(),
        }
        cons, prod = _classify(list(states), states)
        assert cons == ["sensor.p1_meter_energy_import_t1"]
        assert prod == ["sensor.p1_meter_energy_export_t1"]

    def test_unclassified_left_out(self):
        states = {"sensor.solar_lifetime_kwh": _reg(friendly="Lifetime")}
        cons, prod = _classify(list(states), states)
        assert cons == [] and prod == []


class TestPreferTotal:
    def test_prefers_total_over_tariffs(self):
        result = _prefer_total(
            [
                "sensor.x_energy_consumption_tarif_1",
                "sensor.x_energy_consumption_tarif_2",
                "sensor.x_energy_consumption_total",
            ]
        )
        assert result == ["sensor.x_energy_consumption_total"]

    def test_all_tariffs_when_no_total(self):
        result = _prefer_total(
            [
                "sensor.x_energy_consumption_tarif_2",
                "sensor.x_energy_consumption_tarif_1",
            ]
        )
        assert result == [
            "sensor.x_energy_consumption_tarif_1",
            "sensor.x_energy_consumption_tarif_2",
        ]


class TestDetectMeterRegisters:
    def test_real_dsmr_dual_tariff(self):
        result = detect_meter_registers(DSMR)
        assert result["consumption_registers"] == [
            "sensor.electricity_meter_energy_consumption_tarif_1",
            "sensor.electricity_meter_energy_consumption_tarif_2",
        ]
        assert result["production_registers"] == [
            "sensor.electricity_meter_energy_production_tarif_1",
            "sensor.electricity_meter_energy_production_tarif_2",
        ]

    def test_prefers_total_registers(self):
        states = dict(DSMR)
        states["sensor.electricity_meter_energy_consumption_total"] = _reg()
        states["sensor.electricity_meter_energy_production_total"] = _reg()
        result = detect_meter_registers(states)
        assert result["consumption_registers"] == [
            "sensor.electricity_meter_energy_consumption_total"
        ]
        assert result["production_registers"] == [
            "sensor.electricity_meter_energy_production_total"
        ]

    def test_single_tariff_meter(self):
        states = {
            "sensor.electricity_meter_energy_consumption_tarif_1": _reg(),
            "sensor.electricity_meter_energy_production_tarif_1": _reg(),
        }
        result = detect_meter_registers(states)
        assert result["consumption_registers"] == [
            "sensor.electricity_meter_energy_consumption_tarif_1"
        ]
        assert result["production_registers"] == [
            "sensor.electricity_meter_energy_production_tarif_1"
        ]

    def test_language_robust_entity_id_over_translated_name(self):
        # Dutch friendly_name, English entity_id (as HA actually creates them).
        states = {
            "sensor.electricity_meter_energy_consumption_tarif_1": _reg(
                friendly="Energieverbruik (tarief 1)"
            ),
            "sensor.electricity_meter_energy_production_tarif_1": _reg(
                friendly="Energieproductie (tarief 1)"
            ),
        }
        result = detect_meter_registers(states)
        assert result["consumption_registers"] == [
            "sensor.electricity_meter_energy_consumption_tarif_1"
        ]
        assert result["production_registers"] == [
            "sensor.electricity_meter_energy_production_tarif_1"
        ]

    def test_dsmr_plus_pv_excludes_pv(self):
        states = dict(DSMR)
        states["sensor.solaredge_energy_production"] = _reg(friendly="SolarEdge")
        result = detect_meter_registers(states)
        assert (
            "sensor.solaredge_energy_production" not in result["production_registers"]
        )
        assert len(result["production_registers"]) == 2
