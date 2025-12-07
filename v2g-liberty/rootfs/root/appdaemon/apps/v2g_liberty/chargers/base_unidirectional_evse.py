from abc import ABC, abstractmethod
from pyee.asyncio import AsyncIOEventEmitter
from v2g_liberty.evs.electric_vehicle import ElectricVehicle

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
        raise NotImplementedError("Subclasses must implement set_charging_efficiency()")

    # TODO: Should also be a @property?
    @abstractmethod
    async def get_charging_efficiency(self) -> float:
        """Is float between 0.1 and 1.0.
        Preferably this is read from the charger.
        """
        raise NotImplementedError("Subclasses must implement get_charging_efficiency()")


    ####################### STATUS METHODS ######################

    #TODO: Should this not be a @property?
    @abstractmethod
    async def get_connected_car(self) -> ElectricVehicle:
        raise NotImplementedError("Subclasses must implement get_connected_car()")

    #TODO: Should this not be a @property?
    @abstractmethod
    async def is_charging(self) -> bool:
        raise NotImplementedError("Subclasses must implement is_charging()")


    ################### Protected event emitters #####################

    def _emit_is_car_connected_changed(self, is_car_connected: bool):
        """Emit event when connection status changes.
        Parameters:
         - is_car_connected (bool): New connection status
        """
        self.emit("is_car_connected_changed", is_car_connected)
