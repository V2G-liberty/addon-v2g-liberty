"""Unit tests for the charger-phase setting in V2GLibertyGlobals.

A charger occupies one or more phases of the grid connection: a Wallbox
Quasar 1 sits on a single one, an EVtec BiDiPro on all three, and a 2-phase
charger on e.g. [2, 3]. Covers:

- normalise_charger_phases: the accepted shapes and everything rejected.
- charger_phase_is_valid: list-aware, and tolerant of a legacy bare int.

Storing a phase is part of saving the charger settings; that is covered in
test_v2g_globals_charger_settings.py.
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
