# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

V2G Liberty is a Home Assistant add-on that manages bidirectional charging (Vehicle-to-Grid/V2G) for electric vehicles. It integrates with FlexMeasures for schedule optimization, supports multiple charger types via Modbus, and provides a complete UI through Home Assistant.

**Key characteristics:**
- Built on AppDaemon (async Python framework for Home Assistant)
- Distributed as a Home Assistant add-on (runs as a Docker container)
- Event-driven architecture using a centralized EventBus (PyEE)
- Modbus communication for charger control
- Integration with FlexMeasures for AI-powered schedule optimization

## Repository Structure

This is a **multi-workspace repository** containing multiple projects:

```
/workspaces/
├── .devcontainer/              # Devcontainer configurations
│   ├── addon-v2g-liberty/     # Main app devcontainer config
│   │   └── devcontainer.json  # VSCode extensions and settings
│   ├── docker-compose.yaml    # Docker compose for dev environment
│   └── v2g-liberty-cards/     # Cards devcontainer config
├── v2g-liberty/               # Main V2G Liberty application (THIS PROJECT)
│   ├── rootfs/
│   │   └── root/
│   │       ├── appdaemon/     # AppDaemon app code
│   │       │   ├── apps/      # Application modules
│   │       │   └── tests/     # Test suite
│   │       └── homeassistant/ # HA configuration packages
│   ├── CLAUDE.md              # This file
│   ├── Dockerfile             # Production container
│   ├── requirements.txt       # Python dependencies
│   └── pytest.ini             # Test configuration
└── v2g-liberty-cards/         # Companion custom cards project

```

**Important:** When working in this repository:
- The main application code is in `/workspaces/v2g-liberty/`
- The devcontainer configuration is in `/workspaces/.devcontainer/addon-v2g-liberty/`
- Settings and configurations may be at the workspace root level (`/workspaces/`)

## Code Formatting and Style

**All generated code must adhere to the project's formatting standards configured in the devcontainer.**

The project uses **Ruff** for Python code formatting and linting, configured in `/workspaces/.devcontainer/addon-v2g-liberty/devcontainer.json`:

### Formatting Rules

1. **Formatter:** Ruff (charliermarsh.ruff extension)
2. **Format on save:** Enabled
3. **Format on type:** Enabled
4. **Trim trailing whitespace:** Enabled

### Code Generation Requirements

When generating or modifying Python code, ensure it follows these standards:

- **Line length:** Keep lines reasonably short (Ruff will enforce this)
- **Import organization:** Ruff will auto-organize imports
- **String quotes:** Use double quotes for strings (Ruff default)
- **Indentation:** 4 spaces (Python standard)
- **Trailing whitespace:** None (automatically trimmed)
- **Code style:** Follow PEP 8 conventions enforced by Ruff

### Linting

The devcontainer includes:
- **Ruff:** Primary linter and formatter
- **Pylint:** Secondary linter (fromEnvironment strategy)
- **Pylance:** Type checking and IntelliSense

### How to Verify Formatting

When suggesting code changes, ensure they would pass Ruff formatting:
```bash
# If ruff were installed, you would run:
# ruff format <file>
# ruff check <file>
```

**Note:** The Ruff VSCode extension handles formatting automatically on save in the devcontainer.

## Running and Testing

### Development Environment

The project uses VSCode dev containers. The AppDaemon instance connects to Home Assistant via API.

**Run the app in debug mode:**
```bash
# Use VSCode launch configuration "V2G-Liberty"
# This runs: appdaemon -c rootfs/root/appdaemon -C rootfs/root/appdaemon/appdaemon.devcontainer.yaml -D INFO
```

### Testing

**Run all tests:**
```bash
pytest
```

**Run specific test file:**
```bash
pytest rootfs/root/appdaemon/tests/v2g_liberty/test_electric_vehicle.py
```

**Run tests with verbose output:**
```bash
pytest -s
```

**Run specific test:**
```bash
pytest rootfs/root/appdaemon/tests/v2g_liberty/test_electric_vehicle.py::test_name -s
```

**Important:** `pytest.ini` configures the Python path to include the app directories.

### Building

This is a Home Assistant add-on that gets built automatically. The build process:
- Uses `Dockerfile` (based on Home Assistant base image)
- Installs dependencies from `requirements.txt`
- Copies the `rootfs/` directory into the container
- Configuration is in `config.yaml` and `build.yaml`

## Architecture

### Module Organization

The codebase is organized into two main apps under `rootfs/root/appdaemon/apps/`:

**1. `v2g_liberty/` - Main V2G application**
- `v2g_app.py` - Entry point that wires all modules together
- `v2g_globals.py` - Global settings manager and constants initialization
- `main_app.py` - Core V2Gliberty class managing the charging process
- `event_bus.py` - Centralized EventBus for loosely coupled communication (PyEE)

**2. `load_balancer/` - Optional load balancing service**
- Prevents electrical connection overload
- Monitors power flow and adjusts charge power dynamically

### Key Modules

**Chargers (`chargers/`):**
- `base_bidirectional_evse.py` / `base_unidirectional_evse.py` - Abstract base classes
- `wallbox_quasar_1.py` - Wallbox Quasar charger implementation
- `evtec_bidipro.py` - EVtec BiDiPro charger implementation (newer, modernized style)
- `v2g_modbus_client.py` - Modbus TCP communication layer
- `modbus_types.py` - Modbus register definitions and data types

**Electric Vehicles (`evs/`):**
- `base_ev.py` - Abstract base class
- `electric_vehicle.py` - Main EV implementation with SoC tracking

**FlexMeasures Integration:**
- `fm_client.py` - Communicates with FlexMeasures API for schedules
- `get_fm_data.py` - Retrieves price/emission data from FlexMeasures

**Calendar Integration:**
- `reservations_client.py` - Manages calendar events (via CalDAV)
- Calendar events define charging targets and times

**UI and Notifications:**
- `ha_ui_manager.py` - Updates Home Assistant UI entities
- `notifier_util.py` - Sends notifications to users
- Home Assistant dashboard YAML files in `rootfs/root/homeassistant/packages/v2g_liberty/`

**Utilities:**
- `event_bus.py` - Event-driven communication (see Event Bus section)
- `settings_manager.py` - Persistent settings storage (JSON file)
- `data_monitor.py` - Monitors and reports data to FlexMeasures
- `constants.py` - Global constants (settings, URLs, sensor IDs)

### Event Bus Pattern

The codebase uses an event-driven architecture via `EventBus` (based on PyEE's AsyncIOEventEmitter):

**Key events include:**
- `soc_change` - Car battery state of charge changed
- `charger_error_state_change` - Charger communication status
- `car_connection_state_change` - Car connected/disconnected
- `schedule_received` - New charging schedule from FlexMeasures
- `reservation_change` - Calendar events updated

**Pattern:**
```python
# Publishing:
self.event_bus.emit("event_name", arg1="value", arg2="value")

# Subscribing:
self.event_bus.on("event_name", self.handler_method)
```

Modules are loosely coupled through this event bus, avoiding direct dependencies.

### Charger Module Pattern

**Current refactoring direction** (see `evtec_bidipro.py` as the blueprint):

1. **Modbus Register Definitions (MBR):** Use `@dataclass` with `encode()` and `decode()` methods
2. **Modbus Config Entities (MCE):** Use `@dataclass` with type coercion, preprocessing, and validation via `set_value()`
3. **Simplified register operations:** Clean separation between data structure and business logic

**Legacy pattern (pre-refactor):**
- TypedDict for MBR definitions
- Inline encode/decode logic mixed with business logic

**Status:** As of commit `2c0d7f9`, `wallbox_quasar_1` has been refactored to match the EVtec pattern with dataclasses and clean separation.

### Settings Management

Settings are stored in `/data/v2g_liberty_settings.json` and managed by:
- `SettingsManager` class - Handles persistence
- `V2GLibertyGlobals` - Initializes settings and bridges to Home Assistant entities
- `constants.py` - Defines setting schemas with entity names, types, and defaults

Settings are synchronized with Home Assistant input entities (input_text, input_number, input_boolean, input_select).

### Initialization Flow

From `v2g_app.py`:
1. Create all module instances (EventBus, HAUIManager, Notifier, V2GLibertyGlobals, etc.)
2. Wire dependencies (set references between modules)
3. Call `initialize()` on each module in sequence
4. Call `kick_off_settings()` to load settings
5. Call `kick_off_v2g_liberty()` to start main logic

This ensures proper initialization order and dependency injection.

## Key Concepts

### Schedule Management

1. **Target definition:** User sets charging targets via calendar events (SoC needed, departure time)
2. **Schedule request:** `FMClient` requests optimized schedule from FlexMeasures
3. **Schedule execution:** `V2Gliberty` executes the schedule by controlling charger power
4. **Dynamic adjustment:** Schedules refresh when SoC changes significantly or calendar updates

### Modbus Communication

Chargers are controlled via Modbus TCP:
- Register-based read/write operations
- Data types: int16, uint16, int32, float32, string
- Endianness handling (big/little endian)
- Connection pooling and error handling in `v2g_modbus_client.py`

### Power Management

- **Max charge/discharge power:** Hardware limits from charger
- **Dynamic power adjustment:** Based on schedule and SoC
- **Load balancing:** Optional service to prevent grid overload
- **Charge efficiency:** Compensates for charging losses

## Development Guidelines

### When adding a new charger:

**The codebase supports multiple charger types through a factory pattern. Current branch (359-add-new-modbus-charger) implements:**
- `wallbox-quasar-1` - Original Wallbox Quasar (refactored)
- `evtec-bidi-pro-10` - New EVtec BiDiPro charger

**Steps to add a new charger:**

1. **Create charger module** in `chargers/` inheriting from `BidirectionalEVSE` or `UnidirectionalEVSE`

2. **Follow the modern pattern** (see `evtec_bidipro.py` as reference):
   ```python
   # Define Modbus Registers as dataclasses
   _MBR_CHARGER_STATE = MBR(address=100, data_type="int32", length=2)

   # Define Config Entities with validation
   _MCE_ACTUAL_POWER = ModbusConfigEntity(
       modbus_register=MBR(address=110, data_type="float32", length=2),
       minimum_value=-20000,
       maximum_value=20000,
       current_value=None,
       change_handler="_handle_charge_power_change",
   )
   ```
   - **MBR (Modbus Register):** Use `@dataclass` with `encode()`/`decode()` methods
   - **MCE (Modbus Config Entity):** Use `@dataclass` with `set_value()` for type coercion and validation
   - Keep register operations clean and separated from business logic

3. **Implement all abstract methods** from base class:
   - `async def init_charger()` - Initialize charger connection and settings
   - `async def start_charging(power_in_watt)` - Start charge/discharge
   - `async def stop_charging()` - Stop charging
   - `async def is_charging()` - Return charging state
   - `async def is_discharging()` - Return discharging state (bidirectional only)
   - `async def get_hardware_power_limit()` - Get max power from charger
   - `async def set_max_charge_power()` / `set_max_discharge_power()` - Set limits

4. **Add to factory method** in `v2g_globals.py` (~line 546):
   ```python
   def _get_evse_client(self, charger_type):
       if charger_type == "wallbox-quasar-1":
           evse = WallboxQuasar1Client(...)
       elif charger_type == "evtec-bidi-pro-10":
           evse = EVtecBiDiProClient(...)
       elif charger_type == "your-new-charger":
           evse = YourNewChargerClient(...)
       else:
           self.__log(f"Unknown charger_type: {charger_type}")
           return None
   ```

5. **Update settings migration** in `settings_manager.py` if needed for backward compatibility

6. **Test thoroughly:**
   - Connection establishment
   - Power control (charge/discharge)
   - SoC reading
   - Error handling and reconnection
   - State transitions
   - Modbus timeout behavior

### When modifying settings:

1. Add setting definition to `v2g_globals.py` (entity name, type, default)
2. Ensure corresponding Home Assistant entity exists in YAML packages
3. Use `SettingsManager` for persistence
4. Settings are automatically synced with HA entities

### When adding event bus events:

1. Document the event in `event_bus.py` docstring (description, emitter, arguments)
2. Use descriptive event names following existing patterns
3. Pass data as keyword arguments (not positional)
4. Emit events from the appropriate module
5. Subscribe to events in modules that need to react

### Testing:

- Tests are in `rootfs/root/appdaemon/tests/`
- Use pytest fixtures for mocking (see `conftest.py`)
- Mock Home Assistant API calls and AppDaemon methods
- Test event bus interactions by checking emitted events

## File Locations

- **Application code:** `rootfs/root/appdaemon/apps/v2g_liberty/`
- **Tests:** `rootfs/root/appdaemon/tests/v2g_liberty/`
- **Home Assistant config:** `rootfs/root/homeassistant/packages/v2g_liberty/`
- **Settings storage:** `/data/v2g_liberty_settings.json` (runtime)
- **Logs:** `logs/` or `/config/logs/` (in container)

## Common Pitfalls

1. **Circular imports:** Use forward references (string types) or late imports when needed
2. **Async/await:** Most methods in AppDaemon apps must be async
3. **Event bus timing:** Subscribers must be registered before events are emitted
4. **Modbus register addresses:** Some use 0-based indexing, some 1-based (check charger spec)
5. **Time zones:** Always use `get_local_now()` from v2g_globals for consistent timezone handling
6. **State persistence:** Use SettingsManager for persistent state, not just variables
7. **Settings migrations:** When changing entity types, add migration logic to `settings_manager.__upgrade()` to ensure smooth upgrades for existing users
8. **Charger factory validation:** Always validate charger_type in `_get_evse_client()` and handle unknown types gracefully
