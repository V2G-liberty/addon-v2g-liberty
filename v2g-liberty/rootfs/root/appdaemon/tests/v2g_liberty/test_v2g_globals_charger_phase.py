"""Unit tests for the charger-phase setting in V2GLibertyGlobals.

A charger occupies one or more phases of the grid connection: a Wallbox
Quasar 1 sits on a single one, an EVtec BiDiPro on all three, and a 2-phase
charger on e.g. [2, 3]. Covers:

- normalise_charger_phases: the accepted shapes and everything rejected.
- __save_charger_phase: stores a normalised list, accepts the bare phase
  number older settings and the manual 1-phase selection supply, and refuses
  anything else loudly (result event *and* a log line).
- charger_phase_is_valid: list-aware, and tolerant of a legacy bare int.
"""

from unittest.mock import MagicMock, Mock

import pytest
from apps.v2g_liberty import constants as c
from apps.v2g_liberty.v2g_globals import V2GLibertyGlobals


@pytest.fixture
def log_mock():
    return Mock()


@pytest.fixture
def settings_manager_mock():
    """Fake settings manager with a real dict behind store_object/get_object."""
    mock = MagicMock()
    mock.objects = {}
    mock.store_object = Mock(
        side_effect=lambda key, value: mock.objects.__setitem__(key, value)
    )
    mock.get_object = Mock(side_effect=lambda key: mock.objects.get(key))
    return mock


@pytest.fixture
def globals_instance(log_mock, settings_manager_mock):
    instance = object.__new__(V2GLibertyGlobals)
    instance._V2GLibertyGlobals__log = log_mock
    instance.hass = MagicMock()
    instance.hass.fire_event = Mock()
    instance.v2g_settings = settings_manager_mock
    return instance


def _fire_event_kwargs(instance) -> dict:
    """The kwargs of the single fired save_charger_phase.result event."""
    calls = instance.hass.fire_event.call_args_list
    assert len(calls) == 1
    assert calls[0].args[0] == "save_charger_phase.result"
    return calls[0].kwargs


# ── normalise_charger_phases ──────────────────────────────────────────


@pytest.mark.parametrize(
    "value, expected",
    [
        (1, [1]),  # legacy setting / manual 1-phase selection
        (3, [3]),
        ([2], [2]),
        ([2, 3], [2, 3]),  # 2-phase charger
        ([3, 2], [2, 3]),  # order does not matter
        ([1, 2, 3], [1, 2, 3]),  # EVtec BiDiPro
    ],
)
def test_normalise_accepts_and_sorts_phase_sets(value, expected):
    assert V2GLibertyGlobals.normalise_charger_phases(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        0,
        4,
        -1,
        [],  # a charger is on at least one phase
        [1, 1],  # duplicates
        [1, 2, 3, 1],  # more entries than phases
        "1",  # a string is not a phase
        ["1", "2"],
        True,  # bool is an int subclass; not a phase
        [True],
        [1, None],
        {1: 2},
    ],
)
def test_normalise_rejects_unusable_values(value):
    assert V2GLibertyGlobals.normalise_charger_phases(value) is None


# ── __save_charger_phase ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_stores_all_three_phases_for_a_3_phase_charger(globals_instance):
    """The regression: [1, 2, 3] used to be refused, leaving the phase of the
    1-phase charger it replaced in place."""
    await globals_instance._V2GLibertyGlobals__save_charger_phase(
        None, {"connected_to_phase": [1, 2, 3]}, None
    )

    assert globals_instance.v2g_settings.objects["charger_phase"] == {
        "connected_to_phase": [1, 2, 3]
    }
    assert _fire_event_kwargs(globals_instance) == {}


@pytest.mark.asyncio
async def test_save_normalises_a_bare_phase_number(globals_instance):
    """The manual 1-phase selection sends a plain int; it is stored as a list."""
    await globals_instance._V2GLibertyGlobals__save_charger_phase(
        None, {"connected_to_phase": 2}, None
    )

    assert globals_instance.v2g_settings.objects["charger_phase"] == {
        "connected_to_phase": [2]
    }


@pytest.mark.asyncio
async def test_save_stores_a_two_phase_charger(globals_instance):
    await globals_instance._V2GLibertyGlobals__save_charger_phase(
        None, {"connected_to_phase": [3, 2]}, None
    )

    assert globals_instance.v2g_settings.objects["charger_phase"] == {
        "connected_to_phase": [2, 3]
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, 0, 4, [], [1, 1], "1"])
async def test_save_refuses_unusable_values_loudly(globals_instance, log_mock, value):
    """Nothing is stored, the UI is told why, and it is logged -- a silent
    refusal is how the wrong phase survived unnoticed."""
    await globals_instance._V2GLibertyGlobals__save_charger_phase(
        None, {"connected_to_phase": value}, None
    )

    assert "charger_phase" not in globals_instance.v2g_settings.objects
    assert "error" in _fire_event_kwargs(globals_instance)
    assert log_mock.call_count == 1
    assert log_mock.call_args.kwargs.get("level") == "WARNING"


# ── charger_phase_is_valid ────────────────────────────────────────────


@pytest.fixture
def phase_required(monkeypatch):
    """A configured 3-phase grid, so the charger phase is required."""
    monkeypatch.setattr(c, "GRID_PHASES", 3)
    monkeypatch.setattr(c, "GRID_CONSUMPTION_ENTITIES", ["sensor.l1"])


@pytest.mark.parametrize("stored", [[1, 2, 3], [2, 3], [2], 1])
def test_valid_accepts_every_stored_phase_shape(
    globals_instance, phase_required, stored, monkeypatch
):
    monkeypatch.setattr(c, "CHARGER_CONNECTED_TO_PHASE", stored)
    assert globals_instance.charger_phase_is_valid() is True


@pytest.mark.parametrize("stored", [None, 0, 4, []])
def test_valid_rejects_unset_or_broken_phase(
    globals_instance, phase_required, stored, monkeypatch
):
    monkeypatch.setattr(c, "CHARGER_CONNECTED_TO_PHASE", stored)
    assert globals_instance.charger_phase_is_valid() is False


def test_valid_when_phase_is_not_required(globals_instance, monkeypatch):
    """On a 1-phase grid the phase is not required, so anything goes."""
    monkeypatch.setattr(c, "GRID_PHASES", 1)
    monkeypatch.setattr(c, "CHARGER_CONNECTED_TO_PHASE", None)
    assert globals_instance.charger_phase_is_valid() is True
