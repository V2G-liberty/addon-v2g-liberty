#!/usr/bin/env python3
# pylint: disable=unused-argument  # Test functions share a uniform (client, args) signature
"""Standalone hardware test script for the EVtec BiDiPro charger.

Tests IP connectivity, reads all Modbus registers, and optionally sends
write commands to the charger.  No AppDaemon or Home Assistant required —
only pymodbus must be installed.

Usage:
    python hardware_tests/test_evtec_bidipro.py --host 192.168.1.100
    python hardware_tests/test_evtec_bidipro.py --host 192.168.1.100 --write
    python hardware_tests/test_evtec_bidipro.py --host 192.168.1.100 --tests 1,2,6,8

See instructions.md for full documentation.
"""

import argparse
import asyncio
import os
import sys

# ---------------------------------------------------------------------------
# Make modbus_types importable without installing the full AppDaemon app
# ---------------------------------------------------------------------------
_CHARGERS_DIR = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "rootfs",
        "root",
        "appdaemon",
        "apps",
        "v2g_liberty",
        "chargers",
    )
)
sys.path.insert(0, _CHARGERS_DIR)

from modbus_types import MBR  # noqa: E402  # type: ignore[import-not-found]
from pymodbus.client import AsyncModbusTcpClient  # noqa: E402
from pymodbus.exceptions import ConnectionException, ModbusException  # noqa: E402

# ===========================================================================
# Register definitions  (mirrored from EVtecBiDiProClient)
# ===========================================================================

# Read-once at boot
MBR_EVSE_VERSION = MBR(address=0, data_type="string", length=16)
MBR_EVSE_SERIAL_NUMBER = MBR(address=16, data_type="string", length=10)
MBR_EVSE_MODEL = MBR(address=26, data_type="string", length=10)
MBR_IDLE_TIMEOUT_SEC = MBR(address=42, data_type="int32", length=2)
MBR_EVSE_STATE = MBR(address=100, data_type="int32", length=2)
MBR_ACTUAL_POWER = MBR(address=110, data_type="float32", length=2)
MBR_CAR_SOC = MBR(address=112, data_type="float32", length=2)
MBR_CONNECTOR_TYPE = MBR(address=114, data_type="int32", length=2)
MBR_MAX_CHARGE_POWER = MBR(address=130, data_type="float32", length=2)
MBR_ERROR = MBR(address=154, data_type="int64", length=4)  # "int64", not "64int"
MBR_CAR_BATTERY_CAPACITY_WH = MBR(address=158, data_type="float32", length=2)
MBR_CAR_MIN_BATTERY_CAPACITY_WH = MBR(address=162, data_type="float32", length=2)
MBR_CAR_ID = MBR(address=176, data_type="string", length=10)

# Write-only registers
MBR_SET_CHARGE_POWER = MBR(address=186, data_type="int32", length=2)
MBR_SET_ACTION = MBR(address=188, data_type="int32", length=2)

# Convenience collections
_POLL_REGISTERS = [MBR_EVSE_STATE, MBR_ACTUAL_POWER, MBR_CAR_SOC, MBR_ERROR]

DEFAULT_PORT = 5020
DEVICE_ID = 1

# BiDiPro raw state → (base_state_code, description)
BIDIPRO_STATES: dict[int, tuple[int, str]] = {
    0: (0, "Booting"),
    1: (1, "No car connected"),
    2: (9, "Error"),
    3: (7, "OCPP / external control"),
    4: (1, "Contract authorisation → no car connected"),
    5: (1, "Plugged in + power check → no car connected"),
    6: (1, "Initialise connection → no car connected"),
    7: (3, "Charging"),
    8: (4, "Discharging"),
    9: (7, "OCPP / external control"),
    10: (2, "Connected & idle"),
    11: (1, "No car connected"),
    12: (9, "Error"),
}

BASE_STATE_NAMES: dict[int, str] = {
    0: "Starting up",
    1: "No car connected",
    2: "Idle",
    3: "Charging",
    4: "Discharging",
    5: "Charging, externally reduced power",
    6: "Discharging, externally reduced power",
    7: "Controlled by other app",
    8: "Locked",
    9: "Error",
    10: "Communication error",
}

CONNECTOR_TYPE_NAMES: dict[int, str] = {
    0: "Type 2",
    1: "CCS",
    2: "CHAdeMO",
    3: "GBT",
}

# ===========================================================================
# Output helpers
# ===========================================================================

_OK = " OK "
_FAIL = "FAIL"
_WARN = "WARN"
_INFO = "INFO"
_SKIP = "SKIP"

_MARKERS = {_OK: "+", _FAIL: "x", _WARN: "!", _INFO: ".", _SKIP: "-"}


def _print(
    test_num: int, name: str, status: str, value=None, note: str | None = None
) -> None:
    marker = _MARKERS.get(status, " ")
    value_part = f"  ->  {value}" if value is not None else ""
    note_part = f"  [{note}]" if note else ""
    print(f"  [{status}]  {marker}  Test {test_num:02d}: {name}{value_part}{note_part}")


# ===========================================================================
# Low-level Modbus helpers
# ===========================================================================


async def _read(client: AsyncModbusTcpClient, mbr: MBR):
    """Read a single MBR; returns decoded value or None on any error."""
    try:
        resp = await client.read_holding_registers(
            address=mbr.address,
            count=mbr.length,
            device_id=DEVICE_ID,
        )
        if resp.isError():
            return None
        return mbr.decode(resp.registers)
    except (ModbusException, ConnectionException, AttributeError):
        return None


async def _read_batch(client: AsyncModbusTcpClient, mbrs: list[MBR]) -> list:
    """Read multiple MBRs in a single request; returns list matching input order."""
    if not mbrs:
        return []
    sorted_mbrs = sorted(mbrs, key=lambda r: r.address)
    start = sorted_mbrs[0].address
    end = sorted_mbrs[-1].address + sorted_mbrs[-1].length - 1
    try:
        resp = await client.read_holding_registers(
            address=start,
            count=end - start + 1,
            device_id=DEVICE_ID,
        )
        if resp.isError():
            return [None] * len(mbrs)
    except (ModbusException, ConnectionException):
        return [None] * len(mbrs)
    regs = resp.registers
    decoded = {
        id(mbr): mbr.decode(
            regs[mbr.address - start : mbr.address - start + mbr.length]
        )
        for mbr in sorted_mbrs
    }
    return [decoded[id(mbr)] for mbr in mbrs]


async def _write(client: AsyncModbusTcpClient, mbr: MBR, value) -> bool:
    """Write a value to a MBR; returns True on success."""
    registers = mbr.encode(value)
    if not registers:
        return False
    try:
        if len(registers) == 1:
            resp = await client.write_register(
                address=mbr.address,
                value=registers[0],
                device_id=DEVICE_ID,
            )
        else:
            resp = await client.write_registers(
                address=mbr.address,
                values=registers,
                device_id=DEVICE_ID,
            )
        return not resp.isError()
    except (ModbusException, ConnectionException):
        return False


def _fmt_state(raw) -> str:
    """Format a raw BiDiPro state value as a human-readable string."""
    if raw is None:
        return "None"
    if raw not in BIDIPRO_STATES:
        return f"raw={raw} (unknown)"
    base, desc = BIDIPRO_STATES[raw]
    base_name = BASE_STATE_NAMES.get(base, "?")
    return f"BiDiPro={raw} ({desc}) -> base={base} ({base_name})"


# ===========================================================================
# Read tests
# ===========================================================================


async def test_01_connection(
    _client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Test 1: TCP connection (already established before this is called)."""
    _print(1, "IP connection", _OK, f"{args.host}:{args.port}")


async def test_02_firmware_version(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Read firmware version string from register 0."""
    val = await _read(client, MBR_EVSE_VERSION)
    if val and val.strip():
        _print(2, "Firmware version", _OK, val.strip())
    else:
        _print(2, "Firmware version", _FAIL, val)


async def test_03_serial_number(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Read serial number string from register 16."""
    val = await _read(client, MBR_EVSE_SERIAL_NUMBER)
    if val and val.strip():
        _print(3, "Serial number", _OK, val.strip())
    else:
        _print(3, "Serial number", _FAIL, val)


async def test_04_model(client: AsyncModbusTcpClient, args: argparse.Namespace) -> None:
    """Read model identifier string from register 26."""
    val = await _read(client, MBR_EVSE_MODEL)
    if val and val.strip():
        _print(4, "Model", _OK, val.strip())
    else:
        _print(4, "Model", _FAIL, val)


async def test_05_idle_timeout(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Read the current communication idle timeout setting (register 42)."""
    val = await _read(client, MBR_IDLE_TIMEOUT_SEC)
    if val is not None:
        _print(5, "Idle timeout", _INFO, f"{val} s")
    else:
        _print(5, "Idle timeout", _FAIL)


async def test_06_evse_state(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Read EVSE connector state (register 100) and map to base state name."""
    val = await _read(client, MBR_EVSE_STATE)
    if val is None:
        _print(6, "EVSE state", _FAIL, "could not read")
        return
    _print(6, "EVSE state", _INFO, _fmt_state(val))


async def test_07_actual_power(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Read actual charge/discharge power in Watts (register 110)."""
    val = await _read(client, MBR_ACTUAL_POWER)
    if val is None:
        _print(7, "Actual power", _FAIL)
        return
    if -20000 <= val <= 20000:
        direction = "charging" if val > 10 else ("discharging" if val < -10 else "idle")
        _print(7, "Actual power", _OK, f"{val:.1f} W ({direction})")
    else:
        _print(7, "Actual power", _WARN, f"{val:.1f} W", "outside expected range")


async def test_08_soc(client: AsyncModbusTcpClient, args: argparse.Namespace) -> None:
    """Read car battery state of charge in percent (register 112)."""
    val = await _read(client, MBR_CAR_SOC)
    if val is None:
        _print(8, "State of Charge", _WARN, "None", "no car connected or unavailable")
        return
    if 1 <= val <= 100:
        _print(8, "State of Charge", _OK, f"{val:.1f} %")
    else:
        _print(8, "State of Charge", _WARN, f"{val:.1f} %", "outside 1-100 %")


async def test_09_connector_type(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Read connector type code (register 114): 0=Type2, 1=CCS, 2=CHAdeMO, 3=GBT."""
    val = await _read(client, MBR_CONNECTOR_TYPE)
    if val is None:
        _print(9, "Connector type", _FAIL)
        return
    name = CONNECTOR_TYPE_NAMES.get(val, f"unknown (raw={val})")
    status = _OK if val in CONNECTOR_TYPE_NAMES else _WARN
    _print(9, "Connector type", status, name)


async def test_10_max_charge_power(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Read hardware maximum charge power in Watts (register 130)."""
    val = await _read(client, MBR_MAX_CHARGE_POWER)
    if val is not None and val > 0:
        _print(10, "Max charge power", _OK, f"{val:.0f} W ({val / 1000:.1f} kW)")
    else:
        _print(10, "Max charge power", _FAIL, val)


async def test_11_error_register(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Read 64-bit error bitmask (register 154); 0 means no errors."""
    val = await _read(client, MBR_ERROR)
    if val is None:
        _print(11, "Error register", _WARN, "could not read")
        return
    if val == 0:
        _print(11, "Error register", _OK, "0 (no errors)")
    else:
        _print(11, "Error register", _WARN, f"0x{val:016X}", "non-zero error code")


async def test_12_car_battery_capacity(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Read connected EV's full battery capacity in Wh (register 158)."""
    val = await _read(client, MBR_CAR_BATTERY_CAPACITY_WH)
    if val is None:
        _print(12, "Car battery capacity", _WARN, "None (no car?)")
    else:
        _print(
            12, "Car battery capacity", _INFO, f"{val:.0f} Wh ({val / 1000:.1f} kWh)"
        )


async def test_13_car_min_battery_capacity(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Read connected EV's minimum usable battery capacity in Wh (register 162)."""
    val = await _read(client, MBR_CAR_MIN_BATTERY_CAPACITY_WH)
    if val is None:
        _print(13, "Car min battery capacity", _WARN, "None (no car?)")
    else:
        _print(
            13,
            "Car min battery capacity",
            _INFO,
            f"{val:.0f} Wh ({val / 1000:.1f} kWh)",
        )


async def test_14_car_id(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Read car identifier string from register 176 (empty when no car connected)."""
    val = await _read(client, MBR_CAR_ID)
    if val is None or not val.strip():
        _print(14, "Car ID", _INFO, "empty (no car connected?)")
    else:
        _print(14, "Car ID", _OK, val.strip())


async def test_15_continuous_poll(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Poll all MCE registers N times at 5-second intervals and log each cycle."""
    cycles = args.cycles
    interval = 5
    print(
        f"\n  [INFO]  .  Test 15: Continuous polling — {cycles} cycle(s) x {interval} s"
    )
    header = f"  {'Cycle':>5}  {'EVSE state':<50}  {'Power (W)':>10}  {'SoC (%)':>8}  {'Error'}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for cycle in range(1, cycles + 1):
        vals = await _read_batch(client, _POLL_REGISTERS)
        state_raw, power, soc, error = vals
        state_str = _fmt_state(state_raw)[:50]
        power_str = f"{power:.1f}" if power is not None else "None"
        soc_str = f"{soc:.1f}" if soc is not None else "None"
        if error is None:
            error_str = "None"
        elif error == 0:
            error_str = "0 (ok)"
        else:
            error_str = f"0x{error:016X}"
        print(
            f"  {cycle:>5}  {state_str:<50}  {power_str:>10}  {soc_str:>8}  {error_str}"
        )
        if cycle < cycles:
            await asyncio.sleep(interval)
    print()
    _print(15, "Continuous poll", _OK, f"{cycles} cycle(s) completed")


# ===========================================================================
# Write tests  (require --write flag)
# ===========================================================================


async def test_16_set_idle_timeout(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Write idle timeout = 600 s, then read back and verify."""
    target = 600
    ok = await _write(client, MBR_IDLE_TIMEOUT_SEC, target)
    if not ok:
        _print(16, "Set idle timeout", _FAIL, "write failed")
        return
    await asyncio.sleep(0.5)
    val = await _read(client, MBR_IDLE_TIMEOUT_SEC)
    if val == target:
        _print(16, "Set idle timeout", _OK, f"{val} s (readback confirmed)")
    else:
        _print(16, "Set idle timeout", _WARN, f"wrote {target}, read back {val}")


async def test_17_set_power_zero(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Write charge power = 0 W (safe no-op)."""
    ok = await _write(client, MBR_SET_CHARGE_POWER, 0)
    _print(17, "Set charge power 0 W", _OK if ok else _FAIL)


async def test_18_send_start_action(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Send START action (1), then read back EVSE state after 2 s."""
    ok = await _write(client, MBR_SET_ACTION, 1)
    if not ok:
        _print(18, "Send START action", _FAIL, "write failed")
        return
    await asyncio.sleep(2)
    state_raw = await _read(client, MBR_EVSE_STATE)
    _print(18, "Send START action", _INFO, f"state after 2 s: {_fmt_state(state_raw)}")


async def test_19_set_charge_power_500w(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Set charge power = 500 W; read back actual power after 3 s."""
    ok = await _write(client, MBR_SET_CHARGE_POWER, 500)
    if not ok:
        _print(19, "Set charge power 500 W", _FAIL, "write failed")
        return
    _print(19, "Set charge power 500 W", _INFO, "waiting 3 s for power to settle...")
    await asyncio.sleep(3)
    actual = await _read(client, MBR_ACTUAL_POWER)
    actual_str = f"{actual:.1f} W" if actual is not None else "None"
    _print(19, "Set charge power 500 W", _INFO, f"actual power after 3 s: {actual_str}")


async def test_20_set_discharge_power_500w(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Set discharge power = -500 W; skips if SoC < 20 % or no car connected."""
    soc = await _read(client, MBR_CAR_SOC)
    state_raw = await _read(client, MBR_EVSE_STATE)

    if soc is None or soc < 20:
        _print(20, "Set discharge power -500 W", _SKIP, f"SoC={soc}%", "need >= 20 %")
        return

    base_state = BIDIPRO_STATES.get(state_raw, (1,))[0] if state_raw is not None else 1
    if base_state == 1:
        _print(20, "Set discharge power -500 W", _SKIP, None, "no car connected")
        return

    ok = await _write(client, MBR_SET_CHARGE_POWER, -500)
    if not ok:
        _print(20, "Set discharge power -500 W", _FAIL, "write failed")
        return
    _print(
        20, "Set discharge power -500 W", _INFO, "waiting 3 s for power to settle..."
    )
    await asyncio.sleep(3)
    actual = await _read(client, MBR_ACTUAL_POWER)
    actual_str = f"{actual:.1f} W" if actual is not None else "None"
    _print(
        20, "Set discharge power -500 W", _INFO, f"actual power after 3 s: {actual_str}"
    )


async def test_21_send_stop_action(
    client: AsyncModbusTcpClient, args: argparse.Namespace
) -> None:
    """Send STOP action (0) and set power to 0; verify charger is no longer active."""
    # Set power to 0 first for safety
    await _write(client, MBR_SET_CHARGE_POWER, 0)
    ok = await _write(client, MBR_SET_ACTION, 0)
    if not ok:
        _print(21, "Send STOP action", _FAIL, "write failed")
        return
    await asyncio.sleep(2)
    state_raw = await _read(client, MBR_EVSE_STATE)
    base_state = (
        BIDIPRO_STATES.get(state_raw, (None,))[0] if state_raw is not None else None
    )
    is_stopped = base_state not in (3, 4)  # not Charging or Discharging
    status = _OK if is_stopped else _WARN
    _print(21, "Send STOP action", status, f"state after 2 s: {_fmt_state(state_raw)}")


# ===========================================================================
# Test registry
# ===========================================================================

_READ_TESTS: list[tuple[int, str, object]] = [
    (1, "IP connection", test_01_connection),
    (2, "Firmware version", test_02_firmware_version),
    (3, "Serial number", test_03_serial_number),
    (4, "Model", test_04_model),
    (5, "Idle timeout", test_05_idle_timeout),
    (6, "EVSE state", test_06_evse_state),
    (7, "Actual power", test_07_actual_power),
    (8, "State of Charge", test_08_soc),
    (9, "Connector type", test_09_connector_type),
    (10, "Max charge power", test_10_max_charge_power),
    (11, "Error register", test_11_error_register),
    (12, "Car battery capacity", test_12_car_battery_capacity),
    (13, "Car min battery capacity", test_13_car_min_battery_capacity),
    (14, "Car ID", test_14_car_id),
    (15, "Continuous poll", test_15_continuous_poll),
]

_WRITE_TESTS: list[tuple[int, str, object]] = [
    (16, "Set idle timeout", test_16_set_idle_timeout),
    (17, "Set charge power 0 W", test_17_set_power_zero),
    (18, "Send START action", test_18_send_start_action),
    (19, "Set charge power 500 W", test_19_set_charge_power_500w),
    (20, "Set discharge power -500 W", test_20_set_discharge_power_500w),
    (21, "Send STOP action", test_21_send_stop_action),
]

_ALL_TESTS: dict[int, tuple[str, object]] = {
    num: (name, fn) for num, name, fn in _READ_TESTS + _WRITE_TESTS
}
_WRITE_NUMS: set[int] = {num for num, _, _ in _WRITE_TESTS}


# ===========================================================================
# Entry point
# ===========================================================================


async def main() -> None:
    """Parse arguments, connect to charger, and run selected tests."""
    parser = argparse.ArgumentParser(
        description="Standalone hardware test for the EVtec BiDiPro charger"
    )
    parser.add_argument("--host", required=True, help="Charger IP address or hostname")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Modbus TCP port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--tests",
        help="Comma-separated test numbers to run, e.g. --tests 1,6,8",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Enable write tests 16-21 (disabled by default)",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=3,
        help="Polling cycles for test 15 (default: 3)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="TCP connection timeout in seconds (default: 10)",
    )
    args = parser.parse_args()

    # Determine which tests to run
    if args.tests:
        try:
            selected = [int(t.strip()) for t in args.tests.split(",")]
        except ValueError:
            print("ERROR: --tests expects comma-separated integers, e.g. --tests 1,2,3")
            sys.exit(1)
    else:
        selected = [num for num, _, _ in _READ_TESTS]
        if args.write:
            selected += [num for num, _, _ in _WRITE_TESTS]

    # Remove write tests unless --write is explicitly set
    if not args.write:
        selected = [n for n in selected if n not in _WRITE_NUMS]

    print(f"\nEVtec BiDiPro hardware test  —  {args.host}:{args.port}")
    print("=" * 60)
    if args.write:
        print("  *** WRITE MODE ENABLED — commands will be sent to the charger ***\n")

    # Establish connection
    client = AsyncModbusTcpClient(host=args.host, port=args.port)
    try:
        connected = await asyncio.wait_for(client.connect(), timeout=args.timeout)
    except (TimeoutError, asyncio.TimeoutError, OSError) as exc:
        _print(1, "IP connection", _FAIL, f"{args.host}:{args.port}", str(exc))
        sys.exit(1)

    if not connected:
        _print(
            1,
            "IP connection",
            _FAIL,
            f"{args.host}:{args.port}",
            "connect() returned False",
        )
        sys.exit(1)

    try:
        for num in selected:
            if num not in _ALL_TESTS:
                print(f"  [SKIP]  -  Test {num:02d}: unknown test number")
                continue
            _name, fn = _ALL_TESTS[num]
            await fn(client, args)
    finally:
        client.close()

    print("\n" + "=" * 60)
    print("Done.\n")


if __name__ == "__main__":
    asyncio.run(main())
