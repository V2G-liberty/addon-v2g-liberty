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

        settings_manager_mock.store_object.assert_called_once_with(
            "grid_connection", data
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

        settings_manager_mock.store_object.assert_called_once()
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
