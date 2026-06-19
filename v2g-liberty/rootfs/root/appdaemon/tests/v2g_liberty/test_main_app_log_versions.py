"""Unit tests for V2Gliberty.log_versions version-attribute routing."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from apps.v2g_liberty.main_app import V2Gliberty
from apps.v2g_liberty import constants as c


@pytest.fixture
def v2g():
    """Create V2Gliberty with mocked dependencies and a stubbed fm_client_app."""
    hass = AsyncMock()
    hass.log = MagicMock()
    hass.get_state = AsyncMock(
        side_effect=lambda entity_id, attribute=None: {
            "update.v2g_liberty_update": "1.2.3",
            "update.home_assistant_core_update": "2026.5.0",
        }[entity_id]
    )

    instance = V2Gliberty(hass=hass, event_bus=MagicMock(), notifier=MagicMock())
    instance.fm_client_app = MagicMock()
    instance.fm_client_app.set_asset_attributes = AsyncMock()
    return instance


class TestLogVersions:
    @pytest.mark.asyncio
    async def test_writes_to_mains_connection_and_clears_charger(self, v2g):
        """Mains Connection present → versions written there + charger cleanup."""
        with patch.object(c, "FM_MAINS_CONNECTION_ASSET_ID", 171):
            await v2g.log_versions()

        calls = v2g.fm_client_app.set_asset_attributes.await_args_list
        assert len(calls) == 2

        # First call: write the versions to the Mains Connection.
        assert calls[0].args[0] == {
            "v2g-liberty-version": "1.2.3",
            "home-assistant-version": "2026.5.0",
        }
        assert calls[0].kwargs == {"asset_id": 171}

        # Second call: clear the stale attributes on the charger (fallback).
        assert calls[1].args[0] == {
            "v2g-liberty-version": None,
            "home-assistant-version": None,
        }
        assert calls[1].kwargs == {"asset_id": None}

    @pytest.mark.asyncio
    async def test_falls_back_to_charger_without_mains_connection(self, v2g):
        """No Mains Connection → single write (falls back to charger), no cleanup."""
        with patch.object(c, "FM_MAINS_CONNECTION_ASSET_ID", None):
            await v2g.log_versions()

        calls = v2g.fm_client_app.set_asset_attributes.await_args_list
        assert len(calls) == 1
        assert calls[0].args[0] == {
            "v2g-liberty-version": "1.2.3",
            "home-assistant-version": "2026.5.0",
        }
        assert calls[0].kwargs == {"asset_id": None}
