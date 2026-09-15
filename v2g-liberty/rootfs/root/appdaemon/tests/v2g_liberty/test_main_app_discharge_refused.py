"""A charger that refuses to discharge shows nothing: the car is connected,
charging works, and the schedule simply never earns anything back. The driver
knows -- it logs the refusal -- but until now nothing reached the user.

The refusal reason and the remedy are separate concerns on purpose. The driver
reports a code; which remedy belongs to it is a table in main_app, because the
mapping is provisional and must be replaceable without touching logic.
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
    notifier.clear_notification = MagicMock()

    instance = V2Gliberty(hass=hass, event_bus=MagicMock(), notifier=notifier)
    instance.discharge_refusal_timer_handle = None
    instance.notified_discharge_refusal = None
    instance.discharge_refused_reason = None
    instance.set_next_action = AsyncMock()
    instance.set_records_in_chart = AsyncMock()
    return instance


def _last_state(v2g, entity: str):
    for call in reversed(v2g.hass.set_state.await_args_list):
        name = call.kwargs.get("entity_id") or (call.args[0] if call.args else None)
        if name == entity:
            return call.kwargs.get("state")
    return None


async def _refuse(v2g, reason, is_manual=False):
    await v2g._V2Gliberty__handle_discharge_refused(reason=reason, is_manual=is_manual)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    ["session_not_bidirectional", "v2g_not_offered", "window_unknown"],
)
async def test_refusal_is_published_for_the_ui(v2g, reason):
    await _refuse(v2g, reason)

    assert _last_state(v2g, "sensor.discharge_refused") == reason


@pytest.mark.asyncio
async def test_a_manual_request_notifies_at_once(v2g):
    """The user just pressed "Max discharge now" and nothing happened; waiting
    an hour to mention it is no use."""
    await _refuse(v2g, "v2g_not_offered", is_manual=True)

    v2g.notifier.notify_user.assert_awaited_once()
    assert (
        "settings in the car" in v2g.notifier.notify_user.await_args.kwargs["message"]
    )


@pytest.mark.asyncio
async def test_a_scheduled_request_waits_before_notifying(v2g):
    """ "Not offered right now" can be brief and normal; notifying on every
    refusal becomes noise people learn to ignore."""
    await _refuse(v2g, "v2g_not_offered", is_manual=False)

    v2g.notifier.notify_user.assert_not_awaited()
    assert v2g.discharge_refusal_timer_handle is not None


@pytest.mark.asyncio
async def test_repeated_refusals_do_not_restart_the_timer(v2g):
    """Otherwise a refusal every five minutes postpones the notification for
    ever."""
    await _refuse(v2g, "v2g_not_offered")
    first = v2g.discharge_refusal_timer_handle
    await _refuse(v2g, "v2g_not_offered")

    assert v2g.discharge_refusal_timer_handle is first


@pytest.mark.asyncio
async def test_clearing_resets_state_timer_and_notification(v2g):
    await _refuse(v2g, "v2g_not_offered")
    await _refuse(v2g, None)

    assert _last_state(v2g, "sensor.discharge_refused") == "none"
    assert v2g.discharge_refusal_timer_handle is None
    v2g.notifier.clear_notification.assert_called_with(tag="discharge_refused")


@pytest.mark.asyncio
async def test_each_reason_has_its_own_remedy(v2g):
    """The remedies differ per reason: wait, replug, or change a car setting.
    One generic text would make the whole feature pointless."""
    messages = set()
    for reason in V2Gliberty._DISCHARGE_REMEDIES:
        v2g.notifier.notify_user.reset_mock()
        await _refuse(v2g, reason, is_manual=True)
        messages.add(v2g.notifier.notify_user.await_args.kwargs["message"])

    assert len(messages) == len(V2Gliberty._DISCHARGE_REMEDIES)


@pytest.mark.asyncio
async def test_an_unknown_reason_still_says_something_useful(v2g):
    """A charger reporting something we have no remedy for yet must not leave
    the user with an empty message."""
    await _refuse(v2g, "something_new", is_manual=True)

    message = v2g.notifier.notify_user.await_args.kwargs["message"]
    assert "contact your administrator" in message


@pytest.mark.asyncio
async def test_the_schedule_line_is_hidden_while_refused(v2g):
    """The prognosis assumes the discharging that is being refused; drawing it
    next to "the car is not discharging" would contradict the message."""
    v2g.set_records_in_chart = AsyncMock()

    await _refuse(v2g, "v2g_not_offered")

    v2g.set_records_in_chart.assert_awaited_once()
    assert v2g.set_records_in_chart.await_args.kwargs["records"] is None


@pytest.mark.asyncio
async def test_texts_do_not_claim_the_car_belongs_to_the_reader(v2g):
    """The rest of the UI says "the car"; a future installation may charge more
    than one, possibly someone else's."""
    for text in (
        *V2Gliberty._DISCHARGE_REMEDIES.values(),
        V2Gliberty._DISCHARGE_REMEDY_FALLBACK,
    ):
        assert "your car" not in text.lower()
        assert "your charger" not in text.lower()


@pytest.mark.asyncio
async def test_a_condition_that_comes_and_goes_notifies_once(v2g):
    """v2g_not_offered can flip repeatedly. Clearing and re-raising it must not
    notify every time, or the user learns to ignore the message."""
    await _refuse(v2g, "v2g_not_offered", is_manual=True)
    await _refuse(v2g, None)
    await _refuse(v2g, "v2g_not_offered", is_manual=True)

    assert v2g.notifier.notify_user.await_count == 2


@pytest.mark.asyncio
async def test_repeated_manual_attempts_notify_once(v2g):
    """Pressing the button twice while the same refusal stands is one problem,
    not two."""
    await _refuse(v2g, "v2g_not_offered", is_manual=True)
    await _refuse(v2g, "v2g_not_offered", is_manual=True)

    v2g.notifier.notify_user.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_different_reason_does_notify_again(v2g):
    """A new cause means a new remedy, so it has to be said."""
    await _refuse(v2g, "v2g_not_offered", is_manual=True)
    await _refuse(v2g, "session_not_bidirectional", is_manual=True)

    assert v2g.notifier.notify_user.await_count == 2


@pytest.mark.asyncio
async def test_the_schedule_prognosis_is_not_redrawn_while_refused(v2g):
    """Clearing the line once when the refusal arrives is not enough: every new
    schedule painted it straight back, next to a warning saying the car is not
    discharging."""
    await _refuse(v2g, "session_not_bidirectional")

    assert v2g.discharge_refused_reason == "session_not_bidirectional"

    await _refuse(v2g, None)

    assert v2g.discharge_refused_reason is None


@pytest.mark.asyncio
async def test_discharging_is_retried_when_the_refusal_resolves(v2g):
    """Taking the message away is not enough: the refused request is not
    repeated by itself, so the charger would sit idle with the user's
    "Max discharge now" still selected."""
    await _refuse(v2g, "session_not_bidirectional")
    v2g.set_next_action.reset_mock()

    await _refuse(v2g, None)

    v2g.set_next_action.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_retry_when_there_was_nothing_to_resolve(v2g):
    """A clear without a standing refusal (start-up, a second clear) must not
    kick off work."""
    await _refuse(v2g, None)

    v2g.set_next_action.assert_not_awaited()
