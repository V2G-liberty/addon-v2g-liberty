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
