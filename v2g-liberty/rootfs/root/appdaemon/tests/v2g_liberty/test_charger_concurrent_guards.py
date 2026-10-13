"""The charger drivers' error guards hold when two reports arrive at once.

Nothing serialises the Modbus calls, so when the charger drops, a poll and a
power command fail together and both land in _handle_modbus_exception within
milliseconds. Each saw the counter still at 0 and set its own timer; the
first timer's handle was overwritten by the second, so when the charger
recovered only one timer was cancelled and the other still reported the
charger as unreachable a minute later. The error-state timer had the
same shape, and at start-up that handler runs four times within a few
milliseconds.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from apps.v2g_liberty.chargers.evtec_bidipro import EVtecBiDiProClient
from apps.v2g_liberty.chargers.wallbox_quasar_1 import WallboxQuasar1Client


async def _yield_once(*_args, **_kwargs):
    """Stands in for hass.run_in: gives the other task its turn, as the real
    call does, and returns a timer handle."""
    await asyncio.sleep(0)
    return "handle"


@pytest.fixture(
    params=[WallboxQuasar1Client, EVtecBiDiProClient], ids=["quasar", "evtec"]
)
def charger(request):
    hass = MagicMock()
    hass.run_in = AsyncMock(side_effect=_yield_once)
    hass.timer_running = AsyncMock(return_value=False)
    hass.cancel_timer = AsyncMock()
    evse = request.param(hass, MagicMock(), MagicMock())
    evse._is_shut_down = False
    evse.modbus_exception_counter = 0
    evse.timer_id_check_modus_exception_state = None
    evse.timer_id_check_error_state = None
    # This test is about the timers, not the error registers.
    evse.CHARGER_ERROR_ENTITIES = []
    return evse


@pytest.mark.asyncio
async def test_two_modbus_exceptions_at_once_set_one_timer(charger):
    results = await asyncio.gather(
        charger._handle_modbus_exception("poll"),
        charger._handle_modbus_exception("set power"),
    )

    assert charger.hass.run_in.await_count == 1
    assert charger.modbus_exception_counter == 1
    assert charger.timer_id_check_modus_exception_state == "handle"
    # Both see the first exception: recoverable for now.
    assert results == [False, False]


@pytest.mark.asyncio
async def test_two_error_reports_at_once_set_one_timer(charger):
    error_state = charger.ERROR_STATES[0]
    report = charger._handle_charger_error_state_change

    await asyncio.gather(
        report({"new_charger_state": error_state}),
        report({"new_charger_state": error_state}),
    )

    assert charger.hass.run_in.await_count == 1
    assert charger.timer_id_check_error_state == "handle"
