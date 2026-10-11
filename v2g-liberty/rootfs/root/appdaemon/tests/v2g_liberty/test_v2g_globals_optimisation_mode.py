"""Switching the optimisation mode takes effect at that switch.

The flex-context that goes along with every schedule request names the price
sensors (mode "price") or the emissions sensor (any other mode). It used to be
rebuilt before the new mode was read, so it kept the previous mode: switching
to emissions still optimised on price, and switching back to price optimised
on emissions, until a restart rebuilt the context.
"""

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from apps.v2g_liberty import constants as c
from apps.v2g_liberty.v2g_globals import V2GLibertyGlobals

PRICE_CONSUMPTION_SENSOR_ID = 58
PRICE_PRODUCTION_SENSOR_ID = 70
EMISSIONS_SENSOR_ID = 66


@pytest.fixture
def globals_instance(monkeypatch):
    monkeypatch.setattr(
        c, "FM_PRICE_CONSUMPTION_SENSOR_ID", PRICE_CONSUMPTION_SENSOR_ID
    )
    monkeypatch.setattr(c, "FM_PRICE_PRODUCTION_SENSOR_ID", PRICE_PRODUCTION_SENSOR_ID)
    monkeypatch.setattr(c, "FM_EMISSIONS_SENSOR_ID", EMISSIONS_SENSOR_ID)
    monkeypatch.setattr(c, "FM_OPTIMISATION_CONTEXT", {})

    instance = object.__new__(V2GLibertyGlobals)
    instance._V2GLibertyGlobals__log = Mock()
    instance.hass = MagicMock()
    instance.v2g_main_app = MagicMock()
    instance.v2g_main_app.kick_off_v2g_liberty = AsyncMock()

    stored = {}

    def store(entity_id, value):
        stored[entity_id] = value

    async def process(setting_object):
        if setting_object is instance.SETTING_OPTIMISATION_MODE:
            return stored["input_select.optimisation_mode"]
        # The contract and schedule settings are initialised.
        return True

    instance._V2GLibertyGlobals__store_setting = store
    instance._V2GLibertyGlobals__process_setting = process
    return instance


async def _switch_to(instance, mode):
    await instance._V2GLibertyGlobals__save_setting(
        "save_setting", {"entity": "input_select.optimisation_mode", "value": mode}, {}
    )


@pytest.mark.asyncio
async def test_switching_to_emissions_optimises_on_emissions(
    globals_instance, monkeypatch
):
    monkeypatch.setattr(c, "OPTIMISATION_MODE", "price")

    await _switch_to(globals_instance, "emission")

    assert c.FM_OPTIMISATION_CONTEXT["consumption-price"] == {
        "sensor": EMISSIONS_SENSOR_ID
    }
    assert c.FM_OPTIMISATION_CONTEXT["production-price"] == {
        "sensor": EMISSIONS_SENSOR_ID
    }


@pytest.mark.asyncio
async def test_switching_back_to_price_optimises_on_price(
    globals_instance, monkeypatch
):
    monkeypatch.setattr(c, "OPTIMISATION_MODE", "emission")

    await _switch_to(globals_instance, "price")

    assert c.FM_OPTIMISATION_CONTEXT["consumption-price"] == {
        "sensor": PRICE_CONSUMPTION_SENSOR_ID
    }
    assert c.FM_OPTIMISATION_CONTEXT["production-price"] == {
        "sensor": PRICE_PRODUCTION_SENSOR_ID
    }


@pytest.mark.asyncio
async def test_the_new_schedule_is_asked_with_the_new_context(
    globals_instance, monkeypatch
):
    """The kick-off asks FlexMeasures for a schedule; by then the context must
    already carry the new mode."""
    monkeypatch.setattr(c, "OPTIMISATION_MODE", "price")
    seen = {}

    async def kick_off():
        seen["context"] = dict(c.FM_OPTIMISATION_CONTEXT)

    globals_instance.v2g_main_app.kick_off_v2g_liberty = AsyncMock(side_effect=kick_off)

    await _switch_to(globals_instance, "emission")

    assert seen["context"]["consumption-price"] == {"sensor": EMISSIONS_SENSOR_ID}
