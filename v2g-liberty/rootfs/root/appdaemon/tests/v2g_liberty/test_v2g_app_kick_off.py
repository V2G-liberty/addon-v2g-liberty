"""The kick-off runs as a task of its own, after initialize().

Its first schedule request waits for FlexMeasures, half a minute or more.
While an app's initialize() runs, AppDaemon discards that app's timers and
events, so awaiting the kick-off there lost short timers and left the
settings cards without an answer for as long as the request took. As a task
it no longer holds up initialize(), and a failure must still be logged: in
initialize() it reached AppDaemon's log, in a task it would vanish.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from apps.v2g_liberty.v2g_app import V2GLibertyApp


@pytest.fixture
def app():
    instance = object.__new__(V2GLibertyApp)
    instance.log = MagicMock()
    return instance


@pytest.mark.asyncio
async def test_the_kick_off_runs(app):
    main_app = MagicMock()
    main_app.kick_off_v2g_liberty = AsyncMock()

    await app._kick_off(main_app)

    main_app.kick_off_v2g_liberty.assert_awaited_once_with(v2g_args="initialise")
    app.log.assert_not_called()


@pytest.mark.asyncio
async def test_a_failing_kick_off_is_logged_not_lost(app):
    main_app = MagicMock()
    main_app.kick_off_v2g_liberty = AsyncMock(side_effect=RuntimeError("no charger"))

    await app._kick_off(main_app)  # does not raise

    app.log.assert_called_once()
    message = app.log.call_args.args[0]
    assert "Kick-off failed" in message
    assert "RuntimeError: no charger" in message
    assert app.log.call_args.kwargs["level"] == "ERROR"
