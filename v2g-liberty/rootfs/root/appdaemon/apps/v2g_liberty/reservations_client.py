import datetime as dt
import re
import requests
import constants as c
import log_wrapper
from v2g_globals import get_local_now
import caldav
from service_response_app import ServiceResponseApp
from appdaemon.plugins.hass.hassapi import Hass


class ReservationsClient(ServiceResponseApp):
    cal_client: caldav.DAVClient
    principal: object
    car_reservation_calendar: object
    v2g_events: list[dict[str, any]] = []
    # To prevent problems with scheduling, showing in UI and with notifications
    MIN_EVENT_DURATION_IN_MINUTES: int = 30

    # Stores the user reply to a notification "Car still connected during calendar item:
    # keep / dismiss?", it cannot be stored in the remote calendar item.
    # When getting remote calendar items the dismissed status is added from this "local store".
    # Hash_id with True/False
    # Items are removed if v2g_events do not contain and event with this hash_id anymore.
    events_dismissed_statuses: dict = {}

    poll_timer_id: str = ""
    POLLING_INTERVAL_SECONDS: int = 300
    calender_listener_id: str = ""
    v2g_main_app: object = None
    hass: Hass = None

    def __init__(self, hass: Hass):
        self.hass = hass
        self.__log = log_wrapper.get_class_method_logger(hass.log)

        self.principal = None
        self.poll_timer_id = ""
        # TODO: Check if this can really be removed.
        # await self.initialise_calendar()
        self.__log("Completed initialise ReservationsClient")

    ######################################################################
    #                         PUBLIC FUNCTIONS                           #
    ######################################################################

    def test_caldav_connection(self, url, username, password):
        """Test caldav connection and return calendar names.
        Called from settings frontend dialog.

        Args:
            url (str): caldav host URL
            username (str): caldav host username
            password (str): caldav host password

        Returns:
            list: list of calendar names or caldav status
        """
        self.__log(f"{url=}, {username=}, {password=}")
        try:
            with caldav.DAVClient(
                url=url,
                username=username,
                password=password,
            ) as client:
                calendar_names = []
                principal = client.principal()
                if principal is None:
                    self.__log("principal is None", level="WARNING")
                    return "Caldav error"
                else:
                    for calendar in principal.calendars():
                        calendar_names.append(calendar.name)
                self.__log(f"returning {calendar_names=}.")
                return calendar_names

        # TODO: turn the error returns into Exceptions
        except caldav.lib.error.PropfindError:
            self.__log("Wrong URL error", level="WARNING")
            return "Wrong URL or authorisation error"
        except caldav.lib.error.AuthorizationError:
            self.__log("Authorization error", level="WARNING")
            return "Authorization error"
        except requests.exceptions.ConnectionError:
            self.__log("Connection error", level="WARNING")
            return "Connection error"
        except Exception as e:
            self.__log(f"Unknown error: '{e}'.", level="WARNING")
            return "Unknown error"


    async def initialise_calendar(self):
        """Called by globals when:
        + constants have been loaded from config
        + constants have changed from UI.

        Returns:
            string: Connection status
        """

        # Cancel the lister that could be active because the previous setting
        # for c.CAR_CALENDAR_SOURCE was "localIntegration".
        # If source is "remoteCaldav" no listener is used as the calendar gets polled.
        # If source is "localIntegration" the current listener should be removed to make
        # place for the new one.
        if self.calender_listener_id != "":
            await self.hass.cancel_listen_state(self.calender_listener_id)
            self.calender_listener_id = ""

        # To force a "change" even if the local/remote calendar is empty.
        # If changing from one calendar source type to another this needs to be cleared.
        self.v2g_events.clear()
        self.v2g_events = [{"state": "un-initiated"}]

        if c.CAR_CALENDAR_SOURCE == "remoteCaldav":
            self.__log("remoteCaldav")

        if c.CAR_CALENDAR_SOURCE == "remoteCaldav":
            self.__log("remoteCaldav")
            # A configuration has been made earlier, so it is expected the calendar can be
            # initialised and activated.
            if (
                c.CALENDAR_ACCOUNT_INIT_URL == ""
                or c.CALENDAR_ACCOUNT_USERNAME == ""
                or c.CALENDAR_ACCOUNT_PASSWORD == ""
            ):
                return "Incomplete caldav configuration"

            self.cal_client = caldav.DAVClient(
                url=c.CALENDAR_ACCOUNT_INIT_URL,
                username=c.CALENDAR_ACCOUNT_USERNAME,
                password=c.CALENDAR_ACCOUNT_PASSWORD,
            )
            try:
                self.principal = self.cal_client.principal()
            except caldav.lib.error.PropfindError:
                self.__log("Wrong URL error")
                return "Wrong URL or authorisation error"
            except caldav.lib.error.AuthorizationError:
                self.__log("Authorization error")
                return "Authorization error"
            except requests.exceptions.ConnectionError:
                self.__log("Connection error")
                return "Connection error"
            except Exception as e:
                self.__log(f"Unknown error: '{e}'.")
                return "Unknown error"

            if self.principal is None:
                self.__log("No calendars found")
                return "No calendars found"

            if c.CAR_CALENDAR_NAME not in [
                None,
                "",
                "unknown",
                "Please choose an option",
            ]:
                # TODO: Here we should not be aware of "unknown", "Please choose an option",
                # fix in globals.
                self.__log(
                    f"selected calendar: {c.CAR_CALENDAR_NAME}, activating calendar"
                )
                await self.activate_selected_calendar()
            else:
                self.__log("No calendar selected")
            await self.__set_caldav_connection_status(connected=True)
            return "Successfully connected"

        elif c.CAR_CALENDAR_SOURCE == "localIntegration":
            self.__log(f"HA Integration, name: '{c.INTEGRATION_CALENDAR_ENTITY_NAME}'.")
            if c.INTEGRATION_CALENDAR_ENTITY_NAME not in [
                None,
                "",
                "unknown",
                "Please choose an option",
            ]:
                # TODO: Here we should not be aware of "unknown", "Please choose an option",
                # fix in globals.
                self.__log("setting listener")
                self.calender_listener_id = await self.hass.listen_state(
                    self.__handle_changed_event,
                    c.INTEGRATION_CALENDAR_ENTITY_NAME,
                    attribute="all",
                )
                # Unfortunately the listener only get called when the first coming (or current)
                # calendar item changes, not when other calendar items change. So we also set a
                # polling timer.
                self.__cancel_timer(self.poll_timer_id)
                self.poll_timer_id = await self.hass.run_every(
                    self.__poll_calendar_integration,
                    "now",
                    self.POLLING_INTERVAL_SECONDS,
                )
                return "Successfully connected"
            self.__log("No calendar integrations found")
            return "No calendar integrations found"
        else:
            self.__log(
                f"Error, unknown calendar_source: '{c.CAR_CALENDAR_SOURCE}'.",
                level="WARNING",
            )

    async def __set_caldav_connection_status(
        self, connected: bool, error_message: str = ""
    ):
        """Helper to set connection status in HA entity"""
        if connected:
            state = "Successfully connected"
        else:
            if error_message != "":
                state = error_message
            else:
                state = "Error"

        # Force a changed trigger even if the state does not change
        keep_alive = {"keep_alive": get_local_now().strftime(c.DATE_TIME_FORMAT)}

        await self.hass.set_state(
            "sensor.calendar_account_connection_status",
            state=state,
            attributes=keep_alive,
        )

    async def activate_selected_calendar(self):
        """Start polling the selected caldav calendar"""
        self.__log("Called")
        if c.CAR_CALENDAR_NAME in [None, "", "unknown", "Please choose an option"]:
            self.__log("c.CAR_CALENDAR_NAME empty, not activating.", level="WARNING")
            # TODO: Check if this ever occurs, it is tested at initialisation already...
            # TODO: Create persistent notification
            return False
        try:
            self.car_reservation_calendar = self.principal.calendar(
                name=c.CAR_CALENDAR_NAME
            )
        except caldav.lib.error.NotFoundError:
            # There is an old calendar name stored which can't be found on (the new?) caldav remote?
            self.__log(f"{c.CAR_CALENDAR_NAME=}, not found on server, not activating.")
            c.CAR_CALENDAR_NAME = ""
            return False

        self.__cancel_timer(self.poll_timer_id)
        self.poll_timer_id = await self.hass.run_every(
            self.__poll_dav_calendar, "now", self.POLLING_INTERVAL_SECONDS
        )
        self.__log(
            f"started polling_time {self.poll_timer_id} "
            f"every {self.POLLING_INTERVAL_SECONDS} sec."
        )
        self.__log("Completed")

    async def set_event_dismissed_status(self, event_hash_id: str, status: bool):
        """To be called from v2g_liberty main module when the user has reacted to the
        question in the notification."""
        if event_hash_id is None or event_hash_id == "":
            self.__log(f"no valid event_hash_id: '{event_hash_id}'.")
            return
        matching_event_found = False
        for event in self.v2g_events:
            if event["hash_id"] == event_hash_id:
                event["dismissed"] = status
                matching_event_found = True
                break
        if matching_event_found:
            self.events_dismissed_statuses[event_hash_id] = status
            self.__log(f"setting hash_id '{event_hash_id}' to {status}.")
        else:
            self.__log(
                f"no matching event found for '{event_hash_id}', changed/removed?"
            )
            return

        if status == True:
            await self.__set_events_in_main_app(v2g_args="dismissed calendar event")

    ######################################################################
    #                   PRIVATE (CALLBACK) FUNCTIONS                     #
    ######################################################################

    async def __handle_changed_event(
        self, entity=None, attribute=None, old=None, new=None, kwargs=None
    ):
        """Mainly here for logging
        It is expected that this method is called when the first upcoming calendar item changes
        """
        self.__log("Called from listener")
        self.__poll_calendar_integration()

    async def __poll_calendar_integration(
        self, entity=None, attribute=None, old=None, new=None, kwargs=None
    ):
        """For the situation where a calendar integration is used (and not a remoteCaldav online
        calendar). It is expected that this method is called when:
         + the previous calendar item has passed (so there is a new first upcoming calendar item)
         + the first upcoming calendar item changes
         + Regularly by polling timer.
        Ideally the listener would trigger for any change in any future calendar item, then polling
        would not be necessary.
        """
        self.__log("Called")

        now = get_local_now()
        start = now.isoformat()
        end = (now + dt.timedelta(days=7)).isoformat()
        local_events = await self.hass.call_service(
            "calendar.get_events",
            entity_id=c.INTEGRATION_CALENDAR_ENTITY_NAME,
            start_date_time=start,
            end_date_time=end,
            return_result=True,
        )
        if local_events is None:
            self.__log("Could not retrieve events, aborting", level="WARNING")
            return
        # Peel off some unneeded layers
        local_events = local_events.get(c.INTEGRATION_CALENDAR_ENTITY_NAME, None)
        local_events = local_events.get("events", None)
        tmp_v2g_events = []

        for local_event in local_events:
            # Create a v2g_liberty specific event based on the caldav event
            start, is_start_midnight = self.__parse_to_tz_dt(local_event["start"])
            end, is_end_midnight = self.__parse_to_tz_dt(local_event["end"])
            if start is None or end is None:
                self.__log(
                    f"{local_event=} misses start- or end-date, ignoring",
                    level="WARNING",
                )
                continue
            is_all_day = is_start_midnight and is_end_midnight
            summary = str(local_event.get("summary", ""))
            description = str(local_event.get("description", ""))
            v2g_event = self.__make_v2g_event(
                start=start,
                end=end,
                is_all_day=is_all_day,
                summary=summary,
                description=description,
            )
            tmp_v2g_events.append(v2g_event)
        await self.__process_v2g_events(tmp_v2g_events)

    async def __poll_dav_calendar(self, kwargs=None):
        # Get the items in from now to the next week from the calendar
        start = get_local_now()
        end = start + dt.timedelta(days=7)
        # It is a bit strange this is not async... for now we'll live with it.
        # TODO: Optimise by use of sync_tokens so that only the updated events get sent
        try:
            caldav_events = self.car_reservation_calendar.search(
                start=start,
                end=end,
                event=True,
                expand=True,
            )
        except Exception as e:
            self.log(
                f"Could not retrieve caldav items. Exception: '{e}'.", level="WARNING"
            )
            await self.__set_caldav_connection_status(connected=False)
            return
        remote_v2g_events = []
        for caldav_event in caldav_events:
            cdi = caldav_event.icalendar_component
            # Create a v2g_liberty specific event based on the caldav event
            start, is_start_midnight = self.__parse_to_tz_dt(cdi["dtstart"].dt)
            end, is_end_midnight = self.__parse_to_tz_dt(cdi["dtend"].dt)
            if start is None or end is None:
                self.__log(
                    f"{caldav_event=} misses start- or end-date, ignoring",
                    level="WARNING",
                )
                continue
            is_all_day = is_start_midnight and is_end_midnight
            summary = str(cdi.get("summary", ""))
            description = str(cdi.get("description", ""))
            v2g_event = self.__make_v2g_event(
                start=start,
                end=end,
                is_all_day=is_all_day,
                summary=summary,
                description=description,
            )
            remote_v2g_events.append(v2g_event)

        await self.__set_caldav_connection_status(connected=True)
        await self.__process_v2g_events(remote_v2g_events)

    def __make_v2g_event(
        self, start: dt, end: dt, is_all_day: bool, summary: str, description: str
    ):
        """
        Make a standard v2g_event, including target soc, hash_id, and dismissed status.
        :param start: start datetime
        :param end: end datetime
        :param is_all_day:
        :param summary:
        :param description:
        :return: v2g_event
        """
        if is_all_day:
            # Practical solution, normally the end is 00:00 the next day.
            # By subtracting a minute the date is today and time becomes 23:59 in the UI.
            end = end + dt.timedelta(minutes=-1)
        elif (end - start).total_seconds() < self.MIN_EVENT_DURATION_IN_MINUTES * 60:
            end = start + dt.timedelta(minutes=self.MIN_EVENT_DURATION_IN_MINUTES)
            description += " Duration extended to minimum of 30 minutes."
            self.__log(
                "Duration of event '{summary}' too short, "
                "extended the end datetime to reach a minimum duration of 30 min."
            )
        v2g_event = {
            "start": start,
            "end": end,
            "summary": summary,
            "description": description,
        }
        # These next 3 actions on the v2g_event must always be in this order
        # The soc should be taken into account for the hash.
        # The dismissed status can only be set if the hash has been added.
        v2g_event = self.__add_target_soc(v2g_event)
        v2g_event = add_hash_id(v2g_event)
        v2g_event = self.__add_dismissed_status(v2g_event)
        return v2g_event

    def __cancel_timer(self, timer_id: str):
        """Utility function to silently cancel a timer.
        Born because the "silent" flag in cancel_timer does not work and the
        logs get flooded with useless warnings.

        Args:
            timer_id: timer_handle to cancel
        """
        if self.hass.timer_running(timer_id):
            silent = True  # Does not really work
            self.hass.cancel_timer(timer_id, silent)

    def __parse_to_tz_dt(self, any_date_type: any):
        """
        Utility method to robustly convert a string into a dt.datetime object
        String may contain only date info, then time will be set to 00:00:00
        :param any_date_type: date or string
        :return: dt.datetime object with the right timezone.
        """

        if isinstance(any_date_type, str):
            if len(any_date_type) <= 10:
                # Assume this is only a date without timezone info.
                # This happens for all_day events in local calendar.
                any_date_type = dt.datetime.strptime(any_date_type, "%Y-%m-%d")
                any_date_type = c.TZ.localize(any_date_type)
            else:
                try:
                    any_date_type = dt.datetime.fromisoformat(any_date_type)
                except Exception as ex:
                    self.__log(
                        f"Exception: {ex} while trying to parse string:"
                        f" {any_date_type}, returning None.",
                        level="WARNING",
                    )
                    return None, None

        elif isinstance(any_date_type, dt.date):
            if isinstance(any_date_type, dt.datetime):
                any_date_type = any_date_type.astimezone(c.TZ)
            else:
                # No time in date, but for localize we need this.
                # Assume 00:00:00 and local timezone
                tm = dt.time(0, 0, 0)
                any_date_type = dt.datetime.combine(any_date_type, tm)
                any_date_type = c.TZ.localize(any_date_type)
        else:
            self.__log(
                f"Could not parse date {any_date_type}, returning None", level="WARNING"
            )
            return None, None

        is_midnight = False
        if (
            any_date_type.hour == 0
            and any_date_type.minute == 0
            and any_date_type.second == 0
        ):
            is_midnight = True
        return any_date_type, is_midnight

    async def __process_v2g_events(self, new_v2g_events):
        # Check if list has changed and if so send these to v2g_main module
        new_v2g_events = sorted(new_v2g_events, key=lambda d: d["start"])

        if self.v2g_events == new_v2g_events:
            # Nothing has changed...
            return False

        self.__log("changed v2g_events")
        self.v2g_events.clear()
        self.v2g_events = new_v2g_events
        await self.__set_events_in_main_app(v2g_args="changed v2g_events")
        self.__clean_up_events_dismissed_statuses()
        return True

    async def __set_events_in_main_app(self, v2g_args: str = ""):
        try:
            await self.v2g_main_app.handle_calendar_change(
                v2g_events=self.v2g_events, v2g_args=v2g_args
            )
        except Exception as e:
            self.__log(
                f"Could not call v2g_main_app.handle_calendar_change. Exception: {e}."
            )

    def __add_target_soc(self, v2g_event: dict) -> dict:
        """Add a target SoC to a v2g_event dict based upon the summary and description.
        Prevent concatenation of possible None values

        Args:
            v2g_event (dict): v2g_event

        Returns:
            dict: v2g_event with target soc
        """
        text_to_search_in = " ".join(
            filter(None, [v2g_event["summary"], v2g_event["description"]])
        )

        # Removed searching for a number in kWh, not used?
        # Try searching for a number in %
        # ToDo: Add possibility to set target in km
        target_soc_percent = search_for_soc_target("%", text_to_search_in)
        if target_soc_percent is None or target_soc_percent > 100:
            # self.__log(f"__add_target_soc: target soc {target_soc_percent} changed to 100%.")
            target_soc_percent = 100
        elif target_soc_percent < c.CAR_MIN_SOC_IN_PERCENT:
            self.__log(
                f"{target_soc_percent=} raised to meet {c.CAR_MIN_SOC_IN_PERCENT=}"
            )
            target_soc_percent = c.CAR_MIN_SOC_IN_PERCENT

        v2g_event["target_soc_percent"] = target_soc_percent
        return v2g_event

    def __add_dismissed_status(self, v2g_event: dict) -> dict:
        # Adds the dismissed status to a v2g_event that has been fetched from remote.
        # For this the 'locally stored' status from self.events_dismissed_statuses is used.
        dismissed = None
        hid = v2g_event["hash_id"]
        for dismissed_event_hash_id in self.events_dismissed_statuses.keys():
            if dismissed_event_hash_id == hid:
                dismissed = self.events_dismissed_statuses[hid]
                break

        v2g_event["dismissed"] = dismissed
        return v2g_event

    def __clean_up_events_dismissed_statuses(self):
        """Check is any of the self.v2g_events is registered as dismissed (in
        self.events_dismissed_statuses).. Remove any hash_id's from self.events_dismissed_statuses
        that are not in self.v2g_events.
        To be called when new calendar items have come in.
        """
        if len(self.events_dismissed_statuses) == 0:
            # Nothing to clean up
            return

        if len(self.v2g_events) == 0:
            self.events_dismissed_statuses.clear()
            return

        # list creates a copy of the events_dismissed_statuses.keys.
        # This makes the pop on the original possible.
        for dismissed_event_hash_id in list(self.events_dismissed_statuses.keys()):
            hash_id_in_v2g_events = False
            for v2g_event in self.v2g_events:
                if dismissed_event_hash_id == v2g_event["hash_id"]:
                    hash_id_in_v2g_events = True
                    break

            if not hash_id_in_v2g_events:
                # The hash_id in self.events_dismissed_statuses is not current anymore.
                self.events_dismissed_statuses.pop(dismissed_event_hash_id)


######################################################################
#                    PRIVATE UTILITY FUNCTIONS                       #
######################################################################


def add_hash_id(v2g_event: dict):
    """Add a hash_id to a v2g_event
    The hash_id is used for keeping track of dismissed events
    Any changes to the event result in a different hash_id, including target_soc_
    """
    hash_id = hash(
        " ".join(
            filter(
                None,
                [
                    v2g_event["start"].isoformat(),
                    v2g_event["end"].isoformat(),
                    v2g_event["summary"],
                    v2g_event["description"],
                    str(v2g_event["target_soc_percent"]),
                ],
            )
        )
    )
    # As it is a key that will be sent in notifications a string is more convenient.
    v2g_event["hash_id"] = str(hash_id)
    return v2g_event


def search_for_soc_target(search_unit: str, string_to_search_in: str) -> int:
    """Search description for the first occurrence of some (integer) number of the search_unit.
    Forgives errors in incorrect capitalization of the unit and missing/double spaces.

    Parameters:
        search_unit (str): The unit to search for, typically %, km or kWh, found directly following
        the number string_to_search_in (str): The string in which the soc in searched.

    Returns:
        integer number or None if nothing is found
    """
    if string_to_search_in is None:
        return None
    string_to_search_in = string_to_search_in.lower()
    pattern = re.compile(rf"(?P<quantity>\d+) *{search_unit.lower()}")
    match = pattern.search(string_to_search_in)
    if match:
        return int(float(match.group("quantity")))

    return None
