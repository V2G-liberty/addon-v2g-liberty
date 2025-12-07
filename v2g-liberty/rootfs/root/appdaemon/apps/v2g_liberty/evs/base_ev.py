from abc import ABC, abstractmethod

class BaseEV(ABC):
    def __init__(self):
        super().__init__()
        self.SOC_CHANGED_EVENT_NAME = "soc_change"
        self.REMAINING_RANGE_EVENT_NAME="remaining_range_change"

    # --- Initialisation methods ---

    @abstractmethod
    async def initialise_ev(
        self,
        name: str,
        battery_capacity_kwh: int,
        charging_efficiency_percent: int,
        car_consumption_wh_per_km: int,
        min_soc_percent: int,
        max_soc_percent: int,
    ):
        """
        Initialize ElectricVehicle

        Parameters:
        - name (str):
          Name of the car e.g. model/type, chosen by user.
        - battery_capacity_kwh (int):
          Battery capacity in kWh. For now: entered by user, preferably read from car (via charger).
        - charging_efficiency_percent (int):
          Charging efficiency in percent (50-100). For now: entered by user, preferably read from
          car (via charger).
        - car_consumption_wh_per_km (int):
          Consumption in Wh per km (100 - 400).
          For now: entered by user, preferably read from car (via charger).
        - min_soc_percent (int):
          Minimum state of charge in percent (10-55). Entered by user.
        - max_soc_percent (int):
          Maximum state of charge in percent (60-95). Entered by user.
        """
        raise NotImplementedError("Subclasses must implement initialise_ev()")


    #### Car has no actions ####

    #################### PROPERTIES ####################

    @property
    @abstractmethod
    async def name(self) -> str:
        """Name of the car e.g. model/type, chosen by user."""
        raise NotImplementedError("Subclasses must implement name")

    @property
    @abstractmethod
    async def charging_efficiency(self) -> float:
        """Is float between 0.1 and 1.0.
        Preferably this is read from the car (via charger).
        """
        raise NotImplementedError("Subclasses must implement charging_efficiency")

    @property
    @abstractmethod
    async def battery_capacity_kwh(self) -> int | None:
        """Returns car battery capacity in kWh, or None if unavailable"""
        raise NotImplementedError(
            "Subclasses must implement battery_capacity_kwh"
        )

    @property
    @abstractmethod
    async def soc(self) -> float | None:
        """Returns SOC in %, or None if unavailable"""
        raise NotImplementedError("Subclasses must implement soc")

    @property
    @abstractmethod
    async def soc_kwh(self) -> int | None:
        """State of charge in kWh"""
        raise NotImplementedError("Subclasses must implement soc_kwh")

    @property
    @abstractmethod
    async def remaining_range_km(self) -> int | None:
        """Remaining range in km"""
        raise NotImplementedError("Subclasses must implement remaining_range_km")


    #################### SETTER METHODS ####################

    @abstractmethod
    async def set_soc(self):
        """For 'internal use' only: the charger class reads this from the actual vehicle and sets
        the read value via this method.
        If needed the soc_changed event is emitted."""
        raise NotImplementedError("Subclasses must implement set_soc()")

