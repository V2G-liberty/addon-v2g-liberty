"""A setting is kept within its limits, both of them.

__check_and_convert_value clamps a value to the setting's min and max and
says so in a memo. The max was applied to the original value instead of the
one already clamped to the min, which undid the min: a charger power below
the 1380 W minimum passed unchanged.
"""

from unittest.mock import MagicMock, Mock

import pytest
from apps.v2g_liberty.v2g_globals import V2GLibertyGlobals

_POWER = {
    "entity_name": "charger_max_charging_power",
    "entity_type": "input_number",
    "value_type": "int",
    "min": 1380,
    "max": 5600,
}
_FACTOR = {
    "entity_name": "some_factor",
    "entity_type": "input_number",
    "value_type": "float",
    "min": 0.5,
    "max": 1.0,
}


@pytest.fixture
def globals_instance():
    instance = object.__new__(V2GLibertyGlobals)
    instance._V2GLibertyGlobals__log = Mock()
    instance.notifier = MagicMock()
    return instance


async def _convert(instance, setting, value):
    return await instance._V2GLibertyGlobals__check_and_convert_value(setting, value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setting, value, expected",
    [
        (_POWER, 1000, 1380),
        (_POWER, 5700, 5600),
        (_FACTOR, 0.2, 0.5),
        (_FACTOR, 1.4, 1.0),
    ],
    ids=["int-below-min", "int-above-max", "float-below-min", "float-above-max"],
)
async def test_a_value_outside_the_limits_is_clamped_and_reported(
    globals_instance, setting, value, expected
):
    result, changed = await _convert(globals_instance, setting, value)

    assert result == expected
    assert changed is True
    globals_instance.notifier.post_sticky_memo.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setting, value", [(_POWER, 3700), (_FACTOR, 0.85)], ids=["int", "float"]
)
async def test_a_value_within_the_limits_is_left_alone(
    globals_instance, setting, value
):
    result, changed = await _convert(globals_instance, setting, value)

    assert result == value
    assert changed is False
    globals_instance.notifier.post_sticky_memo.assert_not_called()
