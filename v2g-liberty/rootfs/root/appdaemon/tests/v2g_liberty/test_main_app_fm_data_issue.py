"""Unit tests for main_app.handle_fm_data_issue.

Shows/dismisses a persistent memo when FlexMeasures is reachable but refuses
data for a specific sensor (an operational error, not a connection failure),
mirroring how fm_client.post_sensor_data reports the condition.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest

from apps.v2g_liberty.main_app import V2Gliberty


@pytest.fixture
def v2g():
    """Create V2Gliberty with a mocked notifier."""
    hass = AsyncMock()
    hass.log = MagicMock()
    notifier = MagicMock()
    return V2Gliberty(hass=hass, event_bus=MagicMock(), notifier=notifier)


class TestHandleFmDataIssue:
    @pytest.mark.asyncio
    async def test_active_posts_sticky_memo(self, v2g):
        await v2g.handle_fm_data_issue(active=True, sensor_id=531, detail="HTTP 403")

        v2g.notifier.post_sticky_memo.assert_called_once()
        kwargs = v2g.notifier.post_sticky_memo.call_args.kwargs
        assert kwargs["memo_id"] == "fm_data_issue"
        assert "531" in kwargs["message"]
        v2g.notifier.dismiss_sticky_memo.assert_not_called()

    @pytest.mark.asyncio
    async def test_inactive_dismisses_sticky_memo(self, v2g):
        await v2g.handle_fm_data_issue(active=False)

        v2g.notifier.dismiss_sticky_memo.assert_called_once_with(
            memo_id="fm_data_issue"
        )
        v2g.notifier.post_sticky_memo.assert_not_called()
