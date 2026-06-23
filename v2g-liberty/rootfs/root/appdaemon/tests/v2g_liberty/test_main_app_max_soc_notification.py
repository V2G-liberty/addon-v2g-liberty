"""Unit tests for the "battery reached max SoC" notification gate.

The notification must only fire on a genuine rise to the max-SoC setting while
charging — not on the first SoC reading after a restart (when ``old_soc`` is not
yet a valid previous value). That restart case would otherwise compute the range
before the car settings are loaded, reporting a wrong range based on the default
battery capacity.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest

from apps.v2g_liberty.main_app import V2Gliberty
from apps.v2g_liberty import constants as c


@pytest.fixture
def v2g():
    """Create V2Gliberty with mocked dependencies for SoC-change handling."""
    hass = AsyncMock()
    hass.log = MagicMock()

    notifier = MagicMock()
    notifier.notify_user = AsyncMock()

    instance = V2Gliberty(hass=hass, event_bus=MagicMock(), notifier=notifier)

    # Short-circuit the schedule-refresh branch so the test focuses on the
    # notification gate only.
    instance.set_next_action = AsyncMock()
    instance.soc_at_last_schedule_refresh = c.CAR_MAX_SOC_IN_PERCENT

    instance.evse_client_app = MagicMock()
    instance.evse_client_app.is_charging = AsyncMock(return_value=True)
    instance.evse_client_app.get_car_remaining_range = AsyncMock(return_value=278)

    return instance


async def _handle(instance, new_soc, old_soc):
    """Invoke the name-mangled private SoC-change handler."""
    await instance._V2Gliberty__handle_soc_change(new_soc=new_soc, old_soc=old_soc)


class TestMaxSocNotification:
    @pytest.mark.asyncio
    async def test_notifies_on_genuine_rise_to_max_soc(self, v2g):
        """old_soc < max → new_soc == max while charging → one notification."""
        await _handle(
            v2g,
            new_soc=c.CAR_MAX_SOC_IN_PERCENT,
            old_soc=c.CAR_MAX_SOC_IN_PERCENT - 1,
        )

        v2g.notifier.notify_user.assert_awaited_once()
        # The range from get_car_remaining_range and the max-SoC value must be in
        # the message. The message uses a narrow no-break space before the unit,
        # so assert on the number and unit separately rather than on a literal
        # space.
        message = v2g.notifier.notify_user.await_args.kwargs["message"]
        assert "278" in message
        assert str(c.CAR_MAX_SOC_IN_PERCENT) in message
        assert "%" in message

    @pytest.mark.asyncio
    async def test_no_notification_on_first_reading_after_restart(self, v2g):
        """old_soc is not a valid previous value → no notification."""
        await _handle(v2g, new_soc=c.CAR_MAX_SOC_IN_PERCENT, old_soc="unavailable")
        v2g.notifier.notify_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_notification_when_already_at_max(self, v2g):
        """No genuine rise (old_soc == new_soc == max) → no notification."""
        await _handle(
            v2g,
            new_soc=c.CAR_MAX_SOC_IN_PERCENT,
            old_soc=c.CAR_MAX_SOC_IN_PERCENT,
        )
        v2g.notifier.notify_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_notification_when_not_charging(self, v2g):
        """Reached max SoC but not charging → no notification."""
        v2g.evse_client_app.is_charging = AsyncMock(return_value=False)
        await _handle(
            v2g,
            new_soc=c.CAR_MAX_SOC_IN_PERCENT,
            old_soc=c.CAR_MAX_SOC_IN_PERCENT - 1,
        )
        v2g.notifier.notify_user.assert_not_awaited()
