"""Module to monitor reconnect at charge mode other then 'Automatic'"""

from appdaemon.plugins.hass.hassapi import Hass

from .event_bus import EventBus
from .notifier_util import Notifier
from .log_wrapper import get_class_method_logger
from .timer_utils import cancel_timer_silent, set_oneshot_timer


class MonitorPauseAtReconnect:
    """
    When the car is reconnected and the charge mode is Pause ask the user if this is still the
    desired mode or if switching to Automatic is preferred.
    This module also triggers for Charge or Discharge during reconnect, eventhough it is expected
    to occure seldom as these are automatically reset to Automatic at disconnect.

    If the user does not respond to the notification within
    ``AUTO_SWITCH_TIMEOUT_SECONDS`` the charge mode is switched to Automatic
    automatically. Without this a forgotten Pause would leave the reconnected
    car under the charger's own control, which (auto)starts charging on connect.
    """

    hass: Hass = None
    event_bus: EventBus = None
    notifier: Notifier = None
    NOTIFICATION_TAG: str = "switch_to_automatic_or_not"
    ACTION_KEEP_CURRENT: str = "keep_current"
    ACTION_TO_AUTOMATIC: str = "automatic"
    # If the user does not respond to the reconnect prompt within this period,
    # the charge mode is switched to Automatic automatically.
    AUTO_SWITCH_TIMEOUT_SECONDS: int = 10 * 60

    def __init__(self, hass: Hass, event_bus: EventBus, notifier: Notifier):
        self.hass = hass
        self.notifier = notifier
        self.event_bus = event_bus
        self._auto_switch_timer_handle: str = ""

        self.__log = get_class_method_logger(module_name="monitor_pause_at_reconnect")

        self.event_bus.add_event_listener(
            "is_car_connected", self._handle_connected_state_change
        )

        self.__log("Completed MonitorPauseAtReconnect")

    async def initialize(self):
        """Register the charge-mode listener.

        A charge-mode change by ANY means (the UI radio buttons, an automation,
        or the reconnect notification) cancels a pending auto-switch fallback, so
        the fallback only ever fires when the user genuinely did not respond to
        the reconnect prompt.
        """
        await self.hass.listen_state(
            self._handle_charge_mode_change,
            "input_select.charge_mode",
        )
        self.__log("Registered charge_mode listener.")

    async def _handle_charge_mode_change(self, entity, attribute, old, new, kwargs):
        """Cancel a pending auto-switch fallback when the charge mode changes.

        Any deliberate charge-mode change counts as the user having responded,
        so the fallback must not fire afterwards. A no-op when no timer is armed.
        """
        if new == old:
            return
        await cancel_timer_silent(self.hass, self._auto_switch_timer_handle)
        self._auto_switch_timer_handle = ""

    async def _handle_chosen_charge_mode(self, user_action: str):
        self.__log(f"user_action: '{user_action}'.")

        # The user responded in time: cancel the pending auto-switch fallback.
        await cancel_timer_silent(self.hass, self._auto_switch_timer_handle)
        self._auto_switch_timer_handle = ""

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

    async def _auto_switch_to_automatic(self, kwargs: dict = None):
        """Fallback when the user does not respond to the reconnect prompt.

        Scheduled as a one-shot timer by :meth:`_handle_connected_state_change`.
        AppDaemon passes its scheduling kwargs as a single positional dict, which
        is ignored here.
        """
        self._auto_switch_timer_handle = ""
        self.__log(
            "No response to reconnect prompt within timeout: "
            "switching charge_mode to 'Automatic'."
        )
        # Clear the prompt and switch to Automatic (main app reacts via HA event).
        self.notifier.clear_notification(tag=self.NOTIFICATION_TAG)
        await self.hass.turn_on("input_boolean.chargemodeautomatic")

    async def _handle_connected_state_change(self, is_car_connected: bool):
        if not is_car_connected:
            # Car was disconnected, no need to notify now. Drop any pending
            # auto-switch fallback from an earlier reconnect.
            await cancel_timer_silent(self.hass, self._auto_switch_timer_handle)
            self._auto_switch_timer_handle = ""
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
                "title": "Switch to automatic charging",
            },
        ]

        await self.notifier.notify_user(
            message="Would you like to set it to 'Automatic'?",
            title=f"Car connected, the app is set to '{charge_mode}'",
            tag=self.NOTIFICATION_TAG,
            send_to_all=True,
            ttl=self.AUTO_SWITCH_TIMEOUT_SECONDS,
            actions=user_actions,
            callback=self._handle_chosen_charge_mode,
        )

        # If the user does not respond within the timeout, switch to Automatic
        # automatically so a forgotten Pause does not leave the reconnected car
        # under the charger's own (autostart) control.
        self._auto_switch_timer_handle = await set_oneshot_timer(
            self.hass,
            self._auto_switch_timer_handle,
            self._auto_switch_to_automatic,
            delay=self.AUTO_SWITCH_TIMEOUT_SECONDS,
        )

        self.__log(
            "Car reconnected while charge_mode is not Automatic. Notified user: switch to Autom.?"
        )
