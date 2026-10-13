"""A setting whose Home Assistant entity does not exist (yet).

The add-on copies its package to Home Assistant on every start but does not
restart it, so the boot right after an update that adds a helper runs without
that entity. `hass.get_state()` then returns None, and reading from it used to
abort `kick_off_settings()` -- and with it the whole app: no charger, no
schedules, no charging. The stored settings are all that is really needed, so
only what is read FROM the entity is skipped.
"""

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from apps.v2g_liberty.v2g_globals import V2GLibertyGlobals

_SETTING = {
    "entity_name": "charger_type",
    "entity_type": "input_text",
    "value_type": "str",
    "factory_default": None,
}


@pytest.fixture
def log_mock():
    return Mock()


@pytest.fixture
def globals_instance(log_mock):
    instance = object.__new__(V2GLibertyGlobals)
    instance._V2GLibertyGlobals__log = log_mock
    instance.hass = MagicMock()
    instance.hass.get_state = AsyncMock(return_value=None)  # unknown entity
    instance.hass.set_state = AsyncMock()
    instance.v2g_settings = MagicMock()
    instance.v2g_settings.get = Mock(return_value="evtec-bidi-pro-10")
    instance.v2g_settings.store_setting = Mock()
    # Class-level, so it carries over between tests: start each test clean.
    V2GLibertyGlobals._missing_entities = set()
    return instance


def _process(instance, setting=None):
    return instance._V2GLibertyGlobals__process_setting(setting or _SETTING)


@pytest.mark.asyncio
async def test_unknown_entity_returns_the_stored_value(globals_instance):
    """The regression: this used to raise and abort the whole initialisation."""
    assert await _process(globals_instance) == "evtec-bidi-pro-10"


@pytest.mark.asyncio
async def test_unknown_entity_is_reported_once(globals_instance, log_mock):
    await _process(globals_instance)
    await _process(globals_instance)

    warnings = [
        c
        for c in log_mock.call_args_list
        if c.kwargs.get("level") == "WARNING"
        and "input_text.charger_type" in str(c.args[0])
    ]
    assert len(warnings) == 1
    assert "Restart Home Assistant" in warnings[0].args[0]


@pytest.mark.asyncio
async def test_unknown_entity_without_stored_value_or_default(globals_instance):
    """No entity, nothing stored and no factory default: return empty, not raise."""
    globals_instance.v2g_settings.get = Mock(return_value=None)

    assert await _process(globals_instance) == ""
    globals_instance.v2g_settings.store_setting.assert_not_called()


@pytest.mark.asyncio
async def test_known_entity_still_reads_its_attributes(globals_instance, log_mock):
    """The normal path is untouched: mode and unit still come from the entity."""
    globals_instance.hass.get_state = AsyncMock(
        return_value={
            "state": "evtec-bidi-pro-10",
            "attributes": {"unit_of_measurement": "kWh"},
        }
    )
    globals_instance._V2GLibertyGlobals__write_setting_to_ha = AsyncMock()
    globals_instance._V2GLibertyGlobals__check_and_convert_value = AsyncMock(
        return_value=("evtec-bidi-pro-10", False)
    )

    assert await _process(globals_instance) == "evtec-bidi-pro-10"
    assert any("kWh." in str(c.args[0]) for c in log_mock.call_args_list)
    assert not any(c.kwargs.get("level") == "WARNING" for c in log_mock.call_args_list)
