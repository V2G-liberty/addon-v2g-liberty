"""The aggregate sensors in the flex-context.

FlexMeasures stores the scheduled aggregate consumption and production on the
sensors named in the flex-context. Their IDs are only known once the grid
connection has been provisioned in FlexMeasures, which runs in the background
after the context is first built, and not at all without a grid connection.
A `{"sensor": None}` reference makes FlexMeasures reject the whole schedule
request ("Field may not be null"), so no schedule comes in at all.
"""

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from apps.v2g_liberty import constants as c
from apps.v2g_liberty.v2g_globals import V2GLibertyGlobals

AGGREGATE_CONSUMPTION_SENSOR_ID = 667
AGGREGATE_PRODUCTION_SENSOR_ID = 668

# Everything __provision_grid_assets reads or publishes, restored per test.
_PROVISIONING_CONSTANTS = (
    "GRID_PHASES",
    "GRID_CAPACITY_PER_PHASE",
    "GRID_CONSUMPTION_ENTITIES",
    "FM_MAINS_CONNECTION_ASSET_ID",
    "FM_GRID_CONSUMPTION_SENSOR_IDS",
    "FM_GRID_PRODUCTION_SENSOR_IDS",
    "FM_RESIDENTIAL_LOAD_SENSOR_IDS",
    "FM_AGGREGATE_POWER_SENSOR_ID",
    "FM_AGGREGATE_CONSUMPTION_SENSOR_ID",
    "FM_AGGREGATE_PRODUCTION_SENSOR_ID",
    "FM_EMS_STATUS_SENSOR_ID",
)


@pytest.fixture
def globals_instance(monkeypatch):
    for name in _PROVISIONING_CONSTANTS:
        monkeypatch.setattr(c, name, getattr(c, name, None), raising=False)
    monkeypatch.setattr(c, "OPTIMISATION_MODE", "price")
    monkeypatch.setattr(c, "FM_PRICE_CONSUMPTION_SENSOR_ID", 58)
    monkeypatch.setattr(c, "FM_PRICE_PRODUCTION_SENSOR_ID", 70)
    monkeypatch.setattr(c, "FM_OPTIMISATION_CONTEXT", {})
    monkeypatch.setattr(c, "FM_AGGREGATE_CONSUMPTION_SENSOR_ID", None)
    monkeypatch.setattr(c, "FM_AGGREGATE_PRODUCTION_SENSOR_ID", None)

    instance = object.__new__(V2GLibertyGlobals)
    instance._V2GLibertyGlobals__log = Mock()
    # The contract and schedule settings are initialised.
    instance._V2GLibertyGlobals__process_setting = AsyncMock(return_value=True)
    return instance


async def _build_context(instance):
    await instance._V2GLibertyGlobals__set_fm_optimisation_context()
    return c.FM_OPTIMISATION_CONTEXT


@pytest.mark.asyncio
async def test_unknown_aggregate_sensors_are_left_out(globals_instance):
    context = await _build_context(globals_instance)

    assert "aggregate-consumption" not in context
    assert "aggregate-production" not in context
    # The rest of the context is still there.
    assert context["consumption-price"] == {"sensor": 58}


@pytest.mark.asyncio
async def test_known_aggregate_sensors_are_included(globals_instance, monkeypatch):
    monkeypatch.setattr(
        c, "FM_AGGREGATE_CONSUMPTION_SENSOR_ID", AGGREGATE_CONSUMPTION_SENSOR_ID
    )
    monkeypatch.setattr(
        c, "FM_AGGREGATE_PRODUCTION_SENSOR_ID", AGGREGATE_PRODUCTION_SENSOR_ID
    )

    context = await _build_context(globals_instance)

    assert context["aggregate-consumption"] == {
        "sensor": AGGREGATE_CONSUMPTION_SENSOR_ID
    }
    assert context["aggregate-production"] == {"sensor": AGGREGATE_PRODUCTION_SENSOR_ID}


@pytest.mark.asyncio
async def test_provisioning_the_grid_connection_rebuilds_the_context(
    globals_instance, monkeypatch
):
    """At start-up the context is built before provisioning, without the
    aggregate sensors. Provisioning must rebuild it, or they never arrive."""
    await _build_context(globals_instance)
    assert "aggregate-consumption" not in c.FM_OPTIMISATION_CONTEXT

    monkeypatch.setattr(c, "GRID_PHASES", 1)
    monkeypatch.setattr(c, "GRID_CAPACITY_PER_PHASE", 25)
    monkeypatch.setattr(c, "GRID_CONSUMPTION_ENTITIES", ["sensor.l1"])
    sensor_ids = {
        "Aggregate Consumption": AGGREGATE_CONSUMPTION_SENSOR_ID,
        "Aggregate Production": AGGREGATE_PRODUCTION_SENSOR_ID,
    }
    fm = MagicMock()
    fm._asset_id = 38
    fm.ensure_asset = AsyncMock(return_value=206)
    fm.ensure_sensor = AsyncMock(side_effect=lambda **kw: sensor_ids.get(kw["name"], 1))
    fm.client.update_asset = AsyncMock()
    globals_instance.fm_client_app = fm

    await globals_instance._V2GLibertyGlobals__provision_grid_assets()

    assert c.FM_OPTIMISATION_CONTEXT["aggregate-consumption"] == {
        "sensor": AGGREGATE_CONSUMPTION_SENSOR_ID
    }
    assert c.FM_OPTIMISATION_CONTEXT["aggregate-production"] == {
        "sensor": AGGREGATE_PRODUCTION_SENSOR_ID
    }
