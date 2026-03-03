# Plan: Standalone Hardware Test Script for EVtec BiDiPro Charger

## Context

The EVtec BiDiPro charger implementation lives inside the AppDaemon/Home Assistant
framework, making it impossible to run isolated hardware tests without the full stack.
A standalone test script is needed to validate the actual hardware communication —
checking IP connectivity, reading all registers, and optionally writing commands —
without depending on AppDaemon or Home Assistant.

The script is runnable directly with `python test_evtec_bidipro.py --host <ip>` from
any machine that has `pymodbus` installed.

---

## Design Decisions

- **No AppDaemon dependency** — uses `pymodbus` directly (already in `requirements.txt`)
- **Import `modbus_types.py` as-is** — pure Python with no AppDaemon deps; reuse
  `MBR.encode()`/`MBR.decode()` for correctness
- **Async throughout** — matches pymodbus `AsyncModbusTcpClient` API
- **Read-only by default** — write tests require explicit `--write` flag to prevent
  accidental charger commands
- **Step-by-step numbered tests** — each test is a clearly labelled async function;
  results printed as `[ OK ]`, `[FAIL]`, `[WARN]`, `[INFO]`, `[SKIP]`
- **Selective execution** — `--tests 1,3,5` or run all by default
- **No external dependencies beyond pymodbus** — no `rich`, no `tabulate`

---

## Test Sequence

### Read-Only Tests (safe, run by default)

| #  | Name                     | Register         | What it verifies                                         |
|----|--------------------------|------------------|----------------------------------------------------------|
| 1  | IP Connection            | —                | TCP connect to host:port succeeds                        |
| 2  | Firmware version         | addr 0, str×16   | Non-empty version string                                 |
| 3  | Serial number            | addr 16, str×10  | Non-empty serial                                         |
| 4  | Model                    | addr 26, str×10  | Non-empty model string                                   |
| 5  | Idle timeout             | addr 42, int32   | Readable; logs current setting in seconds                |
| 6  | EVSE state               | addr 100, int32  | Valid BiDiPro state 0–12; maps to base state name        |
| 7  | Actual power             | addr 110, float32| Value within –20000..+20000 W; indicates charging dir    |
| 8  | State of Charge          | addr 112, float32| Value within 1..100 %; WARN if unavailable               |
| 9  | Connector type           | addr 114, int32  | Valid type 0–3; logs Type2/CCS/CHAdeMO/GBT               |
| 10 | Max charge power         | addr 130, float32| Positive, non-zero value                                 |
| 11 | Error register           | addr 154, int64  | PASS if 0; WARN with hex code if non-zero                |
| 12 | Car battery capacity     | addr 158, float32| Logs Wh (may be 0 if no car)                             |
| 13 | Car min battery capacity | addr 162, float32| Logs Wh                                                  |
| 14 | Car ID                   | addr 176, str×10 | Logs car ID (empty if no car)                            |
| 15 | Continuous poll          | all MCE registers| N cycles × 5 s: shows EVSE state, power, SoC, error     |

### Write Tests (require `--write` flag)

| #  | Name                      | Register         | Action                                                   |
|----|---------------------------|------------------|----------------------------------------------------------|
| 16 | Set idle timeout          | addr 42, int32   | Write 600; read back and verify                          |
| 17 | Set charge power 0 W      | addr 186, int32  | Write 0 (safe no-op)                                     |
| 18 | Send START action         | addr 188, int32  | Write 1; read back EVSE state after 2 s                  |
| 19 | Set charge power 500 W    | addr 186, int32  | Write 500; read back actual power after 3 s              |
| 20 | Set discharge power –500 W| addr 186, int32  | Write –500; skips if SoC < 20 % or no car connected      |
| 21 | Send STOP action          | addr 188, int32  | Write 0 + power 0; verify state is not charging/discharging|

---

## CLI Interface

```
python hardware_tests/test_evtec_bidipro.py --host 192.168.1.100 [options]

Options:
  --host HOST       Charger IP address (required)
  --port PORT       Modbus TCP port (default: 5020)
  --tests N,N,...   Comma-separated test numbers to run
  --write           Enable write tests 16–21 (disabled by default)
  --cycles N        Polling cycles for test 15 (default: 3)
  --timeout N       Connection timeout in seconds (default: 10)
```

---

## Critical Files

| File | Role |
|------|------|
| `rootfs/.../chargers/evtec_bidipro.py` | Source of all register definitions |
| `rootfs/.../chargers/modbus_types.py`  | `MBR` dataclass, imported via sys.path |
| `hardware_tests/test_evtec_bidipro.py` | The standalone test script |

---

## Notes

- `_MCE_ERROR` in `evtec_bidipro.py` uses `data_type="64int"` which does not match
  the `"int64"` string expected by `MBR.decode()`. The test script uses `"int64"`.
- Default port for EVtec BiDiPro is **5020** (not the standard Modbus 502).
- Write tests 18–21 interact with a live charger. Always run test 21 (STOP) after
  tests 18–20 to leave the charger in a safe state.
