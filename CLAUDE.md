# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

V2G Liberty is a Home Assistant add-on that provides fully automated and optimized control over bidirectional (Vehicle-to-Grid) charging of electric vehicles. The system integrates with:
- **Wallbox Quasar charger** via Modbus protocol
- **FlexMeasures** (Seita B.V.) for optimal scheduling based on dynamic electricity prices or emissions
- **Calendar systems** (CalDAV, Google, iCloud, etc.) for managing charging reservations
- **Home Assistant** for UI, notifications, and automation

The add-on saves users significant costs (€1000+ annually) by optimizing when to charge from the grid vs. discharge to the grid/home based on electricity prices or CO2 emissions.

## Repository Structure

This is a multi-component monorepo:

### 1. Home Assistant Add-on (`v2g-liberty/`)
The main Python-based add-on that runs inside Home Assistant:
- **Core logic**: AppDaemon-based Python apps in `rootfs/root/appdaemon/apps/`
  - `v2g_liberty/`: Main charging control logic, FlexMeasures client, Modbus EVSE communication
  - `load_balancer/`: Optional power load balancing service to prevent electrical overload
- **Home Assistant integration**: YAML configs in `rootfs/root/homeassistant/packages/v2g_liberty/`
- **Tests**: Located in `rootfs/root/appdaemon/tests/` (pytest-based)

### 2. Custom Frontend Cards (`v2g-liberty-cards/`)
TypeScript/Lit web components for the Home Assistant UI:
- Built with Parcel bundler
- Provides settings dialogs and custom cards for the V2G Liberty dashboard

### 3. Charger Mocks (`charger-mocks/`)
Development and testing tools that simulate EV chargers without physical hardware:
- **Modbus server**: Docker-based passive Modbus TCP server (port 5020)
- **Quasar CLI**: Python command-line interface in `charger-mocks/quasar/cli.py`
  - Configurable host/port via command-line arguments
  - Modular structure with separate register maps and state definitions
- **Utilities**: `ping_modbus.py` for connectivity testing
- **Purpose**: Enables V2G Liberty development without physical hardware
- **Integration**: Automatically starts with the devcontainer (see `.devcontainer/docker-compose.yaml`)
- **Future**: Structure supports adding additional charger types as V2G Liberty expands
- **Documentation**: See `charger-mocks/README.md` and [README.devcontainer.md](README.devcontainer.md)

## Development Commands

### Testing

**Run all tests** from the `v2g-liberty/` directory:
```bash
cd v2g-liberty
pytest
```

**Run specific test file**:
```bash
pytest rootfs/root/appdaemon/tests/v2g_liberty/test_settings_manager.py
```

**Run specific test**:
```bash
pytest rootfs/root/appdaemon/tests/v2g_liberty/test_settings_manager.py::TestClassName::test_method_name
```

Note: `pytest.ini` configures the Python path to include the app modules and ignores `rootfs/root/tests/` (which contains older test data).

### Frontend Cards Development

From the `v2g-liberty-cards/` directory:

```bash
npm install          # Install dependencies
npm run watch        # Watch mode for development
npm run build        # Production build (outputs to dist/v2g-liberty-cards.js)
```

The built JS file is included in the Home Assistant package at `rootfs/root/homeassistant/www/`.

### DevContainer Development

There are two ways to run the devcontainer. Both share the same Docker containers and network, so you can switch between them freely.

#### Method 1: PowerShell Script (Recommended for Quick Start)

**Prerequisites**: PowerShell Core 7+ is required
- **Windows**: Usually pre-installed
- **macOS**: `brew install powershell`
- **Linux**: Install via package manager

**Run the script**:
```powershell
# Windows
.\setup-devcontainer.ps1

# macOS/Linux
pwsh ./setup-devcontainer.ps1
```

This automatically:
1. ✅ Starts the Quasar mock Modbus server on port 5020
2. ✅ Builds and starts all devcontainer services (Home Assistant, AppDaemon, Frontend)
3. ✅ Copies V2G Liberty config files to Home Assistant
4. ✅ Runs the V2G Liberty AppDaemon app

**Available commands**:
```powershell
# Windows
.\setup-devcontainer.ps1          # Start dev session (default)
.\setup-devcontainer.ps1 status   # Check what's running
.\setup-devcontainer.ps1 stop     # Stop all services
.\setup-devcontainer.ps1 reset    # Clean slate (deletes all data)
.\setup-devcontainer.ps1 help     # Show all options

# macOS/Linux
pwsh ./setup-devcontainer.ps1          # Start dev session (default)
pwsh ./setup-devcontainer.ps1 status   # Check what's running
pwsh ./setup-devcontainer.ps1 stop     # Stop all services
pwsh ./setup-devcontainer.ps1 reset    # Clean slate (deletes all data)
pwsh ./setup-devcontainer.ps1 help     # Show all options
```

**Control the mock charger** (in a separate terminal):
```bash
cd charger-mocks/quasar
python cli.py

# Commands:
# soc 75          - Set battery to 75%
# charge 3000     - Start charging at 3000W
# charge -2000    - Start discharging at 2000W (V2G mode)
# disconnect      - Simulate car disconnection
# error           - Simulate charger error
```

#### Method 2: VS Code Debugging

**VS Code Integration**:
- **Run**: `Ctrl+F5` (Run without debugging)
- **Debug**: `F5` (Start debugging with breakpoints)
- **Reopen in Container**: `Ctrl+Shift+P` → "Dev Containers: Reopen in Container"

**Note:** VS Code will automatically copy V2G Liberty config files when the container starts. If you already started containers with the PowerShell script, VS Code will reuse them.

See [README.devcontainer.md](README.devcontainer.md) for complete setup instructions.

### Docker Development

Build the add-on container:
```bash
docker build -t v2g-liberty -f v2g-liberty/Dockerfile v2g-liberty/
```

## Architecture Overview

### Core Components

1. **main_app.py (V2Gliberty class)**
   - Primary orchestrator for the charging process
   - Manages communication between EVSE, FlexMeasures, and calendars
   - Handles scheduling, SoC prognosis, notifications, and UI state updates
   - Implements boost mode, max charge now, and schedule optimization

2. **modbus_evse_client.py**
   - Communicates with Wallbox Quasar charger via Modbus TCP
   - Controls charging/discharging power
   - Monitors car connection status, SoC, and charger state

3. **fm_client.py / get_fm_data.py**
   - FlexMeasures API client for retrieving optimized schedules
   - Handles price data, emissions data, and schedule optimization requests
   - Supports multiple energy contracts (EPEX-based European markets, Octopus Energy UK, Amber Electric Australia)

4. **reservations_client.py**
   - CalDAV client for reading calendar events
   - Parses car reservation events to determine required SoC targets and timeframes

5. **settings_manager.py**
   - Manages all user settings via Home Assistant input entities
   - Validates and persists configuration changes

6. **ha_ui_manager.py**
   - Manages Home Assistant UI state (charts, sensors, notifications)
   - Updates SoC prognosis charts and price/emission visualizations

7. **event_bus.py**
   - Internal event system for decoupled component communication
   - Uses pyee (EventEmitter) for pub/sub pattern

8. **load_balancer/** (optional module)
   - Standalone HTTP proxy that sits between V2G Liberty and the Quasar
   - Monitors phase power consumption and dynamically adjusts charge limits
   - Prevents overloading single-phase electrical connections

### Data Flow

1. User sets charging parameters via Home Assistant UI (settings cards)
2. Settings are stored in Home Assistant input entities
3. V2Gliberty reads calendar reservations and current/future SoC targets
4. FlexMeasures is queried for optimal charge/discharge schedule based on prices/emissions
5. Schedule is translated to Modbus commands sent to the charger
6. Load balancer (if enabled) intercepts Modbus commands to enforce power limits
7. UI is continuously updated with SoC prognosis, schedules, and charger status

## Key Patterns

### AppDaemon Integration
- All Python apps inherit from `appdaemon.plugins.hass.hassapi.Hass`
- Use `self.call_service()`, `self.get_state()`, `self.set_state()` for Home Assistant interaction
- Scheduled callbacks via `self.run_every()`, `self.run_at()`, etc.

### Logging
- Custom log wrapper in `log_wrapper.py` provides class/method-specific logging
- Use `get_class_method_logger(self, method_name)` for consistent log prefixes

### Home Assistant Entity Naming
- All entities prefixed with `input_`, `sensor.`, `number.`, etc.
- Entity IDs defined in constants or settings files
- UI updates via `self.set_state()` or `self.call_service("homeassistant", "update_entity")`

### Time Handling
- Always use timezone-aware datetimes via `get_local_now()` from `v2g_globals.py`
- FlexMeasures expects ISO 8601 duration strings (via `isodate` library)
- Schedules are based on 15-minute intervals

## Configuration Files

- **config.yaml**: Add-on metadata (name, version, arch support)
- **requirements.txt**: Python dependencies (AppDaemon, FlexMeasures client, pymodbus, caldav, etc.)
- **appdaemon.yaml**: AppDaemon configuration (normally auto-generated)
- **v2g_liberty_package.yaml**: Main Home Assistant package that imports all UI components
- **quasar_load_balancer.json**: Optional load balancer configuration (in HA config root)

## Testing Strategy

Tests use pytest and mock AppDaemon/Home Assistant APIs. Common patterns:
- Mock `self.get_state()` and `self.set_state()` to simulate Home Assistant state
- Mock time-based functions to test scheduling logic
- Test reservation parsing, settings validation, and schedule calculations in isolation

Legacy test data exists in `rootfs/root/tests/` but is excluded from pytest runs.

## License

Dual-licensed:
- **AGPL-3.0**: For open source and non-commercial use
- **Commercial License**: Required for proprietary/closed-source use (see COMMERCIAL_LICENSE.md)

## Version Management

- Version defined in `v2g-liberty/config.yaml`
- Follows semantic versioning (MAJOR.MINOR.PATCH)
- Changelog maintained in `v2g-liberty/CHANGELOG.md` and `v2g-liberty/changelog_of_all_releases.md`
