"""EVtec BiDiPro charger emulator for the dev environment.

Makes the static EVtec mock (``evtec-mock:5020``) dynamic by acting as a second
Modbus client to it, so V2G Liberty's ``evtec_bidipro`` driver sees a charger
that responds to its commands. Same machinery as the Quasar emulator
(``charger_emulator_base``), mapped onto the ECP4 / Modbus 2.0 register model
from the hardware-tested contract in ``.private/…/MODBUS_PROXY.md``.

Register model (connector ``X`` at base ``X*100``; multi-register values are
big-endian, see ``charger_scenarios_evtec``):

- V2G WRITES, the emulator only reads: ``X+86`` input power (int32 W, negative
  = discharge) and ``X+88`` suspend mode. Suspend mode is a documented on/off
  gate that does nothing on the real charger, so the emulator ignores it and
  follows the setpoint alone — a driver that relies on ``X+88`` to stop will
  visibly keep (dis)charging here, exactly as on hardware.
- Emulator WRITES, V2G reads: ChargePoint identity (0/16/26), connector count
  (36) and state (40); per connector the state (``X+00``), session state
  (``X+02``), session type (``X+04``), voltage/current/power (``X+06``..
  ``X+10``), SoC in per-mille (``X+12``), connector type (``X+14``), energy
  counters (``X+18``/``X+20``), accept windows (``X+22``/``X+30``/``X+38``),
  present consumption (``X+46``), error bitmask (``X+54``), battery capacities
  (``X+58``/``X+62``) and the car id (``X+76``, empty = no car).
- Never written: ``X+86``..``X+89`` (V2G's commands), 38/39 (fallback power)
  and 42/43 (communication timeout; V2G writes it at kick-off).

Interlocks (MODBUS_PROXY.md §4). The real station does not guard a negative
setpoint against the session type and rejects a setpoint outside its accept
window. The emulator makes a violation visible rather than silently obeying:
a discharge request while the session type is not v2xDynamic/bidirectional
(``X+04``), or while ``X+22 >= 0`` (V2G not offered), delivers 0 W and logs a
WARNING. Otherwise the request is clamped into ``[X+22, 0]`` (discharge) or
``[X+38, X+30]`` (charge).

Control surface (own entities, so both emulators can run side by side):
- ``input_select.emulator_evtec_scenario`` — pick a scenario.
- ``input_boolean.emulator_evtec_car_connected`` — connect/disconnect the car.
- ``input_number.emulator_evtec_soc`` — jump the SoC.

Dev-only: this app is not included in the production Docker image.
"""

from dev_tools.charger_emulator_base import (
    PHASE_CHARGING,
    PHASE_DISCHARGING,
    PHASE_DISCONNECTED,
    PHASE_EMPTY,
    PHASE_FULL,
    PHASE_IDLE,
    BaseChargerEmulator,
)
from dev_tools.charger_scenarios_evtec import (
    BIDIRECTIONAL_SESSION_TYPES,
    COMMAND_OFFSETS,
    CONNECTOR_STRIDE,
    CP_MODEL,
    CP_NUM_CONNECTORS,
    CP_SERIAL,
    CP_STATE,
    CP_STATE_IN_USE,
    CP_STATE_READY,
    CP_VERSION,
    DEFAULT_SCENARIO,
    EVTEC_BIDI_PRO_10,
    OFF_BATTERY_CAPACITY,
    OFF_CAR_ID,
    OFF_CHARGED_ENERGY,
    OFF_CONNECTOR_STATE,
    OFF_CONNECTOR_TYPE,
    OFF_CURRENT,
    OFF_DISCHARGED_ENERGY,
    OFF_ERROR,
    OFF_INPUT_POWER,
    OFF_LOWER_LIMIT,
    OFF_LOWER_LIMIT_POTENTIAL,
    OFF_MIN_BATTERY_CAPACITY,
    OFF_POWER,
    OFF_PRESENT_CONSUMPTION,
    OFF_SESSION_STATE,
    OFF_SESSION_TYPE,
    OFF_SOC,
    OFF_UPPER_LIMIT,
    OFF_VOLTAGE,
    PROTECTED_CP_ADDRESSES,
    SCENARIOS,
    SESSION_STATE_CURRENT_DEMAND,
    SESSION_STATE_GRID_FEED_IN,
    SESSION_STATE_PLUGGED_IN,
    SESSION_STATE_POWER_DELIVERY_STOPPED,
    SESSION_STATE_READY,
    STATE_CHARGING_END,
    STATE_CURRENT_DEMAND,
    STATE_GRID_FEED,
    STATE_NAMES,
    STATE_READY,
    STATE_WAIT_FOR_GRID,
    dec_int32,
    enc_float32,
    enc_int32,
    enc_int64,
    enc_string,
    words_at,
)

# Profile fields that can be overridden from apps.yaml.
_PROFILE_ARGS = (
    "connector",
    "hw_max_charge_power_w",
    "hw_max_discharge_power_w",
    "hw_soc_floor_pct",
    "hw_soc_ceiling_pct",
    "battery_capacity_wh",
    "min_battery_capacity_wh",
    "power_jitter_w",
    "session_type",
    "discharge_floor_w",
    "car_id",
    "model",
    "serial",
    "version",
)

# Delivery phase -> (connector state X+00, charge session state X+02). The
# driver treats boot/ready/plugged/initConnection as "no car", so a connected
# idle car reports waitForGrid, and a full one chargingEnd.
_PHASE_STATES = {
    PHASE_DISCONNECTED: (STATE_READY, SESSION_STATE_READY),
    PHASE_IDLE: (STATE_WAIT_FOR_GRID, SESSION_STATE_PLUGGED_IN),
    PHASE_CHARGING: (STATE_CURRENT_DEMAND, SESSION_STATE_CURRENT_DEMAND),
    PHASE_DISCHARGING: (STATE_GRID_FEED, SESSION_STATE_GRID_FEED_IN),
    PHASE_FULL: (STATE_CHARGING_END, SESSION_STATE_POWER_DELIVERY_STOPPED),
    PHASE_EMPTY: (STATE_WAIT_FOR_GRID, SESSION_STATE_POWER_DELIVERY_STOPPED),
}

_CAR_ID_REGS = 10


class EVtecChargerEmulator(BaseChargerEmulator):
    """AppDaemon dev app that drives the static EVtec mock dynamically."""

    SCENARIOS = SCENARIOS
    DEFAULT_SCENARIO = DEFAULT_SCENARIO
    BASE_PROFILE = EVTEC_BIDI_PRO_10
    PROFILE_ARGS = _PROFILE_ARGS
    DEFAULT_HOST = "evtec-mock"
    LOG_TAG = "EVtec emulator"

    _SCENARIO_ENTITY = "input_select.emulator_evtec_scenario"
    _CONNECT_ENTITY = "input_boolean.emulator_evtec_car_connected"
    _SOC_ENTITY = "input_number.emulator_evtec_soc"
    _SCENARIO_FRIENDLY_NAME = "EVtec emulator scenario"
    _CONNECT_FRIENDLY_NAME = "EVtec emulator: car connected"

    # --- Charger-specific hooks --------------------------------------------
    def _init_charger_state(self):
        self._charged_wh = 0.0
        self._discharged_wh = 0.0
        self._last_suspend_mode = None
        self._last_warnings: dict[str, str | None] = {}

    def _battery_capacity_wh(self) -> float:
        return self._profile.battery_capacity_wh

    @property
    def _discharge_floor_w(self) -> float:
        """The discharge floor the charger actually honours, in W (<= 0).

        This is what X+22 advertises AND what the emulator enforces: a real
        station never offers a window it cannot deliver, so the profile's
        ``discharge_floor_w`` is bounded by the hardware maximum. >= 0 means
        V2G is not offered right now (not an error).
        """
        p = self._profile
        return max(p.discharge_floor_w, -p.hw_max_discharge_power_w)

    @property
    def _base(self) -> int:
        """Absolute base address of the bound connector."""
        return self._profile.connector * CONNECTOR_STRIDE

    def _conn(self, offset: int, words: list[int]) -> dict[int, int]:
        """{absolute_address: uint16} for a connector field of this connector."""
        return words_at(self._base + offset, words)

    def _seed_registers(self) -> dict[int, int]:
        p = self._profile
        seed = {
            **words_at(CP_VERSION, enc_string(p.version, 16)),
            **words_at(CP_SERIAL, enc_string(p.serial, 10)),
            **words_at(CP_MODEL, enc_string(p.model, 10)),
            **words_at(CP_NUM_CONNECTORS, enc_int32(1)),
            **self._conn(OFF_SESSION_TYPE, enc_int32(p.session_type)),
            **self._conn(OFF_VOLTAGE, enc_float32(p.voltage_v)),
            **self._conn(OFF_CONNECTOR_TYPE, enc_int32(p.connector_type)),
            **self._conn(
                OFF_LOWER_LIMIT_POTENTIAL, enc_float32(self._discharge_floor_w)
            ),
            **self._conn(OFF_UPPER_LIMIT, enc_float32(p.hw_max_charge_power_w)),
            **self._conn(OFF_LOWER_LIMIT, enc_float32(0.0)),
            **self._conn(OFF_ERROR, enc_int64(0)),
            **self._conn(OFF_BATTERY_CAPACITY, enc_float32(p.battery_capacity_wh)),
            **self._conn(
                OFF_MIN_BATTERY_CAPACITY, enc_float32(p.min_battery_capacity_wh)
            ),
        }
        if self._scenario.mirror and not self._car_connected:
            seed.update(self._disconnected_registers())
        else:
            # Connected and idle; frozen scenarios also start from a connected car.
            seed.update(self._report_registers(PHASE_IDLE, 0))
        return seed

    def _disconnected_registers(self) -> dict[int, int]:
        return {
            **self._conn(OFF_CONNECTOR_STATE, enc_int32(STATE_READY)),
            **self._conn(OFF_SESSION_STATE, enc_int32(SESSION_STATE_READY)),
            **self._conn(OFF_CURRENT, enc_float32(0.0)),
            **self._conn(OFF_POWER, enc_float32(0.0)),
            **self._conn(OFF_SOC, enc_int32(0)),
            **self._conn(OFF_PRESENT_CONSUMPTION, enc_float32(0.0)),
            **self._conn(OFF_CAR_ID, enc_string("", _CAR_ID_REGS)),
            **words_at(CP_STATE, enc_int32(CP_STATE_READY)),
        }

    async def _read_command(self) -> tuple[int, bool] | None:
        # X+86 (setpoint) and X+88 (suspend mode) in one read.
        words = await self._read_words(self._base + OFF_INPUT_POWER, 4)
        if words is None:
            return None
        setpoint = dec_int32(words[0:2])
        suspend_mode = dec_int32(words[2:4])
        if suspend_mode != self._last_suspend_mode:
            self._last_suspend_mode = suspend_mode
            self.log(
                f"suspend mode (X+88) -> {suspend_mode}; ignored, it is a no-op on "
                "the real charger: only the X+86 setpoint counts"
            )
        # Suspend mode never stops power flow; only a setpoint of 0 does.
        return setpoint, False

    def _log_once(self, key: str, message):
        """Log a WARNING when a condition appears, not on every tick it lasts."""
        message = message or None
        if self._last_warnings.get(key) == message:
            return
        self._last_warnings[key] = message
        if message:
            self.log(message, level="WARNING")

    def _derive_phase(self, setpoint: int, stop_requested: bool) -> tuple[str, int]:
        """The base derivation plus the two V2G interlocks and the accept windows."""
        p = self._profile
        interlock = None
        if setpoint < 0 and not stop_requested:
            if p.session_type not in BIDIRECTIONAL_SESSION_TYPES:
                interlock = (
                    f"session type (X+04) is {p.session_type}, not bidirectional"
                )
            elif self._discharge_floor_w >= 0:
                interlock = "V2G is not offered (X+22 >= 0)"
        self._log_once(
            "interlock",
            interlock
            and (
                f"discharge of {setpoint}W requested while {interlock}: "
                "a correct driver would not do this; delivering 0 W"
            ),
        )
        if interlock:
            return PHASE_IDLE, 0

        # Taper at the SoC bounds and clamp to the hardware maximum.
        phase, power = super()._derive_phase(setpoint, stop_requested)
        if phase == PHASE_DISCHARGING:
            # The discharge window is [X+22, 0].
            power = round(max(power, self._discharge_floor_w))
        # The real station rejects a setpoint outside its accept window rather
        # than clamping it, so make the difference visible: a driver that does
        # not clamp itself (MODBUS_PROXY.md section 4, step 4) still works here,
        # but the log says so.
        self._log_once(
            "clamp",
            phase in (PHASE_CHARGING, PHASE_DISCHARGING)
            and power != setpoint
            and (
                f"setpoint {setpoint}W lies outside the accept window "
                f"[{self._discharge_floor_w:g}, {p.hw_max_charge_power_w}] W and was "
                f"clamped to {power}W; the real station would reject it instead"
            ),
        )
        return phase, power

    def _integrate_soc(self, actual_power_w: int):
        super()._integrate_soc(actual_power_w)
        # Keep the energy counters (X+18/X+20) consistent with the SoC movement.
        wh = abs(actual_power_w) * self._interval * self._soc_speedup / 3600.0
        if actual_power_w > 0:
            self._charged_wh += wh
        elif actual_power_w < 0:
            self._discharged_wh += wh

    def _report_registers(self, phase: str, actual: int) -> dict[int, int]:
        state, session_state = _PHASE_STATES[phase]
        p = self._profile
        return {
            **self._conn(OFF_CONNECTOR_STATE, enc_int32(state)),
            **self._conn(OFF_SESSION_STATE, enc_int32(session_state)),
            **self._conn(OFF_CURRENT, enc_float32(actual / p.voltage_v)),
            **self._conn(OFF_POWER, enc_float32(actual)),
            **self._conn(OFF_SOC, enc_int32(round(self._soc * 10))),
            **self._conn(OFF_CHARGED_ENERGY, enc_float32(self._charged_wh)),
            **self._conn(OFF_DISCHARGED_ENERGY, enc_float32(self._discharged_wh)),
            **self._conn(OFF_PRESENT_CONSUMPTION, enc_float32(actual)),
            **self._conn(OFF_CAR_ID, enc_string(p.car_id, _CAR_ID_REGS)),
            **words_at(CP_STATE, enc_int32(CP_STATE_IN_USE)),
        }

    def _phase_name(self, phase: str) -> str:
        state, _ = _PHASE_STATES[phase]
        return f"{STATE_NAMES[state]}({state})"

    def _scenario_registers(self) -> dict[int, int]:
        regs = dict(self._scenario.registers)
        regs.update(
            {
                self._base + offset: word
                for offset, word in self._scenario.connector_registers.items()
            }
        )
        return regs

    def _may_write(self, address: int) -> bool:
        if address in PROTECTED_CP_ADDRESSES:
            return False
        # V2G's command registers, on any connector.
        return not (
            address >= CONNECTOR_STRIDE
            and address % CONNECTOR_STRIDE in COMMAND_OFFSETS
        )

    async def _resume_soc_from_mock(self):
        """Adopt the mock's current SoC (X+12, per-mille) if it is a valid value,
        so a hot reload or restart continues instead of resetting to the default.
        """
        if not await self._ensure_connected():
            return
        words = await self._read_words(self._base + OFF_SOC, 2)
        if words is None:
            return
        raw = dec_int32(words)
        if 20 <= raw <= 970:
            self._soc = raw / 10.0

    # --- Modbus helpers ---------------------------------------------------
    async def _write_many(self, registers: dict[int, int]):
        """Write contiguous runs with one FC16 each, so a multi-word value
        (int32/float32/string) can never be read half-updated."""
        addresses = sorted(registers)
        for address in addresses:
            if not self._may_write(address):
                raise ValueError(f"Emulator may not write command register {address}")
        run: list[int] = []
        for address in addresses:
            if run and address != run[-1] + 1:
                await self._write_words(run[0], [registers[a] for a in run])
                run = []
            run.append(address)
        if run:
            await self._write_words(run[0], [registers[a] for a in run])

    async def _write_words(self, address: int, words: list[int]):
        await self._client.write_registers(
            address=address, values=[int(w) & 0xFFFF for w in words], device_id=1
        )
