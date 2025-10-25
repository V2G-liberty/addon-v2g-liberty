"""Module to monitor reconnect at chargemode Pause"""

from appdaemon.plugins.hass.hassapi import Hass

from . import constants as c
from .event_bus import EventBus
from .notifier_util import Notifier
from .log_wrapper import get_class_method_logger


class MonitorPauseAtReconnect:
    """
    When the car is reconnected and the charge mode is Pause ask the user if this is still the
    desired mode or if switching to Automatic is preferred.
    This module also triggers for Charge or Discharge during reconnect but it expected never to
    occure as these are automatically reset to Automatic at disconnect.
    """

    hass: Hass = None
    event_bus: EventBus = None
    notifier: Notifier = None

    def __init__(self, hass: Hass, event_bus: EventBus, notifier: Notifier):
        self.hass = hass
        self.notifier = notifier
        self.event_bus = event_bus

        self.__log = get_class_method_logger(hass.log)

        self.event_bus.add_event_listener("is_car_connected", self._handle_connected_state_change)

        self.__log("Completed MonitorPauseAtReconnect")

    async def _handle_connected_state_change(self, is_car_connected: bool):
        if not is_car_connected:
            # Car was disconnected, no need to notify now
            return

        charge_mode = await self.hass.get_state("input_select.charge_mode", None)
        if charge_mode is None:
            self.__log("Error: charge_mode is None")
            return

        if charge_mode == "Automatic":
            return

        # TODO: better way of translating...
        if charge_mode == "Stop":
            charge_mode = "Pause"


        user_actions = [
            {
                "action": "keep_current_charge_mode",
                "title": f"Keep charge mode {charge_mode}",
            },
            {
                "action": "set_charge_mode_to_automatic",
                "title": "Switch to automatic charging"
            },
        ]

        self.notifier.notify_user(
            message=f"App is set to '{charge_mode}', would you like to set it to 'Automatic'?",
            title=None,
            tag="switch_to_automatic_or_not",
            send_to_all=True,
            ttl=30*60,
            actions=user_actions
        )

        self.__log("Car reconnected while charge_mode is 'Pause', Notified user: switch to Autom.?")
