#!/usr/bin/env python3
"""Loopback check for the EVtec emulator — run on demand, not part of pytest.

Starts an in-process Modbus TCP server, points the EVtec emulator at it, and
then plays the part of V2G Liberty's ``evtec_bidipro`` driver with a second
client, using that driver's read/write patterns: the model string at address
26, connector discovery over 1..10 on offsets 0/2/4, one block read of
``X+0..X+57``, and FC16 writes to ``X+86``/``X+88``.

Why this is a script and not a test: everything under ``tests/`` in this
repository drives Modbus through an in-memory fake, and no test binds a socket.
This check does the opposite on purpose — it exercises the real pymodbus
serialisation, the big-endian word order, block reads across the object
boundary and the FC16 write path, which a fake cannot cover. That realism costs
a bound port and a few seconds of settling time, so it stays out of the suite
where it would be slow and flaky. Run it after changing the register model, the
encoders or the write path.

Usage:
    python3 rootfs/root/appdaemon/apps/dev_tools/loopback_check_evtec.py [--port N]

Exits 0 when every check passes, 1 otherwise. Dev-only.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import ModbusTcpServer

# Importable both as "python3 <path>" and from the apps directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dev_tools.charger_emulator_evtec import EVtecChargerEmulator
from dev_tools.charger_scenarios_evtec import (
    CP_MODEL,
    CP_STATE,
    OFF_CAR_ID,
    OFF_DISCHARGED_ENERGY,
    OFF_INPUT_POWER,
    OFF_LOWER_LIMIT_POTENTIAL,
    OFF_PRESENT_CONSUMPTION,
    OFF_SUSPEND_MODE,
    dec_float32,
    dec_int32,
    dec_int64,
    dec_string,
    enc_int32,
)

CONNECTOR = 9
BASE = CONNECTOR * 100
# Fast ramp so the checks settle in seconds rather than tens of seconds.
RAMP_UP_S, RAMP_DOWN_S, TICK_S = 3, 1, 0.2
SETTLE_S = RAMP_UP_S + 1

_results = []


def check(name: str, ok: bool, detail: str = ""):
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{'  ' + detail if detail else ''}")


def make_emulator(port: int) -> EVtecChargerEmulator:
    """The emulator with a minimal stand-in for the AppDaemon methods it uses."""
    e = object.__new__(EVtecChargerEmulator)
    e.args = {
        "charger_host": "127.0.0.1",
        "charger_port": port,
        "connector": CONNECTOR,
        "update_interval": TICK_S,
        "soc_speedup": 200,
        "status_log_seconds": 0,
        "power_jitter_w": 0,
        "ramp_up_seconds": RAMP_UP_S,
        "ramp_down_seconds": RAMP_DOWN_S,
    }
    e.logged = []
    e.log = lambda msg="", level="INFO", **kw: e.logged.append((level, msg))

    async def _noop_async(*a, **kw):
        return None

    e.get_state = _noop_async
    e.call_service = _noop_async
    e.set_state = lambda *a, **kw: None
    e.listen_state = lambda *a, **kw: None
    return e


async def run(port: int) -> bool:
    context = ModbusServerContext(
        devices=ModbusDeviceContext(hr=ModbusSequentialDataBlock(0, [0] * 1200)),
        single=True,
    )
    server = ModbusTcpServer(context, address=("127.0.0.1", port))
    server_task = asyncio.create_task(server.serve_forever())
    await asyncio.sleep(0.5)

    emulator = make_emulator(port)
    await emulator.initialize()
    client = AsyncModbusTcpClient("127.0.0.1", port=port, timeout=3)
    await client.connect()

    async def read(address, count):
        return (
            await client.read_holding_registers(
                address=address, count=count, device_id=1
            )
        ).registers

    async def write(address, words):
        return await client.write_registers(address=address, values=words, device_id=1)

    try:
        print("\n1. signature and connector discovery")
        model = dec_string(await read(CP_MODEL, 10))
        check("model at address 26 contains 'crema'", "crema" in model.lower(), model)
        configured = []
        for plug in range(1, 11):
            words = await read(plug * 100, 6)
            if any(dec_int32(words[o : o + 2]) != 0 for o in (0, 2, 4)):
                configured.append(plug)
        check(
            "discovery on offsets 0/2/4 binds only the configured connector",
            configured == [CONNECTOR],
            str(configured),
        )

        print("\n2. the driver's block read of X+0..X+57")
        block = await read(BASE, 58)
        state, power, soc, error = (
            dec_int32(block[0:2]),
            dec_float32(block[10:12]),
            dec_int32(block[12:14]),
            dec_int64(block[54:58]),
        )
        check(
            "idle: state 10, power 0 W, SoC 330 per-mille, no error",
            (state, power, soc, error) == (10, 0.0, 330, 0),
            str((state, power, soc, error)),
        )
        check(
            "car id is non-empty", dec_string(await read(BASE + OFF_CAR_ID, 10)) != ""
        )

        print("\n3. the driver writes the communication timeout at address 42")
        await write(42, enc_int32(600))
        await asyncio.sleep(1.0)
        check(
            "address 42 untouched by the emulator", dec_int32(await read(42, 2)) == 600
        )

        print("\n4. charge at 4000 W")
        await write(BASE + OFF_SUSPEND_MODE, enc_int32(1))
        await write(BASE + OFF_INPUT_POWER, enc_int32(4000))
        await asyncio.sleep(SETTLE_S)
        block = await read(BASE, 58)
        state, power, soc = (
            dec_int32(block[0:2]),
            dec_float32(block[10:12]),
            dec_int32(block[12:14]),
        )
        consumption = dec_float32(await read(BASE + OFF_PRESENT_CONSUMPTION, 2))
        check(
            "connector state 7, session state 16",
            (state, dec_int32(block[2:4])) == (7, 16),
            f"state={state}",
        )
        check(
            "power settled near 0.92 x 4000 W", abs(power - 3680) <= 5, f"{power:.0f} W"
        )
        check("X+46 matches the measured power", consumption == power)
        check("SoC rose above 330 per-mille", soc > 330, f"{soc}")
        check(
            "the setpoint register was left alone",
            dec_int32(await read(BASE + OFF_INPUT_POWER, 2)) == 4000,
        )

        print("\n5. discharge at -3000 W")
        await write(BASE + OFF_INPUT_POWER, enc_int32(-3000))
        await asyncio.sleep(SETTLE_S + RAMP_DOWN_S)
        block = await read(BASE, 58)
        state, power = dec_int32(block[0:2]), dec_float32(block[10:12])
        discharged = dec_float32(await read(BASE + OFF_DISCHARGED_ENERGY, 2))
        check(
            "connector state 8, session state 17",
            (state, dec_int32(block[2:4])) == (8, 17),
            f"state={state}",
        )
        check(
            "power settled near -0.92 x 3000 W",
            abs(power + 2760) <= 5,
            f"{power:.0f} W",
        )
        check(
            "X+20 discharged energy is climbing", discharged > 0, f"{discharged:.1f} Wh"
        )

        print("\n6. stop with setpoint 0, suspend mode left on")
        await write(BASE + OFF_INPUT_POWER, enc_int32(0))
        await asyncio.sleep(RAMP_DOWN_S + 1.0)
        block = await read(BASE, 58)
        check(
            "back to state 10 at 0 W",
            (dec_int32(block[0:2]), dec_float32(block[10:12])) == (10, 0.0),
        )

        print("\n7. scenario v2g_not_offered refuses a discharge")
        await emulator._apply_scenario("v2g_not_offered")
        check(
            "X+22 >= 0 (V2G not offered)",
            dec_float32(await read(BASE + OFF_LOWER_LIMIT_POTENTIAL, 2)) >= 0,
        )
        await write(BASE + OFF_INPUT_POWER, enc_int32(-3000))
        await asyncio.sleep(1.0)
        block = await read(BASE, 58)
        check(
            "refused: state 10 at 0 W",
            (dec_int32(block[0:2]), dec_float32(block[10:12])) == (10, 0.0),
        )
        check(
            "and a warning was logged",
            any(lvl == "WARNING" and "X+22" in m for lvl, m in emulator.logged),
        )
        await write(BASE + OFF_INPUT_POWER, enc_int32(2000))
        await asyncio.sleep(SETTLE_S)
        check("charging is still allowed", dec_int32(await read(BASE, 2)) == 7)

        print("\n8. car disconnected")
        await emulator._apply_scenario("normal")
        emulator._car_connected = False
        await asyncio.sleep(1.0)
        block = await read(BASE, 58)
        check(
            "state 1, SoC 0, empty car id",
            (
                dec_int32(block[0:2]),
                dec_int32(block[12:14]),
                dec_string(await read(BASE + OFF_CAR_ID, 10)),
            )
            == (1, 0, ""),
        )
        check("ChargePoint state back to 1", dec_int32(await read(CP_STATE, 2)) == 1)
        check(
            "no tick errors were logged",
            not any("tick error" in m for _, m in emulator.logged),
        )
    finally:
        emulator.terminate()
        emulator._task.cancel()
        client.close()
        emulator._client.close()
        await server.shutdown()
        server_task.cancel()

    passed = sum(_results)
    print(f"\n{passed}/{len(_results)} checks passed")
    return all(_results)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--port", type=int, default=15020, help="port for the loopback server"
    )
    args = parser.parse_args()
    print(f"EVtec emulator loopback check on 127.0.0.1:{args.port}")
    sys.exit(0 if asyncio.run(run(args.port)) else 1)


if __name__ == "__main__":
    main()
