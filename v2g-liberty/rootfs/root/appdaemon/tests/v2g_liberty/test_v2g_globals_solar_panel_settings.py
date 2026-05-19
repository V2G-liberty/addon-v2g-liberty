"""Unit tests for solar panel CRUD handlers in V2GLibertyGlobals.

Covers Fase 2 tasks 13-16 of the grid/PV monitoring plan:
- 13: save_solar_panel (insert/update, sp_<n> id generation, persistence)
- 14: get_solar_panels (empty, populated)
- 15: delete_solar_panel (existing, missing, unknown id)
- 16: validation rules (name, power_entity_id, phases, connected_to_phase,
      curtailable/curtail_entity_id)
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
    """Fake settings manager whose get_object mirrors the real dict|list filter."""
    mock = Mock()
    store: dict = {}

    def fake_store_object(key, data):
        store[key] = data

    def fake_get_object(key, default=None):
        value = store.get(key, None)
        if isinstance(value, (dict, list)):
            return value
        return default

    mock.store_object = Mock(side_effect=fake_store_object)
    mock.get_object = Mock(side_effect=fake_get_object)
    return mock


@pytest.fixture
def hass_mock():
    mock = Mock()
    mock.fire_event = Mock()
    return mock


@pytest.fixture
def fm_client_mock():
    mock = MagicMock()
    mock.client = None
    return mock


@pytest.fixture
def globals_instance(log_mock, settings_manager_mock, hass_mock, fm_client_mock):
    instance = object.__new__(V2GLibertyGlobals)
    instance._V2GLibertyGlobals__log = log_mock
    instance.v2g_settings = settings_manager_mock
    instance.hass = hass_mock
    instance.fm_client_app = fm_client_mock
    return instance


@pytest.fixture(autouse=True)
def reset_solar_panels_constant():
    """Reset c.SOLAR_PANELS around each test to avoid module-state leakage."""
    c.SOLAR_PANELS = []
    yield
    c.SOLAR_PANELS = []


def _valid_panel_payload(**overrides):
    """Return a valid 1-phase panel payload, suitable for a 1-phase grid."""
    payload = {
        "name": "South",
        "phases": 1,
        "power_entity_id": "sensor.pv_south_power",
        "peak_power_wp": 4000,
        "curtailable": False,
    }
    payload.update(overrides)
    return payload


# ── Taak 14: get_solar_panels ─────────────────────────────────────────


class TestGetSolarPanels:
    @pytest.mark.asyncio
    async def test_empty_when_no_panels_configured(self, globals_instance, hass_mock):
        await globals_instance._V2GLibertyGlobals__get_solar_panels("event", {}, {})

        hass_mock.fire_event.assert_called_once_with(
            "get_solar_panels.result", solar_panels=[]
        )

    @pytest.mark.asyncio
    async def test_returns_configured_panels(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        panels = [
            {
                "id": "sp_1",
                "name": "South",
                "phases": 1,
                "power_entity_id": "sensor.s",
            },
            {
                "id": "sp_2",
                "name": "North",
                "phases": 1,
                "power_entity_id": "sensor.n",
            },
        ]
        settings_manager_mock.store_object("solar_panels", panels)
        # Reset to ignore the setup call in fire_event history
        hass_mock.fire_event.reset_mock()

        await globals_instance._V2GLibertyGlobals__get_solar_panels("event", {}, {})

        hass_mock.fire_event.assert_called_once_with(
            "get_solar_panels.result", solar_panels=panels
        )


# ── Taak 13: save_solar_panel — insert ────────────────────────────────


class TestSaveSolarPanelInsert:
    @pytest.mark.asyncio
    async def test_first_new_panel_gets_sp_1(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        c.GRID_PHASES = 1
        payload = _valid_panel_payload()

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", payload, {}
        )

        expected = {**payload, "id": "sp_1"}
        assert settings_manager_mock.store_object.call_args_list[-1] == (
            ("solar_panels", [expected]),
        )
        assert c.SOLAR_PANELS == [expected]
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result", solar_panels=[expected]
        )

    @pytest.mark.asyncio
    async def test_second_new_panel_gets_sp_2(
        self, globals_instance, settings_manager_mock
    ):
        c.GRID_PHASES = 1
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "South",
                    "phases": 1,
                    "power_entity_id": "sensor.s",
                }
            ],
        )

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(name="North"), {}
        )

        new_list = settings_manager_mock.store_object.call_args_list[-1][0][1]
        assert len(new_list) == 2
        assert new_list[1]["id"] == "sp_2"
        assert new_list[1]["name"] == "North"

    @pytest.mark.asyncio
    async def test_never_reuses_id_after_lower_one_was_removed(
        self, globals_instance, settings_manager_mock
    ):
        """When sp_1 is missing but sp_2 exists, new panel must be sp_3 (max+1)."""
        c.GRID_PHASES = 1
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_2",
                    "name": "Existing",
                    "phases": 1,
                    "power_entity_id": "sensor.x",
                }
            ],
        )

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(), {}
        )

        new_list = settings_manager_mock.store_object.call_args_list[-1][0][1]
        assert new_list[-1]["id"] == "sp_3"


# ── Taak 13: save_solar_panel — update ────────────────────────────────


class TestSaveSolarPanelUpdate:
    @pytest.mark.asyncio
    async def test_update_existing_merges_fields(
        self, globals_instance, settings_manager_mock
    ):
        c.GRID_PHASES = 1
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "Old",
                    "phases": 1,
                    "power_entity_id": "sensor.x",
                    "peak_power_wp": 3000,
                }
            ],
        )

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", {"id": "sp_1", "name": "New"}, {}
        )

        new_list = settings_manager_mock.store_object.call_args_list[-1][0][1]
        assert new_list[0]["name"] == "New"  # updated
        assert new_list[0]["phases"] == 1  # retained
        assert new_list[0]["power_entity_id"] == "sensor.x"  # retained
        assert new_list[0]["peak_power_wp"] == 3000  # retained

    @pytest.mark.asyncio
    async def test_update_unknown_id_returns_error(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        c.GRID_PHASES = 1

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", {"id": "sp_99", "name": "Ghost"}, {}
        )

        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result", error="Solar panel 'sp_99' not found"
        )


# ── Taak 15: delete_solar_panel ───────────────────────────────────────


class TestDeleteSolarPanel:
    @pytest.mark.asyncio
    async def test_delete_existing_panel(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "A",
                    "phases": 1,
                    "power_entity_id": "sensor.a",
                },
                {
                    "id": "sp_2",
                    "name": "B",
                    "phases": 1,
                    "power_entity_id": "sensor.b",
                },
            ],
        )

        await globals_instance._V2GLibertyGlobals__delete_solar_panel(
            "event", {"id": "sp_1"}, {}
        )

        new_list = settings_manager_mock.store_object.call_args_list[-1][0][1]
        assert len(new_list) == 1
        assert new_list[0]["id"] == "sp_2"
        hass_mock.fire_event.assert_called_with(
            "delete_solar_panel.result", solar_panels=new_list
        )

    @pytest.mark.asyncio
    async def test_delete_without_id_returns_error(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        await globals_instance._V2GLibertyGlobals__delete_solar_panel("event", {}, {})

        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "delete_solar_panel.result", error="Missing 'id' field"
        )

    @pytest.mark.asyncio
    async def test_delete_unknown_id_returns_error(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "A",
                    "phases": 1,
                    "power_entity_id": "sensor.a",
                }
            ],
        )

        await globals_instance._V2GLibertyGlobals__delete_solar_panel(
            "event", {"id": "sp_99"}, {}
        )

        # Only the setup store call, no delete-side store call
        assert settings_manager_mock.store_object.call_count == 1
        hass_mock.fire_event.assert_called_with(
            "delete_solar_panel.result", error="Solar panel 'sp_99' not found"
        )


# ── Taak 16: validation ───────────────────────────────────────────────


class TestSolarPanelValidation:
    @pytest.mark.asyncio
    async def test_empty_name_rejected(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(name=""), {}
        )
        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result", error="Name must be a non-empty string"
        )

    @pytest.mark.asyncio
    async def test_missing_name_rejected(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        c.GRID_PHASES = 1
        payload = _valid_panel_payload()
        payload.pop("name")

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", payload, {}
        )
        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result", error="Name must be a non-empty string"
        )

    @pytest.mark.asyncio
    async def test_missing_power_entity_rejected(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(power_entity_id=""), {}
        )
        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result", error="power_entity_id is required"
        )

    @pytest.mark.asyncio
    async def test_invalid_phases_value_rejected(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        c.GRID_PHASES = 3
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(phases=2, connected_to_phase=1), {}
        )
        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result", error="phases must be 1 or 3"
        )

    @pytest.mark.asyncio
    async def test_phases_exceeds_grid_rejected(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(phases=3), {}
        )
        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result",
            error="phases (3) cannot exceed grid phases (1)",
        )

    @pytest.mark.asyncio
    async def test_1phase_on_3phase_grid_without_connected_phase_rejected(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        c.GRID_PHASES = 3
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(phases=1), {}
        )
        settings_manager_mock.store_object.assert_not_called()
        call = hass_mock.fire_event.call_args_list[-1]
        assert "connected_to_phase" in call.kwargs["error"]

    @pytest.mark.asyncio
    async def test_1phase_on_3phase_grid_with_valid_connected_phase_accepted(
        self, globals_instance, settings_manager_mock
    ):
        c.GRID_PHASES = 3
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(phases=1, connected_to_phase=2), {}
        )
        settings_manager_mock.store_object.assert_called_once()
        stored = settings_manager_mock.store_object.call_args_list[-1][0][1]
        assert stored[0]["connected_to_phase"] == 2

    @pytest.mark.asyncio
    async def test_1phase_on_1phase_grid_no_connected_phase_required(
        self, globals_instance, settings_manager_mock
    ):
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(phases=1), {}
        )
        settings_manager_mock.store_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_curtailable_without_curtail_entity_rejected(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(curtailable=True), {}
        )
        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result",
            error="curtail_entity_id is required when curtailable is True",
        )

    @pytest.mark.asyncio
    async def test_curtailable_with_curtail_entity_accepted(
        self, globals_instance, settings_manager_mock
    ):
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event",
            _valid_panel_payload(
                curtailable=True, curtail_entity_id="sensor.pv_curtail"
            ),
            {},
        )
        settings_manager_mock.store_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_curtailable_false_no_curtail_entity_required(
        self, globals_instance, settings_manager_mock
    ):
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(curtailable=False), {}
        )
        settings_manager_mock.store_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_validation_uses_merged_payload(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Partial update keeps stored fields; validation passes if merged result is valid."""
        c.GRID_PHASES = 1
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "South",
                    "phases": 1,
                    "power_entity_id": "sensor.x",
                }
            ],
        )

        # Partial update: only change peak_power_wp; stored name/phases/entity
        # remain — merged payload must validate successfully.
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", {"id": "sp_1", "peak_power_wp": 5500}, {}
        )

        # Stored a second time (after the setup call) with the merged record
        assert settings_manager_mock.store_object.call_count == 2
        stored = settings_manager_mock.store_object.call_args_list[-1][0][1]
        assert stored[0]["peak_power_wp"] == 5500
        assert stored[0]["name"] == "South"

    @pytest.mark.asyncio
    async def test_update_validation_rejects_invalid_merged_payload(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Update that would invalidate the merged record is rejected."""
        c.GRID_PHASES = 1
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "South",
                    "phases": 1,
                    "power_entity_id": "sensor.x",
                }
            ],
        )

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", {"id": "sp_1", "name": ""}, {}
        )

        # Only the setup store call — the bad update did not persist
        assert settings_manager_mock.store_object.call_count == 1
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result", error="Name must be a non-empty string"
        )
