"""Resuming by hand checks whether the charger still reports a fault.

After a charger problem the app sets the charge mode to Stop. Switching back
to Automatic yourself counts as a recovery: set_active cancels the recovery
probe. The error check only runs on a change of the charger state, though,
and a charger that still reports the fault it reported before shows no
change. The app then stood in Automatic beside a charger that could not
charge, and said nothing. set_active now runs the check itself, so a fault
that is still there is escalated again after the usual grace period.

That only works from a clean slate: the escalation used to cancel its timers
but leave their handles, and the Modbus error count, behind. A resume then
read "a timer is still running" or "still within the grace period", and a
charger that was still broken never escalated again.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from apps.v2g_liberty.chargers.evtec_bidipro import EVtecBiDiProClient
from apps.v2g_liberty.chargers.wallbox_quasar_1 import WallboxQuasar1Client


@pytest.fixture(
    params=[WallboxQuasar1Client, EVtecBiDiProClient], ids=["quasar", "evtec"]
)
def charger(request):
    hass = MagicMock()
    hass.run_in = AsyncMock(return_value="error-timer")
    hass.timer_running = AsyncMock(return_value=False)
    hass.cancel_timer = AsyncMock()
    evse = request.param(hass, MagicMock(), MagicMock())
    evse._is_shut_down = False
    evse.timer_id_check_error_state = None
    # This test is about the state, not the error registers.
    evse.CHARGER_ERROR_ENTITIES = []
    # What set_active does around the check; not under test here.
    evse._mb_client = MagicMock()
    evse._mb_client.is_initialised = True
    evse._recovery_probe = MagicMock()
    evse._recovery_probe.cancel = AsyncMock()
    evse._set_charger_control = AsyncMock()
    evse._get_car_soc = AsyncMock()
    evse._get_and_process_registers = AsyncMock()
    evse._set_poll_strategy = AsyncMock()
    # What the escalation does besides its bookkeeping; not under test here.
    evse._recovery_probe.arm = AsyncMock()
    evse._cancel_polling = AsyncMock()
    evse.is_car_connected = AsyncMock(return_value=True)
    evse._update_charger_communication_state = AsyncMock()
    evse._update_evse_entity = AsyncMock()
    evse.v2g_main_app = MagicMock()
    evse.v2g_main_app.handle_none_responsive_charger = AsyncMock()
    return evse


def _error_timer_started(charger) -> bool:
    return any(
        call.args and call.args[0] == charger._handle_charger_error_state_change
        for call in charger.hass.run_in.await_args_list
    )


@pytest.mark.asyncio
async def test_resuming_beside_a_charger_still_in_error_starts_the_check(charger):
    charger._get_charger_state = AsyncMock(return_value=charger.ERROR_STATES[0])

    await charger.set_active()

    assert _error_timer_started(charger)
    assert charger.timer_id_check_error_state == "error-timer"


@pytest.mark.asyncio
async def test_resuming_beside_a_healthy_charger_starts_nothing(charger):
    healthy = next(state for state in range(20) if state not in charger.ERROR_STATES)
    charger._get_charger_state = AsyncMock(return_value=healthy)

    await charger.set_active()

    assert not _error_timer_started(charger)
    assert charger.timer_id_check_error_state is None


def _modbus_timer_started(charger) -> bool:
    return any(
        call.args and call.args[0] == charger._handle_un_recoverable_error
        for call in charger.hass.run_in.await_args_list
    )


@pytest.mark.asyncio
async def test_after_an_escalation_a_fault_that_stays_is_escalated_again(charger):
    """The sequence of the manual test: the fault timer ran out and escalated,
    then the user resumed while the charger still reported the fault."""
    charger._get_charger_state = AsyncMock(return_value=charger.ERROR_STATES[0])
    charger.timer_id_check_error_state = "fault-timer-that-ran-out"
    await charger._handle_un_recoverable_error(reason="charger reports error")

    await charger.set_active()

    assert _error_timer_started(charger)


@pytest.mark.asyncio
async def test_after_an_escalation_a_charger_still_unreachable_is_escalated_again(
    charger,
):
    """The Modbus timer ran out and escalated; on resume the charger is still
    unreachable. The first failure starts a new grace period."""
    charger.modbus_exception_counter = 1
    charger.timer_id_check_modus_exception_state = "modbus-timer-that-ran-out"
    await charger._handle_un_recoverable_error(reason="no Modbus response")

    is_unrecoverable = await charger._handle_modbus_exception("resume")

    assert is_unrecoverable is False
    assert _modbus_timer_started(charger)
