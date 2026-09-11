"""EVtec BiDiPro (ECP4 / Modbus 2.0) scenarios and device profile for the dev
charger emulator.

Pure data — no Home Assistant, pymodbus or AppDaemon imports — so this module
is importable by both the dev emulator and by pytest (which can drive the same
scenarios against a fake Modbus client to assert the real ``evtec_bidipro``
driver behaviour). Mirrors the sibling ``charger_scenarios.py`` (Wallbox Quasar)
but for the structurally different EVtec register model.

Register layout, enums and the discharge/charge windows are the hardware-tested
contract from ``.private/Integrating EVtec BiDi Pro/MODBUS_PROXY.md`` (2.0 map):
nine 100-register objects on one unit; the ChargePoint object at absolute
0..43, connector ``X`` at base ``X*100`` with fields at ``X*100 + offset``.
Multi-register values are **big-endian**: int32/float32 occupy 2 registers,
int64 occupies 4, strings 2 chars per register (NUL-terminated).

Dev-only: not part of the production add-on.
"""

import struct
from dataclasses import dataclass, field

# --- Addressing model ------------------------------------------------------
# ChargePoint object lives at absolute addresses; connector fields are given as
# OFFSETS relative to the connector base (connector X -> base X*100). The lab
# charger binds connector 9 (see MODBUS_PROXY.md §2), so that is the default.
DEFAULT_CONNECTOR = 9

# --- ChargePoint object (absolute addresses) -------------------------------
CP_VERSION = 0  # string, 16 regs
CP_SERIAL = 16  # string, 10 regs
CP_MODEL = 26  # string, 10 regs — must contain 'crema' on supported firmware
CP_NUM_CONNECTORS = 36  # int32
CP_STATE = 40  # int32

# --- Connector object field offsets (relative to X*100) --------------------
# Registers V2G Liberty WRITES (the emulator only reads these):
OFF_INPUT_POWER = 86  # int32, signed W — THE setpoint (neg = discharge)
OFF_SUSPEND_MODE = 88  # int32 — documented on/off gate; a no-op in practice

# Registers V2G Liberty READS (the emulator writes these):
OFF_CONNECTOR_STATE = 0  # int32, enum (see below)
OFF_SESSION_STATE = 2  # int32, CCS finer state
OFF_SESSION_TYPE = 4  # int32 — 3=v2xDynamic / 5=bidirectional permit V2G
OFF_VOLTAGE = 6  # float32, V
OFF_CURRENT = 8  # float32, A
OFF_POWER = 10  # float32, signed W — measured power
OFF_SOC = 12  # int32, per-mille (‰)
OFF_CONNECTOR_TYPE = 14  # int32 (0=type2,1=ccs,2=chademo,3=gbt)
OFF_DISCHARGED_ENERGY = 20  # float32, Wh
OFF_LOWER_LIMIT_POTENTIAL = 22  # float32, W — < 0 ⇒ V2G offered; discharge floor
OFF_UPPER_LIMIT = 30  # float32, W — charge ceiling
OFF_LOWER_LIMIT = 38  # float32, W — charge floor
OFF_PRESENT_CONSUMPTION = 46  # float32, W — live realized setpoint
OFF_ERROR = 54  # int64/uint64, 40-bit bitmask
OFF_BATTERY_CAPACITY = 58  # float32, Wh
OFF_MIN_BATTERY_CAPACITY = 62  # float32, Wh
OFF_CAR_ID = 76  # string — non-empty ⇒ a car is connected (EvccId)

# --- Connector state codes (offset 0) --------------------------------------
STATE_BOOT = 0
STATE_READY = 1  # no car connected
STATE_UNAVAILABLE = 2
STATE_PLUGGED = 5  # connected, not yet controllable
STATE_INIT_CONNECTION = 6
STATE_CURRENT_DEMAND = 7  # charging
STATE_GRID_FEED = 8  # discharging (V2G)
STATE_SESSION_STOP = 9
STATE_WAIT_FOR_GRID = 10
STATE_CHARGING_END = 11  # car still connected, awaiting unplug
STATE_ERROR = 12

# --- Charge session type (offset 4) ----------------------------------------
SESSION_OTHER = 0
SESSION_DEFAULT = 1
SESSION_DYNAMIC = 2
SESSION_V2X_DYNAMIC = 3  # permits bidirectional
SESSION_HPC_CHADEMO = 4
SESSION_BIDIRECTIONAL = 5  # permits bidirectional
BIDIRECTIONAL_SESSION_TYPES = frozenset({SESSION_V2X_DYNAMIC, SESSION_BIDIRECTIONAL})

CONNECTOR_TYPE_CCS = 1


# --- Big-endian register encoding ------------------------------------------
# oitc/modbus-server stores uint16 holding registers; multi-register values are
# consecutive big-endian words. These helpers return the uint16 word list to
# seed/write, mirroring MBR.encode() in chargers/modbus_types.py.
def enc_int32(value: int) -> list[int]:
    """Signed int32 → [hi, lo] big-endian uint16 words."""
    hi, lo = struct.unpack(">HH", struct.pack(">i", int(value)))
    return [hi, lo]


def enc_float32(value: float) -> list[int]:
    """float32 → [hi, lo] big-endian uint16 words."""
    hi, lo = struct.unpack(">HH", struct.pack(">f", float(value)))
    return [hi, lo]


def enc_int64(value: int) -> list[int]:
    """uint64 → 4 big-endian uint16 words."""
    return list(
        struct.unpack(">HHHH", struct.pack(">Q", int(value) & 0xFFFFFFFFFFFFFFFF))
    )


def enc_string(text: str, length_regs: int) -> list[int]:
    """String → uint16 words (2 chars per register, NUL-padded to length_regs)."""
    raw = text.encode("ascii", "ignore")[: length_regs * 2]
    raw = raw.ljust(length_regs * 2, b"\x00")
    return list(struct.unpack(f">{length_regs}H", raw))


def words_at(base: int, words: list[int]) -> dict[int, int]:
    """Expand a big-endian word list into an {absolute_address: uint16} map."""
    return {base + i: w for i, w in enumerate(words)}


# --- Charger device profile ------------------------------------------------
@dataclass
class EVtecProfile:  # pylint: disable=too-many-instance-attributes
    """Hardware constants the EVtec charger/car enforces.

    Independent of V2G Liberty's user settings — the point is to test whether
    V2G respects its own limits and the charger's offered windows. SoC
    floor/ceiling are behavioural (the emulator freezes SoC and tapers power at
    these bounds). ``model`` must contain 'crema' for the driver's signature
    check to pass. ``session_type`` and ``discharge_floor_w`` drive the two V2G
    interlocks the driver must honour (MODBUS_PROXY.md §4).
    """

    connector: int = DEFAULT_CONNECTOR
    hw_max_charge_power_w: int = 10000
    hw_max_discharge_power_w: int = 10000
    hw_soc_floor_pct: float = 10.0
    hw_soc_ceiling_pct: float = 97.0
    battery_capacity_wh: float = 58000.0
    min_battery_capacity_wh: float = 6000.0
    model: str = "cremacharge"
    serial: str = "EVTEC-DEV-01"
    version: str = "ECP4-2.0-dev"
    car_id: str = "DEVCAR-EVCCID-01"
    session_type: int = SESSION_BIDIRECTIONAL
    connector_type: int = CONNECTOR_TYPE_CCS
    # Discharge is offered only when the lower-limit-potential-total (X+22) < 0;
    # the valid discharge range is [discharge_floor_w, 0]. >= 0 means "V2G not
    # offered right now" (not an error).
    discharge_floor_w: float = -10000.0
    power_jitter_w: int = 50


EVTEC_BIDI_PRO_10 = EVtecProfile()


# --- Scenarios -------------------------------------------------------------
@dataclass
class EVtecScenario:
    """A selectable emulator scenario for the EVtec charger.

    ``mirror=True`` (dynamic): each tick the emulator derives state and power
    from V2G's setpoint (X+86) and ramps SoC. ``mirror=False`` (frozen): holds
    the seeded registers (error/fault scenarios). ``registers`` are extra raw
    {absolute_address: uint16} words written on activation (they win over the
    seed). ``profile_overrides`` tweak the device profile for this scenario.
    """

    name: str
    description: str
    mirror: bool = True
    start_soc: int = 33
    registers: dict[int, int] = field(default_factory=dict)
    profile_overrides: dict = field(default_factory=dict)


def _connector_reg(offset: int, words: list[int], connector: int = DEFAULT_CONNECTOR):
    """Helper: {absolute_address: uint16} for a connector field (base X*100)."""
    return words_at(connector * 100 + offset, words)


SCENARIOS: dict[str, EVtecScenario] = {
    "normal": EVtecScenario(
        name="normal",
        description=(
            "Normal bidirectional operation: session type = bidirectional, V2G "
            "offered (X+22 < 0); state and power follow V2G's X+86 setpoint, SoC "
            "ramps. Use the car-connected toggle to test connect/disconnect."
        ),
    ),
    "v2g_not_offered": EVtecScenario(
        name="v2g_not_offered",
        description=(
            "Charger does NOT offer V2G right now: lower-limit-potential-total "
            "(X+22) >= 0. A correct driver must treat this as 'no discharge "
            "available', NOT an error — charging still allowed. Exercises the "
            "X+22 discharge-window gap from MODBUS_PROXY.md."
        ),
        profile_overrides={"discharge_floor_w": 0.0},
    ),
    "session_not_bidirectional": EVtecScenario(
        name="session_not_bidirectional",
        description=(
            "Session type = default (1), i.e. not v2xDynamic(3)/bidirectional(5). "
            "A correct driver must refuse to discharge (X+04 interlock). Exercises "
            "the missing session-type interlock from MODBUS_PROXY.md."
        ),
        profile_overrides={"session_type": SESSION_DEFAULT},
    ),
    "not_recognised": EVtecScenario(
        name="not_recognised",
        description=(
            "Wrong charger signature: model string (addr 26) does not contain "
            "'crema'. test_connection should return 'not_recognised'."
        ),
        registers=words_at(CP_MODEL, enc_string("acme-evse", 10)),
    ),
    "booting_connector": EVtecScenario(
        name="booting_connector",
        description=(
            "Bound connector is still booting (state 0) while other fields are "
            "set. Connector-discovery on offset-0 only would treat it as "
            "unconfigured; the contract says scan offsets 0/2/4. Frozen."
        ),
        mirror=False,
        registers=_connector_reg(OFF_CONNECTOR_STATE, enc_int32(STATE_BOOT)),
    ),
    "error_state": EVtecScenario(
        name="error_state",
        description=(
            "Frozen error: connector state 12 (error), power 0. Held long enough "
            "triggers the driver's un-recoverable-error handling."
        ),
        mirror=False,
        registers={
            **_connector_reg(OFF_CONNECTOR_STATE, enc_int32(STATE_ERROR)),
            **_connector_reg(OFF_POWER, enc_float32(0.0)),
        },
    ),
    "internal_error": EVtecScenario(
        name="internal_error",
        description=(
            "Frozen internal error: error bitmask (X+54) non-zero (bit 0 = "
            "powerUnitError), power 0. Decode as unsigned."
        ),
        mirror=False,
        registers={
            **_connector_reg(OFF_ERROR, enc_int64(1)),
            **_connector_reg(OFF_POWER, enc_float32(0.0)),
        },
    ),
}

DEFAULT_SCENARIO = "normal"
