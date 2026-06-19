"""Unit tests for solar panel CRUD handlers in V2GLibertyGlobals.

Covers Fase 2 tasks 13-16 of the grid/PV monitoring plan:
- 13: save_solar_panel (insert/update, sp_<n> id generation, persistence)
- 14: get_solar_panels (empty, populated)
- 15: delete_solar_panel (existing, missing, unknown id)
- 16: validation rules (name, power_entity_id, phases, connected_to_phase,
      curtailable/curtail_entity_id)
"""

from unittest.mock import AsyncMock, MagicMock, Mock
import pytest

from apps.v2g_liberty import constants as c
from apps.v2g_liberty.v2g_globals import V2GLibertyGlobals

# Sentinel values returned by the happy-path fm_client mock; tests that
# care about the full saved record assert these in fm_asset_id / fm_sensor_id.
_FM_ASSET_ID = 101
_FM_SENSOR_ID = 201
_FM_MAINS_CONNECTION_ID = 100


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
    """Happy-path FM client: connected, ensure_* return sentinel ids.

    Tests that need to exercise FM failure paths can override ``client``,
    or set ``ensure_asset`` / ``ensure_sensor`` to raise.
    """
    mock = MagicMock()
    mock.client = Mock()  # truthy → "connected"
    mock.client.update_asset = AsyncMock()
    mock.ensure_asset = AsyncMock(return_value=_FM_ASSET_ID)
    mock.ensure_sensor = AsyncMock(return_value=_FM_SENSOR_ID)
    mock.mark_asset_deleted = AsyncMock(return_value=None)
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
    """Reset module-level FM/SOLAR state around each test."""
    c.SOLAR_PANELS = []
    c.FM_MAINS_CONNECTION_ASSET_ID = _FM_MAINS_CONNECTION_ID
    yield
    c.SOLAR_PANELS = []
    c.FM_MAINS_CONNECTION_ASSET_ID = None


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
        c.GRID_PHASES = 1
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

        # Each panel is annotated with an inconsistency_reason field; here
        # both panels fit a 1-phase grid so the reason is None.
        expected = [{**p, "inconsistency_reason": None} for p in panels]
        hass_mock.fire_event.assert_called_once_with(
            "get_solar_panels.result", solar_panels=expected
        )

    @pytest.mark.asyncio
    async def test_phases_greater_than_grid_flagged(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """3-phase panel on a now-1-phase grid is flagged inconsistent."""
        c.GRID_PHASES = 1
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "Roof",
                    "phases": 3,
                    "power_entity_id": "sensor.r",
                    "peak_power_wp": 4000,
                }
            ],
        )
        hass_mock.fire_event.reset_mock()

        await globals_instance._V2GLibertyGlobals__get_solar_panels("event", {}, {})

        sent = hass_mock.fire_event.call_args.kwargs["solar_panels"][0]
        assert sent["inconsistency_reason"] is not None
        assert "3-phase" in sent["inconsistency_reason"]
        assert "1-phase" in sent["inconsistency_reason"]

    @pytest.mark.asyncio
    async def test_missing_connected_to_phase_on_3phase_grid_flagged(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """1-phase panel on 3-phase grid without connected_to_phase is flagged."""
        c.GRID_PHASES = 3
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "Side",
                    "phases": 1,
                    "power_entity_id": "sensor.s",
                    "peak_power_wp": 4000,
                    # connected_to_phase absent — typical after grid was 1-phase
                }
            ],
        )
        hass_mock.fire_event.reset_mock()

        await globals_instance._V2GLibertyGlobals__get_solar_panels("event", {}, {})

        sent = hass_mock.fire_event.call_args.kwargs["solar_panels"][0]
        assert sent["inconsistency_reason"] is not None
        assert "connected_to_phase" in sent["inconsistency_reason"]

    @pytest.mark.asyncio
    async def test_consistent_panels_not_flagged(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """All-fitting panels have inconsistency_reason=None."""
        c.GRID_PHASES = 3
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "Side",
                    "phases": 1,
                    "connected_to_phase": 2,
                    "power_entity_id": "sensor.s",
                    "peak_power_wp": 4000,
                },
                {
                    "id": "sp_2",
                    "name": "Roof",
                    "phases": 3,
                    "power_entity_id": "sensor.r",
                    "peak_power_wp": 5000,
                },
            ],
        )
        hass_mock.fire_event.reset_mock()

        await globals_instance._V2GLibertyGlobals__get_solar_panels("event", {}, {})

        for sent in hass_mock.fire_event.call_args.kwargs["solar_panels"]:
            assert sent["inconsistency_reason"] is None


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

        expected = {
            **payload,
            "id": "sp_1",
            "fm_asset_id": _FM_ASSET_ID,
            "fm_sensor_id": _FM_SENSOR_ID,
        }
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

    @pytest.mark.asyncio
    async def test_duplicate_name_rejected(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """A new panel cannot reuse the name of an existing one."""
        c.GRID_PHASES = 1
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "South",
                    "phases": 1,
                    "power_entity_id": "sensor.s",
                    "peak_power_wp": 4000,
                }
            ],
        )

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(name="South"), {}
        )

        # Only the setup store call — the duplicate was not saved.
        assert settings_manager_mock.store_object.call_count == 1
        call = hass_mock.fire_event.call_args
        assert "already exists" in call.kwargs["error"]
        assert "South" in call.kwargs["error"]

    @pytest.mark.asyncio
    async def test_rename_to_another_panels_name_rejected(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Updating a panel to take another panel's name is blocked."""
        c.GRID_PHASES = 1
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "South",
                    "phases": 1,
                    "power_entity_id": "sensor.s",
                    "peak_power_wp": 4000,
                },
                {
                    "id": "sp_2",
                    "name": "North",
                    "phases": 1,
                    "power_entity_id": "sensor.n",
                    "peak_power_wp": 4000,
                },
            ],
        )

        # Try to rename sp_2 (North) to South — collides with sp_1.
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", {"id": "sp_2", "name": "South"}, {}
        )

        # Only the setup store call.
        assert settings_manager_mock.store_object.call_count == 1
        assert "already exists" in hass_mock.fire_event.call_args.kwargs["error"]

    @pytest.mark.asyncio
    async def test_duplicate_name_check_is_case_insensitive(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """'South' and 'south' / ' SOUTH ' are treated as the same name."""
        c.GRID_PHASES = 1
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "South",
                    "phases": 1,
                    "power_entity_id": "sensor.s",
                    "peak_power_wp": 4000,
                }
            ],
        )

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(name=" south "), {}
        )

        assert settings_manager_mock.store_object.call_count == 1
        assert "already exists" in hass_mock.fire_event.call_args.kwargs["error"]

    @pytest.mark.asyncio
    async def test_duplicate_power_entity_id_rejected(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Two panels cannot share the same power_entity_id sensor."""
        c.GRID_PHASES = 1
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "South",
                    "phases": 1,
                    "power_entity_id": "sensor.shared_pv",
                    "peak_power_wp": 4000,
                }
            ],
        )

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event",
            _valid_panel_payload(name="North", power_entity_id="sensor.shared_pv"),
            {},
        )

        assert settings_manager_mock.store_object.call_count == 1
        err = hass_mock.fire_event.call_args.kwargs["error"]
        assert "sensor.shared_pv" in err
        assert "South" in err  # mentions the panel already using it

    @pytest.mark.asyncio
    async def test_rename_keeps_own_power_entity_id(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Updating a panel without changing its power_entity_id passes
        cleanly — the self-exclusion in the uniqueness check must work for
        the sensor field too, not just the name.
        """
        c.GRID_PHASES = 1
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "South",
                    "phases": 1,
                    "power_entity_id": "sensor.s",
                    "peak_power_wp": 4000,
                }
            ],
        )

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", {"id": "sp_1", "peak_power_wp": 4500}, {}
        )

        # Saved (setup + update), no error fired.
        assert settings_manager_mock.store_object.call_count == 2
        last_call = hass_mock.fire_event.call_args
        assert "error" not in last_call.kwargs

    @pytest.mark.asyncio
    async def test_unknown_fields_are_ignored(
        self, globals_instance, settings_manager_mock
    ):
        """AppDaemon attaches a ``metadata`` key (time_fired, origin, context)
        to event data. That — and any other unexpected key — must not leak
        into the persisted panel dict.
        """
        c.GRID_PHASES = 1
        payload = _valid_panel_payload()
        payload["metadata"] = {"time_fired": "2026-01-01T00:00:00Z", "origin": "REMOTE"}
        payload["random_other_key"] = "garbage"

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", payload, {}
        )

        stored = settings_manager_mock.store_object.call_args_list[-1][0][1]
        assert "metadata" not in stored[0]
        assert "random_other_key" not in stored[0]
        # Real fields preserved.
        assert stored[0]["peak_power_wp"] == 4000


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

    @pytest.mark.asyncio
    async def test_delete_marks_fm_asset_when_connected(
        self, globals_instance, settings_manager_mock, fm_client_mock
    ):
        """Delete with FM connected → mark_asset_deleted called with stored id."""
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "South",
                    "phases": 1,
                    "power_entity_id": "sensor.s",
                    "fm_asset_id": 777,
                }
            ],
        )

        await globals_instance._V2GLibertyGlobals__delete_solar_panel(
            "event", {"id": "sp_1"}, {}
        )

        fm_client_mock.mark_asset_deleted.assert_awaited_once()
        called_id = fm_client_mock.mark_asset_deleted.call_args.args[0]
        assert called_id == 777
        # Second arg is an ISO timestamp string — just sanity-check shape.
        called_ts = fm_client_mock.mark_asset_deleted.call_args.args[1]
        assert isinstance(called_ts, str) and "T" in called_ts

    @pytest.mark.asyncio
    async def test_delete_without_fm_asset_id_skips_mark(
        self, globals_instance, settings_manager_mock, fm_client_mock
    ):
        """Panel that was never FM-provisioned → no mark_asset_deleted call."""
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "South",
                    "phases": 1,
                    "power_entity_id": "sensor.s",
                    # no fm_asset_id
                }
            ],
        )

        await globals_instance._V2GLibertyGlobals__delete_solar_panel(
            "event", {"id": "sp_1"}, {}
        )

        fm_client_mock.mark_asset_deleted.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_marks_fm_failure_does_not_block_local_delete(
        self, globals_instance, settings_manager_mock, hass_mock, fm_client_mock
    ):
        """FM call raises → local delete still succeeds, success event fires."""
        settings_manager_mock.store_object(
            "solar_panels",
            [
                {
                    "id": "sp_1",
                    "name": "South",
                    "phases": 1,
                    "power_entity_id": "sensor.s",
                    "fm_asset_id": 777,
                }
            ],
        )
        fm_client_mock.mark_asset_deleted = AsyncMock(
            side_effect=RuntimeError("FM down")
        )

        await globals_instance._V2GLibertyGlobals__delete_solar_panel(
            "event", {"id": "sp_1"}, {}
        )

        # Local persist happened (setup call + delete-side store)
        assert settings_manager_mock.store_object.call_count == 2
        # Success event fired despite FM failure
        hass_mock.fire_event.assert_called_with(
            "delete_solar_panel.result", solar_panels=[]
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
    async def test_missing_peak_power_wp_rejected(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        c.GRID_PHASES = 1
        payload = _valid_panel_payload()
        payload.pop("peak_power_wp")

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", payload, {}
        )
        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result",
            error="peak_power_wp must be a whole number between 500 and 15000",
        )

    @pytest.mark.asyncio
    async def test_peak_power_wp_below_range_rejected(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(peak_power_wp=400), {}
        )
        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result",
            error="peak_power_wp must be a whole number between 500 and 15000",
        )

    @pytest.mark.asyncio
    async def test_peak_power_wp_above_range_rejected(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(peak_power_wp=20000), {}
        )
        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result",
            error="peak_power_wp must be a whole number between 500 and 15000",
        )

    @pytest.mark.asyncio
    async def test_peak_power_wp_non_integer_rejected(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Floats with a fractional part are rejected; integer-valued floats (e.g. 4000.0) pass."""
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(peak_power_wp=4000.5), {}
        )
        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result",
            error="peak_power_wp must be a whole number between 500 and 15000",
        )

    @pytest.mark.asyncio
    async def test_peak_power_wp_string_rejected(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(peak_power_wp="4000"), {}
        )
        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result",
            error="peak_power_wp must be a whole number between 500 and 15000",
        )

    @pytest.mark.asyncio
    async def test_peak_power_wp_boundary_values_accepted(
        self, globals_instance, settings_manager_mock
    ):
        """Both edges of the range (500, 15000) are valid."""
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(peak_power_wp=500), {}
        )
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event",
            _valid_panel_payload(
                name="North",
                peak_power_wp=15000,
                power_entity_id="sensor.pv_north_power",
            ),
            {},
        )
        # Both saved successfully.
        assert settings_manager_mock.store_object.call_count == 2

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


# ── Taak 17: FM provisioning hook — happy-path call args ──────────────


class TestSolarPanelFMProvisioningCalls:
    """The save handler invokes ensure_asset / ensure_sensor with the
    correct arguments per the design (asset under Mains Connection, sensor
    named ``Power`` in kW), and reuses the same asset name on update so
    the FM side stays idempotent.
    """

    @pytest.mark.asyncio
    async def test_ensure_asset_called_with_correct_arguments(
        self, globals_instance, fm_client_mock
    ):
        """Asset name carries the prefix; type, parent and attributes match."""
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(name="South"), {}
        )

        fm_client_mock.ensure_asset.assert_called_once_with(
            name="South",
            generic_asset_type="solar",
            parent_asset_id=_FM_MAINS_CONNECTION_ID,
            attributes={
                "peak_power_wp": 4000,
                "connected_to_phase": None,
                # Every save re-asserts the asset is active, clearing
                # the v2g_liberty_deleted_at marker from any prior
                # delete (see __delete_solar_panel).
                "v2g_liberty_deleted_at": None,
            },
            asset_id=None,
        )

    @pytest.mark.asyncio
    async def test_ensure_sensor_called_with_correct_arguments(
        self, globals_instance, fm_client_mock
    ):
        """Sensor sits on the panel asset, named ``Power``, unit kW."""
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(), {}
        )

        fm_client_mock.ensure_sensor.assert_called_once_with(
            name="Power",
            unit="kW",
            asset_id=_FM_ASSET_ID,
        )

    @pytest.mark.asyncio
    async def test_sensors_to_show_set_on_panel_asset(
        self, globals_instance, fm_client_mock
    ):
        """The panel's Power sensor is written to the asset's top-level
        sensors_to_show field (not as an attribute)."""
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(), {}
        )

        fm_client_mock.client.update_asset.assert_called_once_with(
            _FM_ASSET_ID, {"sensors_to_show": [_FM_SENSOR_ID]}
        )

    @pytest.mark.asyncio
    async def test_connected_to_phase_attribute_propagates_to_fm(
        self, globals_instance, fm_client_mock
    ):
        """1-phase panel on a 3-phase grid: connected_to_phase ends up in attributes."""
        c.GRID_PHASES = 3
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(phases=1, connected_to_phase=2), {}
        )

        attributes = fm_client_mock.ensure_asset.call_args.kwargs["attributes"]
        assert attributes["connected_to_phase"] == 2

    @pytest.mark.asyncio
    async def test_update_existing_panel_uses_same_asset_name(
        self, globals_instance, fm_client_mock
    ):
        """Update without renaming → second call passes the stored asset_id.

        First save creates the FM asset (asset_id=None → create path).
        Second save (without renaming) passes the asset_id from the first
        save, so FM updates the existing asset in place.
        """
        c.GRID_PHASES = 1
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(name="South"), {}
        )
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", {"id": "sp_1", "peak_power_wp": 5500}, {}
        )

        assert fm_client_mock.ensure_asset.call_count == 2
        first_call = fm_client_mock.ensure_asset.call_args_list[0].kwargs
        second_call = fm_client_mock.ensure_asset.call_args_list[1].kwargs
        # First call creates; second updates the same asset by id.
        assert first_call["asset_id"] is None
        assert second_call["asset_id"] == _FM_ASSET_ID
        assert first_call["name"] == second_call["name"] == "South"

    @pytest.mark.asyncio
    async def test_rename_preserves_fm_asset_identity(
        self, globals_instance, fm_client_mock, settings_manager_mock
    ):
        """Renaming a panel updates the existing FM asset in place.

        The second ensure_asset call receives the original asset_id and the
        new name — so the FM asset is renamed, not duplicated. The locally
        stored fm_asset_id stays the same across the rename.
        """
        c.GRID_PHASES = 1
        # First save creates the FM asset.
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(name="South"), {}
        )
        first_stored = settings_manager_mock.store_object.call_args_list[-1][0][1]
        assert first_stored[0]["fm_asset_id"] == _FM_ASSET_ID

        # Rename: same id, different name.
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", {"id": "sp_1", "name": "South Roof"}, {}
        )

        assert fm_client_mock.ensure_asset.call_count == 2
        rename_call = fm_client_mock.ensure_asset.call_args_list[1].kwargs
        assert rename_call["name"] == "South Roof"
        assert rename_call["asset_id"] == _FM_ASSET_ID  # update by id, not by name

        renamed_stored = settings_manager_mock.store_object.call_args_list[-1][0][1]
        # fm_asset_id is preserved (no orphan created).
        assert renamed_stored[0]["fm_asset_id"] == _FM_ASSET_ID
        assert renamed_stored[0]["name"] == "South Roof"


# ── Taak 17: FM provisioning hook — blocking paths ────────────────────


class TestSolarPanelFMProvisioningBlocking:
    """FM-side failures block local persistence and surface as fm_error.

    The retry/partial-failure sensor case lives in
    :class:`TestSolarPanelFMProvisioningRetry` (taak 11a).
    """

    @pytest.mark.asyncio
    async def test_fm_not_connected_returns_fm_error(
        self, globals_instance, fm_client_mock, settings_manager_mock, hass_mock
    ):
        """client is None → fm_error, no FM calls attempted, no persist."""
        c.GRID_PHASES = 1
        fm_client_mock.client = None

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(), {}
        )

        fm_client_mock.ensure_asset.assert_not_called()
        fm_client_mock.ensure_sensor.assert_not_called()
        settings_manager_mock.store_object.assert_not_called()
        assert c.SOLAR_PANELS == []

        call = hass_mock.fire_event.call_args
        assert call.args[0] == "save_solar_panel.result"
        assert "FlexMeasures is not connected" in call.kwargs["fm_error"]

    @pytest.mark.asyncio
    async def test_mains_connection_missing_returns_fm_error(
        self, globals_instance, fm_client_mock, settings_manager_mock, hass_mock
    ):
        """Grid not yet provisioned → fm_error, no FM calls, no persist."""
        c.GRID_PHASES = 1
        c.FM_MAINS_CONNECTION_ASSET_ID = None  # override autouse default

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(), {}
        )

        fm_client_mock.ensure_asset.assert_not_called()
        fm_client_mock.ensure_sensor.assert_not_called()
        settings_manager_mock.store_object.assert_not_called()
        assert c.SOLAR_PANELS == []

        call = hass_mock.fire_event.call_args
        assert call.args[0] == "save_solar_panel.result"
        assert "Grid connection" in call.kwargs["fm_error"]

    @pytest.mark.asyncio
    async def test_ensure_asset_exception_returns_fm_error(
        self, globals_instance, fm_client_mock, settings_manager_mock, hass_mock
    ):
        """ensure_asset raises → fm_error; ensure_sensor short-circuited; no persist."""
        c.GRID_PHASES = 1
        fm_client_mock.ensure_asset = AsyncMock(
            side_effect=RuntimeError("Asset service unavailable")
        )

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(), {}
        )

        assert fm_client_mock.ensure_asset.call_count == 1
        fm_client_mock.ensure_sensor.assert_not_called()
        settings_manager_mock.store_object.assert_not_called()
        assert c.SOLAR_PANELS == []

        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result", fm_error="Asset service unavailable"
        )


# ── Taak 11a: partial FM failure + idempotent retry ───────────────────


class TestSolarPanelFMProvisioningRetry:
    """Edge case 11a — partial FM failure followed by a clean retry.

    ``ensure_asset`` succeeds, ``ensure_sensor`` fails: blocking
    semantics (10c) keep the panel out of the local settings. The
    follow-up Retry uses the same payload; ``ensure_asset`` is
    idempotent (matches by name) so no duplicate asset is created and
    ``ensure_sensor`` is retried until it succeeds.
    """

    @pytest.mark.asyncio
    async def test_ensure_sensor_failure_blocks_local_save(
        self, globals_instance, fm_client_mock, settings_manager_mock, hass_mock
    ):
        """ensure_sensor exception → fm_error, no persist, no constants update."""
        c.GRID_PHASES = 1
        fm_client_mock.ensure_sensor = AsyncMock(
            side_effect=RuntimeError("Sensor service unavailable")
        )

        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", _valid_panel_payload(), {}
        )

        # Both FM calls were attempted; ensure_asset succeeded.
        assert fm_client_mock.ensure_asset.call_count == 1
        assert fm_client_mock.ensure_sensor.call_count == 1
        # Local registration is blocked.
        settings_manager_mock.store_object.assert_not_called()
        assert c.SOLAR_PANELS == []
        # Response carries fm_error (not error, not solar_panels).
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result", fm_error="Sensor service unavailable"
        )

    @pytest.mark.asyncio
    async def test_retry_after_partial_failure_succeeds_without_duplicates(
        self, globals_instance, fm_client_mock, settings_manager_mock, hass_mock
    ):
        """First save fails on ensure_sensor; identical second save succeeds.

        Verifies that ensure_asset on retry uses the same ``name`` (the
        idempotency key) and that the panel is persisted with both FM
        ids once ensure_sensor succeeds.
        """
        c.GRID_PHASES = 1
        # First call to ensure_sensor raises; second call returns the sensor id.
        fm_client_mock.ensure_sensor = AsyncMock(
            side_effect=[RuntimeError("Sensor service unavailable"), _FM_SENSOR_ID]
        )
        payload = _valid_panel_payload()

        # First attempt: blocked.
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", payload, {}
        )
        settings_manager_mock.store_object.assert_not_called()
        assert c.SOLAR_PANELS == []

        # Retry with the exact same payload (what the UI Retry button does).
        await globals_instance._V2GLibertyGlobals__save_solar_panel(
            "event", payload, {}
        )

        # Both FM calls attempted twice; second sensor call returned the id.
        assert fm_client_mock.ensure_asset.call_count == 2
        assert fm_client_mock.ensure_sensor.call_count == 2

        # ensure_asset was invoked with the same name on both attempts —
        # this is what makes the FM side idempotent in the real client.
        first_call_name = fm_client_mock.ensure_asset.call_args_list[0].kwargs["name"]
        second_call_name = fm_client_mock.ensure_asset.call_args_list[1].kwargs["name"]
        assert first_call_name == second_call_name

        expected_panel = {
            **payload,
            "id": "sp_1",
            "fm_asset_id": _FM_ASSET_ID,
            "fm_sensor_id": _FM_SENSOR_ID,
        }
        settings_manager_mock.store_object.assert_called_once_with(
            "solar_panels", [expected_panel]
        )
        assert c.SOLAR_PANELS == [expected_panel]
        hass_mock.fire_event.assert_called_with(
            "save_solar_panel.result", solar_panels=[expected_panel]
        )
