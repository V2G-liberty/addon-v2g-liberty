# Dev Tools

AppDaemon apps for the development environment. These are **not included in production** — the `dev_tools` directory is excluded via `exclude_dirs` in `appdaemon.yaml`.

## Grid & PV Emulator

`grid_pv_emulator.py` — Creates emulated smart meter and PV inverter sensor entities in Home Assistant. Useful for testing the grid connection settings, entity validation, and charger phase detection without real hardware.

### What it does

Updates sensor entities every ~10 seconds with realistic values:

- **Household base load** per phase, with time-of-day variation (peak morning/evening, low at night)
- **Charger impact** — reads `sensor.charger_real_charging_power` and adds it to the correct phase based on `CHARGER_CONNECTED_TO_PHASE`
- **PV production** — sine curve over the day (0 at night, peak around 13:30), per configured panel
- **Grid feed-in** — when PV surplus exceeds consumption on a phase, production entity increases

All values are in whole watts, matching real smart meter (P1/DSMR) behaviour.

### Entities created

| Entity | Description |
|--------|-------------|
| `sensor.emulated_grid_consumption_l1..l3` | Grid consumption per phase (W) |
| `sensor.emulated_grid_production_l1..l3` | Grid feed-in per phase (W) |
| `sensor.emulated_pv_power_1..n` | PV inverter power per panel (W) |
| `sensor.emulated_fuse_threshold_l1` | Fuse threshold / connection capacity (A) |
| `input_boolean.emulator_paused` | Toggle to pause/resume the emulator |

Entities are created on-the-fly via `set_state()` — no HA configuration needed. They appear in Developer Tools → States after the first update cycle (~10 sec after start).

### Configuration

Edit `dev_tools/apps.yaml`:

```yaml
grid_pv_emulator:
  module: dev_tools.grid_pv_emulator
  class: GridPvEmulator
  update_interval: 10        # seconds between updates
  pv_panels:
    - peak_wp: 3200           # panel 1: 3.2 kWp, single phase on L1
      phases: 1
      connected_to_phase: 1
    - peak_wp: 3600           # panel 2: 3.6 kWp, single phase on L2
      phases: 1
      connected_to_phase: 2
  base_load:
    l1: 300                   # base household load per phase (W)
    l2: 300
    l3: 300
  fuse_threshold: 25          # capacity per phase in ampere
```

### Pausing the emulator

Turn on `input_boolean.emulator_paused` in Developer Tools → States to stop sensor updates. This is useful for testing the "no sensor activity" scenario in the grid entity validation UI.

Turn it off to resume.

### Using with V2G Liberty

1. Start the app (it runs automatically with the V2G Liberty app via AppDaemon)
2. In the grid connection settings dialog, use the `sensor.emulated_*` entity IDs
3. The entity validation test will confirm they are emitting data
4. For charger phase detection testing: ensure the charger is connected and charging — the emulator will reflect the charger power on the correct phase

### Running outside the dev container (e.g. on pre-production)

The emulator is **dev-only** and is **stripped from the built add-on image** (`rm -rf …/dev_tools` in the `Dockerfile`), so a normal install never contains it.

**Recommended:** run it in the **dev container**, where it loads automatically from the repo. For most grid/PV UI, entity-validation and flow testing this is enough.

If you do want it on a pre-production host, you can add it by hand — and it now survives restarts:

1. Place the `dev_tools/` directory in the add-on's `/config/apps/` directory (e.g. via the add-on's file access or `docker cp` into the container). The startup copy only adds/overwrites from the image, and the cleanup no longer touches `dev_tools/`, so your folder is kept.
2. Restart AppDaemon / the add-on.
3. After ~10 seconds the `sensor.emulated_*` entities appear (Developer Tools → States). Use those entity IDs in the grid connection settings dialog; toggle `input_boolean.emulator_paused` to test the "no sensor activity" scenario.

No `appdaemon.yaml` edit is needed: `dev_tools` is no longer listed under `exclude_dirs`, so AppDaemon discovers `/config/apps/dev_tools/apps.yaml` once the folder is present. (On a normal install the folder isn't there, so nothing is loaded.)

> ⚠️ **Never place the emulator on a real production install** — it would emit fake meter/PV data.

## Charger Emulator

`charger_emulator.py` — Makes the static Wallbox Quasar Modbus mock (`quasar-mock`) **dynamic**, so V2G Liberty sees a charger that responds to its commands. This unblocks automatic charger-phase detection and lets charging, discharging, SoC and error handling be tested without real hardware.

### What it does

Acts as a **second Modbus client** to the mock and, every tick, mirrors V2G's setpoint onto the report registers:

- **Power mirror** — reads the requested power (register 260) and writes the actual power (526) and charger state (537): charging / discharging / paused.
- **Ramp** — actual power ramps up slowly and down fast, hard-clamped to the hardware max (never above it).
- **SoC** — integrates power over time into register 538, with a hardware taper at the physical bounds.

It only ever writes the report registers and never touches V2G's command registers `{81, 82, 83, 88, 257, 260}`, so it cannot fight V2G on the shared mock datastore. Per-tick status is logged to a dedicated log, `charger_emulator.log`.

### Configuration

Edit `dev_tools/apps.yaml` (`charger_emulator`):

```yaml
charger_emulator:
  module: dev_tools.charger_emulator
  class: ChargerEmulator
  charger_host: quasar-mock
  charger_port: 5020
  update_interval: 0.5          # tick, seconds
  soc_speedup: 1.0              # >1 accelerates the SoC ramp for testing
  ramp_up_seconds: 15           # time to reach full max power (slow)
  ramp_down_seconds: 2          # time to fall back to zero (fast)
  power_target_fraction: 0.92   # delivered = this fraction of the requested power
  battery_capacity_kwh: 58
  hw_soc_floor_pct: 10
  hw_soc_ceiling_pct: 97
  hw_max_charge_power_w: 5600
  hw_max_discharge_power_w: 5600
```

### Scenarios and controls

Pick a scenario and connect/disconnect the car via `input_select.emulator_charger_scenario` and `input_boolean.emulator_car_connected`. With the dev package + dashboard (`homeassistant/packages/dev_tools/`) these are interactive; otherwise change them in Developer Tools → States. A disconnect→connect triggers V2G's automatic phase detection and a fresh SoC read.

| Scenario | Simulates |
|--------|-------------|
| `normal` | Follows V2G's setpoint; SoC ramps. |
| `reduced_max_power` | Hardware max limited to 3700 W. |
| `wrong_fingerprint` | Wrong charger signature (firmware = 0); the 359 connection test flags it (`not_recognised`), dev only shows it. Charger fingerprint, not car-ID recognition. |
| `error_state` | Charger error (state 7); held > 60 s → un-recoverable. |
| `internal_error` | A non-zero internal error register. |

### Crash / no-connection scenarios (container control)

Two failure modes are genuine communication losses that **cannot** be faked with registers — they need control of the mock **container**. Run these from `.devcontainer/` (where the compose file lives), or use `docker pause`/`stop` with the container name from `docker ps`:

| Scenario | Command | Effect | Restore |
|--------|-------------|--------|---------|
| **Hung charger** (comms lost) | `docker compose pause quasar-mock` | Modbus stops responding; V2G's reads time out. | `docker compose unpause quasar-mock` |
| **No connection** | `docker compose stop quasar-mock` | TCP refused; V2G cannot connect. | `docker compose start quasar-mock` |

V2G reacts by flagging communication loss (the `charger_communication_state_change` event) and, after persistent failure, its un-recoverable-error handling. Restoring the container clears it (communication restored). Watch `v2g_liberty_main.log` for the transition.

> ⚠️ **Dev-only** — like the grid/PV emulator, `charger_emulator.py` is stripped from the built add-on image.

## EVtec BiDiPro Emulator

`charger_emulator_evtec.py` — The same idea for the **EVtec BiDiPro** (ECP4 / Modbus 2.0 register map): makes the static EVtec mock (`evtec-mock`, host port 5021) dynamic for the `evtec_bidipro` driver. It shares the tick loop, power ramp and SoC logic with the Quasar emulator (`charger_emulator_base.py`) and maps them onto the EVtec register model. The register layout, enums, device profile and scenarios live in `charger_scenarios_evtec.py` (pure data, importable by pytest); they follow the hardware-tested EVtec Modbus 2.0 contract.

### What it does

Connector `X` lives at base address `X*100` (default connector 9, like the lab charger); all multi-register values are big-endian.

- **Command** — every tick reads V2G's setpoint `X+86` (int32 W, negative = discharge). `X+88` (suspend mode) is read but **ignored**: it is a no-op on the real charger, so only a setpoint of 0 stops power flow — a driver that relies on `X+88` to stop keeps (dis)charging here, exactly as on hardware.
- **Report** — writes connector state `X+00` (7 charging, 8 discharging, 10 idle, 11 full, 1 no car), session state `X+02`, measured power `X+10` and present consumption `X+46` (float32 W), SoC `X+12` in **per-mille**, energy counters `X+18`/`X+20`, the car id `X+76` (empty when disconnected), the ChargePoint identity (model contains `crema`) and the accept windows `X+22`/`X+30`/`X+38`.
- **Interlocks** — a discharge request while the session type (`X+04`) is not v2xDynamic (3) / bidirectional (5), or while `X+22 >= 0` (V2G not offered), delivers **0 W and logs a WARNING**: a correct driver never gets there. Requests are clamped into the accept windows `[X+22, 0]` and `[X+38, X+30]`.
- Values are written per contiguous run with FC16, so an int32/float32 can never be read half-updated. It never writes `X+86..X+89` (V2G's commands), 38/39 (fallback power) or 42/43 (communication timeout, written by V2G).

Per-tick status goes to `charger_emulator_evtec.log`.

### Configuration

Edit `dev_tools/apps.yaml` (`charger_emulator_evtec`):

```yaml
charger_emulator_evtec:
  module: dev_tools.charger_emulator_evtec
  class: EVtecChargerEmulator
  charger_host: evtec-mock
  charger_port: 5020
  connector: 9                  # bound connector (base address 900)
  update_interval: 0.5          # tick, seconds
  soc_speedup: 1.0              # >1 accelerates the SoC ramp for testing
  ramp_up_seconds: 15
  ramp_down_seconds: 2
  power_target_fraction: 0.92
  battery_capacity_wh: 58000
  hw_soc_floor_pct: 10
  hw_soc_ceiling_pct: 97
  hw_max_charge_power_w: 10000  # X+30 upper limit
  hw_max_discharge_power_w: 10000
```

Further profile fields (`session_type`, `discharge_floor_w` = X+22, `car_id`, `model`, `serial`, `version`, `min_battery_capacity_wh`, `power_jitter_w`) can be set the same way.

### Scenarios and controls

Own entities, so both emulators can run side by side: `input_select.emulator_evtec_scenario`, `input_boolean.emulator_evtec_car_connected`, `input_number.emulator_evtec_soc` (interactive with the dev package + dashboard, otherwise via Developer Tools → States).

| Scenario | Simulates |
|--------|-------------|
| `normal` | Bidirectional session, V2G offered; follows V2G's setpoint, SoC ramps. |
| `v2g_not_offered` | `X+22 >= 0`: V2G not offered right now. Charging still works; a discharge request is refused (0 W + warning). Not an error. |
| `session_not_bidirectional` | Session type 1 (default): a discharge request is refused (0 W + warning). |
| `not_recognised` | Model string without `crema` — the connection test must report `not_recognised`. |
| `booting_connector` | Connector state 0 while offsets 2/4 are set: discovery on offset 0 only misses it; scan 0/2/4. Frozen. |
| `error_state` | Connector state 12 (error), power 0. Frozen. |
| `internal_error` | Error bitmask `X+54` non-zero (bit 0 = powerUnitError), decode unsigned. Frozen. |

Communication loss is faked with the container, as for the Quasar mock: `docker compose pause evtec-mock` (hung charger) / `docker compose stop evtec-mock` (no connection), from `.devcontainer/`.

> ⚠️ **Dev-only** — stripped from the built add-on image together with the rest of `dev_tools`.

### Loopback check (on demand)

`loopback_check_evtec.py` — starts an in-process Modbus TCP server, points the emulator at it and plays the part of the `evtec_bidipro` driver with a second client, using that driver's read/write patterns (model at address 26, connector discovery over 1..10 on offsets 0/2/4, one block read of `X+0..X+57`, FC16 writes to `X+86`/`X+88`).

```bash
python3 rootfs/root/appdaemon/apps/dev_tools/loopback_check_evtec.py [--port N]
```

It exits non-zero on the first failed check, so it can be wired into a script. Run it after changing the register model, the encoders or the write path.

**Why it is a script and not a test:** every test under `tests/` drives Modbus through an in-memory fake, and none of them binds a socket. This check deliberately does the opposite — it exercises the real pymodbus serialisation, the big-endian word order, block reads across the object boundary and the FC16 write path, which a fake cannot cover. That realism costs a bound port and a few seconds of settling time, so it stays out of the suite (`pytest rootfs/root/appdaemon/tests/dev_tools`, ~1 s) where it would be slow and flaky.
