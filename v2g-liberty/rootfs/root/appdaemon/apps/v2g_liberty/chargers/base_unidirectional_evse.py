from abc import ABC, abstractmethod
from pyee.asyncio import AsyncIOEventEmitter
from v2g_liberty.evs.electric_vehicle import ElectricVehicle


class UnidirectionalEVSE(AsyncIOEventEmitter, ABC):
    # 0: Charger is booting or we are trying to startup communication (default state)
    # 1: Plug is not connected to a car.
    # 2: Connected and available but not in use for dis-/charging
    # 3: Charging the car battery
    # 4: Discharging, feeding power into the home net.
    # 5: Charging, the scheduled power is reduced externally. E.g. the car or loadbalancing.
    # 6: Discharging, the scheduled power is reduced externally. E.g. the car or loadbalancing.
    # 7: Controlled by other app: An other captain on the ship (regardless of car being
    #    connected or not). Keep info up to date but do not actively manage the charging.
    # 8: Charger is locked (RFID) and cannot be controlled.
    # 9: Error: Sometimes EVSE goes into this state during booting.
    # 10: Error communicating with the charger, state us unknown.
    _EVSE_STATES: dict[int, str] = {
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

    # One could argue that Error states should also be considered "not connected",
    # but these are handled in other ways.
    _DISCONNECTED_STATES = [0, 1]
    _CHARGING_STATES: int = [3, 5]
    _DISCHARGING_STATES: int = [4, 6]
    _AVAILABILITY_STATES = [1, 2, 3, 4, 5, 6]
    _ERROR_STATES = [9, 10]

    def __init__(self):
        super().__init__()

    # --- Initialisation methods ---

    def get_evse_state_str(self, evse_state: int) -> str:
        """Get a text representing the _EVSE_STATE (0..10)"""
        # TODO: these should be id's for lookup in a strings.json file for easy translation.
        return self._EVSE_STATES.get(evse_state, "Unknown")

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
    async def set_active(self):
        """To be called when charge_mode in UI is (switched to) Automatic or Boost"""
        raise NotImplementedError("Subclasses must implement set_active()")

    @abstractmethod
    async def set_inactive(self):
        """To be called when charge_mode in UI is (switched to) Stop.
        This:
        - Stop (dis)charging
        - Give control to the charger (if applicable) to be managed by other software.
        Do not cancel polling, the information is still relevant.
        """
        raise NotImplementedError("Subclasses must implement set_inactive()")

    @abstractmethod
    async def start_charging(self, power_in_watt: int):
        """Start charging at given power in Watts (> 0)."""
        raise NotImplementedError("Subclasses must implement start_charging()")

    @abstractmethod
    async def stop_charging(self):
        raise NotImplementedError("Subclasses must implement stop_charging()")

    ############### GETTER/SETTER METHODS ###############

    # TODO: Should this not be a @property?
    @abstractmethod
    async def get_hardware_power_limit(self) -> int | None:
        """Get the EVSE (or car) hardware charge power limit in Watt.
        If None then the hardware power limit could not be retrieved."""
        raise NotImplementedError(
            "Subclasses must implement get_hardware_power_limit()"
        )

    @property
    @abstractmethod
    def max_charge_power_w(self) -> int:
        """Get the maximum charge power in Watt (usually above 1380 and below 25000)."""
        raise NotImplementedError("Subclasses must implement max_charge_power_w")

    @property
    @abstractmethod
    def max_discharge_power_w(self) -> int:
        """Get the maximum discharge power in Watt (usually above 1380 and below 25000)."""
        raise NotImplementedError("Subclasses must implement max_discharge_power_w")

    @abstractmethod
    async def set_max_charge_power(self, power_in_watt: int):
        """Must be lower than EVSE hardware limit, see get_hardware_power_limit.
        If not set the hardware limit will be used."""
        raise NotImplementedError("Subclasses must implement set_max_charge_power()")

    # # TODO: Split efficiency in EVSE and EV part, for now: only use EV.
    # @abstractmethod
    # async def set_charging_efficiency(self, efficiency_percent: int):
    #     """Must be between 10 and 100.
    #     If not set the hardware limit will be used.
    #     Preferably this is read from the charger.
    #     """
    #     raise NotImplementedError("Subclasses must implement set_charging_efficiency()")

    # @abstractmethod
    # async def get_charging_efficiency(self) -> float:
    #     """Is float between 0.1 and 1.0.
    #     Preferably this is read from the charger.
    #     """
    #     raise NotImplementedError("Subclasses must implement get_charging_efficiency()")

    ####################### STATUS METHODS ######################

    # TODO: Should this not be a @property?
    @abstractmethod
    async def get_connected_car(self) -> ElectricVehicle:
        raise NotImplementedError("Subclasses must implement get_connected_car()")

    # TODO: Should this not be a @property?
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
