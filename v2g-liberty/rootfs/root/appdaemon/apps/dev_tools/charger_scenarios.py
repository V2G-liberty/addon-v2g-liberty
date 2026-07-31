"""Charger scenarios and device profile for the dev charger emulator.

Pure data — no Home Assistant, pymodbus or AppDaemon imports — so this module
is importable by both the dev emulator (``dev_tools.charger_emulator``) and by
pytest, which can drive the same scenarios against a fake Modbus client to
assert the real ``modbus_evse_client`` behaviour.

Register addresses, state codes and the signed-power encoding mirror the real
Wallbox Quasar driver (``v2g_liberty/modbus_evse_client.py``) and the static
mock (``charger-mocks/quasar``).

Dev-only: not part of the production add-on.
"""

from dataclasses import dataclass, field

# --- Wallbox Quasar register addresses -------------------------------------
# Registers V2G Liberty WRITES (the emulator only reads these):
REG_CONTROL = 81  # 1 = remote (V2G in control)
REG_ACTION = 257  # 1 = start, 2 = stop
REG_SETPOINT = 260  # requested power, signed W

# Registers V2G Liberty READS (the emulator writes these):
REG_FIRMWARE = 1
REG_SERIAL_HIGH = 2
REG_SERIAL_LOW = 3
REG_LOCKED = 256  # 0 = unlocked
REG_MAX_POWER = 514  # max available power, W
REG_ACTUAL_POWER = 526  # actual power, signed W
REG_STATE = 537  # charger state (see below)
REG_SOC = 538  # state of charge, %
REG_ERROR_1 = 539
REG_ERROR_2 = 540
REG_ERROR_3 = 541
REG_ERROR_4 = 542

# The emulator may ONLY write these report registers. It must never write V2G's
# command registers {81, 82, 83, 88, 257, 260}: on the shared mock datastore
# that would clobber V2G's own control/action/setpoint.
EMULATOR_WRITE_REGISTERS = frozenset(
    {
        REG_FIRMWARE,
        REG_SERIAL_HIGH,
        REG_SERIAL_LOW,
        REG_LOCKED,
        REG_MAX_POWER,
        REG_ACTUAL_POWER,
        REG_STATE,
        REG_SOC,
        REG_ERROR_1,
        REG_ERROR_2,
        REG_ERROR_3,
        REG_ERROR_4,
    }
)

# --- Charger state codes (register 537) ------------------------------------
STATE_DISCONNECTED = 0
STATE_CHARGING = 1
STATE_WAITING = 2  # connected, waiting for car demand
STATE_PAUSED = 4  # connected, not charging
STATE_LOCKED = 6
STATE_ERROR = 7
STATE_DISCHARGING = 11

# --- Signed-power (two's complement) encoding ------------------------------
_MAX_UNSIGNED_SHORT = 65536


def int16_to_uint16(value: int) -> int:
    """Encode a signed value as an unsigned 16-bit Modbus register value."""
    return value + _MAX_UNSIGNED_SHORT if value < 0 else value


def uint16_to_int16(value: int) -> int:
    """Decode an unsigned 16-bit Modbus register value as a signed value."""
    return value - _MAX_UNSIGNED_SHORT if value > _MAX_UNSIGNED_SHORT // 2 else value


# --- Charger device profile ------------------------------------------------
@dataclass
class ChargerProfile:  # pylint: disable=too-many-instance-attributes
    """Hardware constants the physical charger/car enforces.

    Independent of V2G Liberty's user settings (min/max SoC, capacity) — the
    point is to test whether V2G respects its own limits. The SoC floor/ceiling
    are behavioural (there is no such register): the emulator freezes the SoC
    and tapers power to zero at these bounds. ``hw_max_*_power_w`` surfaces on
    register 514, the identity fields on registers 1-3.

    Keep ``hw_soc_floor_pct`` >= 2: the driver treats a polled SoC of 0/1 as
    'unavailable', not as a real low battery.
    """

    hw_max_charge_power_w: int = 5600
    hw_max_discharge_power_w: int = 5600
    hw_soc_floor_pct: float = 10.0
    hw_soc_ceiling_pct: float = 97.0
    battery_capacity_kwh: float = 58.0
    firmware: int = 3400
    serial_high: int = 5
    serial_low: int = 19659
    power_jitter_w: int = 50


QUASAR_1 = ChargerProfile()


# --- Scenarios -------------------------------------------------------------
@dataclass
class ChargerScenario:
    """A selectable emulator scenario.

    ``mirror=True`` (dynamic): each tick the emulator derives the state and
    actual power from V2G's setpoint (register 260) and ramps the SoC — normal
    operation. ``mirror=False`` (frozen): the emulator holds ``registers`` as
    seeded and does not run the mirror — used for error/fault scenarios.

    ``registers`` are extra raw uint16 values written on activation (a forced
    error state, a non-zero error register, a wrong identity). They win over
    the seeded defaults. ``profile_overrides`` tweak the device profile for
    this scenario only (e.g. a reduced or invalid max power).
    """

    name: str
    description: str
    mirror: bool = True
    start_soc: int = 33
    registers: dict[int, int] = field(default_factory=dict)
    profile_overrides: dict = field(default_factory=dict)


SCENARIOS: dict[str, ChargerScenario] = {
    "normal": ChargerScenario(
        name="normal",
        description=(
            "Normal operation: state and power follow V2G's setpoint, SoC ramps. "
            "Use the car-connected toggle to test connect/disconnect."
        ),
    ),
    "wrong_fingerprint": ChargerScenario(
        name="wrong_fingerprint",
        description=(
            "Wrong charger signature (firmware register = 0). The 359 connection "
            "test flags it (test_connection -> not_recognised); dev does not "
            "validate it. This is the charger fingerprint, not car-ID recognition."
        ),
        registers={REG_FIRMWARE: 0, REG_SERIAL_HIGH: 0, REG_SERIAL_LOW: 0},
    ),
    "reduced_max_power": ChargerScenario(
        name="reduced_max_power",
        description="Mirror with a reduced hardware max power (register 514 = 3700 W).",
        profile_overrides={
            "hw_max_charge_power_w": 3700,
            "hw_max_discharge_power_w": 3700,
        },
    ),
    "error_state": ChargerScenario(
        name="error_state",
        description="Frozen error: state 7, power 0. Held > 60 s triggers un-recoverable handling.",
        mirror=False,
        registers={REG_STATE: STATE_ERROR, REG_ACTUAL_POWER: 0},
    ),
    "internal_error": ChargerScenario(
        name="internal_error",
        description="Frozen internal error: error register 539 non-zero, power 0.",
        mirror=False,
        registers={REG_STATE: STATE_PAUSED, REG_ACTUAL_POWER: 0, REG_ERROR_1: 1234},
    ),
}

DEFAULT_SCENARIO = "normal"
