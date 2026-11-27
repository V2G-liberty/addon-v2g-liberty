"""The grid/PV emulator's deviation knob: the extra household load is spread
over the three phases, an unreadable value counts as nothing, a change is
logged once and the force-negative sensor leaves it out. Built with
``object.__new__`` to skip ``hass.Hass.__init__``, like the other emulator
tests; the time-of-day, charger and PV parts are replaced by fixed values."""

from unittest.mock import MagicMock

import pytest
from dev_tools.grid_pv_emulator import GridPvEmulator

SURPRISE = "input_number.emulator_surprise_w"
FORCE_NEGATIVE = "input_boolean.emulator_force_negative"
BASE = {1: 800.0, 2: 600.0, 3: 400.0}
NOTHING = {1: 0.0, 2: 0.0, 3: 0.0}


def make_emulator(states):
    e = object.__new__(GridPvEmulator)
    e.log = MagicMock()
    e.get_state = lambda entity_id: states.get(entity_id)
    e.set_state = MagicMock()
    e._update_interval = 10
    e._energy = {"import_t1": 0.0, "import_t2": 0.0, "export_t1": 0.0, "export_t2": 0.0}
    e._status_every = 0
    e._ticks_since_status = 0
    e._cycle = 1  # The first tick then adds an offset of 0.
    e._last_surprise_w = 0.0
    e._calculate_base_load = lambda now: dict(BASE)
    e._calculate_charger_impact = lambda: dict(NOTHING)
    e._calculate_pv_per_phase = lambda now: dict(NOTHING)
    e._calculate_pv_totals = lambda now: []
    return e


def sensors_of(e):
    return {call.args[0]: call.kwargs["state"] for call in e.set_state.call_args_list}


def consumption_of(e):
    s = sensors_of(e)
    return {p: s[f"sensor.emulated_grid_consumption_l{p}"] for p in (1, 2, 3)}


def test_the_extra_load_is_spread_equally_over_the_three_phases():
    e = make_emulator({SURPRISE: "3000"})
    e._update_sensors({})
    assert consumption_of(e) == {1: 1800, 2: 1600, 3: 1400}


def test_the_energy_registers_count_the_extra_load():
    e = make_emulator({SURPRISE: "3000"})
    e._update_sensors({})
    imported_kwh = e._energy["import_t1"] + e._energy["import_t2"]
    assert imported_kwh == pytest.approx((1800 + 1600 + 1400) / 1000 * 10 / 3600)


@pytest.mark.parametrize("state", [None, "unknown", "", "oven"])
def test_an_unreadable_value_counts_as_nothing(state):
    e = make_emulator({SURPRISE: state})
    e._update_sensors({})
    assert consumption_of(e) == {1: 800, 2: 600, 3: 400}
    assert e.log.call_count == 0


def test_a_change_is_logged_once():
    states = {SURPRISE: "2500"}
    e = make_emulator(states)
    e._update_sensors({})
    e._update_sensors({})
    assert e.log.call_count == 1
    assert "0 W -> 2500 W" in e.log.call_args.args[0]
    states[SURPRISE] = "0"
    e._update_sensors({})
    assert e.log.call_count == 2
    assert "2500 W -> 0 W" in e.log.call_args.args[0]


def test_the_forced_negative_sensor_leaves_the_extra_load_out():
    e = make_emulator({SURPRISE: "3000", FORCE_NEGATIVE: "on"})
    e._update_sensors({})
    assert consumption_of(e)[1] == 1800
    assert sensors_of(e)["sensor.emulated_grid_net_l1"] == -(500 + 800)
