"""Unit tests for grid connection and charger phase settings in V2GLibertyGlobals."""

from unittest.mock import AsyncMock, Mock, patch
import pytest

from apps.v2g_liberty import constants as c
from apps.v2g_liberty.v2g_globals import V2GLibertyGlobals


@pytest.fixture
def log_mock():
    return Mock()


@pytest.fixture
def settings_manager_mock():
    mock = Mock()
    store = {}

    def fake_store_object(key, data):
        store[key] = data

    def fake_get_object(key):
        return store.get(key, None)

    mock.store_object = Mock(side_effect=fake_store_object)
    mock.get_object = Mock(side_effect=fake_get_object)
    return mock


@pytest.fixture
def hass_mock():
    mock = Mock()
    mock.fire_event = Mock()
    mock.listen_state = Mock(return_value="listen_handle")
    mock.run_in = Mock(return_value="timer_handle")
    mock.cancel_listen_state = Mock()
    mock.cancel_timer = Mock()
    return mock


@pytest.fixture
def globals_instance(log_mock, settings_manager_mock, hass_mock):
    """Create a V2GLibertyGlobals instance with mocked dependencies."""
    instance = object.__new__(V2GLibertyGlobals)
    # Use the name-mangled attribute for the private __log method
    instance._V2GLibertyGlobals__log = log_mock
    instance.v2g_settings = settings_manager_mock
    instance.hass = hass_mock
    return instance


class TestInitialiseGridConnectionSettings:
    def test_no_config_sets_defaults(self, globals_instance, settings_manager_mock):
        """When no grid connection is configured, constants get default values."""
        # Store is empty → get_object returns None

        globals_instance._V2GLibertyGlobals__initialise_grid_connection_settings()

        assert c.GRID_PHASES == 3
        assert c.GRID_CAPACITY_PER_PHASE == 25
        assert c.GRID_CONSUMPTION_ENTITIES == []
        assert c.GRID_PRODUCTION_ENTITIES == []

    def test_loads_existing_config(self, globals_instance, settings_manager_mock):
        """When grid connection is configured, constants are set from stored data."""
        settings_manager_mock.store_object(
            "grid_connection",
            {
                "phases": 1,
                "capacity_per_phase": 40,
                "consumption_entities": ["sensor.grid_consumption_l1"],
                "production_entities": ["sensor.grid_production_l1"],
            },
        )

        globals_instance._V2GLibertyGlobals__initialise_grid_connection_settings()

        assert c.GRID_PHASES == 1
        assert c.GRID_CAPACITY_PER_PHASE == 40
        assert c.GRID_CONSUMPTION_ENTITIES == ["sensor.grid_consumption_l1"]
        assert c.GRID_PRODUCTION_ENTITIES == ["sensor.grid_production_l1"]

    def test_loads_3_phase_config(self, globals_instance, settings_manager_mock):
        """3-phase config loads all three entities per list."""
        settings_manager_mock.store_object(
            "grid_connection",
            {
                "phases": 3,
                "capacity_per_phase": 25,
                "consumption_entities": [
                    "sensor.grid_cons_l1",
                    "sensor.grid_cons_l2",
                    "sensor.grid_cons_l3",
                ],
                "production_entities": [
                    "sensor.grid_prod_l1",
                    "sensor.grid_prod_l2",
                    "sensor.grid_prod_l3",
                ],
            },
        )

        globals_instance._V2GLibertyGlobals__initialise_grid_connection_settings()

        assert c.GRID_PHASES == 3
        assert len(c.GRID_CONSUMPTION_ENTITIES) == 3
        assert len(c.GRID_PRODUCTION_ENTITIES) == 3

    def test_partial_config_uses_defaults(
        self, globals_instance, settings_manager_mock
    ):
        """Missing fields in stored config fall back to defaults."""
        settings_manager_mock.store_object(
            "grid_connection",
            {
                "phases": 1,
                "consumption_entities": ["sensor.grid_consumption_l1"],
                "production_entities": ["sensor.grid_production_l1"],
            },
        )

        globals_instance._V2GLibertyGlobals__initialise_grid_connection_settings()

        assert c.GRID_PHASES == 1
        # capacity_per_phase missing → default
        assert c.GRID_CAPACITY_PER_PHASE == 25


class TestSaveGridConnectionSettings:
    @pytest.mark.asyncio
    async def test_save_valid_1_phase(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Valid 1-phase config is stored and constants are set."""
        data = {
            "phases": 1,
            "capacity_per_phase": 40,
            "consumption_entities": ["sensor.grid_cons_l1"],
            "production_entities": ["sensor.grid_prod_l1"],
        }

        await globals_instance._V2GLibertyGlobals__save_grid_connection_settings(
            "event", data, {}
        )

        # grid_connection stored + charger_phase cleared (new config, no previous)
        assert settings_manager_mock.store_object.call_args_list[0] == (
            ("grid_connection", data),
        )
        hass_mock.fire_event.assert_called_with("save_grid_connection_settings.result")
        assert c.GRID_PHASES == 1

    @pytest.mark.asyncio
    async def test_save_valid_3_phase(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Valid 3-phase config is stored."""
        data = {
            "phases": 3,
            "capacity_per_phase": 25,
            "consumption_entities": ["sensor.l1", "sensor.l2", "sensor.l3"],
            "production_entities": ["sensor.p1", "sensor.p2", "sensor.p3"],
        }

        await globals_instance._V2GLibertyGlobals__save_grid_connection_settings(
            "event", data, {}
        )

        # grid_connection stored (+ charger_phase cleared since no previous config)
        assert settings_manager_mock.store_object.call_args_list[0] == (
            ("grid_connection", data),
        )
        assert c.GRID_PHASES == 3

    @pytest.mark.asyncio
    async def test_save_invalid_phases(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Invalid phases value is rejected."""
        data = {
            "phases": 2,
            "capacity_per_phase": 25,
            "consumption_entities": ["sensor.l1", "sensor.l2"],
            "production_entities": ["sensor.p1", "sensor.p2"],
        }

        await globals_instance._V2GLibertyGlobals__save_grid_connection_settings(
            "event", data, {}
        )

        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_grid_connection_settings.result",
            error="phases must be 1 or 3",
        )

    @pytest.mark.asyncio
    async def test_save_invalid_capacity(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Zero or negative capacity is rejected."""
        data = {
            "phases": 1,
            "capacity_per_phase": 0,
            "consumption_entities": ["sensor.l1"],
            "production_entities": ["sensor.p1"],
        }

        await globals_instance._V2GLibertyGlobals__save_grid_connection_settings(
            "event", data, {}
        )

        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_grid_connection_settings.result",
            error="capacity_per_phase must be a positive number",
        )

    @pytest.mark.asyncio
    async def test_save_entity_count_mismatch(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Entity list length must match phase count."""
        data = {
            "phases": 3,
            "capacity_per_phase": 25,
            "consumption_entities": ["sensor.l1"],  # only 1 instead of 3
            "production_entities": ["sensor.p1", "sensor.p2", "sensor.p3"],
        }

        await globals_instance._V2GLibertyGlobals__save_grid_connection_settings(
            "event", data, {}
        )

        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_grid_connection_settings.result",
            error="Expected 3 entity/entities per list",
        )


class TestSaveGridClearsChargerPhase:
    @pytest.mark.asyncio
    async def test_clears_phase_when_phases_change(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Changing from 1 to 3 phases clears charger connected_to_phase."""
        # Pre-existing 1-phase config with detected charger phase
        settings_manager_mock.store_object(
            "grid_connection",
            {
                "phases": 1,
                "capacity_per_phase": 25,
                "consumption_entities": ["sensor.l1"],
                "production_entities": ["sensor.p1"],
            },
        )
        settings_manager_mock.store_object(
            "charger_phase", {"connected_to_phase": 1, "detected_at": "2026-04-29"}
        )
        globals_instance._V2GLibertyGlobals__initialise_charger_phase_settings()
        assert c.CHARGER_CONNECTED_TO_PHASE == 1

        # Save with 3 phases
        await globals_instance._V2GLibertyGlobals__save_grid_connection_settings(
            "event",
            {
                "phases": 3,
                "capacity_per_phase": 25,
                "consumption_entities": ["sensor.l1", "sensor.l2", "sensor.l3"],
                "production_entities": ["sensor.p1", "sensor.p2", "sensor.p3"],
            },
            {},
        )

        assert c.CHARGER_CONNECTED_TO_PHASE is None

    @pytest.mark.asyncio
    async def test_clears_phase_when_entities_change(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Changing consumption entities clears charger connected_to_phase."""
        settings_manager_mock.store_object(
            "grid_connection",
            {
                "phases": 3,
                "capacity_per_phase": 25,
                "consumption_entities": ["sensor.old1", "sensor.old2", "sensor.old3"],
                "production_entities": ["sensor.p1", "sensor.p2", "sensor.p3"],
            },
        )
        settings_manager_mock.store_object(
            "charger_phase", {"connected_to_phase": 2, "detected_at": "2026-04-29"}
        )
        globals_instance._V2GLibertyGlobals__initialise_charger_phase_settings()

        await globals_instance._V2GLibertyGlobals__save_grid_connection_settings(
            "event",
            {
                "phases": 3,
                "capacity_per_phase": 25,
                "consumption_entities": ["sensor.new1", "sensor.new2", "sensor.new3"],
                "production_entities": ["sensor.p1", "sensor.p2", "sensor.p3"],
            },
            {},
        )

        assert c.CHARGER_CONNECTED_TO_PHASE is None

    @pytest.mark.asyncio
    async def test_keeps_phase_when_only_capacity_changes(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Changing only capacity_per_phase does NOT clear charger phase."""
        settings_manager_mock.store_object(
            "grid_connection",
            {
                "phases": 3,
                "capacity_per_phase": 25,
                "consumption_entities": ["sensor.l1", "sensor.l2", "sensor.l3"],
                "production_entities": ["sensor.p1", "sensor.p2", "sensor.p3"],
            },
        )
        settings_manager_mock.store_object(
            "charger_phase", {"connected_to_phase": 2, "detected_at": "2026-04-29"}
        )
        globals_instance._V2GLibertyGlobals__initialise_charger_phase_settings()

        await globals_instance._V2GLibertyGlobals__save_grid_connection_settings(
            "event",
            {
                "phases": 3,
                "capacity_per_phase": 35,  # only this changed
                "consumption_entities": ["sensor.l1", "sensor.l2", "sensor.l3"],
                "production_entities": ["sensor.p1", "sensor.p2", "sensor.p3"],
            },
            {},
        )

        assert c.CHARGER_CONNECTED_TO_PHASE == 2


class TestGetGridConnectionSettings:
    @pytest.mark.asyncio
    async def test_get_returns_stored_config(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """When config exists, it is returned via result event."""
        stored = {
            "phases": 1,
            "capacity_per_phase": 40,
            "consumption_entities": ["sensor.l1"],
            "production_entities": ["sensor.p1"],
        }
        # Pre-populate the fake store via store_object
        settings_manager_mock.store_object("grid_connection", stored)

        await globals_instance._V2GLibertyGlobals__get_grid_connection_settings(
            "event", {}, {}
        )

        hass_mock.fire_event.assert_called_with(
            "get_grid_connection_settings.result",
            phases=1,
            capacity_per_phase=40,
            configured=True,
            consumption_entities=["sensor.l1"],
            production_entities=["sensor.p1"],
        )

    @pytest.mark.asyncio
    async def test_get_returns_defaults_when_not_configured(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """When no config exists, defaults are returned."""
        # Store is empty by default → get_object returns None

        await globals_instance._V2GLibertyGlobals__get_grid_connection_settings(
            "event", {}, {}
        )

        hass_mock.fire_event.assert_called_with(
            "get_grid_connection_settings.result",
            phases=3,
            capacity_per_phase=25,
            consumption_entities=[],
            production_entities=[],
            configured=False,
        )


# ── Charger phase settings tests ─────────────────────────────────────


class TestInitialiseChargerPhaseSettings:
    def test_no_config_sets_none(self, globals_instance):
        """When not configured, CHARGER_CONNECTED_TO_PHASE is None."""
        globals_instance._V2GLibertyGlobals__initialise_charger_phase_settings()

        assert c.CHARGER_CONNECTED_TO_PHASE is None

    def test_loads_existing_phase(self, globals_instance, settings_manager_mock):
        """Stored phase value is loaded into constant."""
        settings_manager_mock.store_object("charger_phase", {"connected_to_phase": 2})

        globals_instance._V2GLibertyGlobals__initialise_charger_phase_settings()

        assert c.CHARGER_CONNECTED_TO_PHASE == 2


class TestSaveChargerPhase:
    @pytest.mark.asyncio
    async def test_save_valid_phase(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Valid phase value is stored."""
        await globals_instance._V2GLibertyGlobals__save_charger_phase(
            "event", {"connected_to_phase": 3}, {}
        )

        settings_manager_mock.store_object.assert_called_once_with(
            "charger_phase", {"connected_to_phase": 3}
        )
        hass_mock.fire_event.assert_called_with("save_charger_phase.result")
        assert c.CHARGER_CONNECTED_TO_PHASE == 3

    @pytest.mark.asyncio
    async def test_save_invalid_phase(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Invalid phase value is rejected."""
        await globals_instance._V2GLibertyGlobals__save_charger_phase(
            "event", {"connected_to_phase": 4}, {}
        )

        settings_manager_mock.store_object.assert_not_called()
        hass_mock.fire_event.assert_called_with(
            "save_charger_phase.result",
            error="connected_to_phase must be 1, 2, or 3",
        )

    @pytest.mark.asyncio
    async def test_save_none_phase(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """None phase value is rejected."""
        await globals_instance._V2GLibertyGlobals__save_charger_phase(
            "event", {"connected_to_phase": None}, {}
        )

        settings_manager_mock.store_object.assert_not_called()


class TestChargerPhaseValidation:
    def test_not_required_when_1_phase(self, globals_instance, settings_manager_mock):
        """Phase selection is not required for 1-phase grid."""
        # Set up 1-phase grid
        settings_manager_mock.store_object(
            "grid_connection",
            {
                "phases": 1,
                "capacity_per_phase": 25,
                "consumption_entities": ["sensor.l1"],
                "production_entities": ["sensor.p1"],
            },
        )
        globals_instance._V2GLibertyGlobals__initialise_grid_connection_settings()

        assert globals_instance.charger_phase_is_required() is False
        assert globals_instance.charger_phase_is_valid() is True

    def test_required_when_3_phase(self, globals_instance, settings_manager_mock):
        """Phase selection is required for 3-phase grid."""
        settings_manager_mock.store_object(
            "grid_connection",
            {
                "phases": 3,
                "capacity_per_phase": 25,
                "consumption_entities": ["sensor.l1", "sensor.l2", "sensor.l3"],
                "production_entities": ["sensor.p1", "sensor.p2", "sensor.p3"],
            },
        )
        globals_instance._V2GLibertyGlobals__initialise_grid_connection_settings()

        assert globals_instance.charger_phase_is_required() is True

    def test_invalid_when_3_phase_and_no_phase_set(
        self, globals_instance, settings_manager_mock
    ):
        """3-phase grid without charger phase configured is invalid."""
        settings_manager_mock.store_object(
            "grid_connection",
            {
                "phases": 3,
                "capacity_per_phase": 25,
                "consumption_entities": ["sensor.l1", "sensor.l2", "sensor.l3"],
                "production_entities": ["sensor.p1", "sensor.p2", "sensor.p3"],
            },
        )
        globals_instance._V2GLibertyGlobals__initialise_grid_connection_settings()
        # charger_phase not set → None
        c.CHARGER_CONNECTED_TO_PHASE = None

        assert globals_instance.charger_phase_is_valid() is False

    def test_valid_when_3_phase_and_phase_set(
        self, globals_instance, settings_manager_mock
    ):
        """3-phase grid with charger phase configured is valid."""
        settings_manager_mock.store_object(
            "grid_connection",
            {
                "phases": 3,
                "capacity_per_phase": 25,
                "consumption_entities": ["sensor.l1", "sensor.l2", "sensor.l3"],
                "production_entities": ["sensor.p1", "sensor.p2", "sensor.p3"],
            },
        )
        globals_instance._V2GLibertyGlobals__initialise_grid_connection_settings()
        settings_manager_mock.store_object("charger_phase", {"connected_to_phase": 2})
        globals_instance._V2GLibertyGlobals__initialise_charger_phase_settings()

        assert globals_instance.charger_phase_is_valid() is True


class TestGetChargerPhase:
    @pytest.mark.asyncio
    async def test_get_returns_stored_phase_with_validation(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """Response includes stored phase, required, and valid flags."""
        # Set up 3-phase grid
        settings_manager_mock.store_object(
            "grid_connection",
            {
                "phases": 3,
                "capacity_per_phase": 25,
                "consumption_entities": ["sensor.l1", "sensor.l2", "sensor.l3"],
                "production_entities": ["sensor.p1", "sensor.p2", "sensor.p3"],
            },
        )
        globals_instance._V2GLibertyGlobals__initialise_grid_connection_settings()
        # Set charger phase
        settings_manager_mock.store_object("charger_phase", {"connected_to_phase": 1})
        globals_instance._V2GLibertyGlobals__initialise_charger_phase_settings()

        await globals_instance._V2GLibertyGlobals__get_charger_phase("event", {}, {})

        hass_mock.fire_event.assert_called_with(
            "get_charger_phase.result",
            connected_to_phase=1,
            required=True,
            valid=True,
        )

    @pytest.mark.asyncio
    async def test_get_returns_not_required_for_1_phase(
        self, globals_instance, settings_manager_mock, hass_mock
    ):
        """For 1-phase grid, required is False and valid is True."""
        settings_manager_mock.store_object(
            "grid_connection",
            {
                "phases": 1,
                "capacity_per_phase": 25,
                "consumption_entities": ["sensor.l1"],
                "production_entities": ["sensor.p1"],
            },
        )
        globals_instance._V2GLibertyGlobals__initialise_grid_connection_settings()

        await globals_instance._V2GLibertyGlobals__get_charger_phase("event", {}, {})

        hass_mock.fire_event.assert_called_with(
            "get_charger_phase.result",
            connected_to_phase=None,
            required=False,
            valid=True,
        )


# ── Grid entity validation tests ─────────────────────────────────────


class TestTestGridEntities:
    @pytest.mark.asyncio
    async def test_no_entities_returns_error(self, globals_instance, hass_mock):
        """Empty entity lists return an error immediately."""
        await globals_instance._V2GLibertyGlobals__test_grid_entities(
            "event", {"consumption_entities": [], "production_entities": []}, {}
        )

        hass_mock.fire_event.assert_called_with(
            "test_grid_entities.result",
            success=False,
            error="No entities to test",
            results={},
        )
        hass_mock.listen_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_registers_listeners_and_timeout(self, globals_instance, hass_mock):
        """Listeners are registered for all entities, plus a timeout."""
        data = {
            "consumption_entities": ["sensor.cons_l1"],
            "production_entities": ["sensor.prod_l1"],
        }

        await globals_instance._V2GLibertyGlobals__test_grid_entities("event", data, {})

        assert hass_mock.listen_state.call_count == 2
        hass_mock.run_in.assert_called_once()
        # Timeout should be 30 seconds
        timeout_seconds = hass_mock.run_in.call_args[0][1]
        assert timeout_seconds == 30

    @pytest.mark.asyncio
    async def test_all_entities_respond_finishes_early(
        self, globals_instance, hass_mock
    ):
        """When all entities respond, the test finishes before timeout."""
        # Capture the listen_state callbacks
        callbacks = {}

        def fake_listen_state(callback, entity):
            callbacks[entity] = callback
            return f"handle_{entity}"

        hass_mock.listen_state = Mock(side_effect=fake_listen_state)

        data = {
            "consumption_entities": ["sensor.cons_l1"],
            "production_entities": ["sensor.prod_l1"],
        }

        await globals_instance._V2GLibertyGlobals__test_grid_entities("event", data, {})

        # Simulate both entities responding with numeric values
        callbacks["sensor.cons_l1"]("sensor.cons_l1", "state", "0", "850", {})
        callbacks["sensor.prod_l1"]("sensor.prod_l1", "state", "0", "120", {})

        # Should have fired progress events + result
        fire_calls = [call.args[0] for call in hass_mock.fire_event.call_args_list]
        assert "test_grid_entities.progress" in fire_calls
        assert "test_grid_entities.result" in fire_calls

        # Result should be success
        result_call = [
            call
            for call in hass_mock.fire_event.call_args_list
            if call.args[0] == "test_grid_entities.result"
        ][0]
        assert result_call.kwargs["success"] is True
        assert result_call.kwargs["failed"] == []

    @pytest.mark.asyncio
    async def test_timeout_reports_failed_entities(self, globals_instance, hass_mock):
        """When timeout fires, unresponsive entities are reported as failed."""
        callbacks = {}

        def fake_listen_state(callback, entity):
            callbacks[entity] = callback
            return f"handle_{entity}"

        hass_mock.listen_state = Mock(side_effect=fake_listen_state)

        data = {
            "consumption_entities": ["sensor.cons_l1", "sensor.cons_l2"],
            "production_entities": [],
        }

        await globals_instance._V2GLibertyGlobals__test_grid_entities("event", data, {})

        # Only one entity responds
        callbacks["sensor.cons_l1"]("sensor.cons_l1", "state", "0", "850", {})

        # Simulate timeout firing
        timeout_callback = hass_mock.run_in.call_args[0][0]
        timeout_kwargs = hass_mock.run_in.call_args[1]
        timeout_callback(timeout_kwargs)

        # Result should report cons_l2 as failed
        result_call = [
            call
            for call in hass_mock.fire_event.call_args_list
            if call.args[0] == "test_grid_entities.result"
        ][0]
        assert result_call.kwargs["success"] is False
        assert "sensor.cons_l2" in result_call.kwargs["failed"]

    @pytest.mark.asyncio
    async def test_ignores_non_numeric_state(self, globals_instance, hass_mock):
        """Non-numeric state values (unknown, unavailable) are ignored."""
        callbacks = {}

        def fake_listen_state(callback, entity):
            callbacks[entity] = callback
            return f"handle_{entity}"

        hass_mock.listen_state = Mock(side_effect=fake_listen_state)

        data = {
            "consumption_entities": ["sensor.cons_l1"],
            "production_entities": [],
        }

        await globals_instance._V2GLibertyGlobals__test_grid_entities("event", data, {})

        # Send non-numeric values — should be ignored
        callbacks["sensor.cons_l1"]("sensor.cons_l1", "state", "0", "unknown", {})
        callbacks["sensor.cons_l1"]("sensor.cons_l1", "state", "0", "unavailable", {})

        # No progress event should have been fired
        progress_calls = [
            call
            for call in hass_mock.fire_event.call_args_list
            if call.args[0] == "test_grid_entities.progress"
        ]
        assert len(progress_calls) == 0

    @pytest.mark.asyncio
    async def test_finish_only_fires_once(self, globals_instance, hass_mock):
        """The result event is only fired once, even if timeout fires after completion."""
        callbacks = {}

        def fake_listen_state(callback, entity):
            callbacks[entity] = callback
            return f"handle_{entity}"

        hass_mock.listen_state = Mock(side_effect=fake_listen_state)

        data = {
            "consumption_entities": ["sensor.cons_l1"],
            "production_entities": [],
        }

        await globals_instance._V2GLibertyGlobals__test_grid_entities("event", data, {})

        # Entity responds → finishes early
        callbacks["sensor.cons_l1"]("sensor.cons_l1", "state", "0", "850", {})

        # Simulate timeout also firing (race condition)
        timeout_callback = hass_mock.run_in.call_args[0][0]
        timeout_kwargs = hass_mock.run_in.call_args[1]
        timeout_callback(timeout_kwargs)

        # Result should only have been fired once
        result_calls = [
            call
            for call in hass_mock.fire_event.call_args_list
            if call.args[0] == "test_grid_entities.result"
        ]
        assert len(result_calls) == 1
