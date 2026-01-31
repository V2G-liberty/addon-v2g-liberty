"""Base fetcher class for FlexMeasures data operations."""

from appdaemon.plugins.hass.hassapi import Hass
from ...log_wrapper import get_class_method_logger


class BaseFetcher:
    """
    Base class for all FlexMeasures data fetchers.

    Provides common client availability checking that can be used by specific
    fetcher implementations.
    """

    def __init__(self, hass: Hass, fm_client_app: object):
        """
        Initialise the base fetcher.

        Args:
            hass: AppDaemon Hass instance for logging
            fm_client_app: FlexMeasures client for API calls
        """
        self.hass = hass
        self.fm_client_app = fm_client_app
        self.__log = get_class_method_logger(hass.log)

    def is_client_available(self) -> bool:
        """
        Check if FlexMeasures client is available.

        Returns:
            bool: True if client is available, False otherwise
        """
        if self.fm_client_app is None:
            self.__log("Could not call get_sensor_data on fm_client_app as it is None.")
            return False
        return True
