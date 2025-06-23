from abc import ABC, abstractmethod
from pyee.asyncio import AsyncIOEventEmitter
from enum import Enum
from typing import Union


class DataStatus(Enum):
    UNAVAILABLE = "unavailable"


class UnidirectionalEVSE(AsyncIOEventEmitter, ABC):
    def __init__(self):
        super().__init__()

    # --- Initialisation methods ---

    @abstractmethod
    def initialise_EVSE(
        self,
        battery_capacity_kwh: int,
        efficienty_percent: int,
        communication_config: dict,
    ):
        """
        Initialize EVSE .

        Parameters:
        - battery_capacity_kwh (int):
          Usable battery capacity of the car in kilowatt-hours (kWh).
        - roundtrip_efficiency_percent (int):
          Round-trip efficiency of the car and charger as a percnt (%).
        - communication_config (dict):
          Configuration dictionary, typically including IP address, and optionally port, protocol,
          or other settings depending on the charger type.
        """
        # TODO: Move battery_capacity_kwh to a new class EV, split the efficiency in car / charger part.
        raise NotImplementedError("Subclasses must implement initialise_EVSE()")

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

    # --- Base methods ---

    @abstractmethod
    async def start_charge(self, power_in_watt: int):
        """Start charging at given power in Watts (> 0)."""
        raise NotImplementedError("Subclasses must implement start_charge_with_power()")

    @abstractmethod
    async def stop_charging(self):
        raise NotImplementedError("Subclasses must implement stop_charging()")

    @abstractmethod
    async def get_car_soc(self) -> Union[float, DataStatus]:
        """Returns SOC in %, or DataStatus.UNAVAILABLE"""
        raise NotImplementedError("Subclasses must implement get_car_soc()")

    @abstractmethod
    async def get_car_soc_kwh(self) -> Union[int, DataStatus]:
        """State of charge in kWh"""
        raise NotImplementedError("Subclasses must implement get_car_soc_kwh()")

    @abstractmethod
    async def is_car_connected(self) -> bool:
        raise NotImplementedError("Subclasses must implement is_car_connected()")

    @abstractmethod
    async def is_charging(self) -> bool:
        raise NotImplementedError("Subclasses must implement is_charging()")

    # --- Protected event emitters ---

    def _emit_soc_changed(self, old_soc: int, new_soc: int):
        """Emit event when SOC changes."""
        self.emit("soc_changed", old_soc, new_soc)

    def _emit_is_car_connected_changed(self, is_car_connected: bool):
        """Emit event when connection status changes."""
        self.emit("is_car_connected_changed", is_car_connected)
