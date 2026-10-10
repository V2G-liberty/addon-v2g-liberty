"""Characterisation tests for the ElectricVehicle abstraction (Fase 1).

Pin the EV's SoC mirror and its derived soc_kwh / remaining_range so they stay
bit-identical to the charger getters (modbus_evse_client.get_car_soc_kwh /
get_car_remaining_range) after main_app is repointed onto the EV. In Fase 1 the
EV is a NON-emitting mirror: it subscribes to soc_change and must never re-emit,
and it derives from the same live car constants the charger reads.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import apps.v2g_liberty.constants as c
from apps.v2g_liberty.event_bus import EventBus
from apps.v2g_liberty.evs.electric_vehicle import ElectricVehicle
from apps.v2g_liberty.chargers.wallbox_quasar_1 import WallboxQuasar1Client
from apps.dev_tools.charger_scenarios import STATE_CHARGING


def _make_ev():
    hass = MagicMock()
    bus = EventBus(hass)
    return ElectricVehicle(hass, event_bus=bus), bus


def test_derivations_for_known_soc():
    ev, _ = _make_ev()
    ev.update_soc(55)
    assert ev.soc == 55
    # Same formula + live constant as modbus_evse_client.get_car_soc_kwh / range.
    assert ev.soc_kwh == round(55 * float(c.CAR_MAX_CAPACITY_IN_KWH / 100), 2)
    assert ev.remaining_range_km == int(
        round(ev.soc_kwh * 1000 / c.CAR_CONSUMPTION_WH_PER_KM, 0)
    )


@pytest.mark.parametrize("empty", ["unavailable", "unknown", None])
def test_empty_soc_sentinels_are_safe(empty):
    """A non-numeric SoC yields "unavailable" from every derived getter (no crash)."""
    ev, _ = _make_ev()
    ev.update_soc(empty)
    assert ev.soc == empty
    assert ev.soc_kwh == "unavailable"
    assert ev.remaining_range_km == "unavailable"


def test_update_soc_does_not_emit():
    ev, bus = _make_ev()
    seen = []
    bus.add_event_listener("soc_change", lambda **kw: seen.append("soc_change"))
    bus.add_event_listener("remaining_range_change", lambda **kw: seen.append("range"))
    ev.update_soc(55)
    # The EV mirror must not re-emit either event in Fase 1.
    assert seen == []


def test_ev_updates_synchronously_on_emit():
    """The EV subscribes to soc_change and refreshes ev.soc inline during emit."""
    ev, bus = _make_ev()
    bus.emit_event("soc_change", new_soc=55, old_soc=50)
    assert ev.soc == 55


@pytest.mark.asyncio
async def test_ev_derivations_match_charger_getters():
    """Differential parity: the EV and the REAL charger getters agree bit-for-bit.

    This is the "behaviour unchanged" anchor for Fase 1 — it drives one SoC
    through both runtimes so a drift on either side (formula or constant) fails.
    """
    hass = MagicMock()
    for m in ("set_state", "run_every", "run_in", "get_state", "cancel_timer"):
        setattr(hass, m, AsyncMock())
    bus = EventBus(hass)

    charger = WallboxQuasar1Client(hass, bus, MagicMock())
    charger._mb_client._mbc = (
        MagicMock()
    )  # is_car_connected needs an initialised client
    charger._am_i_active = True
    charger._MCE_CHARGER_STATE.current_value = STATE_CHARGING  # connected
    charger._MCE_CAR_SOC.current_value = 55

    ev = ElectricVehicle(hass, event_bus=bus)
    ev.update_soc(55)
    try:
        assert ev.soc == await charger.get_car_soc()
        assert ev.soc_kwh == await charger.get_car_soc_kwh()
        assert ev.remaining_range_km == await charger.get_car_remaining_range()
    finally:
        # Reset the shared class-level entity caches for test isolation.
        charger._MCE_CHARGER_STATE.current_value = None
        charger._MCE_CAR_SOC.current_value = None
