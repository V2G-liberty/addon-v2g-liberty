import constants as c
from event_bus import EventBus
from v2g_liberty import V2Gliberty
import log_wrapper
from appdaemon.plugins.hass.hassapi import Hass


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
    v2g_main_app: V2Gliberty = None

    def __init__(self, hass: Hass, event_bus: EventBus):
        self.hass = hass
        self.__log = log_wrapper.get_class_method_logger(hass.log)
        self.event_bus = event_bus
        self._initialize()
        self.__log("Completed __init__ NissanLeafMonitor")

    def _initialize(self):
        self.event_bus.add_event_listener("soc_change", self._handle_soc_change)
        self.__log("Completed initialize")

    def _handle_soc_change(self, new_soc: int, old_soc: int):
        """Handle changes in the car's state of charge (SoC).
        Assumption:
        soc values are numbers from 1 to 100, but can be "unknow" or None
        """

        if not isinstance(new_soc, (int, float)) or not isinstance(
            old_soc, (int, float)
        ):
            self.__log(
                f"Aborting: new_soc '{new_soc}' and/or old_soc '{old_soc}' not an int."
            )
            return

        if (new_soc < c.CAR_MIN_SOC_IN_PERCENT < old_soc) or (
            old_soc < c.CAR_MIN_SOC_IN_PERCENT < new_soc
        ):
            self.__log(
                f"SoC change jump: old_soc '{old_soc}', new_soc '{new_soc}', "
                f"skipped c.CAR_MIN_SOC_IN_PERCENT '{c.CAR_MIN_SOC_IN_PERCENT}'. "
                f"Warning user and not checking for the next 24h.",
                level="WARNING",
            )

            # As the skip-problem is specific to the Nissan Leaf it's message content (incl. a
            # solution) is documented here in this module and not in the general module V2G Liberty.
            message = (
                f"The Nissan Leaf faulted, skipping the state-of-charge "
                f"{c.CAR_MIN_SOC_IN_PERCENT}%. This often leads to toggled charging. "
                f"A possible solution is to change the setting for 'schedule lower limit'"
                f"to 1%-point higher or lower."
            )
            try:
                relevant_duration = 24 * 60 * 60
                self.v2g_main_app.notify_user(
                    message=message, tag="soc_skipped", ttl=relevant_duration
                )
                # Stop listening (and possibly repeating the message) for a day.
                self.event_bus.remove_event_listener(
                    "soc_change", self._handle_soc_change
                )
                self.hass.run_in(self._initialize, relevant_duration)

            except Exception as e:
                self.__log(
                    f"Problem soc_skipped_warning event. Exception: {e}.",
                    level="WARNING",
                )
