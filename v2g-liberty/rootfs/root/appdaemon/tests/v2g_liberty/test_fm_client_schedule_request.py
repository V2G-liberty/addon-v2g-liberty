"""The guard around a schedule request in FMClient.get_new_schedule.

flexmeasures-client 0.9.6 waits for the scheduling job on FlexMeasures 0.33+,
for up to job_polling_timeout per attempt, so a healthy request can now run
for many minutes. The guard that treats a request as lost must allow for
that, and the busy flag must come down on every path, because the guard is
no longer short enough to repair a flag that was left standing.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apps.v2g_liberty import constants as c
from apps.v2g_liberty.fm_client import FMClient
from flexmeasures_client import FlexMeasuresClient
from flexmeasures_client.exceptions import JobTimeoutError

TEST_TZ = timezone(timedelta(hours=1))
TEST_NOW = datetime(2026, 2, 22, 12, 0, 0, tzinfo=TEST_TZ)


@pytest.fixture
def fm(monkeypatch):
    monkeypatch.setattr(c, "TZ", TEST_TZ, raising=False)
    monkeypatch.setattr(c, "EVENT_RESOLUTION", timedelta(minutes=5), raising=False)
    hass = AsyncMock()
    hass.log = MagicMock()
    client = FMClient(hass, MagicMock())
    # The real limits matter here, not a mock's.
    client.client = MagicMock(
        request_retry_timeout=200.0,
        job_polling_timeout=600.0,
        trigger_and_get_schedule=AsyncMock(),
    )
    client.emit = MagicMock()
    client.wait_for_complete = AsyncMock()
    return client


async def _get(fm):
    with patch("apps.v2g_liberty.fm_client.get_local_now", return_value=TEST_NOW):
        return await fm.get_new_schedule(
            targets=[], current_soc_kwh=20.0, back_to_max_soc=None
        )


@pytest.mark.asyncio
async def test_budget_comes_from_the_clients_own_limits():
    """With the 0.9.6 defaults: 3 attempts of (3 requests + job wait + one lookup)."""
    fm = FMClient(AsyncMock(), MagicMock())
    # A real client, so a change of defaults in a future version shows up here.
    fm.client = FlexMeasuresClient(
        host="localhost:5000", email="a@b.cd", password="x", ssl=False
    )
    try:
        assert fm._FMClient__schedule_request_budget() == 4202
    finally:
        await fm.client.close()


@pytest.mark.asyncio
async def test_a_slow_but_healthy_request_is_not_taken_for_lost(fm):
    """Five minutes in is normal for 0.9.6; the old 116 s bound fired a duplicate."""
    fm.fm_busy_getting_schedule = True
    fm.fm_date_time_last_schedule = TEST_NOW - timedelta(seconds=300)
    inner = AsyncMock()
    with patch.object(fm, "_FMClient__get_new_schedule_while_busy", inner):
        assert await _get(fm) is None
    inner.assert_not_awaited()
    assert fm.fm_busy_getting_schedule is True  # the request in flight keeps it


@pytest.mark.asyncio
async def test_a_request_older_than_the_budget_is_taken_for_lost(fm):
    fm.fm_busy_getting_schedule = True
    budget = fm._FMClient__schedule_request_budget()
    fm.fm_date_time_last_schedule = TEST_NOW - timedelta(seconds=budget + 1)
    inner = AsyncMock(return_value={"values": []})
    with patch.object(fm, "_FMClient__get_new_schedule_while_busy", inner):
        await _get(fm)
    inner.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [RuntimeError("bad target"), asyncio.CancelledError()]
)
async def test_busy_flag_comes_down_when_the_request_does_not_finish(fm, error):
    """An exception while building the request, or a cancelled task, used to
    leave the flag up until the guard timed it out — now that is over an hour."""
    with (
        patch.object(
            fm, "_FMClient__get_new_schedule_while_busy", AsyncMock(side_effect=error)
        ),
        pytest.raises(type(error)),
    ):
        await _get(fm)
    assert fm.fm_busy_getting_schedule is False


@pytest.mark.asyncio
async def test_job_timeout_on_every_attempt_ends_in_no_new_schedule(fm):
    """0.9.6 raises JobTimeoutError when the scheduling job runs out of time.
    The retry loop must treat it like any other failure."""
    fm.client.trigger_and_get_schedule = AsyncMock(
        side_effect=JobTimeoutError("job did not finish in time")
    )

    assert await _get(fm) is None

    assert fm.client.trigger_and_get_schedule.await_count == fm.SCHEDULE_MAX_RETRIES + 1
    fm.emit.assert_called_with(
        "no_new_schedule", "timeouts_on_schedule", error_state=True
    )
    assert fm.fm_busy_getting_schedule is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "password", "error"),
    [
        ("https://ems.example.com", "", "password cannot be empty"),
        ("https://https://ems.example.com", "secret", "should not be included"),
    ],
)
async def test_bad_credentials_at_start_up_are_reported_not_raised(
    monkeypatch, url, password, error
):
    """EmptyPasswordError and WrongHostError do not subclass ValueError. Raised
    out of initialise_and_test_fm_client they escaped kick_off_settings, and the
    app did not start; returned, they become a FlexMeasures connection error."""
    monkeypatch.setattr(c, "TZ", TEST_TZ, raising=False)
    monkeypatch.setattr(c, "FM_BASE_URL", url, raising=False)
    monkeypatch.setattr(c, "FM_ACCOUNT_USERNAME", "user@example.com", raising=False)
    monkeypatch.setattr(c, "FM_ACCOUNT_PASSWORD", password, raising=False)
    fm = FMClient(AsyncMock(), MagicMock())

    result = await fm.initialise_and_test_fm_client()

    assert error in str(result)
    assert fm.client is None


@pytest.mark.asyncio
async def test_a_refused_request_is_remembered_until_the_running_one_is_in(fm):
    """A reservation arrives while a schedule is being fetched. The call it
    triggers is refused, but not forgotten: once the running request is in,
    main_app must be told to ask again, with the reservation in it."""
    release = asyncio.Event()

    async def slow_trigger(**kwargs):
        await release.wait()
        return {
            "values": [0.001],
            "duration": "PT5M",
            "start": "2026-02-22T12:00:00+01:00",
        }

    fm.client.trigger_and_get_schedule = AsyncMock(side_effect=slow_trigger)
    fm.set_fm_connection_status = AsyncMock()

    first = asyncio.create_task(_get(fm))
    while not fm.fm_busy_getting_schedule:
        await asyncio.sleep(0)

    assert await _get(fm) is None  # refused: one is in flight
    assert fm.schedule_request_deferred is True

    release.set()
    assert (await first) is not None
    assert fm.pop_deferred_schedule_request() is True
    assert fm.pop_deferred_schedule_request() is False  # taken once


@pytest.mark.asyncio
async def test_starting_a_request_drops_an_older_deferral(fm):
    """The new request reads the current data, so it covers what the refused
    call wanted; a deferral left behind by a failed request must not cause an
    extra round later."""
    fm.schedule_request_deferred = True
    fm.client.trigger_and_get_schedule = AsyncMock(
        side_effect=JobTimeoutError("job did not finish in time")
    )

    await _get(fm)

    assert fm.schedule_request_deferred is False


@pytest.mark.asyncio
async def test_a_trigger_during_the_reporting_of_an_outcome_is_refused(fm):
    """The busy flag stays up until get_new_schedule returns. It used to be
    cleared before the outcome was reported; a trigger landing in that yield
    then started a second request unrefused, and the finally of the first
    request cleared the second one's flag — the guard was off from there on."""
    fm.client.trigger_and_get_schedule = AsyncMock(
        return_value={
            "values": [0.001],
            "duration": "PT5M",
            "start": "2026-02-22T12:00:00+01:00",
        }
    )
    fm.set_fm_connection_status = AsyncMock()
    seen_during_reporting = {}

    async def trigger_arrives_while_reporting():
        if seen_during_reporting:
            return  # only the first report gets a trigger; the second call reports too
        seen_during_reporting["busy"] = fm.fm_busy_getting_schedule
        seen_during_reporting["result"] = await _get(fm)  # the second call
        seen_during_reporting["deferred"] = fm.schedule_request_deferred

    fm.wait_for_complete = AsyncMock(side_effect=trigger_arrives_while_reporting)

    assert (await _get(fm)) is not None

    assert seen_during_reporting["busy"] is True, (
        "flag must still be up while reporting"
    )
    assert seen_during_reporting["result"] is None, "the second call must be refused"
    assert seen_during_reporting["deferred"] is True, "... and remembered"
    assert fm.client.trigger_and_get_schedule.await_count == 1
    assert fm.fm_busy_getting_schedule is False  # the finally, afterwards
