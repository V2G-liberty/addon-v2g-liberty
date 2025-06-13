"""Client for goodwe inverter"""

import log_wrapper
from goodwe import connect, ET

from appdaemon.plugins.hass.hassapi import Hass


class GoodWeClient:
    """
    A client to communicate with GoodWe hybrid inverters of the ET series.
    Only the follwoing operation modes are used:
    - Eco Charge
    - Eco Discharge
    - Self-use (when schedule is idle)

    """

    hass: Hass = None
    inverter = None

    # Constants
    UNAVAILABLE_STATE: str = "unavailable"

    # Inverter settings
    gw_ip_address: str = None
    battery_capacity_kwh: int = None
    max_charge_power_w: int = None

    # Polling constants/variables
    BASE_POLLING_INTERVAL_SECONDS: int = 30
    poll_timer_handle: str = None

    # Live values from inverter
    battery_soc_percent: int = UNAVAILABLE_STATE
    is_car_connected: bool = False

    def __init__(self, hass: Hass):
        """Initialise GoodWeClient"""
        super().__init__()
        self.hass = hass
        self.__log = log_wrapper.get_class_method_logger(hass.log)
        self.__log("Initialise GoodWeClient complete")

    def initialise_charger(self, config: dict, **kwargs):
        """config: dict
        Should contain: ip_address, battery_capacity_kwh, max_charge_power_w
        """
        setting = config.get("ip_address", None)
        if setting is None:
            return False
        self.gw_ip_address = setting

        setting = config.get("battery_capacity_kwh", None)
        if setting is None:
            return False
        self.battery_capacity_kwh = int(float(setting))

        setting = config.get("max_charge_power_w", 10000)
        self.battery_capacity_kwh = int(float(setting))

        return True

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

    def get_soc_percent(self) -> int:
        """
        Get most recent state of charge in percent of the connected (car) battery.
        Can be 'unavailable'.
        """
        return self.battery_soc_percent

    def get_soc_kwh(self) -> float:
        """
        Get most recent state of charge in kwh of the connected (car) battery.
        Can be 'unavailable'.
        """
        if not isinstance(self.battery_soc_percent, (int, float)) or not isinstance(
            self.battery_capacity_kwh, (float)
        ):
            return self.UNAVAILABLE_STATE
        return round(self.battery_soc_percent * self.battery_capacity_kwh, 2)

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
        data = await self.inverter.read_runtime_data()
        soc = data.get("battery_soc", self.UNAVAILABLE_STATE)
        # TODO: convert to float, make util function to do so and handle "unavailable"
        if soc != self.battery_soc_percent:
            # Fire event soc_change
            self.battery_soc_percent = soc
        self.__log(f"Battery State of Charge: {soc}%")

        # TODO: also handle connected_state, current_charge_power, etc.

    # TODO:
    # Implement:
    # - setters
    # - start/stop charging
    # - etc.

    # async def get_initial_data(self):
    #     if self.inverter is None:
    #         await self._connect()
    #     om = await self.inverter.get_operation_mode()
    #     gel = await self.inverter.get_grid_export_limit()
    #     obd = await self.inverter.get_ongrid_battery_dod()
    #     data = await self.inverter.read_runtime_data()
    #     self.__log(
    #         f"Initial data, Data: {data}, Operation mode: '{om}', "
    #         f"Grid Export Limit: '{gel}', Ongrid Battarij DoD: '{obd}'."
    #     )
