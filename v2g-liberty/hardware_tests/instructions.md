# Hardware Test Instructions — EVtec BiDiPro Charger

## Prerequisites

You need **Python 3.11+** and **pymodbus 3.x** installed.

```bash
pip install pymodbus==3.11.4
```

No other non-standard packages are required.

---

## Running the Tests

### Minimal: just check connectivity and read all registers

```bash
python hardware_tests/test_evtec_bidipro.py --host 192.168.x.x
```

This runs tests 1–15 (read-only). The default Modbus port is **5020**.

### Custom port

```bash
python hardware_tests/test_evtec_bidipro.py --host 192.168.x.x --port 5020
```

### Select specific tests

```bash
# Only connection + SoC + EVSE state
python hardware_tests/test_evtec_bidipro.py --host 192.168.x.x --tests 1,6,8

# Only static info (no polling)
python hardware_tests/test_evtec_bidipro.py --host 192.168.x.x --tests 2,3,4,9,10
```

### Enable write tests (⚠ sends commands to the charger!)

```bash
python hardware_tests/test_evtec_bidipro.py --host 192.168.x.x --write
```

This runs tests 1–21. Test 21 (STOP) always runs last to leave the charger safe.

### Increase polling cycles

```bash
# Poll for 6 × 5 s = 30 s in test 15
python hardware_tests/test_evtec_bidipro.py --host 192.168.x.x --cycles 6
```

---

## Expected Output

```
EVtec BiDiPro hardware test — 192.168.1.100:5020
============================================================
  [ OK ]  +  Test 01: IP connection  →  192.168.1.100:5020
  [ OK ]  +  Test 02: Firmware version  →  1.2.3.4
  [ OK ]  +  Test 03: Serial number  →  EV123456
  [ OK ]  +  Test 04: Model  →  BiDiPro10
  [INFO]  .  Test 05: Idle timeout  →  600 s
  [INFO]  .  Test 06: EVSE state  →  BiDiPro=10 (Connected & idle) → base=2 (Idle)
  [ OK ]  +  Test 07: Actual power  →  0.0 W (idle)
  [ OK ]  +  Test 08: State of Charge  →  72.5 %
  [ OK ]  +  Test 09: Connector type  →  CCS
  [ OK ]  +  Test 10: Max charge power  →  10000 W (10.0 kW)
  [ OK ]  +  Test 11: Error register  →  0 (no errors)
  [INFO]  .  Test 12: Car battery capacity  →  82000 Wh (82.0 kWh)
  [INFO]  .  Test 13: Car min battery capacity  →  4100 Wh (4.1 kWh)
  [ OK ]  +  Test 14: Car ID  →  VIN123456789
  ...
============================================================
Done.
```

---

## Status Codes

| Code   | Meaning                                             |
|--------|-----------------------------------------------------|
| `[ OK ]` | Value read/written successfully and within range  |
| `[FAIL]` | Register could not be read, or write failed       |
| `[WARN]` | Value outside expected range or unusual condition |
| `[INFO]` | Informational — no pass/fail judgement            |
| `[SKIP]` | Test skipped (conditions not met, e.g. no car)    |

---

## Register Reference

| Register | Address | Type    | Len | Description                          |
|----------|---------|---------|-----|--------------------------------------|
| Version  | 0       | string  | 16  | Firmware version string              |
| Serial   | 16      | string  | 10  | Serial number                        |
| Model    | 26      | string  | 10  | Model identifier                     |
| Idle TO  | 42      | int32   | 2   | Communication idle timeout (seconds) |
| State    | 100     | int32   | 2   | EVSE connector state (0–12)          |
| Power    | 110     | float32 | 2   | Actual charge power (W, ± = dir)     |
| SoC      | 112     | float32 | 2   | Car battery state of charge (%)      |
| Conn.    | 114     | int32   | 2   | Connector type (0=Type2 … 3=GBT)     |
| MaxPwr   | 130     | float32 | 2   | Hardware max charge power (W)        |
| Error    | 154     | int64   | 4   | Error bitmask (0 = no error)         |
| BatCap   | 158     | float32 | 2   | Car battery capacity (Wh)            |
| MinCap   | 162     | float32 | 2   | Car min usable battery (Wh)          |
| CarID    | 176     | string  | 10  | Car identifier from charger          |
| SetPwr   | 186     | int32   | 2   | **WRITE** Desired charge power (W)   |
| SetAct   | 188     | int32   | 2   | **WRITE** Action (0=stop, 1=start)   |

---

## EVSE State Mapping

| BiDiPro | Base state | Meaning                                     |
|---------|-----------|---------------------------------------------|
| 0       | 0         | Booting                                     |
| 1       | 1         | No car connected                            |
| 2       | 9         | Error                                       |
| 3       | 7         | OCPP / external control                     |
| 4       | 1         | Contract authorisation → no car connected   |
| 5       | 1         | Plugged in + power check → no car connected |
| 6       | 1         | Initialise connection → no car connected    |
| 7       | 3         | Charging                                    |
| 8       | 4         | Discharging                                 |
| 9       | 7         | OCPP / external control                     |
| 10      | 2         | Connected & idle                            |
| 11      | 1         | No car connected                            |
| 12      | 9         | Error                                       |

---

## Troubleshooting

**Test 1 fails (connection refused)**
- Check the IP address — the charger must be reachable on the network
- Check the port: EVtec BiDiPro uses **5020** by default, not 502
- Verify the charger is powered on and the Modbus TCP service is running

**Test 8 returns WARN / None SoC**
- Normal when no car is connected — the charger cannot report SoC
- If a car is connected and SoC is still None, check the charging cable / car state

**Test 11 returns non-zero error code**
- The charger is reporting an error condition
- A non-zero error shortly after charger startup (< 5 min) may clear automatically
- Refer to the EVtec BiDiPro documentation for error bitmask definitions

**Write tests: charger does not start charging**
- The charger may require the car to be connected (EVSE state 10 = connected & idle)
- Some EV models require the charger to initiate a handshake before accepting power
