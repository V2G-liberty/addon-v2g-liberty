"""Module to monitor Nissan Leaf for strange charging behaviour"""

from appdaemon.plugins.hass.hassapi import Hass
from .event_bus import EventBus
from .notifier_util import Notifier
from .log_wrapper import get_class_method_logger


class NissanLeafMonitor:
    """
    The Nissan Leaf (or to be specific, it's BMS) has a strange behaviour. When (dis-) charging the
    state of charge (soc) changes but skips 20%, so from 21% jumps to 19%, even when discharge power
    is low or slowly build up.
    This 20% typicaly is the lower limit for the schedules. So, the software at 19% changes form a
    scheduled discharge to a boost charge. Then the 20% is skipped again, jumping from 19% to 21%.
    This cycle repeats every 15 minutes to an hour or so untill the schedules optimum is no longer
    discharging to the lower limit.
    This is undesired behaviour as it is in-efficient and possibly harmfull for the battery.

    This module monitors if this behaviour is happening and then warns the users, suggesting to set
    the lower-limit to e.g. 18 or 19%.
    """

    hass: Hass = None
    event_bus: EventBus = None
    notifier: Notifier = None

    def __init__(self, hass: Hass, event_bus: EventBus, notifier: Notifier):
        self.hass = hass
        self.notifier = notifier
        self.event_bus = event_bus

        self.__log = get_class_method_logger(hass.log)

        self._initialize()

        self.__log("Completed")

    def _initialize(self):
        self.event_bus.add_event_listener("nissan_leaf_soc_skipped", self._check_notification)

    async def _check_notification(self, min_soc: int, ev_name: str):
        """Handles notification when soc skip detected."""
        message = (
            f"The '{ev_name}' faulted, skipping the battery state-of-charge "
            f"{min_soc}%. This often leads to toggled charging. "
            f"A possible solution is to change the setting for 'schedule lower limit'"
            f"to 1%-point higher or lower."
        )
        try:
            relevant_duration = 24 * 60 * 60
            self.notifier.notify_user(
                message=message, tag="soc_skipped", ttl=relevant_duration
            )
            # Stop listening (and possibly repeating the message) for a day.
            self.event_bus.remove_event_listener(
                "nissan_leaf_soc_skipped", self._check_notification
            )
            self.hass.run_in(self._initialize, relevant_duration)
            self.__log(f"Notified user with message:/n{message}.")

        except Exception as e:
            self.__log(
                f"Problem soc_skipped_warning event. Exception: {e}.",
                level="WARNING",
            )
