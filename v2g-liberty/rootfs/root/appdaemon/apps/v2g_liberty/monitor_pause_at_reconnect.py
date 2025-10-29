"""Module to monitor reconnect at charge mode other then 'Automatic'"""

from appdaemon.plugins.hass.hassapi import Hass

from . import constants as c
from .event_bus import EventBus
from .notifier_util import Notifier
from .log_wrapper import get_class_method_logger


class MonitorPauseAtReconnect:
    """
    When the car is reconnected and the charge mode is Pause ask the user if this is still the
    desired mode or if switching to Automatic is preferred.
    This module also triggers for Charge or Discharge during reconnect, eventhough it is expected
    to occure seldom as these are automatically reset to Automatic at disconnect.
    """

    hass: Hass = None
    event_bus: EventBus = None
    notifier: Notifier = None
    NOTIFICATION_TAG:str = "switch_to_automatic_or_not"
    ACTION_KEEP_CURRENT:str = "keep_current"
    ACTION_TO_AUTOMATIC:str = "automatic"

    def __init__(self, hass: Hass, event_bus: EventBus, notifier: Notifier):
        self.hass = hass
        self.notifier = notifier
        self.event_bus = event_bus

        self.__log = get_class_method_logger(hass.log)

        self.event_bus.add_event_listener("is_car_connected", self._handle_connected_state_change)

        self.__log("Completed MonitorPauseAtReconnect")

    async def _handle_chosen_charge_mode(self, user_action:str):
        self.__log(f"user_action: '{user_action}'.")

        # Clear notification from other users phone
        self.notifier.clear_notification(tag=self.NOTIFICATION_TAG)

        if user_action == self.ACTION_TO_AUTOMATIC:
             # The main app reacts to this via HA event
             await self.hass.turn_on("input_boolean.chargemodeautomatic")
             self.__log("By user request the charge_mode is set to 'Automatic'.")
        elif user_action == self.ACTION_KEEP_CURRENT:
            #  Do nothing
             self.__log("By user request the charge_mode is unchanged.")
        else:
            self.__log(f"Unknown user_action: '{user_action}'.", level="WARNING")

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
                "action": self.ACTION_KEEP_CURRENT,
                "title": f"Keep charge mode {charge_mode}",
            },
            {
                "action": self.ACTION_TO_AUTOMATIC,
                "title": "Switch to automatic charging"
            },
        ]

        self.notifier.notify_user(
            message=f"Would you like to set it to 'Automatic'?",
            title=f"Car connected, the app is set to '{charge_mode}'",
            tag=self.NOTIFICATION_TAG,
            send_to_all=True,
            ttl=30*60,
            actions=user_actions,
            callback=self._handle_chosen_charge_mode
        )

        self.__log(
            "Car reconnected while charge_mode is not Automatic. Notified user: switch to Autom.?"
        )
