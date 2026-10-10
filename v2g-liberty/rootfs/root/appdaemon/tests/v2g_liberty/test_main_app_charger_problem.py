"""The charger can become unusable in two very different ways, and the user
needs a different first step for each:

- Modbus is unreachable -- check network and plugs;
- the charger is perfectly reachable and reporting a fault -- read the fault
  code on the charger itself.

Both escalate through handle_none_responsive_charger. It used to describe both
as a communication error, via an entity literally named
input_boolean.charger_modbus_communication_fault. sensor.charger_problem now
carries *which* of the two it is, so the UI can say something true.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from apps.v2g_liberty.main_app import V2Gliberty


@pytest.fixture
def v2g():
    hass = AsyncMock()
    hass.log = MagicMock()

    notifier = MagicMock()
    notifier.notify_user = AsyncMock()

    instance = V2Gliberty(hass=hass, event_bus=MagicMock(), notifier=notifier)
    instance._V2Gliberty__set_charge_mode_in_ui = AsyncMock()
    return instance


def _state_writes(v2g) -> dict:
    """{entity_id: state} for every set_state call."""
    writes = {}
    for call in v2g.hass.set_state.await_args_list:
        entity = call.kwargs.get("entity_id") or (call.args[0] if call.args else None)
        writes[entity] = call.kwargs.get("state")
    return writes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason, expected_state, expected_title",
    [
        ("no Modbus response", "communication", "Charger communication error"),
        ("charger reports error", "charger_error", "Charger reports a fault"),
    ],
)
async def test_reason_decides_problem_state_and_title(
    v2g, reason, expected_state, expected_title
):
    await v2g.handle_none_responsive_charger(was_car_connected=True, reason=reason)

    assert _state_writes(v2g)["sensor.charger_problem"] == expected_state
    assert v2g.notifier.notify_user.await_args.kwargs["title"] == expected_title


@pytest.mark.asyncio
async def test_unknown_reason_falls_back_to_communication(v2g):
    """The safer of the two to tell someone, and the older of the two paths."""
    await v2g.handle_none_responsive_charger(was_car_connected=True, reason="who knows")

    assert _state_writes(v2g)["sensor.charger_problem"] == "communication"


@pytest.mark.asyncio
async def test_missing_reason_falls_back_to_communication(v2g):
    """A caller that has not been updated must not crash the escalation."""
    await v2g.handle_none_responsive_charger(was_car_connected=True)

    assert _state_writes(v2g)["sensor.charger_problem"] == "communication"


@pytest.mark.asyncio
async def test_charger_state_text_is_set_to_error(v2g):
    await v2g.handle_none_responsive_charger(
        was_car_connected=True, reason="charger reports error"
    )

    assert _state_writes(v2g)["sensor.charger_state_text"] == "Error"


@pytest.mark.asyncio
async def test_critical_only_when_the_car_was_connected(v2g):
    await v2g.handle_none_responsive_charger(was_car_connected=False, reason="x")
    assert v2g.notifier.notify_user.await_args.kwargs["critical"] is False

    await v2g.handle_none_responsive_charger(was_car_connected=True, reason="x")
    assert v2g.notifier.notify_user.await_args.kwargs["critical"] is True


@pytest.mark.asyncio
async def test_reset_clears_the_problem(v2g):
    await v2g.reset_charger_communication_fault()

    assert _state_writes(v2g)["sensor.charger_problem"] == "none"
    v2g.notifier.clear_notification.assert_called_once()


# ── Recovery ──────────────────────────────────────────────────────────
# The driver's probe reports the charger back; the main app clears the
# problem and puts the charge mode back only if it was the one that forced
# Stop. A user who had Stop before keeps it.


@pytest.mark.asyncio
async def test_escalation_remembers_the_charge_mode_it_overrides(v2g):
    v2g.hass.get_state = AsyncMock(return_value="Automatic")
    await v2g.handle_none_responsive_charger(was_car_connected=True, reason="x")

    assert v2g.charge_mode_before_charger_problem == "Automatic"
    v2g._V2Gliberty__set_charge_mode_in_ui.assert_awaited_once_with("Stop")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "previous", ["Automatic", "Max boost now", "Max discharge now"]
)
async def test_recovery_resumes_automatic_when_the_app_forced_stop(v2g, previous):
    """Always Automatic, never the boost that may have been on: a boost is a
    deliberate, momentary action and hours may have passed."""
    v2g.charge_mode_before_charger_problem = previous
    await v2g.handle_charger_recovered()

    v2g._V2Gliberty__set_charge_mode_in_ui.assert_awaited_once_with("Automatic")
    assert _state_writes(v2g)[v2g.CHARGER_PROBLEM_ENTITY] == v2g.CHARGER_PROBLEM_NONE
    kwargs = v2g.notifier.notify_user.await_args.kwargs
    assert kwargs["title"] == "Charger recovered"
    assert kwargs["tag"] == v2g.CHARGER_PROBLEM_TAG
    assert kwargs["critical"] is False
    assert "resumed" in kwargs["message"]
    assert v2g.charge_mode_before_charger_problem is None


@pytest.mark.asyncio
@pytest.mark.parametrize("previous", ["Stop", None, "unknown"])
async def test_recovery_leaves_a_deliberate_or_unknown_stop_alone(v2g, previous):
    v2g.charge_mode_before_charger_problem = previous
    await v2g.handle_charger_recovered()

    v2g._V2Gliberty__set_charge_mode_in_ui.assert_not_awaited()
    assert _state_writes(v2g)[v2g.CHARGER_PROBLEM_ENTITY] == v2g.CHARGER_PROBLEM_NONE
    assert "resumed" not in v2g.notifier.notify_user.await_args.kwargs["message"]
