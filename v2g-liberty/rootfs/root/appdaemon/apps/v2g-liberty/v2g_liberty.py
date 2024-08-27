from datetime import datetime, timedelta
import isodate
from typing import AsyncGenerator, List, Optional
from itertools import accumulate
import math
import asyncio
from v2g_globals import time_round, get_local_now
from v2g_globals import V2GLibertyGlobals
import constants as c
# from reservations_client import ReservationsClient

import appdaemon.plugins.hass.hassapi as hass


class V2Gliberty(hass.Hass):
    """ This class manages the bi-directional charging process.
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
    HA_NAME: str = ""

    # Wait time before notifying the user(s) if the car is still connected during a calendar event
    MAX_EVENT_WAIT_TO_DISCONNECT: timedelta
    timer_id_event_wait_to_disconnect: str = ""


    # Utility variables for preventing a frozen app. Call set_next_action at least every x seconds
    timer_handle_set_next_action: object = None
    call_next_action_at_least_every: int = 15 * 60
    scheduling_timer_handles: List[AsyncGenerator]

    # A SoC of 0 means: unknown/car not connected.
    # Keep local variables, so it is not needed to get it every time from evse client
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
    # If charger_in_error_since is not equal to this date we know timing has started.
    date_reference: datetime

    # For handling no_schedule_errors
    no_schedule_errors: dict
    notification_timer_handle: object
    user_was_notified_of_no_schedule: bool
    no_schedule_notification_is_planned: bool

    evse_client: object = None
    fm_client: object = None
    reservations_client: object = None

    async def initialize(self):
        self.log("Initializing V2Gliberty")

        self.MIN_RESOLUTION = timedelta(minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES)
        self.HA_NAME = await self.get_state("zone.home", attribute="friendly_name")
        self.log(f"Name of HA instance: '{self.HA_NAME}'.")

        # If this variable is None it means the current SoC is below the max-soc.
        self.back_to_max_soc = None

        self.MAX_EVENT_WAIT_TO_DISCONNECT = timedelta(minutes=7)

        self.in_boost_to_reach_min_soc = False
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
        self.fm_client = await self.get_app("fm_client")
        self.reservations_client = await self.get_app("reservations-client")

        self.listen_state(self.__update_charge_mode, "input_select.charge_mode", attribute="all")
        self.listen_event(self.__disconnect_charger, "DISCONNECT_CHARGER")
        self.listen_event(self.__handle_phone_action, event="mobile_app_notification_action")

        self.listen_state(self.__handle_charger_state_change, "sensor.charger_charger_state", attribute="all")
        self.listen_state(self.__handle_soc_change, "sensor.charger_connected_car_state_of_charge", attribute="all")
        self.listen_state(self.__process_schedule, "input_text.chargeschedule", attribute="all")

        self.scheduling_timer_handles = []

        # Set to initial 'empty' values, makes rendering of graph faster.
        await self.__set_soc_prognosis_boost_in_ui()
        await self.__set_soc_prognosis_in_ui()

        ####### V2G Liberty init complete ################
        if self.evse_client is not None:
            await self.evse_client.complete_init()
        else:
            self.log(f"initialize. Could not call evse_client.complete_init. evse_client is None, not init yet?")

        charge_mode = await self.get_state("input_select.charge_mode")
        if self.evse_client is not None:
            if charge_mode == "Stop":
                self.log("initialize. Charge_mode == 'Stop' -> Setting EVSE client to in_active!")
                await self.evse_client.set_inactive()
            else:
                self.log("initialize.  Charge_mode != 'Stop' -> Setting EVSE client to active!")
                await self.evse_client.set_active()
        else:
            self.log(f"initialize. Could not call set_(in)active on evse_client as it is None, not init yet?")

        current_soc = await self.get_state("sensor.charger_connected_car_state_of_charge")
        await self.__process_soc(current_soc)

        await self.initialise_v2g_liberty(v2g_args="initialise")

        self.log("Completed Initializing V2Gliberty")

    ######################################################################
    #                         PUBLIC FUNCTIONS                           #
    ######################################################################

    async def initialise_v2g_liberty(self, v2g_args=None):
        # Show the settings in the UI
        self.set_textvalue("input_text.optimisation_mode", c.OPTIMISATION_MODE)
        self.set_textvalue("input_text.utility_display_name", c.UTILITY_CONTEXT_DISPLAY_NAME)
        await self.set_next_action(v2g_args=v2g_args)  # on initializing the app


    def notify_user(self,
                      message: str,
                      title: Optional[str] = None,
                      tag: Optional[str] = None,
                      critical: bool = False,
                      send_to_all: bool = False,
                      ttl: Optional[int] = 0,
                      actions: list = None,
                    ):
        """ Utility function to send notifications to the user
            - critical    : send with high priority to Admin only. Always delivered and sound is play. Use with caution.
            - send_to_all : send to all users (can't be combined with critical), default = only send to Admin.
            - tag         : id that can be used to replace or clear a previous message
            - ttl         : time to live in seconds, after that the message will be cleared. 0 = do not clear.
                            A tag is required.
            - actions     : A list of dicts with action and title stings

            We assume there always is an ADMIN and there might be several other users that need to be notified.
            When a new call to this function with the same tag is made, the previous message will be overwritten
            if it still exists.
        """
        if c.ADMIN_MOBILE_NAME == "":
            self.log("notify_user: No registered devices to notify, cancel notification.")
            return

        # All notifications always get sent to admin
        to_notify = [c.ADMIN_MOBILE_NAME]

        # Use abbreviation to make more room for title itself.
        title = "V2G-L: " + title if title else "V2G Liberty"

        notification_data = {}

        # critical trumps send_to_all
        if critical:
            self.log(f"notify_user: Critical! Send to: {to_notify}.")
            notification_data = c.PRIORITY_NOTIFICATION_CONFIG

        if send_to_all and not critical:
            self.log(f"notify_user: Send to all and not critical! Send to: {to_notify}.")
            to_notify = c.NOTIFICATION_RECIPIENTS

        if tag:
            notification_data["tag"] = tag

        if actions:
            notification_data['actions'] = actions

        message = message + " [" + self.HA_NAME + "]"

        self.log(f"Notifying recipients: {to_notify} with message: '{message[0:15]}...' data: {notification_data}.")
        for recipient in to_notify:
            service = "notify/mobile_app_" + recipient
            try:
                if notification_data:
                    self.call_service(service, title=title, message=message, data=notification_data)
                else:
                    self.call_service(service, title=title, message=message)
            except Exception as e:
                self.log(f"notify_user. Could not notify: exception on {recipient}. Exception: {e}.")

            if ttl > 0 and tag and not critical:
                # Remove the notification after a time-to-live.
                # A tag is required for clearing.
                # Critical notifications should not auto clear.
                self.run_in(self.__clear_notification, delay=ttl, recipient=recipient, tag=tag)

    def clear_notification(self, tag: str):
        """Wrapper methode for easy clearing of notifications"""
        self.__clear_notification_for_all_recipients(tag = tag)

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

    async def notify_user_of_charger_needs_restart(self, was_car_connected: bool):
        """Notify admin with critical message of a presumably crashed modbus server
           module in the charger.
           To be called from modbus_evse_client module.
        """
        # Assume the charger has crashed.
        self.log(f"The charger probably crashed, notifying user")
        title = "Charger communication error"
        message = "Automatic charging has been stopped!\n" \
                  "Please click this notification to open the V2G Liberty App " \
                  "and follow the steps to solve this problem."
        # Do not send a critical warning if car was not connected.
        critical = was_car_connected
        self.notify_user(
            message=message,
            title=title,
            tag="charger_modbus_crashed",
            critical=critical,
            send_to_all=False
        )
        await self.set_state("input_boolean.charger_modbus_communication_fault", state="on")
        await self.__set_chargemode_in_ui("Stop")
        return

    async def reset_charger_communication_fault(self):
        self.log(f"reset_charger_communication_fault called")
        await self.set_state("input_boolean.charger_modbus_communication_fault", state="off")
        identification = {"recipient": c.ADMIN_MOBILE_NAME, "tag": "charger_modbus_crashed"}
        self.__clear_notification(identification)

    ######################################################################
    #                    PRIVATE CALLBACK FUNCTIONS                      #
    ######################################################################

    async def __handle_phone_action(self, event_name, data, kwargs):
        self.log(f"__handle_phone_action, called.")
        action_parts = str(data["action"]).split("~")
        action = action_parts[0]
        hid = action_parts[1]
        if action == "dismiss_event":
            dismiss = True
        elif action == "keep_event":
            dismiss = False
        else:
            self.log(f"__handle_phone_action, aborting: unknown action: '{action}'.")
            return

        if self.reservations_client is not None:
            await self.reservations_client.set_event_dismissed_status(event_hash_id=hid, status=dismiss)
        else:
            self.log(f"__handle_phone_action. "
                     f"Could not call set_event_dismissed_status on reservations_client as it is None.")
            return
        self.log(f"__handle_phone_action, completed set hid: {hid} to {dismiss}.")


    async def __update_charge_mode(self, entity, attribute, old, new, kwargs):
        """Function to handle updates in the charge mode"""
        new_state = new["state"]
        old_state = old.get("state")
        self.log(f"Charge mode has changed from '{old_state}' to '{new_state}'")

        if old_state == 'Max boost now' and new_state == 'Automatic':
            # When mode goes from "Max boost now" to "Automatic" charging needs to be stopped.
            # Let schedule (later) decide if starting is needed
            if self.evse_client is not None:
                await self.evse_client.stop_charging()
            else:
                self.log("__update_charge_mode. Could not call stop_charging on evse_client as it is None.")

        if old_state != 'Stop' and new_state == 'Stop':
            # New mode "Stop" is handled by set_next_action
            self.log("Stop charging (if in action) and give control based on chargemode = Stop")
            # Cancel previous scheduling timers
            await self.__cancel_charging_timers()
            self.in_boost_to_reach_min_soc = False
            if self.evse_client is not None:
                await self.evse_client.set_inactive()
            else:
                self.log("__update_charge_mode. Could not call set_inactive on evse_client as it is None.")

        if old_state == 'Stop' and new_state != 'Stop':
            if self.evse_client is not None:
                await self.evse_client.set_active()
            else:
                self.log("__update_charge_mode. Could not call set_active on evse_client as it is None.")

        await self.set_next_action(v2g_args="__update_charge_mode")
        return

    async def __handle_soc_change(self, entity, attribute, old, new, kwargs):
        """Function to handle updates in the car SoC"""
        reported_soc = new["state"]
        self.log(f"__handle_soc_change called with raw SoC: {reported_soc}")
        res = await self.__process_soc(reported_soc)
        if not res:
            return
        await self.set_next_action(v2g_args="__handle_soc_change")
        return

    async def __disconnect_charger(self, *args, **kwargs):
        """ Function te disconnect the charger.
        Reacts to button in UI that fires DISCONNECT_CHARGER event.
        """
        self.log("************* Disconnect charger requested *************")
        await self.__reset_no_new_schedule()

        if self.evse_client is not None:
            await self.evse_client.stop_charging()
            message="Charger is disconnected"
        else:
            message="Charger cloud not be disconnected, please check the app."
            self.log("__disconnect_charger. Could not call stop_charging on evse_client as it is None.")

        # Control is not given to user, this is only relevant if charge_mode is "Off" (stop).
        self.notify_user(
            message=message,
            title=None,
            tag="charger_disconnected",
            critical=False,
            send_to_all=True,
            ttl=5 * 60
        )


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
                await self.__set_chargemode_in_ui("Automatic")
                self.notify_user(
                    message="Charge mode set from 'Max charge now' to 'Automatic' as car is disconnected.",
                    title=None,
                    tag="charge_mode_change",
                    critical=False,
                    send_to_all=True,
                    ttl=15 * 60
                )
            return

        # **** Handle connected:
        if new_charger_state != self.DISCONNECTED_STATE:
            await self.set_next_action(v2g_args="handle_charger_state_change")
            return

        # Handling errors is left to the evse_client as this knows what specific situations there are for
        # the charger. If the charger needs a restart the evse_client calls notify_user_of_charger_needs_restart
        return

    ######################################################################
    #               PRIVATE GENERAL NOTIFICATION FUNCTIONS               #
    ######################################################################

    def __clear_notification_for_all_recipients(self, tag: str):
        for recipient in c.NOTIFICATION_RECIPIENTS:
            identification = {"recipient": recipient, "tag": tag}
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
        except Exception as e:
            self.log(f"__clear_notification. Could not clear notification: exception on {recipient}. Exception: {e}.")


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
        await self.__notify_no_new_schedule(reset=True)


    async def __cancel_timer(self, timer_id: str):
        """Utility function to silently cancel a timer.
        Born because the "silent" flag in cancel_timer does not work and the
        logs get flooded with useless warnings.

        Args:
            timer_id: timer_handle to cancel
        """
        if self.info_timer(timer_id):
            silent = True  # Does not really work
            await self.cancel_timer(timer_id, silent)


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
            self.__clear_notification_for_all_recipients(tag="no_new_schedule")
            await self.set_state("input_boolean.error_no_new_schedule_available", state="off")
            return

        any_errors = False
        for error_name in self.no_schedule_errors:
            if self.no_schedule_errors[error_name]:
                any_errors = True
                break

        if any_errors:
            await self.set_state("input_boolean.error_no_new_schedule_available", state="on")
            await self.set_value("input_text.fm_connection_status", "Failed to connect/login.")
            if not self.no_schedule_notification_is_planned:
                # Plan a notification in case the error situation remains for more than an hour
                self.notification_timer_handle = await self.run_in(self.no_new_schedule_notification, delay=60 * 60)
                self.no_schedule_notification_is_planned = True
        else:
            await self.set_state("input_boolean.error_no_new_schedule_available", state="off")
            canceled_before_run = await self.cancel_timer(self.notification_timer_handle, True)
            self.log(f"__notify_no_new_schedule, notification timer cancelled before run: {canceled_before_run}.")
            if self.no_schedule_notification_is_planned and not canceled_before_run:
                # Only send this message if "no_schedule_notification" was actually sent
                title = "Schedules available again"
                message = f"The problems with schedules have been solved.\n" \
                          f"If you've set charging via the chargers app, " \
                          f"consider to end that and use automatic charging again."
                self.notify_user(
                    message=message,
                    title=title,
                    tag="no_new_schedule",
                    critical=False,
                    send_to_all=True,
                    ttl=30 * 60
                )
            self.no_schedule_notification_is_planned = False

    def no_new_schedule_notification(self, v2g_args=None):
        # Work-around to have this in a separate function (without arguments) and not inline in handle_no_new_schedule
        # This is needed because self.run_in() with kwargs does not really work well and results in this app crashing
        title = "No new schedules available"
        message = f"The current schedule will remain active.\n" \
                  f"Usually this problem is solved automatically in an hour or so.\n" \
                  f"If the schedule does not fit your needs, consider charging manually via the chargers app."
        self.notify_user(
            message=message,
            title=title,
            tag="no_new_schedule",
            critical=False,
            send_to_all=True
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
        self.log("__ask_for_new_schedule called")

        # Check whether we're in automatic mode
        charge_mode = await self.get_state("input_select.charge_mode")
        if charge_mode != "Automatic":
            self.log(f"__ask_for_new_schedule Not getting new schedule. "
                     f"Charge mode is not 'Automatic' but '{charge_mode}'.")
            return

        # Check whether we're not in boost mode.
        if self.in_boost_to_reach_min_soc:
            self.log(f"__ask_for_new_schedule Not getting new schedule. "
                     f"SoC below minimum, boosting to reach that first.")
            return

        # Check if reservations_client has any (relevant) items, if so try to retrieve target_soc
        if self.reservations_client is not None:
            car_reservations = await self.reservations_client.get_v2g_events()
        else:
            self.log(f"__ask_for_new_schedule. Could not call get_v2g_events on reservations_client as it is None.")

        targets = []
        is_first_reservation = True
        if car_reservations is not None and len(car_reservations) > 0:
            # Adding the target one week from now is FM specific, so this is done in the fm_client
            for car_reservation in car_reservations:
                if car_reservation == 'un-initiated':
                    self.log(f"__ask_for_new_schedule, reservation: {car_reservation}."
                             f" The reservations_client module is not initiated yet. Stop processing")
                    # The module reservations_client is not initiated yet. Stop processing
                    continue

                # Do not take dismissed car reservations into account for schedule.
                if car_reservation['dismissed']:
                    continue

                target_start = time_round(car_reservation['start'], self.MIN_RESOLUTION)
                target_end = time_round(car_reservation['end'], self.MIN_RESOLUTION)
                target_soc_kwh = round(float(car_reservation['target_soc_percent'])
                                       / 100 * c.CAR_MAX_CAPACITY_IN_KWH, 2)
                # Check target_soc above max_capacity and below min_soc is done in reservations_client
                target = {
                    'start': target_start,
                    'end': target_end,
                    'target_soc_kwh': target_soc_kwh,
                }
                targets.append(target)

                # Only for the first target check if a dismissal is applicable
                if is_first_reservation and self.timer_id_event_wait_to_disconnect == "":
                    now = get_local_now()
                    self.log(f"__ask_for_new_schedule is_first_reservation == True.")
                    if target_end < now:
                        self.log(f"__ask_for_new_schedule first_reservation event is in the (recent) past, skipping")
                        # Not needed as the timer_id == ""
                        # await self.__cancel_timer(self.timer_id_event_wait_to_disconnect)
                        # self.timer_id_event_wait_to_disconnect = ""
                    else:
                        # Only set the dismissal timer when start of the reservation is near.
                        if (now + self.MAX_EVENT_WAIT_TO_DISCONNECT) > target_start:
                            self.log(f"__ask_for_new_schedule first_reservation almost starting.")
                            # Set a timer for MAX_EVENT_WAIT_TO_DISCONNECT (15) min. after the start of
                            # the reservation to check if car is still connected.
                            # If still connected, ask user via notification if the item can be dismissed.

                            # Not needed as the timer_id == ""
                            # await self.__cancel_timer(self.timer_id_event_wait_to_disconnect)
                            # self.timer_id_event_wait_to_disconnect = ""

                            ask_at = target_start + self.MAX_EVENT_WAIT_TO_DISCONNECT
                            if ask_at < now:
                                # A last minute added event(?) Inform immediately, give some slack for processing time.
                                ask_at = now + timedelta(seconds=5)
                            self.timer_id_event_wait_to_disconnect = await self.run_at(
                                callback=self.__ask_user_dismiss_event_or_not, start=ask_at, v2g_event=car_reservation)
                            self.log(f"__ask_for_new_schedule: __ask_user_dismiss_event_or_not is set "
                                     f"to run at {ask_at.isoformat()}.")
                is_first_reservation = False
            # End for car_reservation loop
        # self.log(f"__ask_for_new_schedule, targets: '{targets}'.")

        # self.log(f"__ask_for_new_schedule calling get_new_schedule on self.fm_client: {self.fm_client}.")
        if self.fm_client is not None:
            await self.fm_client.get_new_schedule(
                targets=targets,
                current_soc_kwh=self.connected_car_soc_kwh,
                back_to_max_soc=self.back_to_max_soc
            )
        else:
            self.log(f"__ask_for_new_schedule. Could not call get_new_schedule on fm_client as it is None.")

        return


    async def __ask_user_dismiss_event_or_not(self, v2g_event: dict):
        self.log(f"__ask_user_dismiss_event_or_not, called with v2g_event: {v2g_event}.")

        is_car_connected = True
        if self.evse_client is not None:
            is_car_connected = await self.evse_client.is_car_connected()
        else:
            self.log(f"__ask_user_dismiss_event_or_not. Could not call is_car_connected on evse_client as it is None.")

        if is_car_connected:
            v2g_event = v2g_event['v2g_event']
            identification = " ".join(filter(None, [v2g_event['summary'], v2g_event['description']]))
            if len(identification) > 25:
                identification = identification[0: 25] + "..."
            self.log(f"__ask_user_dismiss_event_or_not, event_title: {identification}.")
            # Will be cancelled when car gets disconnected.
            hid=v2g_event['hash_id']
            message = f"The car is still connected while it was reserved to leave: '{identification}'.\n" \
                      f"What would you like to do?"
            user_actions = [
                {'action': f'dismiss_event~{hid}', 'title': 'Dismiss reservation'},
                {'action': f'keep_event~{hid}', 'title': 'Keep reservation'}
            ]
            self.notify_user(
                message=message,
                title="Car connected during reservation",
                tag=f"dismiss_event_or_not_{hid}",
                actions=user_actions
            )
        else:
            self.log(f"__ask_user_dismiss_event_or_not, unexpected call for event with hash_id '{v2g_event['hash_id']}' "
                     f"as car is already disconnected.")

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

        if self.evse_client is not None:
            is_car_connected = await self.evse_client.is_car_connected()
        else:
            self.log(f"__process_schedule. Could not call is_car_connected on evse_client as it is None.")
            return

        if not is_car_connected:
            self.log("__process_schedule aborted: car is not connected")
            return

        schedule = new.get("attributes", None)
        if schedule is None:
            self.log("__process_schedule aborted: no schedule found.")
            return

        values = schedule.get("values", None)
        if values is None:
            self.log("__process_schedule aborted: no values found.")
            return

        duration = schedule.get("duration", None)
        if duration is None:
            self.log("__process_schedule aborted: no duration found.")
            return

        start = schedule.get("start", None)
        if start is None:
            self.log("__process_schedule aborted: no start datetime found.")
            return

        duration = isodate.parse_duration(duration)
        resolution = duration / len(values)
        start = isodate.parse_datetime(start)

        # Check against expected control signal resolution
        if resolution < self.MIN_RESOLUTION:
            self.log(f"__process_schedule aborted: the resolution ({resolution}) is below "
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
        now = get_local_now()
        timer_datetimes = [start + i * resolution for i in range(len(values))]
        MW_TO_W_FACTOR = 1000000  # convert from MegaWatt from schedule to Watt for charger
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

        exp_soc_values = list(
            accumulate(
                [self.connected_car_soc] + convert_MW_to_percentage_points(
                    values,
                    resolution,
                    c.CAR_MAX_CAPACITY_IN_KWH,
                    c.ROUNDTRIP_EFFICIENCY_FACTOR
                )
            )
        )

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
        now = get_local_now()
        if records is None:
            # There seems to be no way to hide the SoC series from the graph,
            # so it is filled with "empty" data, one record of 0.
            # Set it at a week from now, so it's not visible in the default view.
            records = [dict(time=(now + timedelta(days=7)).isoformat(), soc=None)]

        # To make sure the new attributes are treated as new we set a new state as well
        new_state = "SoC prognosis based on schedule available at " + now.isoformat()
        result = dict(records=records)
        await self.set_state("input_text.soc_prognosis", state=new_state, attributes=result)

    async def __set_soc_prognosis_boost_in_ui(self, records: Optional[dict] = None):
        """Write or remove SoC prognosis boost in graph via HA entity input_text.soc_prognosis_boost
            Boost is in action when SoC is below minimum.
            The only difference with normal SoC prognosis is the line color.
            We do not use APEX chart color_threshold feature on SoC prognosis as
            it is experimental and the min_soc is a setting and can change.

            If records = None the SoC boost line will be removed from the graph,
            e.g. when the car gets disconnected and the SoC prognosis boost is not relevant (any more)

            Parameters:
                records(Optional[dict] = None): a dictionary of time (isoformat) + SoC (%) records

            Returns:
                Nothing
        """
        now = get_local_now()
        if records is None:
            # There seems to be no way to hide the SoC series from the graph,
            # so it is filled with "empty" data, one record of 0.
            # Set it at a week from now, so it's not visible in the default view.
            records = [dict(time=(now + timedelta(days=7)).isoformat(), soc=None)]

        # To make sure the new attributes are treated as new we set a new state as well
        new_state = "SoC prognosis boost based on boost 'schedule' available at " + now.isoformat()
        result = dict(records=records)
        await self.set_state("input_text.soc_prognosis_boost", state=new_state, attributes=result)

    async def __start_max_charge_now(self):
        if self.evse_client is not None:
            # TODO: Check if .set_active() is really a good idea here?
            #       If the client is not active there might be a good reason for that...
            await self.evse_client.set_active()
            await self.evse_client.start_charge_with_power(kwargs=dict(charge_power=c.CHARGER_MAX_CHARGE_POWER))
        else:
            self.log(f"__start_max_charge_now. Could not call methods on evse_client as it is None.")


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

        # Cleaned_up SoC value for UI
        # TODO: This is no longer needed, use input_number.charger_connected_car_state_of_charge in UI.
        self.set_value("input_number.car_state_of_charge", self.connected_car_soc)

        self.connected_car_soc_kwh = round(reported_soc * float(c.CAR_MAX_CAPACITY_IN_KWH / 100), 2)
        remaining_range = int(round((self.connected_car_soc_kwh * 1000 / c.CAR_CONSUMPTION_WH_PER_KM), 0))
        self.set_value("input_number.car_remaining_range", remaining_range)
        self.log(f"New SoC processed, self.connected_car_soc is now set to: {self.connected_car_soc}%.")
        self.log(f"New SoC processed, self.connected_car_soc_kwh is now set to: {self.connected_car_soc_kwh}kWh.")
        self.log(f"New SoC processed, car_remaining_range is now set to: {remaining_range} km.")

        if self.evse_client is not None:
            is_charging = await self.evse_client.is_charging()
        else:
            self.log(f"__process_soc. Could not call is_charging on evse_client as it is None.")
            return False
        # Notify user of reaching CAR_MAX_SOC_IN_PERCENT (default 80%) charge while charging (not dis-charging).
        if is_charging and self.connected_car_soc == c.CAR_MAX_SOC_IN_PERCENT:
            message = f"Car battery at {self.connected_car_soc} %, range ≈ {remaining_range} km."
            self.notify_user(
                message=message,
                title=None,
                tag="battery_max_soc_reached",
                critical=False,
                send_to_all=True,
                ttl=60 * 15
            )
        return True

    async def set_next_action(self, v2g_args=None):
        """The function determines what action should be taken next based on current SoC, Charge_mode, Charger_state

        This function is meant to be called upon:
        - Initialisation
        - Settings updates (from v2g_globals.py)
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

        if self.evse_client is not None:
            is_car_connected = await self.evse_client.is_car_connected()
        else:
            self.log(f"set_next_action. Can not call is_car_connected on evse_client, is None. Try again in 15s")
            # Retry in 15 seconds
            self.timer_handle_set_next_action = await self.run_in(
                self.set_next_action,
                delay=15,
            )
            return

        self.timer_handle_set_next_action = await self.run_in(
            self.set_next_action,
            delay=self.call_next_action_at_least_every,
        )

        if not is_car_connected:
            self.log("No car connected or error, stopped setting next action.")
            return

        if self.evse_client is not None:
            try_get_new_soc_in_process = self.evse_client.try_get_new_soc_in_process
        else:
            self.log(f"set_next_action. Could not call try_get_new_soc_in_process on evse_client as it is None.")
            return

        if try_get_new_soc_in_process:
            self.log("set_next_action: evse_client.try_get_new_soc_in_process, stopped setting next action.")
            return

        if self.connected_car_soc == 0:
            self.log("SoC is 0, stopped setting next action.")
            # Maybe (but it is dangerous) do try_get_soc??
            return

        # If the SoC of the car is higher than the max-soc (intended for battery protection)
        # a target is to return to the max-soc within the ALLOWED_DURATION_ABOVE_MAX_SOC
        if (self.back_to_max_soc is None) and (self.connected_car_soc_kwh > c.CAR_MAX_SOC_IN_KWH):
            now = get_local_now()
            self.back_to_max_soc = time_round((now + timedelta(hours=c.ALLOWED_DURATION_ABOVE_MAX_SOC)),
                                              self.MIN_RESOLUTION)
            self.log(
                f"SoC above max-soc, aiming to schedule with target {c.CAR_MAX_SOC_IN_PERCENT}% at {self.back_to_max_soc}.")
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
                now = get_local_now()
                boost_schedule = [dict(time=(now).isoformat(), soc=self.connected_car_soc)]

                # How much energy (wh) is needed, taking roundtrip efficiency into account
                # For % /100, for kwh to wh * 1000 results in *10..
                delta_to_min_soc_wh = (c.CAR_MIN_SOC_IN_PERCENT - self.connected_car_soc) * c.CAR_MAX_CAPACITY_IN_KWH * 10
                delta_to_min_soc_wh = delta_to_min_soc_wh / (c.ROUNDTRIP_EFFICIENCY_FACTOR ** 0.5)

                # How long will it take to charge this amount with max power, we use ceil to avoid 0 minutes as
                # this would not show in graph.
                minutes_to_reach_min_soc = int(math.ceil((delta_to_min_soc_wh / c.CHARGER_MAX_CHARGE_POWER * 60)))
                expected_min_soc_time = (now + timedelta(minutes=minutes_to_reach_min_soc)).isoformat()
                boost_schedule.append(dict(time=expected_min_soc_time, soc=c.CAR_MIN_SOC_IN_PERCENT))
                await self.__set_soc_prognosis_boost_in_ui(boost_schedule)

                message = f"Car battery state of charge ({self.connected_car_soc}%) is too low.\n" \
                          f"Charging with maximum power until minimum of ({c.CAR_MIN_SOC_IN_PERCENT}%) is reached.\n" \
                          f"This is expected around {expected_min_soc_time}."
                self.notify_user(
                    message=message,
                    title="Car battery is too low",
                    tag="battery_too_low",
                    critical=False,
                    send_to_all=True,
                    ttl=minutes_to_reach_min_soc * 60
                )
                return

            if self.connected_car_soc > c.CAR_MIN_SOC_IN_PERCENT and self.in_boost_to_reach_min_soc:
                self.log(f"SoC above minimum ({c.CAR_MIN_SOC_IN_PERCENT}%) again while in max_boost.")
                if self.evse_client is not None:
                    await self.evse_client.start_charge_with_power(kwargs=dict(charge_power=0))
                else:
                    self.log(f"set_next_action. Could not call start_charge_with_power to stop max_boost "
                             f"on evse_client as it is None.")
                    return
                self.log(f"Stopping max charge now.")
                self.in_boost_to_reach_min_soc = False
                # Remove "boost schedule" from graph.
                await self.__set_soc_prognosis_boost_in_ui(None)

            if self.connected_car_soc <= (c.CAR_MIN_SOC_IN_PERCENT + 1):
                # Fail-safe, this should not happen...
                # Assume discharging to be safe
                is_discharging = True
                if self.evse_client is not None:
                    is_discharging = await self.evse_client.is_discharging()
                else:
                    self.log(f"set_next_action. Could not call is_discharging on evse_client as it is None.")
                if is_discharging:
                    self.log(f"Discharging while SoC has reached minimum ({c.CAR_MIN_SOC_IN_PERCENT}%).")
                    if self.evse_client is not None:
                        await self.evse_client.start_charge_with_power(kwargs=dict(charge_power=0))
                    else:
                        self.log(f"set_next_action. "
                                 f"Could not call start_charge_with_power on evse_client as it is None.")

            # Not checking for > max charge (97%) because we could also want to discharge based on schedule
            await self.__ask_for_new_schedule()

        elif charge_mode == "Max boost now":
            # self.set_charger_control("take")
            # If charger_state = "not connected", the UI shows an (error) message.

            if self.connected_car_soc >= 100:
                self.log(f"Reset charge_mode to 'Automatic' because max_charge is reached.")
                # TODO: Wait 15 min, than ask user if they want to postpone scheduled charging or not.
                await self.__set_chargemode_in_ui("Automatic")
            else:
                self.log("Starting max charge now based on charge_mode = Max boost now")
                await self.__start_max_charge_now()

        elif charge_mode == "Stop":
            # Stopping charger and giving control is also done in the callback function update_charge_mode
            pass

        else:
            raise ValueError(f"Unknown option for set_next_action: {charge_mode}")

        return

    async def __set_chargemode_in_ui(self, setting: str):
        """ This function sets the charge mode in the UI to setting.
        By setting the UI switch an event will also be fired. So other code will run due to this setting.

        Parameters:
        setting (str): Automatic, MaxBoostNow or Stop (=Off))

        Returns:
        nothing.
        """

        if setting == "Automatic":
            # Used when car gets disconnected and ChargeMode was MaxBoostNow.
            await self.turn_on("input_boolean.chargemodeautomatic")
        elif setting == "MaxBoostNow":
            # Not used for now, just here for completeness.
            # The situation with SoC below the set minimum is handled without setting the UI to MaxBoostNow
            await self.turn_on("input_boolean.chargemodemaxboostnow")
        elif setting == "Stop":
            # Used when charger crashes to stop further processing
            await self.turn_on("input_boolean.chargemodeoff")
        else:
            self.log(f"In valid charge_mode in UI setting: '{setting}'.")
            return


######################################################################
#                    PRIVATE UTILITY FUNCTIONS                       #
######################################################################

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
