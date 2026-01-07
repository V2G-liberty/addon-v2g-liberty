from abc import ABC, abstractmethod


class BaseEV(ABC):
    def __init__(self):
        super().__init__()
        self.SOC_CHANGED_EVENT_NAME = "soc_change"
        self.REMAINING_RANGE_EVENT_NAME = "remaining_range_change"

    # --- Initialisation methods ---

    @abstractmethod
    def initialise_ev(
        self,
        name: str,
        battery_capacity_kwh: int,
        charging_efficiency_percent: int,
        consumption_wh_per_km: int,
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
        - consumption_wh_per_km (int):
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
    def name(self) -> str:
        """Name of the car e.g. model/type, chosen by user."""
        raise NotImplementedError("Subclasses must implement name")

    @property
    @abstractmethod
    def charging_efficiency(self) -> float:
        """Is float between 0.1 and 1.0.
        Preferably this is read from the car (via charger).
        """
        raise NotImplementedError("Subclasses must implement charging_efficiency")

    @property
    @abstractmethod
    def battery_capacity_kwh(self) -> int | None:
        """Returns car battery capacity in kWh, or None if unavailable"""
        raise NotImplementedError("Subclasses must implement battery_capacity_kwh")

    @property
    @abstractmethod
    def soc(self) -> float | None:
        """Returns SOC in %, or None if unavailable"""
        raise NotImplementedError("Subclasses must implement soc")

    @property
    @abstractmethod
    def soc_kwh(self) -> int | None:
        """State of charge in kWh"""
        raise NotImplementedError("Subclasses must implement soc_kwh")

    @property
    @abstractmethod
    def remaining_range_km(self) -> int | None:
        """Remaining range in km"""
        raise NotImplementedError("Subclasses must implement remaining_range_km")

    @property
    @abstractmethod
    def max_range_km(self) -> int | None:
        """Maximum range in km"""
        raise NotImplementedError("Subclasses must implement max_range_km")

    @property
    @abstractmethod
    def min_soc_percent(self) -> int:
        """Minimum state of charge in percent (10-55). Entered by user."""
        raise NotImplementedError("Subclasses must implement min_soc_percent")

    @property
    @abstractmethod
    def max_soc_percent(self) -> int:
        """Maximum state of charge in percent (60-95). Entered by user."""
        raise NotImplementedError("Subclasses must implement max_soc_percent")

    @property
    @abstractmethod
    def min_soc_kwh(self) -> int:
        """Minimum state of charge in kwh. Entered by user."""
        raise NotImplementedError("Subclasses must implement min_soc_kwh")

    @property
    @abstractmethod
    def max_soc_kwh(self) -> int:
        """Maximum state of charge in kwh. Entered by user."""
        raise NotImplementedError("Subclasses must implement max_soc_kwh")

    @property
    @abstractmethod
    def is_soc_below_minimum(self) -> bool:
        """Returns True if SoC is below minimum SoC setting."""
        raise NotImplementedError("Subclasses must implement is_soc_below_minimum")

    @property
    @abstractmethod
    def is_soc_above_maximum(self) -> bool:
        """Returns True if SoC is above maximum SoC setting."""
        raise NotImplementedError("Subclasses must implement is_soc_above_maximum")

    @property
    @abstractmethod
    def wh_to_min_soc(self) -> int:
        """Returns Wh needed to reach min. SoC."""
        raise NotImplementedError("Subclasses must implement wh_to_min_soc")

    @property
    @abstractmethod
    def soc_system_limit_percent(self) -> int:
        """Returns the system limit for SoC in percent. Represents hardware limit."""
        raise NotImplementedError("Subclasses must implement soc_system_limit_percent")

    #################### SETTER METHODS ####################

    @abstractmethod
    def set_soc(self):
        """For 'internal use' only: the charger class reads this from the actual vehicle and sets
        the read value via this method.
        If needed the soc_changed event is emitted."""
        raise NotImplementedError("Subclasses must implement set_soc()")
