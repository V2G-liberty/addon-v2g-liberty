from abc import ABC, abstractmethod
from appdaemon.plugins.hass.hassapi import Hass

from apps.v2g_liberty.chargers.base_unidirectional_evse import UnidirectionalEVSE


class BidirectionalEVSE(UnidirectionalEVSE, ABC):
    _hass: Hass = None

    @abstractmethod
    async def start_charging(self, power_in_watt: int):
        """
        Start charging or discharging with the specified power in Watts.
        Positive = charging, Negative = discharging.
        """
        raise NotImplementedError("Subclasses must implement start_charging()")

    # TODO: Should this not be a @property?
    @abstractmethod
    async def is_discharging(self) -> bool:
        """
        Return True if currently discharging.
        """
        raise NotImplementedError("Subclasses must implement is_discharging()")

    @abstractmethod
    async def set_max_discharge_power(self, power_in_watt: int):
        """
        Set the maximum discharge power in Watt.
        Must be lower than EVSE hardware limit, see get_hardware_power_limit.
        If not set the hardware limit will be used.
        """
        raise NotImplementedError("Subclasses must implement set_max_discharge_power()")
