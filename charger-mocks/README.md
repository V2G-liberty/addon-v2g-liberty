# Charger Mocks for V2G Liberty Testing

Mock chargers for testing [V2G Liberty](https://github.com/V2G-liberty/addon-v2g-liberty) without physical hardware.

**Currently supported:** Wallbox Quasar bidirectional (V2G) charger

---

## Quick Start (5 Minutes)

### 1. Install Dependencies

```bash
cd charger-mocks
pip install -r requirements.txt
```

### 2. Start the Mock Server

```powershell
# From repository root
.\setup-devcontainer.ps1 start-quasar
```

### 3. Start the CLI

```bash
cd charger-mocks/quasar
python cli.py
```

**Expected output:**
```
============================================================
Wallbox Quasar Mock CLI v2.1.0
Connecting to Modbus server at localhost:5020
============================================================

✓ Connected successfully to localhost:5020

Control (0=user, 1=remote): 1
state: Charging (No internal error)
power: 4663 W  (req.: 5750 W, max.: 7400 W)
soc  : 33 %
Quasar:
```

### 4. Try Commands

```
status              # Check current state
soc 75              # Set battery to 75%
charge 3000         # Charge at 3000W
charge -2000        # Discharge at 2000W (V2G)
disconnect          # Simulate car disconnect
error               # Simulate error
connect             # Clear error
quit                # Exit
```

**What's happening:**
- CLI sends Modbus commands to the Docker server
- Server stores values in memory (passive - only changes when you command it)
- Power randomizes to 81-97% of requested (simulates real charger)

---

## Directory Structure

```
charger-mocks/
├── README.md                          # This file
├── requirements.txt                   # Python dependencies (pyModbusTCP)
├── configs/                           # Initial register values
│   └── quasar_charging_33pct.json     # Default: charging at 33% SoC
└── quasar/                            # Wallbox Quasar mock
    ├── cli.py                         # Command-line interface
    ├── register_map.py                # Modbus register addresses
    └── states.py                      # Charger state definitions
```

---

## Architecture

**Two components:**

1. **Modbus Server (Docker)**: Passive Modbus TCP server on port 5020. Responds to requests but doesn't change values automatically.

2. **CLI (Python)**: Sends Modbus commands to simulate charging scenarios.

**Modular design:**
- `register_map.py` - Register addresses, encoding helpers, limits
- `states.py` - State codes and descriptions
- `cli.py` - Interactive commands, imports from above

**Key Modbus Registers (Quasar):**

| Register | Address | Purpose | Range |
|----------|---------|---------|-------|
| 81 | 0x0051 | Control mode | 0=User, 1=Remote |
| 260 | 0x0104 | Requested power | -7400 to 7400 W |
| 526 | 0x020E | Actual power | -7400 to 7400 W |
| 537 | 0x0219 | Charger state | 0-11 (see states.py) |
| 538 | 0x021A | State of Charge | 0-100 % |

*Negative power uses unsigned encoding: value + 65536*

---

## Networking

### Docker Networking Overview

The mock charger runs in a Docker container with a **static IP address** for reliable connectivity in the devcontainer environment.

**Network Configuration:**
- Network: `v2g_network` (custom bridge network)
- Subnet: `172.20.0.0/16`
- Gateway: `172.20.0.1`
- Quasar Mock IP: **`172.20.0.10`** (static)

This configuration is defined in [../.devcontainer/docker-compose.yaml](../.devcontainer/docker-compose.yaml).

### When to Use Different Addresses

The correct address depends on **where you're connecting from**:

| Context | Use This Address | Why |
|---------|------------------|-----|
| **Home Assistant UI** (charger config) | `172.20.0.10:5020` | Home Assistant runs in Docker network, uses static IP |
| **V2G Liberty app** (in container) | `172.20.0.10:5020` | AppDaemon runs in Docker network, uses static IP |
| **CLI from host machine** | `localhost:5020` | CLI runs on host, uses Docker port mapping |
| **With Load Balancer enabled** | `127.0.0.1:5020` | Load balancer proxies between app and charger |

### Why NOT to Use `0.0.0.0`

You may see `"listenerAddress": "0.0.0.0"` in the config files. This is the **server bind address**, which means "listen on all network interfaces."

- ✅ **Server bind**: `0.0.0.0` (tells server where to listen)
- ❌ **Client connect**: `0.0.0.0` (not a valid connection target)

When configuring V2G Liberty in Home Assistant, you must use the **static IP** (`172.20.0.10`), not the bind address.

### Port Mapping

The Docker container exposes port 5020 to the host:

```yaml
ports:
  - "5020:5020"  # host:container
```

This allows:
- **External access** from host machine (e.g., CLI): `localhost:5020`
- **Internal access** from other containers: `172.20.0.10:5020`

---

## CLI Usage

### Starting the CLI

**Local (default):**
```bash
cd charger-mocks/quasar
python cli.py
```

**Remote:**
```bash
python cli.py --host 192.168.1.100 --port 5020
python cli.py -H 192.168.1.100 -p 5020 -v  # verbose
```

### Commands

**Status:**
- `status` - Show state, power, SoC
- `version` - CLI version
- `connection` - Connection details

**Control:**
- `connect` / `disconnect` - Simulate car connection
- `soc <0-100>` - Set state of charge
- `charge <watts>` - Set power (-7400 to 7400)
- `set_max_power <watts>` - Set max available power
- `follow` - Match actual to requested power (auto-runs)

**Error Simulation:**
- `error` / `no_error` - Set/clear error state
- `ie` / `no_ie` - Set/clear internal error
- `app_control` - Wallbox app takes control
- `take_control` - Remote control mode

**Exit:**
- `quit` / `exit` / `q`

---

## Configuration

Config files in `configs/` set initial Modbus register values.

**Default:** `quasar_charging_33pct.json`
- State: Charging
- SoC: 33%
- Power: 5750W
- Control: Remote

**Create custom configs:**
1. Copy existing config
2. Modify register values
3. Name: `{charger}_{state}_{param}.json`
4. Update docker-compose volume mount

---

## Development

### Adding a New Charger Type

When V2G Liberty adds support for another charger:

1. **Create directory:** `charger-mocks/new-charger/`
2. **Add files:**
   - `register_map.py` - Register addresses and helpers
   - `states.py` - State definitions
   - `cli.py` - Commands (import from above, use argparse)
3. **Add config:** `configs/new_charger_default.json`
4. **Update docker-compose** if needed

**Result:**
```
charger-mocks/
├── configs/
│   ├── quasar_charging_33pct.json
│   └── new_charger_default.json
├── quasar/
│   ├── cli.py
│   ├── register_map.py
│   └── states.py
└── new-charger/
    ├── cli.py
    ├── register_map.py
    └── states.py
```

### Code Style

- Descriptive names, no abbreviations
- Docstrings on classes and methods
- Keep register mappings separate from CLI
- Group related commands

### Common Tasks

**Add CLI command:**
1. Add `def do_command(self, args):` to CLI class
2. Add docstring (shows in help)
3. Write/read Modbus registers
4. Call `_show_status()`

**Update registers:**
1. Edit `register_map.py`
2. Add constant
3. Import in `cli.py`

---

## Troubleshooting

### Can't Connect

1. Check Docker: `docker ps | grep quasar-mock`
2. Check port: `netstat -an | grep 5020`
3. Test CLI: `python cli.py -v`

### Commands Don't Work

1. Install deps: `pip install -r requirements.txt`
2. Check directory: `cd charger-mocks/quasar`
3. Run verbose: `python cli.py -v`

### Server Won't Start

1. Docker running: `docker info`
2. Config exists: `ls configs/quasar_charging_33pct.json`
3. Check logs: `docker-compose logs quasar-mock`
4. Port available: `netstat -ano | findstr :5020`

---

**Repository:** https://github.com/V2G-liberty/addon-v2g-liberty
