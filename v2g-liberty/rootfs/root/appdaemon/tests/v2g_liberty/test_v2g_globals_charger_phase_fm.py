"""The charger's phase reaches its FlexMeasures asset.

Solar panels have carried their phase as the `connected_to_phase` attribute
of their FlexMeasures asset from the start; the charger's phase stayed local,
used only to split the household load per phase. It is now written to the
charger asset too, as a list (a charger can take one phase or several):
whenever the local setting changes, and after the grid connection is
provisioned at start-up or on a FlexMeasures reconnect, so an existing
install is brought in line without a save.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from apps.v2g_liberty import constants as c
from apps.v2g_liberty.v2g_globals import V2GLibertyGlobals

_DETECTOR = "apps.v2g_liberty.v2g_globals.ChargerPhaseDetector"


async def _let_background_tasks_run():
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.fixture
def globals_instance(monkeypatch):
    monkeypatch.setattr(c, "CHARGER_CONNECTED_TO_PHASE", None)
    monkeypatch.setattr(c, "GRID_CONSUMPTION_ENTITIES", ["sensor.l1", "sensor.l2"])

    settings = MagicMock()
    settings.objects = {}
    settings.store_object = Mock(
        side_effect=lambda key, value: settings.objects.__setitem__(key, value)
    )
    settings.get_object = Mock(side_effect=lambda key: settings.objects.get(key))
    settings.store_setting = Mock()

    instance = object.__new__(V2GLibertyGlobals)
    instance._V2GLibertyGlobals__log = Mock()
    instance.hass = MagicMock()
    instance.hass.fire_event = Mock()
    instance.v2g_settings = settings
    instance.fm_client_app = MagicMock()
    instance.fm_client_app.set_asset_attributes = AsyncMock()
    instance.evse_client_app = MagicMock()  # handed to the (patched) detector
    return instance


def _written(instance):
    return [
        call.args[0]
        for call in instance.fm_client_app.set_asset_attributes.await_args_list
    ]


@pytest.mark.asyncio
async def test_a_detected_phase_is_written_to_the_charger_asset(globals_instance):
    detector = MagicMock()
    detector.run = AsyncMock(
        return_value={
            "success": True,
            "connected_to_phase": [2],
            "detected_at": "2026-10-12T17:00:00+02:00",
        }
    )
    with patch(_DETECTOR, return_value=detector):
        await globals_instance._V2GLibertyGlobals__detect_charger_phase("event", {}, {})
    await _let_background_tasks_run()

    assert _written(globals_instance) == [{"connected_to_phase": [2]}]


@pytest.mark.asyncio
async def test_a_failed_detection_writes_nothing(globals_instance):
    detector = MagicMock()
    detector.run = AsyncMock(return_value={"success": False, "error": "no change"})
    with patch(_DETECTOR, return_value=detector):
        await globals_instance._V2GLibertyGlobals__detect_charger_phase("event", {}, {})
    await _let_background_tasks_run()

    assert _written(globals_instance) == []


@pytest.mark.asyncio
async def test_an_older_bare_phase_number_is_written_as_a_list(globals_instance):
    """Settings from before multi-phase chargers hold a bare number."""
    c.CHARGER_CONNECTED_TO_PHASE = 3

    await globals_instance._V2GLibertyGlobals__write_charger_phase_to_fm()

    assert _written(globals_instance) == [{"connected_to_phase": [3]}]


class TestAtStartUp:
    """__provision_grid_assets_best_effort runs at start-up and when
    FlexMeasures becomes reachable again."""

    @pytest.fixture
    def provisioning(self, globals_instance):
        globals_instance._grid_prov_in_flight = False
        globals_instance._FM_PROVISION_TIMEOUT = 5
        mock = AsyncMock()
        globals_instance._V2GLibertyGlobals__provision_grid_assets = mock
        return mock

    @pytest.mark.asyncio
    async def test_the_phase_is_written_after_provisioning(
        self, globals_instance, provisioning
    ):
        c.CHARGER_CONNECTED_TO_PHASE = [2]

        await globals_instance._V2GLibertyGlobals__provision_grid_assets_best_effort()

        provisioning.assert_awaited_once()
        assert _written(globals_instance) == [{"connected_to_phase": [2]}]

    @pytest.mark.asyncio
    async def test_nothing_is_written_when_provisioning_failed(
        self, globals_instance, provisioning
    ):
        provisioning.side_effect = RuntimeError("FlexMeasures is not connected.")

        await globals_instance._V2GLibertyGlobals__provision_grid_assets_best_effort()

        assert _written(globals_instance) == []
        assert globals_instance._grid_prov_in_flight is False

    @pytest.mark.asyncio
    async def test_nothing_is_written_without_a_grid_connection(
        self, globals_instance, provisioning
    ):
        """The phase only means something on a configured grid connection."""
        c.GRID_CONSUMPTION_ENTITIES = []

        await globals_instance._V2GLibertyGlobals__provision_grid_assets_best_effort()

        assert _written(globals_instance) == []
