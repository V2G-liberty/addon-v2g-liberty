from datetime import datetime, timedelta
import isodate
from typing import AsyncGenerator, List, Optional
from itertools import accumulate
import re
import math
import asyncio
from v2g_globals import time_round
import constants as c
from v2g_globals import V2GLibertyGlobals

import appdaemon.plugins.hass.hassapi as hass

class V2Gliberty(hass.Hass):
    """ This class manages the bi-directional charging proces.
    For this it communicates with:
    + The EVSE client, that communicates with the EV
    + The fm_client, that communicates with the FlexMeasures platform (which delivers the charging schedules).
    + Retrieves the calendar items

    This class is the primarily responsible module for providing information to the UI. The EVSE client 
    does keep EVSE data up to date for presentation in the UI.
    """

    # CONSTANTS
    # TODO: Move to EVSE client? 
    DISCONNECTED_STATE: int = 0
    # Fail-safe for processing schedules that might have schedule with too high update frequency
    MIN_RESOLUTION: timedelta
    CAR_AVERAGE_WH_PER_KM: int
    ADMIN_MOBILE_NAME: str
    HA_NAME: str = ""

    CAR_RESERVATION_CALENDAR: str

    # Utility variables for preventing a frozen app. Call set_next_action at least every x seconds
    timer_handle_set_next_action: object
    call_next_action_at_least_every: int
    scheduling_timer_handles: List[AsyncGenerator]

    # A SoC of 0 means: unknown/car not connected.
    # Keep local variables so it is not needed to get it every time from evse client
    # or HA entity. They are updated via "process_soc", which is triggered via event listener.
    connected_car_soc: int
    connected_car_soc_kwh: float

    # This is a target datetime at which the SoC that is above the max_soc must return back to or below this value.
    # It is dependent on the user setting for allowed duration above max soc.
    back_to_max_soc: datetime

    in_boost_to_reach_min_soc: bool

    # To keep track of duration of charger in error state.
    charger_in_error_since: datetime
    # initially charger_in_error_since is set to this date reference.
    # If charger_in_error_since is not equal to this date we know timeing has started.
    date_reference: datetime

    # For handling no_schedule_errors
    no_schedule_errors: dict
    notification_timer_handle: object
    user_was_notified_of_no_schedule: bool

    # For notifying users
    PRIORITY_NOTIFICATION_CONFIG: list
    recipients: list

    evse_client: object
    fm_client: object

    async def initialize(self):
        self.log("Initializing V2Gliberty")

        self.MIN_RESOLUTION = timedelta(minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES)
        self.CAR_AVERAGE_WH_PER_KM = int(float(self.args["car_average_wh_per_km"]))

        self.CAR_RESERVATION_CALENDAR = self.args["car_reservation_calendar"]

        self.HA_NAME = await self.get_state("zone.home", attribute="friendly_name")
        self.log(f"Name of HA instance: '{self.HA_NAME}'.")

        # If this variable is None it means the current SoC is below the max-soc.
        self.back_to_max_soc = None

        # Show the settings in the UI
        #TODO: Use set_textvalue instead??
        self.set_value("input_text.v2g_liberty_version", c.V2G_LIBERTY_VERSION)
        self.set_value("input_text.optimisation_mode", c.OPTIMISATION_MODE)
        self.set_value("input_text.utility_display_name", c.UTILITY_CONTEXT_DISPLAY_NAME)

        self.ADMIN_MOBILE_NAME = self.args["admin_mobile_name"].lower()
        self.PRIORITY_NOTIFICATION_CONFIG = {}
        self.recipients = []
        self.__init_notification_configuration()

        self.in_boost_to_reach_min_soc = False
        self.call_next_action_at_least_every = 15 * 60
        self.timer_handle_set_next_action = ""
        self.connected_car_soc = 0
        self.connected_car_soc_kwh = 0

        # For checking how long the charger has been in error
        self.date_reference = datetime(2000, 1, 1)
        self.charger_in_error_since = self.date_reference

        # For handling no_schedule errors
        self.no_schedule_errors = {
            "invalid_schedule": False,
            "timeouts_on_schedule": False,
            "no_communication_with_fm": False
        }
        # Reset at init
        self.turn_off("input_boolean.charger_modbus_communication_fault")

        self.notification_timer_handle = None
        self.no_schedule_notification_is_planned = False

        self.evse_client = await self.get_app("modbus_evse_client")
        self.fm_client = await self.get_app("flexmeasures-client")

        self.listen_state(self.__update_charge_mode, "input_select.charge_mode", attribute="all")
        self.listen_event(self.__disconnect_charger, "DISCONNECT_CHARGER")

        self.listen_state(self.__handle_charger_state_change, "sensor.charger_charger_state", attribute="all")
        self.listen_state(self.__handle_soc_change, "sensor.charger_connected_car_state_of_charge", attribute="all")
        self.listen_state(self.__process_schedule, "input_text.chargeschedule", attribute="all")

        self.scheduling_timer_handles = []

        # Set to initial 'empty' values, makes rendering of graph faster.
        await self.__set_soc_prognosis_boost_in_ui()
        await self.__set_soc_prognosis_in_ui()

        ####### V2G Liberty init complete ################

        await self.evse_client.complete_init()

        charge_mode = await self.get_state("input_select.charge_mode")
        if charge_mode != "Stop":
            self.log("Setting EVSE client to active!")
            await self.evse_client.set_active()

        current_soc = await self.get_state("sensor.charger_connected_car_state_of_charge")
        await self.__process_soc(current_soc)
        await self.__set_next_action(v2g_args="initialise") # on initializing the app

        self.log("Completed Initializing V2Gliberty")


    ######################################################################
    #                         PUBLIC FUNCTIONS                           #
    ######################################################################

    async def handle_no_new_schedule(self, error_name: str, error_state: bool):
        """ Keep track of situations where no new schedules are available:
            - invalid schedule
            - timeouts on schedule
            - no communication with FM
            They can occur simultaneously/overlapping, so they are accumulated in
            the dictionary self.no_schedule_errors.
            To be called from fm_client.
        """

        if error_name in self.no_schedule_errors:
            self.log(f"handle_no_valid_schedule called with {error_name}: {error_state}.")
        else:
            self.log(f"handle_no_valid_schedule called unknown error_name: '{error_name}'.")
            return
        self.no_schedule_errors[error_name] = error_state
        await self.__notify_no_new_schedule()


    async def notify_user_of_charger_needs_restart(self):
        """Notify admin with critical message of a presumably crashed modbus server 
           module in the charger.
           To be called from modbus evse client
        """
        # Assume the charger has crashed.
        self.log(f"The charger probably crashed, notifying user")
        title = "Modbus communication error"
        message = "Automatic charging has been stopped. Please click this notification to open the V2G Liberty App and follow the steps to solve this problem."
        self.__notify_user(
            message     = message,
            title       = title,
            tag         = "critical_error",
            critical    = False,
            send_to_all = False
        )
        # TODO: 
        # Just changed the critical to False as i expect this to occur less frequent.
        # It seems self-fixing now. This should be handled so that the notification is
        # removed and functions are restored if communication is restored.
         
        await self.set_state("input_boolean.charger_modbus_communication_fault", state="on")
        self.__set_chargemode_in_ui("Stop")
        return


    ######################################################################
    #                   INITILIZATION RELATED FUNCTIONS                  #
    ######################################################################

    def __init_notification_configuration(self):
        # List of all the recipients to notify
        # Check if Admin is configured correctly
        # Warn user about bad config with persistent notification in UI.
        self.log("Initializing notification configuration")

        self.recipients.clear()
        # Service "mobile_app_" seems more reliable than using get_trackers,
        # as these names do not always match with the service.
        for service in self.list_services():
            if service["service"].startswith("mobile_app_"):
                self.recipients.append(service["service"].replace("mobile_app_", ""))
        self.log(f"Recipients for notifications: {self.recipients}.")

        message = ""
        if len(self.recipients) == 0:
            message = f"No mobile devices (e.g. phone, tablet, etc.) have been registered in Home Assistant " \
                      f"for notifications.<br/>" \
                      f"It is highly recommended to do so. Please install the HA companion app on your mobile device " \
                      f"and connect it to Home Assistant."
            self.log(f"Configuration error: {message}.")
        elif self.ADMIN_MOBILE_NAME not in self.recipients:
            alternative_admin = self.recipients[0]
            message = f"The admin mobile name ***{self.ADMIN_MOBILE_NAME}*** in configuration is not a registered " \
                      f"recipient.<br/>" \
                      f"Please use one of the following: {self.recipients}.<br/>" \
                      f"Now, ***{alternative_admin}*** will be used for high-priority/technical notifications with " \
                      f"the assumption it is a iOS device."
            self.log(f"Configuration error: The admin_mobile_name '{self.ADMIN_MOBILE_NAME}' in configuration not a "
                     f"registered recipient.")
            self.ADMIN_MOBILE_NAME = self.recipients[0]
        else:
            # Only check platform config if admin mobile name is valid.
            platform = self.args["admin_mobile_platform"].lower()
            if platform == "ios":
                self.PRIORITY_NOTIFICATION_CONFIG = {
                    "push": {"sound": {"critical": 1, "name": "default", "volume": 0.9}}}
            elif platform == "android":
                self.PRIORITY_NOTIFICATION_CONFIG = {"ttl": 0, "priority": "high"}
            else:
                message = f"The admin_mobile_platform in configuration: '{platform}' is unknown."
                self.log(f"Configuration error: {message}")

        if message != "":
            # TODO: Research if showing this only to admin users is possible.
            self.call_service('persistent_notification/create', title="Configuration error", message=message,
                              notification_id="notification_config_error")

        self.log("Completed Initializing notification configuration")


    ######################################################################
    #                    PRIVATE CALLBACK FUNCTIONS                      #
    ######################################################################

    async def __update_charge_mode(self, entity, attribute, old, new, kwargs):
        """Function to handle updates in the charge mode"""
        new_state = new["state"]
        old_state = old.get("state")
        self.log(f"Charge mode has changed from '{old_state}' to '{new_state}'")

        if old_state == 'Max boost now' and new_state == 'Automatic':
            # When mode goes from "Max boost now" to "Automatic" charging needs to be stopped.
            # Let schedule (later) decide if starting is needed
            await self.evse_client.stop_charging()

        if old_state != 'Stop' and new_state == 'Stop':
            # New mode "Stop" is handled by set_next_action
            self.log("Stop charging (if in action) and give control based on chargemode = Stop")
            # Cancel previous scheduling timers
            await self.__cancel_charging_timers()
            self.in_boost_to_reach_min_soc = False
            await self.evse_client.set_inactive()

        if old_state == 'Stop' and new_state != 'Stop':
            await self.evse_client.set_active()

        await self.__set_next_action(v2g_args="__update_charge_mode")
        return


    async def __handle_soc_change(self, entity, attribute, old, new, kwargs):
        """Function to handle updates in the car SoC"""
        reported_soc = new["state"]
        self.log(f"__handle_soc_change called with raw SoC: {reported_soc}")
        res = await self.__process_soc(reported_soc)
        if not res:
            return
        await self.__set_next_action(v2g_args="__handle_soc_change")
        return


    async def __disconnect_charger(self, *args, **kwargs):
        """ Function te disconnect the charger.
        Reacts to button in UI that fires DISCONNECT_CHARGER event.
        """
        self.log("************* Disconnect charger requested *************")
        await self.__reset_no_new_schedule()
        await self.evse_client.stop_charging()
        # Control is not given to user, this is only relevant if charge_mode is "Off" (stop).
        self.__notify_user(
            message     = "Charger is disconnected",
            title       = None,
            tag         = "charger_disconnected",
            critical    = False,
            send_to_all = True,
            ttl         = 5 * 60
        )
        return


    async def __handle_charger_state_change(self, entity, attribute, old, new, kwargs):
        """ A callback function for handling any changes in the charger state.
        Has a sister function in evse module to handle stuff there"""
        if new is None:
            return
        new_charger_state = int(float(new["state"]))

        # Initialise to be always different then current if not in state
        old_charger_state = -1

        if old is not None:
            old_charger_state = int(float(old["state"]))

        if old_charger_state == new_charger_state:
            return

        # **** Handle disconnect:
        # Goes to this status when the plug is removed from the socket (not when disconnect is requested from the UI)
        if new_charger_state == self.DISCONNECTED_STATE:
            # Reset any possible target for discharge due to SoC > max-soc
            self.back_to_max_soc = None

            # Cancel current scheduling timers
            await self.__cancel_charging_timers()

            # Setting charge_mode set to automatic (was Max boost Now) as car is disconnected.
            charge_mode = await self.get_state("input_select.charge_mode", None)
            if charge_mode == "Max boost now":
                self.__set_chargemode_in_ui("Automatic")
                self.__notify_user(
                    message     = "Charge mode set from 'Max charge now' to 'Automatic' as car is disconnected.",
                    title       = None,
                    tag         = "charge_mode_change",
                    critical    = False,
                    send_to_all = True,
                    ttl         = 15*60
                )
            return

        # **** Handle connected:
        if new_charger_state != self.DISCONNECTED_STATE:
            await self.__set_next_action(v2g_args="handle_charger_state_change")
            return

        # Handling errors is left to the evse_client as this knows what specific situations there are for 
        # the charger. If the charger needs a restart the evse_client calls notify_user_of_charger_needs_restart 
        return


    ######################################################################
    #               PRIVATE GENERAL NOTIFICATION FUNCTIONS               #
    ######################################################################

    def __notify_user(self,
                    message: str,
                    title: Optional[str] = None,
                    tag: Optional[str] = None,
                    critical: bool = False,
                    send_to_all: bool = False,
                    ttl: Optional[int] = 0
                    ):
        """ Utility function to send notifications to the user
            - critical    : send with high priority to Admin only. Always delivered and sound is play. Use with caution.
            - send_to_all : send to all users (can't be combined with critical), default = only send to Admin.
            - tag         : id that can be used to replace or clear a previous message
            - ttl         : time to live in seconds, after that the message will be cleared. 0 = do not clear.
                            A tag is required.

            We assume there always is an ADMIN and there might be several other users that need to be notified.
            When a new call to this function with the same tag is made, the previous message will be overwritten
            if it still exists.
        """

        self.log(f"Notifying user..")

        if title:
            # Use abbreviation to make more room for title itself.
            title = "V2G-L: " + title
        else:
            title = "V2G Liberty"

        # All notifications always get sent to admin
        to_notify = [self.ADMIN_MOBILE_NAME]
        notification_data = {}

        # critical trumps send_to_all
        if critical:
            notification_data = self.PRIORITY_NOTIFICATION_CONFIG

        if send_to_all and not critical:
            to_notify = self.recipients

        if tag:
            notification_data["tag"] = tag
        
        message = message + " [" + self.HA_NAME + "]"

        self.log(f"Notifying recipients: {to_notify} with message: '{message[0:15]}...' data: {notification_data}.")
        for recipient in to_notify:
            service = "notify/mobile_app_" + recipient
            try:
                if notification_data:
                    self.call_service(service, title=title, message=message, data=notification_data)
                else:
                    self.call_service(service, title=title, message=message)
            except:
                self.log(f"Could not notify: exception on {recipient}.")

            if ttl > 0 and tag and not critical:
                # Remove the notification after a time-to-live
                # A tag is required for clearing.
                # Critical notifications should not auto clear.
                self.run_in(self.__clear_notification, ttl, recipient=recipient, tag=tag)

    def __clear_notification_for_all_recipients(self, tag: str):
        for recipient in self.recipients:
            identification = { "recipient": recipient, "tag": tag }
            self.__clear_notification(identification)

    def __clear_notification(self, identification: dict):
        self.log(f"Clearing notification. Data: {identification}")
        recipient = identification["recipient"]
        if recipient == "" or recipient is None:
            self.log(f"Cannot clear notification, recipient is empty '{recipient}'.")
            return
        tag = identification["tag"]
        if tag == "" or tag is None:
            self.log(f"Cannot clear notification, tag is empty '{tag}'.")
            return

        # Clear the notification
        try:
            self.call_service(
                "notify/mobile_app_" + recipient,
                message="clear_notification",
                data={"tag": tag}
            )
        except:
            self.log(f"Could not clear notification: exception on {recipient}.")


    ######################################################################
    #                PRIVATE FUNCTIONS FOR NO-NEW-SCHEDULE               #
    ######################################################################

    async def __reset_no_new_schedule(self):
        """ Sets all errors to False and removes notification / UI messages

        To be used when the car gets disconnected, so that while it stays in this state there is no
        unneeded "alarming" message/notification.
        Also, when the car returns with an SoC below the minimum no new schedule is retrieved and
        in that case the message / notification would remain without a need.
        """

        for error_name in self.no_schedule_errors:
            self.no_schedule_errors[error_name] = False
        await self.__notify_no_new_schedule(reset = True)


    async def __cancel_timer(self, timer):
        """Utility function to silently cancel timers.
        Born because the "silent" flag in cancel_timer does not work and the 
        logs get flooded with un_useful warnings.

        Args:
            timers (list): list of timer_handles to cancel
        """
        if self.info_timer(timer):
            silent = True #Does not really work
            await self.cancel_timer(timer, silent)

    async def __notify_no_new_schedule(self, reset: Optional[bool] = False):
        """ Check if notification of user about no new schedule available is needed,
            based on self.no_schedule_errors. The administration for the errors is done by
            handle_no_new_schedule().

            When error_state = True of any of the errors:
                Set immediately in UI
                Notify once if remains for an hour
            When error state = False:
                If all errors are solved:
                    Remove from UI immediately
                    If notification has been sent:
                        Notify user the situation has been restored.

            Parameters
            ----------
            reset : bool, optional
                    Reset is meant for the situation where the car gets disconnected and all
                    notifications can be cancelled and messages in UI removed.
                    Then also no "problems are solved" notification is sent.

        """

        if reset:
            if self.info_timer(self.notification_timer_handle):
                res = await self.cancel_timer(self.notification_timer_handle)
                self.log(f"__notify_no_new_schedule, notification timer cancelled: {res}.")
            self.no_schedule_notification_is_planned = False
            self.__clear_notification_for_all_recipients(tag = "no_new_schedule")
            self.set_state("input_boolean.error_no_new_schedule_available", state="off")
            return

        any_errors = False
        for error_name in self.no_schedule_errors:
            if self.no_schedule_errors[error_name]:
                any_errors = True
                break

        if any_errors:
            self.set_state("input_boolean.error_no_new_schedule_available", state="on")
            if not self.no_schedule_notification_is_planned:
                # Plan a notification in case the error situation remains for more than an hour
                self.notification_timer_handle = self.run_in(self.no_new_schedule_notification, 60 * 60)
                self.no_schedule_notification_is_planned = True
        else:
            self.set_state("input_boolean.error_no_new_schedule_available", state="off")
            canceled_before_run = await self.cancel_timer(self.notification_timer_handle, True)
            self.log(f"__notify_no_new_schedule, notification timer cancelled before run: {canceled_before_run}.")
            if self.no_schedule_notification_is_planned and not canceled_before_run:
                # Only send this message if "no_schedule_notification" was actually sent
                title = "Schedules available again"
                message = f"The problems with schedules have been solved. " \
                          f"If you've set charging via the chargers app, " \
                          f"consider to end that and use automatic charging again."
                self.__notify_user(
                    message     = message,
                    title       = title,
                    tag         = "no_new_schedule",
                    critical    = False,
                    send_to_all = True,
                    ttl         = 30 * 60
                )
            self.no_schedule_notification_is_planned = False

    def no_new_schedule_notification(self):
        # Work-around to have this in a separate function (without arguments) and not inline in handle_no_new_schedule
        # This is needed because self.run_in() with kwargs does not really work well and results in this app crashing
        title = "No new schedules available"
        message = f"The current schedule will remain active." \
                  f"Usually this problem is solved automatically in an hour or so." \
                  f"If the schedule does not fit your needs, consider charging manually via the chargers app."
        self.__notify_user(
            message     = message,
            title       = title,
            tag         = "no_new_schedule",
            critical    = False,
            send_to_all = True
        )
        self.log("Notification 'No new schedules' sent.")


    ######################################################################
    # PRIVATE FUNCTIONS FOR COMPOSING, GETTING AND PROCESSING SCHEDULES  #
    ######################################################################

    async def __ask_for_new_schedule(self):
        """
        This function is meant to be called upon:
        - SOC updates
        - charger state updates
        - every 15 minutes if none of the above
        """
        self.log("Ask for a new schedule...")

        # Check whether we're in automatic mode
        charge_mode = await self.get_state("input_select.charge_mode")
        if charge_mode != "Automatic":
            self.log(f"Not getting new schedule. Charge mode is not 'Automatic' but '{charge_mode}'.")
            return

        # Check whether we're not in boost mode
        if self.in_boost_to_reach_min_soc:
            self.log(f"Not getting new schedule. SoC below minimum, boosting to reach that first.")
            return

        # AJO 2023-03-31:
        # ToDo: Would it be more efficient to determine the target every 15/30/60? minutes instead of at every schedule
        # Set default target_soc to 100% one week from now
        now = await self.get_now()
        resolution = timedelta(minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES)
        target_datetime = (time_round(now, resolution) + timedelta(days=7))
        # By default, we assume no calendar item so no relaxation window is needed
        target_soc_kwh = c.CAR_MAX_CAPACITY_IN_KWH

        # Check if calendar has a relevant item that is within one week (*) from now.
        # (*) 7 days is the setting in v2g_liberty_package.yaml
        # If so try to retrieve target_soc
        car_reservation = await self.get_state(self.CAR_RESERVATION_CALENDAR, attribute="all")
        # This should get the first item from the calendar. If no item is found (i.e. items are too far into the future)
        # it returns a general entity that does not contain a start_time, message or description.

        if car_reservation is None:
            self.log("No calendar item found, no calendar configured?")
        else:
            self.log(f"Calender: {car_reservation}")
            calendar_item_start = car_reservation["attributes"].get("start_time", None)
            if calendar_item_start is not None:
                # Prepare for date parsing
                calendar_item_start = calendar_item_start.replace(" ", "T")
                TZ = isodate.parse_tzinfo(self.get_timezone())
                calendar_item_start = isodate.parse_datetime(calendar_item_start).astimezone(TZ)
                # self.log(f"calendar_item_start: {calendar_item_start}.")
                if calendar_item_start < target_datetime:
                    # There is a relevant calendar item with a start date less than a week in the future.
                    # Set the calendar_item_start as the target for the schedule
                    target_datetime = time_round(calendar_item_start, resolution)

                    # Now try to retrieve target_soc.
                    # Depending on the type of calendar the description or message contains the possible target_soc.
                    m = car_reservation["attributes"]["message"]
                    d = car_reservation["attributes"]["description"]
                    # Prevent concatenation of possible None values
                    text_to_search_in = " ".join(filter(None, [m, d]))
                    # First try searching for a number in kWh
                    found_target_soc_in_kwh = search_for_soc_target("kWh", text_to_search_in)
                    if found_target_soc_in_kwh is not None:
                        self.log(f"Target SoC from calendar: {found_target_soc_in_kwh} kWh.")
                        target_soc_kwh = found_target_soc_in_kwh
                    else:
                        # No kWh number found, try searching for a number in %
                        found_target_soc_in_percentage = search_for_soc_target("%", text_to_search_in)
                        if found_target_soc_in_percentage is not None:
                            self.log(f"Target SoC from calendar: {found_target_soc_in_percentage} %.")
                            target_soc_kwh = round(float(found_target_soc_in_percentage) / 100 * c.CAR_MAX_CAPACITY_IN_KWH, 2)
                    # ToDo: Add possibility to set target in km

                    # Prevent target_soc above max_capacity
                    if target_soc_kwh > c.CAR_MAX_CAPACITY_IN_KWH:
                        self.log(f"Target SoC from calendar too high: {target_soc_kwh}, "
                                 f"adjusted to {c.CAR_MAX_CAPACITY_IN_KWH}kWh.")
                        target_soc_kwh = c.CAR_MAX_CAPACITY_IN_KWH
                    elif target_soc_kwh < c.CAR_MIN_SOC_IN_KWH:
                        self.log(f"Target SoC from calendar too low: {target_soc_kwh}, "
                                 f"adjusted to {c.CAR_MIN_SOC_IN_KWH}kWh.")
                        target_soc_kwh = c.CAR_MIN_SOC_IN_KWH

        self.log(f"Calling get_new_schedule with "
                 f"current_soc_kwh: {self.connected_car_soc_kwh}kWh ({type(self.connected_car_soc_kwh)}, "
                 f"target_datetime: {target_datetime}kWh ({type(target_datetime)}, "
                 f"target_soc_kwh: {target_soc_kwh}kWh ({type(target_soc_kwh)}, "
                 f"back_to_max:  {self.back_to_max_soc} ({type(self.back_to_max_soc)}.")
        await self.fm_client.get_new_schedule(
            target_soc_kwh = target_soc_kwh,
            target_datetime = target_datetime,
            current_soc_kwh = self.connected_car_soc_kwh,
            back_to_max_soc = self.back_to_max_soc
        )

        return


    async def __cancel_charging_timers(self):
        # self.log(f"cancel_charging_timers - scheduling_timer_handles length: {len(self.scheduling_timer_handles)}.")
        for h in self.scheduling_timer_handles:
            await self.__cancel_timer(h)
        self.scheduling_timer_handles = []
        # Also remove any visible schedule from the graph in the UI..
        await self.__set_soc_prognosis_in_ui(None)
        # self.log(f"Canceled all charging timers.")


    async def __reset_charging_timers(self, handles):
        self.log(f"__reset_charging_timers: cancel current and set {len(handles)} new charging timers.")
        # We need to be sure no now timers are added unless the old are removed
        await self.__cancel_charging_timers()
        self.scheduling_timer_handles = handles
        # self.log("finished __reset_charging_timers")


    async def __process_schedule(self, entity, attribute, old, new, kwargs):
        """Process a schedule by setting timers to start charging the EVSE.

        If appropriate, also starts a charge directly.
        Finally, the expected SoC (given the schedule) is calculated and saved to input_text.soc_prognosis.
        """
        self.log("__process_schedule called, triggered by change in input_text.chargeschedule.")

        if not await self.evse_client.is_car_connected():
            self.log("Stopped processing schedule; car is not connected")
            return

        self.log("Retrieving schedule from input_text.chargeschedule...")
        schedule = new["attributes"]
        values = schedule["values"]
        duration = isodate.parse_duration(schedule["duration"])
        resolution = duration / len(values)
        start = isodate.parse_datetime(schedule["start"])

        # Check against expected control signal resolution
        if resolution < self.MIN_RESOLUTION:
            self.log(f"Stopped processing schedule; the resolution ({resolution}) is below "
                     f"the set minimum ({self.MIN_RESOLUTION}).")
            await self.handle_no_new_schedule("invalid_schedule", True)
            return

        # Detect invalid schedules
        # If a fallback schedule is sent assume that the schedule is invalid if all values (usually 0) are the same
        is_fallback = (schedule["scheduler_info"]["scheduler"] == "StorageFallbackScheduler")
        if is_fallback and (all(val == values[0] for val in values)):
            self.log(f"Invalid fallback schedule, all values are the same: {values[0]}. Stopped processing.")
            await self.handle_no_new_schedule("invalid_schedule", True)
            # Skip processing this schedule to keep the previous
            return
        else:
            await self.handle_no_new_schedule("invalid_schedule", False)


        # Create new scheduling timers, to send a control signal for each value
        handles = []
        now = await self.get_now()
        timer_datetimes = [start + i * resolution for i in range(len(values))]
        MW_TO_W_FACTOR = 1000000 # convert from MegaWatt from schedule to Watt for charger
        for t, value in zip(timer_datetimes, values):
            if t > now:
                # AJO 17-10-2021
                # ToDo: If value is the same as previous, combine them so we have less timers and switching moments?
                h = await self.run_at(self.evse_client.start_charge_with_power, t, charge_power=value * MW_TO_W_FACTOR)  
                # self.log(f"Timer info: {self.info_timer(h)}, handle type: {type(h)}.")
                handles.append(h)
            else:
                # self.log(f"Cannot time a charging scheduling in the past, specifically, at {t}."
                #          f" Setting it immediately instead.")
                await self.evse_client.start_charge_with_power(kwargs=dict(charge_power=value * MW_TO_W_FACTOR))

        # This also cancels previous timers
        await self.__reset_charging_timers(handles)
        # self.log(f"{len(handles)} charging timers set.")

        exp_soc_values = list(accumulate([self.connected_car_soc] + convert_MW_to_percentage_points(values,
                                                                                 resolution,
                                                                                 c.CAR_MAX_CAPACITY_IN_KWH,
                                                                                 c.CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY)))
        exp_soc_datetimes = [start + i * resolution for i in range(len(exp_soc_values))]
        expected_soc_based_on_scheduled_charges = [dict(time=t.isoformat(), soc=round(v, 2)) for v, t in
                                                   zip(exp_soc_values, exp_soc_datetimes)]
        await self.__set_soc_prognosis_in_ui(expected_soc_based_on_scheduled_charges)

    async def __set_soc_prognosis_in_ui(self, records: Optional[dict] = None):
        """Write or remove SoC prognosis in graph via HA entity input_text.soc_prognosis

            If records = None the SoC line will be removed from the graph,
            e.g. when the car gets disconnected and the SoC prognosis is not relevant (anymore)

            Parameters:
                records(Optional[dict] = None): a dictionary of time (isoformat) + SoC (%) records

            Returns:
                Nothing
        """
        now = await self.get_now()
        if records is None:
            # There seems to be no way to hide the SoC series from the graph,
            # so it is filled with "empty" data, one record of 0.
            # Set it at a week from now, so it's not visible in the default view.
            records = [dict(time=(now + timedelta(days=7)).isoformat(), soc=0.0)]

        # To make sure the new attributes are treated as new we set a new state as well
        new_state = "SoC prognosis based on schedule available at " + now.isoformat()
        result = dict(records=records)
        self.set_state("input_text.soc_prognosis", state=new_state, attributes=result)

    async def __set_soc_prognosis_boost_in_ui(self, records: Optional[dict] = None):
        """Write or remove SoC prognosis boost in graph via HA entity input_text.soc_prognosis_boost
            Boost is in action when SoC is below minimum.
            The only difference with normal SoC prognosis is the line color.
            We do not use APEX chart color_threshold feature on SoC prognosis as
            it is experimental and the min_soc is a setting and can change.

            If records = None the SoC boost line will be removed from the graph,
            e.g. when the car gets disconnected and the SoC prognosis boost is not relevant (anymore)

            Parameters:
                records(Optional[dict] = None): a dictionary of time (isoformat) + SoC (%) records

            Returns:
                Nothing
        """
        now = await self.get_now()
        if records is None:
            # There seems to be no way to hide the SoC series from the graph,
            # so it is filled with "empty" data, one record of 0.
            # Set it at a week from now, so it's not visible in the default view.
            records = [dict(time=(now + timedelta(days=7)).isoformat(), soc=0.0)]

        # To make sure the new attributes are treated as new we set a new state as well
        new_state = "SoC prognosis boost based on boost 'schedule' available at " + now.isoformat()
        result = dict(records=records)
        self.set_state("input_text.soc_prognosis_boost", state=new_state, attributes=result)


    async def __start_max_charge_now(self):
        #just to be shure..
        await self.evse_client.set_active()
        await self.evse_client.start_charge_with_power(kwargs=dict(charge_power=c.CHARGER_MAX_CHARGE_POWER))


    async def __process_soc(self, reported_soc: str) -> bool:
        """Process the reported SoC by saving it to self.connected_car_soc (realistic values only).

        :param reported_soc: string representation of the SoC (in %) as reported by the charger (e.g. "42" denotes 42%)
        :returns: True if a realistic numeric SoC was reported, False otherwise.
        """
        try:
            reported_soc = float(reported_soc)
            assert 0 < reported_soc <= 100
        except (TypeError, AssertionError, ValueError):
            self.log(f"New SoC '{reported_soc}' ignored.")
            return False
        self.connected_car_soc = round(reported_soc, 0)
        self.connected_car_soc_kwh = round(reported_soc * float(c.CAR_MAX_CAPACITY_IN_KWH / 100), 2)
        remaining_range = int(round((self.connected_car_soc_kwh*1000/self.CAR_AVERAGE_WH_PER_KM), 0))
        self.set_value("input_number.car_remaining_range", remaining_range)
        self.log(f"New SoC processed, self.connected_car_soc is now set to: {self.connected_car_soc}%.")
        self.log(f"New SoC processed, self.connected_car_soc_kwh is now set to: {self.connected_car_soc_kwh}kWh.")
        self.log(f"New SoC processed, car_remaining_range is now set to: {remaining_range} km.")

        # Notify user of reaching CAR_MAX_SOC_IN_PERCENT (default 80%) charge while charging (not dis-charging).
        # ToDo: Discuss with users if this is useful.
        if self.connected_car_soc == c.CAR_MAX_SOC_IN_PERCENT and await self.evse_client.is_charging():
            message = f"Car battery at {self.connected_car_soc} %, range ≈ {remaining_range} km."
            self.__notify_user(
                message     = message,
                title       = None,
                tag         = "battery_max_soc_reached",
                critical    = False,
                send_to_all = True,
                ttl         = 60*15
            )
        return True


    async def __set_next_action(self, v2g_args = None):
        """The function determines what action should be taken next based on current SoC, Charge_mode, Charger_state

        This function is meant to be called upon:
        - Initialisation
        - SoC updates
        - Charger state updates
        - Charge mode updates
        - Every 15 minutes if none of the above
        """
        # Only for debugging:
        if v2g_args is not None:
            source = v2g_args
        else:
            source = "unknown"
        self.log(f"Set next action called from source: {source}.")

        # Make sure this function gets called every x minutes to prevent a "frozen" app.
        if self.timer_handle_set_next_action:
            await self.__cancel_timer(self.timer_handle_set_next_action)

        self.timer_handle_set_next_action = await self.run_in(
            self.__set_next_action,
            self.call_next_action_at_least_every,
        )

        if not await self.evse_client.is_car_connected():
            self.log("No car connected or error, stopped setting next action.")
            return

        if self.evse_client.try_get_new_soc_in_process:
            self.log("__set_next_action: evse_client.try_get_new_soc_in_process, stopped setting next action.")
            return
        
        if self.connected_car_soc == 0:
            self.log("SoC is 0, stopped setting next action.")
            # Maybe (but it is dangerous) do try_get_soc??
            return

        # If the SoC of the car is higher than the max-soc (intended for battery protection)
        # a target is to return to the max-soc within the ALLOWED_DURATION_ABOVE_MAX_SOC
        if (self.back_to_max_soc is None) and (self.connected_car_soc_kwh > c.CAR_MAX_SOC_IN_KWH):
            now = await self.get_now()
            self.back_to_max_soc = time_round((now + timedelta(hours=c.ALLOWED_DURATION_ABOVE_MAX_SOC)), self.MIN_RESOLUTION)
            self.log(f"SoC above max-soc, aiming to schedule with target {c.CAR_MAX_SOC_IN_PERCENT}% at {self.back_to_max_soc}.")
        elif (self.back_to_max_soc is not None) and self.connected_car_soc_kwh <= c.CAR_MAX_SOC_IN_KWH:
            self.back_to_max_soc = None
            self.log(f"SoC was below max-soc, has been restored.")

        charge_mode = await self.get_state("input_select.charge_mode", attribute="state")
        self.log(f"Setting next action based on charge_mode '{charge_mode}'.")

        if charge_mode == "Automatic":
            # This should be handled by update_charge_mode
            # self.set_charger_control("take")
            
            if self.connected_car_soc < c.CAR_MIN_SOC_IN_PERCENT and not self.in_boost_to_reach_min_soc:
                # Intended for the situation where the car returns from a trip with a low battery.
                # An SoC below the minimum SoC is considered "unhealthy" for the battery,
                # this is why the battery should be charged to this minimum asap.
                # Cancel previous scheduling timers as they might have discharging instructions as well
                self.log(f"set_next_action, soc: {self.connected_car_soc}")
                await self.__cancel_charging_timers()
                await self.__start_max_charge_now()
                self.in_boost_to_reach_min_soc = True

                # Create a minimal schedule to show in graph that gives user an estimation of when the min. SoC will
                # be reached. The schedule starts now with current SoC
                now = await self.get_now()
                boost_schedule = [dict(time=(now).isoformat(), soc=self.connected_car_soc)]

                # How much energy (wh) is needed, taking roundtrip efficiency into account
                # For % /100, for kwh to wh * 1000 results in *10..
                delta_to_min_soc_wh = (c.CAR_MIN_SOC_IN_PERCENT - self.connected_car_soc) * c.CAR_MAX_CAPACITY_IN_KWH * 10
                delta_to_min_soc_wh = delta_to_min_soc_wh / (c.CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY ** 0.5)

                # How long will it take to charge this amount with max power, we use ceil to avoid 0 minutes as
                # this would not show in graph.
                minutes_to_reach_min_soc = int(math.ceil((delta_to_min_soc_wh / c.CHARGER_MAX_CHARGE_POWER * 60)))
                expected_min_soc_time = (now + timedelta(minutes=minutes_to_reach_min_soc)).isoformat()
                boost_schedule.append(dict(time=expected_min_soc_time, soc=c.CAR_MIN_SOC_IN_PERCENT))
                await self.__set_soc_prognosis_boost_in_ui(boost_schedule)

                message = f"Car battery state of charge ({self.connected_car_soc}%) is too low. " \
                          f"Charging with maximum power until minimum of ({c.CAR_MIN_SOC_IN_PERCENT}%) is reached. " \
                          f"This is expected around {expected_min_soc_time}."
                self.__notify_user(
                    message     = message,
                    title       = "Car battery is too low",
                    tag         = "battery_too_low",
                    critical    = False,
                    send_to_all = True,
                    ttl         = minutes_to_reach_min_soc * 60
                )
                return

            if self.connected_car_soc > c.CAR_MIN_SOC_IN_PERCENT and self.in_boost_to_reach_min_soc:
                self.log(f"Stopping max charge now, SoC above minimum ({c.CAR_MIN_SOC_IN_PERCENT}%) again.")
                self.in_boost_to_reach_min_soc = False
                await self.evse_client.start_charge_with_power(kwargs=dict(charge_power=0))
                # Remove "boost schedule" from graph.
                await self.__set_soc_prognosis_boost_in_ui(None)
            elif self.connected_car_soc <= (c.CAR_MIN_SOC_IN_PERCENT + 1) and await self.evse_client.is_discharging():
                # Failsafe, this should not happen...
                self.log(f"Stopped discharging as SoC has reached minimum ({c.CAR_MIN_SOC_IN_PERCENT}%).")
                await self.evse_client.start_charge_with_power(kwargs=dict(charge_power=0))

            # Not checking for > max charge (97%) because we could also want to discharge based on schedule

            # Check for discharging below minimum done in the function for setting the (dis)charge_current.
            await self.__ask_for_new_schedule()

        elif charge_mode == "Max boost now":
            #self.set_charger_control("take")
            # If charger_state = "not connected", the UI shows an (error) message.

            if self.connected_car_soc >= 100:
                self.log(f"Reset charge_mode to 'Automatic' because max_charge is reached.")
                # ToDo: Maybe do this after 20 minutes or so..
                self.__set_chargemode_in_ui("Automatic")
            else:
                self.log("Starting max charge now based on charge_mode = Max boost now")
                await self.__start_max_charge_now()

        elif charge_mode == "Stop":
            # Stopping charger and giving control is also done in the callback function update_charge_mode
            pass

        else:
            raise ValueError(f"Unknown option for set_next_action: {charge_mode}")

        return

    def __set_chargemode_in_ui(self, setting: str):
        """ This function sets the charge mode in the UI to setting.
        By setting the UI switch an event will also be fired. So other code will run due to this setting.

        Parameters:
        setting (str): Automatic, MaxBoostNow or Stop (=Off))

        Returns:
        nothing.
        """

        res = False
        if setting == "Automatic":
            # Used when car gets disconnected and ChargeMode was MaxBoostNow.
            res = self.turn_on("input_boolean.chargemodeautomatic")
        elif setting == "MaxBoostNow":
            # Not used for now, just here for completeness.
            # The situation with SoC below the set minimum is handled without setting the UI to MaxBoostNow
            res = self.turn_on("input_boolean.chargemodemaxboostnow")
        elif setting == "Stop":
            # Used when charger crashes to stop further processing
            res = self.turn_on("input_boolean.chargemodeoff")
        else:
            self.log(f"In valid charge_mode in UI setting: '{setting}'.")
            return

        if not res is True:
            self.log(f"Failed to set charge_mode in UI to '{setting}'. Home Assistant responded with: {res}")
        else:
            self.log(f"Successfully set charge_mode in UI to '{setting}'.")


######################################################################
#                    PRIVATE UTILITY FUNCTIONS                       #
######################################################################

def search_for_soc_target(search_unit: str, string_to_search_in: str) -> int:
    """Search description for the first occurrence of some (integer) number of the search_unit.

    Parameters:
        search_unit (int): The unit to search for, typically % or kWh, found directly following the number
        string_to_search_in (str): The string in which the soc in searched
    Returns:
        integer number or None if nothing is found

    Forgives errors in incorrect capitalization of the unit and missing/double spaces.
    """
    if string_to_search_in is None:
        return None
    string_to_search_in = string_to_search_in.lower()
    pattern = re.compile(rf"(?P<quantity>\d+) *{search_unit.lower()}")
    match = pattern.search(string_to_search_in)
    if match:
        return int(float(match.group("quantity")))

    return None


def convert_MW_to_percentage_points(
        values_in_MW,
        resolution: timedelta,
        max_soc_in_kWh: float,
        round_trip_efficiency: float,
):
    """
    For example, if a 62 kWh battery produces at 0.00575 MW for a period of 15 minutes,
    its SoC increases by just over 2.3%.
    """
    e = round_trip_efficiency ** 0.5
    scalar = resolution / timedelta(hours=1) * 1000 * 100 / max_soc_in_kWh
    lst = []
    for v in values_in_MW:
        if v >= 0:
            lst.append(v * scalar * e)
        else:
            lst.append(v * scalar / e)
    return lst
