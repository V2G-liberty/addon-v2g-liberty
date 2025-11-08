from abc import ABC, abstractmethod
from pyee.asyncio import AsyncIOEventEmitter
from v2g_liberty.enum import DataStatus
from typing import Union

class UnidirectionalEVSE(AsyncIOEventEmitter, ABC):
    def __init__(self):
        super().__init__()

    # --- Initialisation methods ---

    @abstractmethod
    async def initialise_evse(
        self,
        communication_config: dict,
    ):
        """
        Initialize EVSE .

        Parameters:
        - communication_config (dict):
          Configuration dictionary, typically including IP address, and optionally port, protocol,
          or other settings depending on the charger type.
        """
        # TODO: Move battery_capacity_kwh to a new class EV, split the efficiency in car / charger part.
        raise NotImplementedError("Subclasses must implement initialise_EVSE()")

    #################### ACTIONS ####################

    @abstractmethod
    async def start_charging(self, power_in_watt: int):
        """Start charging at given power in Watts (> 0)."""
        raise NotImplementedError("Subclasses must implement start_charging()")

    @abstractmethod
    async def stop_charging(self):
        raise NotImplementedError("Subclasses must implement stop_charging()")

    # TODO: should set_active() and set_inactive() be abstract methods?

    ############### GETTER/SETTER METHODS ###############

    #TODO: Should this not be a @property?
    @abstractmethod
    async def get_hardware_power_limit(self) -> int | None:
        """Get the EVSE (or car) hardware charge power limit in Watt.
        If None then the hardware power limit could not be retrieved."""
        raise NotImplementedError(
            "Subclasses must implement get_hardware_power_limit()"
        )

    @abstractmethod
    async def set_max_charge_power(self, power_in_watt: int):
        """Must be lower than EVSE hardware limit, see get_EVSE_charge_power_limit.
        If not set the hardware limit will be used."""
        raise NotImplementedError("Subclasses must implement set_max_charge_power()")


    # TODO: Should also be a @property?
    @abstractmethod
    async def set_charging_efficiency(self, efficiency_percent: int):
        """Must be between 10 and 100.
        If not set the hardware limit will be used.
        Preferably this is read from the charger.
        """
        # TODO: Split efficiency in car and EVSE part if Car is modelled separately.
        raise NotImplementedError("Subclasses must implement set_charging_efficiency()")

    # TODO: Should also be a @property?
    @abstractmethod
    async def get_charging_efficiency(self) -> float:
        """Is float between 0.1 and 1.0.
        Preferably this is read from the charger.
        """
        # TODO: Split efficiency in car and EVSE part if Car is modelled separately.
        raise NotImplementedError("Subclasses must implement get_charging_efficiency()")


    @abstractmethod
    async def get_car_battery_capacity_kwh(self) -> Union[int, DataStatus]:
        """Returns car battery capacity in kWh, or DataStatus.UNAVAILABLE"""
        raise NotImplementedError(
            "Subclasses must implement get_car_battery_capacity_kwh()"
        )

    @abstractmethod
    async def set_car_battery_capacity_kwh(self, capacity_kwh: int):
        """Sets car battery capacity in kWh, or DataStatus.UNAVAILABLE"""
        raise NotImplementedError(
            "Subclasses must implement set_car_battery_capacity_kwh()"
        )


    #TODO: Should this not be a @property?
    @abstractmethod
    async def get_car_soc(self) -> Union[float, DataStatus]:
        """Returns SOC in %, or DataStatus.UNAVAILABLE"""
        raise NotImplementedError("Subclasses must implement get_car_soc()")

    #TODO: Should this not be a @property?
    @abstractmethod
    async def get_car_soc_kwh(self) -> Union[int, DataStatus]:
        """State of charge in kWh"""
        raise NotImplementedError("Subclasses must implement get_car_soc_kwh()")

    #TODO: Should this not be a @property?
    @abstractmethod
    async def get_car_remaining_range_km(self) -> Union[int, DataStatus]:
        """Remaining range in km"""
        raise NotImplementedError("Subclasses must implement get_car_remaining_range_km()")


    ####################### STATUS METHODS ######################

    #TODO: Should this not be a @property?
    @abstractmethod
    async def is_car_connected(self) -> bool:
        raise NotImplementedError("Subclasses must implement is_car_connected()")

    #TODO: Should this not be a @property?
    @abstractmethod
    async def is_charging(self) -> bool:
        raise NotImplementedError("Subclasses must implement is_charging()")


    ################### Protected event emitters #####################

    def _emit_soc_changed(self, old_soc: int, new_soc: int):
        """Emit event when SoC changes.
        Parameters:
         - old_soc (int): Previous state of charge in %
         - new_soc (int): New state of charge in %
        """
        self.emit("soc_changed", old_soc, new_soc)

    def _emit_is_car_connected_changed(self, is_car_connected: bool):
        """Emit event when connection status changes.
        Parameters:
         - is_car_connected (bool): New connection status
        """
        self.emit("is_car_connected_changed", is_car_connected)
