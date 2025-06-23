"""Client for goodwe inverter"""

from typing import Optional
from goodwe import connect, ET
from base_bidirectional_EVSE import BidirectionalEVSE, DataStatus
from appdaemon.plugins.hass.hassapi import Hass
from event_bus import EventBus
from log_wrapper import get_class_method_logger


class GoodWeClient(BidirectionalEVSE):
    """
    A client to communicate with GoodWe hybrid inverters of the ET series.
    Only the following operation modes are used:
    - Eco Charge
    - Eco Discharge
    - Self-use (when schedule is idle)
    """

    BATTERY_MODES: dict[int, str] = {
        0: "No battery",
        1: "Standby",
        2: "Discharge",
        3: "Charge",
        4: "To be charged",
        5: "To be discharged",
        -1: "No able to read battery mode",
    }

    def __init__(self, hass, event_bus):
        super().__init__()
        self.hass = hass
        self.eb = event_bus
        self.__log = get_class_method_logger(hass.log)

        self.poll_timer_handle: Optional[str] = None

        # EVSE Properties
        self.gw_ip_address: Optional[str] = None
        self.max_charge_power_w: Optional[int] = None
        self.efficiency: float = 0.85
        self.inverter = None

        # Car properties
        self.battery_capacity_kwh: Optional[int] = None

        # Live inverter data
        self.battery_soc_percent: int | DataStatus = DataStatus.UNAVAILABLE
        self.actual_charge_power: int | DataStatus = DataStatus.UNAVAILABLE

    def initialise_EVSE(
        self,
        battery_capacity_kwh: int,
        efficiency_percent: int,
        communication_config: dict,
    ):
        """
        Initialise GoodWe ET hybrid inverter (from abstract BidirectionalEVSE method).
        """
        self.__log(f"Initialising GoodWeClient with config: {communication_config}")

        ipa = communication_config.get("ip_address")
        if not ipa:
            self.__log(
                "GoodWe Inverter initialisation failed, no IP address.",
                level="WARNING",
            )
            raise ValueError("IP address required for GoodWeClient")

        self.gw_ip_address = ipa
        self.battery_capacity_kwh = battery_capacity_kwh
        self.efficiency = round(efficiency_percent / 100, 2)
        self.max_charge_power_w = communication_config.get("max_charge_power_w", 10000)

        self.__log(
            f"GoodWe Inverter initialised with IP: {self.gw_ip_address}, "
            f"Battery capacity: {self.battery_capacity_kwh} kWh, "
            f"Efficiency: {self.efficiency_percent}%, "
            f"Max charge power: {self.max_charge_power_w} W"
        )
        # TODO: retun max charge power?

    async def get_hardware_power_limit(self) -> Optional[int]:
        # TODO: Implement according to GoodWe API specifics
        # For example, retrieve limit from inverter, else None
        return self.max_charge_power_w

    async def set_max_charge_power(self, power_in_watt: int):
        # TODO: Implement setting max charge power logic here
        if power_in_watt > self.max_charge_power_w:
            self.__log(
                f"Requested max charge power {power_in_watt}W exceeds hardware limit {self.max_charge_power_w}W",
                level="WARNING",
            )
            power_in_watt = self.max_charge_power_w
        self.max_charge_power_w = power_in_watt
        # You would add code to apply this limit to the inverter if supported

    async def kick_off_polling(self):
        """Start polling"""
        if self.poll_timer_handle is not None:
            self.hass.cancel_timer(self.poll_timer_handle)
        if self.inverter is None:
            await self._connect()
        self.poll_timer_handle = await self.hass.run_every(
            self._get_and_process_inverter_data,
            "now",
            self.BASE_POLLING_INTERVAL_SECONDS,
        )
        self.__log("Kicked off polling.")

        # BETA debugging
        data = await self.inverter.read_runtime_data()
        self.__log(f"debug data for inverter: {data}.")

    def get_soc_percent(self) -> int:
        """Get most recent state of charge in percent of the connected (car) battery.
        Can be 'unavailable'.
        """
        return self.battery_soc_percent

    def get_soc_kwh(self) -> float:
        """Get most recent state of charge in kwh of the connected (car) battery.
        Can be 'unavailable'.
        """
        if not isinstance(self.battery_soc_percent, (int, float)) or not isinstance(
            self.battery_capacity_kwh, (float)
        ):
            return DataStatus.UNAVAILABLE
        return round(self.battery_soc_percent * self.battery_capacity_kwh, 2)

    async def is_car_connected(self) -> bool:
        """Is chargeplug in the socket."""
        # Unfortunately in this version this cannot be detected directly
        return self.get_soc_percent() != DataStatus.UNAVAILABLE

    async def start_charging(self, power_in_watt: int):
        """Start charging with specified power in Watt, can be negative.
        A power_in_watt of 0 will result in stop charging.
        """
        pass

    async def stop_charging(self):
        """Stop charging"""
        pass

    async def is_charging(self) -> bool:
        """Is the battery being charged (positive power value, soc is increasing)"""
        return False

    async def is_discharging(self) -> bool:
        """Is the battery being discharged (negative power value, soc is decreasing)"""
        return False

    ######################################################################
    #                           PRIVATE METHODS                          #
    ######################################################################

    async def _handle_soc_change(self, new_soc: int, old_soc: int):
        """Handle changed soc"""
        self.event_bus.emit_event("soc_change", new_soc=new_soc, old_soc=old_soc)
        self.event_bus.emit_event(
            "remaining_range_change",
            remaining_range=await self.get_car_remaining_range(),
        )

    def _proces_number(
        self,
        number_to_process: int | float | str,
        min_value: int | float = None,
        max_value: int | float = None,
        number_name: str = None,
    ):
        if number_to_process == DataStatus.UNAVAILABLE or number_to_process is None:
            return DataStatus.UNAVAILABLE

        try:
            processed_number = int(float(number_to_process))
        except ValueError as ve:
            self.__log(
                f"Number named '{number_name}' with value '{number_to_process}' cannot be processed"
                f"due to ValueError: {ve}.",
                level="WARNING",
            )
            return DataStatus.UNAVAILABLE

        if isinstance(min_value, (int, float)) and isinstance(max_value, (int, float)):
            if min_value <= processed_number <= max_value:
                return processed_number
            else:
                self.__log(
                    f"Number named '{number_name}' with value '{number_to_process}' is out of range"
                    f"min '{min_value}' - max '{max_value}'.",
                    level="WARNING",
                )
                return DataStatus.UNAVAILABLE

    async def _connect(self):
        """Establish a connection to the GoodWe ET inverter."""
        if self.inverter is None:
            try:
                self.inverter = await connect(self.gw_ip_address, family=ET)
                self.__log("Connected to GoodWe ET inverter.")
            except Exception as e:
                self.__log(f"Error connecting to inverter: {e}", level="WARNING")

    async def _get_and_process_inverter_data(self, *_args):
        """Retrieve and process all data from inverter"""
        try:
            data = await self.inverter.read_runtime_data()
        except Exception as e:
            self.__log(f"Error reading runtime data: {e}", level="WARNING")
            # TODO: When to warn the user this requiers action?

        # ---- Battery Mode ----
        bm = data.get("battery_mode", -1)
        self.__log(f"Battery mode: {self.BATTERY_MODES.get(bm)}")

        # ---- Actual Charge Power ----
        # Power is reversed in sign (-x is charging with x, not discharging).
        # To simplify checks abs is used
        acp = abs(data.get("pbattery1", DataStatus.UNAVAILABLE))

        # Interface box draws energy? So, even without a car connected the power is not 0.
        # Powers below 25 W are most likely only standby energy use.
        if acp != DataStatus.UNAVAILABLE:
            acp = self._proces_number(
                acp, min_value=25, max_value=20000, number_name="charge_power"
            )
            if bm == 2:
                # Discharging
                acp = -acp

        # TODO: What value is present/sent when no battery is connected?
        if acp != self.actual_charge_power:
            # TODO: Fire event power_change
            self.actual_charge_power = acp

        self.__log(f"Actual Charge Power: {acp} W")
        # pbattery2 is not implemented in the library

        # ---- Car battery State of Charge ----
        soc = data.get("battery_soc", DataStatus.UNAVAILABLE)
        soc = self._proces_number(
            soc, min_value=0, max_value=100, number_name="battery_soc"
        )
        # TODO: Check how inverter / interfacebox / car return the soc values, Is a value of 0 ever
        # sent?
        if soc != self.battery_soc_percent:
            self._handle_soc_change(new_soc=soc, old_soc=self.battery_soc_percent)
            self.battery_soc_percent = soc

            if (
                self.battery_soc_percent == DataStatus.UNAVAILABLE
                or soc == DataStatus.UNAVAILABLE
            ):
                # TODO: Fire event "connected state change"
                pass

        self.__log(f"Battery State of Charge: {soc}%")

        # TODO: also handle errors, etc.

    # TODO:
    # Implement:
    # - setters
    # - start/stop charging
    # - etc.

    # TODO:
    # See if these date need to play any role:
    # om = await self.inverter.get_operation_mode()
    # gel = await self.inverter.get_grid_export_limit()
    # obd = await self.inverter.get_ongrid_battery_dod()
