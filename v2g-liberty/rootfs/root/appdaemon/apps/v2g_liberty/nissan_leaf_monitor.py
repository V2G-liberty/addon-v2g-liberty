from datetime import datetime, timedelta
import constants as c
import log_wrapper
from pyee.asyncio import AsyncIOEventEmitter
from appdaemon.plugins.hass.hassapi import Hass


class NissanLeafMonitor(AsyncIOEventEmitter):
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

    evse_client_app: object = None
    hass: Hass = None
    previous_soc_value = None

    def __init__(self, hass: Hass):
        self.hass = hass
        self.__log = log_wrapper.get_class_method_logger(hass.log)
        self.__log("Completed __init__ NissanLeafMonitor")

    async def initialize(self):
        self.evse_client_app.add_listener("soc_change", self._handle_soc_change)
        self.__log("Initializing NissanLeafMonitor")

    async def _handle_soc_change(self, new_soc: int, old_soc: int):
        """Handle changes in the car's state of charge (SoC).
        Assumption:
        soc values are numbers from 1 to 100, but can be "unknow" or None
        """

        self.__log(f"_handle_soc_change: new_soc '{new_soc}', old_soc '{old_soc}'.")

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
            self.__log("TA-TU-TA-TU! Skipped Min-SoC!")

        # message = f"Toggling behaviour because of Nissan Leaf skipped {c.CAR_MIN_SOC_IN_PERCENT}%, "
        #     f"leading to toggling charging. A possible solution is to change the "
        #     f"'schedule lower limit' to a non multiple of 10 (eg. 19 instead of 20, "
        #     f"29 instead of 30)."
        # try:
        #     self.emit("soc_skipped_warning", message=message)
        #     await self.wait_for_complete()
        # except Exception as e:
        #     self.__log(f"Problem soc_skipped_warning event. Exception: {e}.")
