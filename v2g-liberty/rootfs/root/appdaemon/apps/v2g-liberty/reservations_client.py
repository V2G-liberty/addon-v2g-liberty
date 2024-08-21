import datetime as dt
import time
import asyncio
import re
import adbase as ad
import requests
import constants as c
from v2g_globals import time_round, he, get_local_now
import caldav
from service_response_app import ServiceResponseApp


class ReservationsClient(ServiceResponseApp):
    cal_client: caldav.DAVClient
    principal: object
    car_reservation_calendar: object
    v2g_events: list = []

    # Stores the user reply to a notification "Car still connected during calendar item: keep / dismiss?"
    # It cannot be stored in the remote calendar item.
    # When getting remote calendar items the dismissed status is added from this "local store".
    # Hash_id with True/False
    # Items are removed if v2g_events do not contain and event with this hash_id anymore.
    events_dismissed_statuses: dict = {}

    poll_timer_id: str = ""
    POLLING_INTERVAL_SECONDS: int = 300
    calender_listener_id: str = ""
    v2g_main_app: object = None

    async def initialize(self):
        self.log("initialise ReservationsClient")
        self.v2g_main_app = await self.get_app("v2g_liberty")
        self.principal = None
        self.poll_timer_id = ""
        # To force a "change" even if the local/remote calendar is empty.
        self.v2g_events = ["un-initiated"]
        await self.initialise_calendar()
        self.log(f"Completed initialise ReservationsClient")


    ######################################################################
    #                         PUBLIC FUNCTIONS                           #
    ######################################################################


    async def set_event_dismissed_status(self, event_hash_id: str, status: bool):
        # To be called from v2g_liberty main module when the user has reacted to the
        # question in the notification.
        if event_hash_id is None or event_hash_id == "":
            self.log(f"set_event_dismissed_status no valid event_hash_id: '{event_hash_id}'.")
            return
        matching_event_found = False
        for event in self.v2g_events:
            if event['hash_id'] == event_hash_id:
                event['dismissed'] = status
                matching_event_found = True
                break
        if matching_event_found:
            self.events_dismissed_statuses[event_hash_id] = status
            self.log(f"set_event_dismissed_status setting hash_id '{event_hash_id}' to {status}.")
        else:
            self.log(f"set_event_dismissed_status no matching event found for '{event_hash_id}', changed/removed?")
            return

        if status == True:
            await self.__process_calendar_change(v2g_args="dismissed calendar event")

    async def get_v2g_events(self):
        return self.v2g_events


    async def initialise_calendar(self):
        # Called by globals when:
        # + constants have been loaded from config
        # + constants have changed from UI.
        # self.log(f"initialise_calendar called")

        # Cancel the lister that could be active because the previous setting
        # for c.CAR_CALENDAR_SOURCE was "Home Assistant Integration".
        # If source is "Direct caldav source" no listener is used as the calendar gets polled.
        # If source is "Home Assistant Integration" the current listener should be removed to make place for the new one.
        if self.calender_listener_id != "":
            await self.cancel_listen_state(self.calender_listener_id)
            self.calender_listener_id = ""

        if c.CAR_CALENDAR_SOURCE == "Direct caldav source":
            self.log("initialise_calendar called - Direct caldav source")

            # A configuration has been made earlier, so it is expected the calendar can be
            # initialised and activated.
            if c.CALENDAR_ACCOUNT_INIT_URL == "" or c.CALENDAR_ACCOUNT_USERNAME == "" or c.CALENDAR_ACCOUNT_PASSWORD == "":
                return "Incomplete caldav configuration"

            self.cal_client = caldav.DAVClient(
                url=c.CALENDAR_ACCOUNT_INIT_URL,
                username=c.CALENDAR_ACCOUNT_USERNAME,
                password=c.CALENDAR_ACCOUNT_PASSWORD
            )
            try:
                self.principal = self.cal_client.principal()
            except caldav.lib.error.PropfindError:
                self.log(f"initialise_calendar: Wrong URL error")
                return "Wrong URL or authorisation error"
            except caldav.lib.error.AuthorizationError:
                self.log(f"initialise_calendar: Authorization error")
                return "Authorization error"
            except requests.exceptions.ConnectionError:
                self.log(f"initialise_calendar: Connection error")
                return "Connection error"
            except Exception as e:
                self.log(f"initialise_calendar: Unknown error: '{e}'.")
                return "Unknown error"

            if self.principal is None:
                self.log(f"initialise_calendar: No calendars found")
                return "No calendars found"

            if c.CAR_CALENDAR_NAME is not None and \
                    c.CAR_CALENDAR_NAME not in ["", "unknown", "Please choose an option"]:
                # TODO: Here we should not be aware of "unknown", "Please choose an option", fix in globals.
                self.log(f"initialise_calendar, selected calendar: {c.CAR_CALENDAR_NAME}, activating calendar")
                await self.activate_selected_calendar()
            else:
                self.log(f"initialise_calendar: No calendar selected")
                # TODO: Would be nice if it could say "Please choose a calendar"

            return "Successfully connected"

        else:
            self.log(f"initialise_calendar called - HA Integration, name: '{c.INTEGRATION_CALENDAR_ENTITY_NAME}'.")
            if c.INTEGRATION_CALENDAR_ENTITY_NAME is not None and \
                    c.INTEGRATION_CALENDAR_ENTITY_NAME not in ["", "unknown", "Please choose an option"]:
                # TODO: Here we should not be aware of "unknown", "Please choose an option", fix in globals.
                self.log(f"initialise_calendar, setting listener")
                self.calender_listener_id = await self.listen_state(
                    self.__handle_calendar_integration_change,
                    c.INTEGRATION_CALENDAR_ENTITY_NAME,
                    attribute="all"
                )
                await self.__handle_calendar_integration_change()
                return "Successfully connected"
            else:
                self.log(f"initialise_calendar, No calendar integrations found")
                return "No calendar integrations found"


    async def get_dav_calendar_names(self):
        # For situation where c.CAR_CALENDAR_SOURCE == "Direct caldav source"
        # Called by globals to populate the input_select after a connection to
        # dav the calendar has been established.
        self.log("get_dav_calendar_names called")
        cal_names = []
        if self.principal is None:
            self.log(f"get_dav_calendar_names principle is none")
            return cal_names
        for calendar in self.principal.calendars():
            cal_names.append(calendar.name)
        self.log(f"get_dav_calendar_names, returning calendars: {cal_names}")
        return cal_names


    async def get_ha_calendar_names(self):
        # For situation where c.CAR_CALENDAR_SOURCE == "HA Integration"
        # Called by globals to populate the input_select at init and when calendar source changes
        self.log("get_ha_calendar_names called")
        cal_names = []
        calendar_states = await self.get_state("calendar")
        # self.log(f"get_ha_calendar_names calendar states: {calendar_states}")
        for calendar in calendar_states:
            cal_names.append(calendar)
        self.log(f"get_ha_calendar_names, returning calendars: {cal_names}")
        return cal_names


    async def activate_selected_calendar(self):
        # Only used for "Direct caldav source"
        self.log("activate_selected_calendar called")
        if c.CAR_CALENDAR_NAME is None or c.CAR_CALENDAR_NAME in ["", "unknown", "Please choose an option"]:
            self.log(f"activate_selected_calendar empty, not activating.")
            # TODO: Check if this ever occurs, it is tested at initialisation already...
            # TODO: Create persistent notification
            return False
        try:
            self.car_reservation_calendar = self.principal.calendar(name=c.CAR_CALENDAR_NAME)
        except caldav.lib.error.NotFoundError:
            # There is an old calendar name stored which cannot be found on (the new?) caldav remote?
            self.log(f"activate_selected_calendar c.CAR_CALENDAR_NAME {c.CAR_CALENDAR_NAME}, "
                     f"not found on server, not activating.")
            c.CAR_CALENDAR_NAME = ""
            return False

        if self.poll_timer_id != "" and self.timer_running(self.poll_timer_id):
            # Making sure there won't be parallel polling treads.
            self.log(f"activate_selected_calendar, there seems to be a poll-timer already: "
                     f"{self.poll_timer_id}: cancelling.")
            await self.cancel_timer(self.poll_timer_id)
            self.poll_timer_id = ""

        self.poll_timer_id = await self.run_every(self.__poll_calendar, "now", self.POLLING_INTERVAL_SECONDS)
        # self.log(f"activate_selected_calendar started polling_time {self.poll_timer_id} "
        #          f"every {self.POLLING_INTERVAL_SECONDS} sec.")
        self.log("Completed activate_selected_calendar")


    ######################################################################
    #                   PRIVATE (CALLBACK) FUNCTIONS                     #
    ######################################################################

    async def __handle_calendar_integration_change(self, entity=None, attribute=None, old=None, new=None, kwargs=None):
        """For the situation where a calendar integration is used (and not a direct caldav online calendar).
           It is expected that this listener is only called when:
            + the previous event has passed
            + any upcoming event gets changed
        """
        self.log(f"__handle_calendar_integration_change called.")

        now = get_local_now()
        start = now.isoformat()
        end = (now + dt.timedelta(days=7)).isoformat()
        local_events = await self.call_service(
            "calendar.get_events",
            entity_id = c.INTEGRATION_CALENDAR_ENTITY_NAME,
            start_date_time=start,
            end_date_time=end,
            return_result = True
        )
        # Peel off some unneeded layers
        local_events = local_events.get(c.INTEGRATION_CALENDAR_ENTITY_NAME, None)
        local_events = local_events.get('events', None)
        tmp_v2g_events = []

        for local_event in local_events:
            # Create a v2g_liberty specific event based on the caldav event
            start, is_all_day = self.__parse_to_tz_dt(local_event['start'])
            end, is_all_day = self.__parse_to_tz_dt(local_event['end'])

            if start is None or end is None:
                continue

            if is_all_day:
                # This way the end date becomes 23:59 in the UI.
                end = end + dt.timedelta(minutes=-1)

            v2g_event = {
                'start': start,
                'end': end,
                'summary': str(local_event.get('summary', "")),
                'description': str(local_event.get('description', "")),
            }
            v2g_event = await self.__post_process_v2g_event(v2g_event)
            tmp_v2g_events.append(v2g_event)

        tmp_v2g_events = sorted(tmp_v2g_events, key=lambda d: d['start'])

        # TODO: Is this usefully here? This method should only be called when a calendar items changes..
        if self.v2g_events == tmp_v2g_events:
            # Nothing has changed...
            return
        self.v2g_events.clear()
        self.v2g_events = tmp_v2g_events
        self.log("__handle_calendar_integration_change: changed v2g_events")
        await self.__process_calendar_change(v2g_args="changed HA calendar events")


    def __parse_to_tz_dt(self, any_date_type: any):
        """
        Utility method to robustly convert a string into a dt.datetime object
        String may contain only date info, then time will be set to 00:00:00
        :param any_date_type: date or string
        :return: dt.datetime object with the right timezone.
        """

        # All-day is detected by the fact that the any_date_type does not have a time.
        is_all_day_event = False
        if type(any_date_type) is str:
            is_all_day_event = "T" not in any_date_type
            try:
                any_date_type = dt.datetime.fromisoformat(any_date_type)
            except Exception as ex:
                self.log(f"__parse_to_tz_dt, fromisoformat Error: {ex} while trying to parse string:"
                         f" {any_date_type}, returning None.")
                return None, None
        if type(any_date_type) is dt.date:
            # No time in date, this is the case for "all day" events, but for timezone we need this.
            tm = dt.time(0, 0, 0)  # hr/min/sec
            any_date_type = dt.datetime.combine(any_date_type, tm)
            is_all_day_event = True
        any_date_type = any_date_type.astimezone(c.TZ)
        return any_date_type, is_all_day_event


    async def __poll_calendar(self, kwargs=None):
        # Get the items in from now to the next week from the calendar
        start = get_local_now()
        end = (start + dt.timedelta(days=7))
        # It is a bit strange this is not async... for now we'll live with it.
        # TODO: Optimise by use of sync_tokens so that only the updated events get sent
        caldav_events = self.car_reservation_calendar.search(
            start=start,
            end=end,
            event=True,
            expand=True,
        )

        remote_v2g_events = []

        for caldav_event in caldav_events:
            cdi = caldav_event.icalendar_component
            # Create a v2g_liberty specific event based on the caldav event
            start, is_all_day = self.__parse_to_tz_dt(cdi['dtstart'].dt)
            end, is_all_day = self.__parse_to_tz_dt(cdi['dtend'].dt)
            if start is None or end is None:
                continue
            if is_all_day:
                # This way the end date becomes 23:59 in the UI.
                end = end + dt.timedelta(minutes=-1)

            v2g_event = {
                'start': start,
                'end': end,
                'summary': str(cdi.get('summary', "")),
                'description': str(cdi.get('description', "")),
            }
            v2g_event = await self.__post_process_v2g_event(v2g_event)
            remote_v2g_events.append(v2g_event)

        attributes = {"keep_alive": start}
        await self.set_state(
            "input_text.calendar_account_connection_status",
            state="Successfully connected",
            attributes=attributes
        )

        remote_v2g_events = sorted(remote_v2g_events, key=lambda d: d['start'])
        if self.v2g_events == remote_v2g_events:
            # Nothing has changed...
            return

        self.v2g_events.clear()
        self.v2g_events = remote_v2g_events
        self.log("__poll_calendar: changed v2g_events")
        await self.__process_calendar_change(v2g_args="changed dav calendar events")

    async def __post_process_v2g_event(self, v2g_event):
        # Add target_soc, hash_id and dismissed status.
        # These three actions on the v2g_event must always be in this order
        # The soc should be taken into account for the hash.
        # The dismissed status can only be set if the hash has been added.
        v2g_event = self.__add_target_soc(v2g_event)
        v2g_event = add_hash_id(v2g_event)
        v2g_event = self.__add_dismissed_status(v2g_event)
        return v2g_event


    def __add_target_soc(self, v2g_event: dict) -> dict:
        # Add a target SoC to a v2g_event dict based upon the summary and description.
        # Prevent concatenation of possible None values
        text_to_search_in = " ".join(filter(None, [v2g_event['summary'], v2g_event['description']]))

        # Removed searching for a number in kWh, not used?
        # Try searching for a number in %
        # ToDo: Add possibility to set target in km
        target_soc_percent = search_for_soc_target("%", text_to_search_in)
        if target_soc_percent is None or target_soc_percent > 100:
            # self.log(f"__add_target_soc: target soc {target_soc_percent} changed to 100%.")
            target_soc_percent = 100
        elif target_soc_percent < c.CAR_MIN_SOC_IN_PERCENT:
            self.log(f"__add_target_soc: target soc {target_soc_percent} below "
                     f"c.CAR_MIN_SOC_IN_PERCENT ({c.CAR_MIN_SOC_IN_PERCENT}), changed.")
            target_soc_percent = c.CAR_MIN_SOC_IN_PERCENT

        v2g_event['target_soc_percent'] = target_soc_percent
        return v2g_event

    def __add_dismissed_status(self, v2g_event: dict) -> dict:
        # Adds the dismissed status to a v2g_event that has been fetched from remote.
        # For this the 'locally stored' status from self.events_dismissed_statuses is used.
        dismissed = None
        hid = v2g_event['hash_id']
        for dismissed_event_hash_id in self.events_dismissed_statuses.keys():
            if dismissed_event_hash_id == hid:
                dismissed = self.events_dismissed_statuses[hid]
                break

        v2g_event['dismissed'] = dismissed
        return v2g_event


    async def __process_calendar_change(self, v2g_args: str =  None):
        await self.__write_events_in_ui_entity()
        await self.__draw_event_in_graph()
        try:
            await self.v2g_main_app.set_next_action(v2g_args=v2g_args)
        except Exception as e:
            self.log(f"__process_calendar_change. Could not call v2g_main_app.set_next_action. Exception: {e}.")
        self.__clean_up_events_dismissed_statuses()


    async def __write_events_in_ui_entity(self):
        # Prepare for rendering in the UI
        start = get_local_now()
        v2g_ui_event_calendar = []
        for n in range(7):
            calendar_date = start + dt.timedelta(days=n)
            events_in_day = []
            for v2g_ui_event in self.v2g_events:
                if v2g_ui_event["start"].date() == calendar_date.date():
                    # HTML Escape text before writing to UI
                    v2g_ui_event['summary'] = he(v2g_ui_event['summary'])
                    v2g_ui_event['description'] = he(v2g_ui_event['description'])
                    events_in_day.append(v2g_ui_event)
            if len(events_in_day) > 0:
                day = {"day": calendar_date, "events": events_in_day}
                v2g_ui_event_calendar.append(day)
        attributes = {"v2g_ui_event_calendar": v2g_ui_event_calendar}
        await self.set_state(
            "input_text.calendar_events",
            state=start,
            attributes=attributes
        )


    async def __draw_event_in_graph(self):
        now = get_local_now()
        if len(self.v2g_events) == 0:
            # There seems to be no way to hide the SoC series from the graph,
            # so it is filled with "empty" data, one record of 0.
            # Set it at a week from now, so it's not visible in the default view.
            ci_chart_items = [dict(time=(now + dt.timedelta(days=7)).isoformat(), soc=None)]
        else:
            # TODO: deal with overlapping items
            ci_chart_items = []

            for ci in self.v2g_events:
                # If ci is dismissed, do not draw
                status = ci.get("dismissed", None)
                if status is not None and status == True:
                    continue
                ci_chart_items.append({'time': ci['start'].isoformat(), 'soc': ci['target_soc_percent']})
                ci_chart_items.append({'time': ci['end'].isoformat(), 'soc': ci['target_soc_percent']})
                # Add to create a gap between ci's in the graph.
                ci_chart_items.append({'time': (ci['end'] + dt.timedelta(minutes=1)).isoformat(), 'soc': None})

        # To make sure the new attributes are treated as new we set a new state as well
        new_state = f"Calendar item available at {now.isoformat()}."
        result = dict(records=ci_chart_items)
        await self.set_state("input_text.calender_item_in_chart", state=new_state, attributes=result)
        # self.log(f"__draw_events_in_graph: {result}.")


    def __clean_up_events_dismissed_statuses(self):
        """Check is any of the self.v2g_events is registered as dismissed (in self.events_dismissed_statuses)
           Remove any hash_id's from self.events_dismissed_statuses that are not in self.v2g_events.
           To be called when new calendar items have come in.
        """
        if len(self.events_dismissed_statuses) == 0:
            # Nothing to clean up
            return

        if len(self.v2g_events) == 0:
            self.events_dismissed_statuses.clear()
            return

        for dismissed_event_hash_id in self.events_dismissed_statuses.keys():
            hash_id_in_v2g_events = False
            for v2g_event in self.v2g_events:
                if dismissed_event_hash_id == v2g_event['hash_id']:
                    hash_id_in_v2g_events = True
                    break

            if not hash_id_in_v2g_events:
                # The hash_id in self.events_dismissed_statuses is not current anymore.
                self.events_dismissed_statuses.pop(dismissed_event_hash_id)


######################################################################
#                    PRIVATE UTILITY FUNCTIONS                       #
######################################################################

def add_hash_id(v2g_event: dict):
    """ Add a hash_id to a v2g_event
        The hash_id is used for keeping track of dismissed events
        Any changes to the event result in a different hash_id, including target_soc_
    """
    hash_id = hash(" ".join(filter(None,[
        v2g_event['start'].isoformat(),
        v2g_event['end'].isoformat(),
        v2g_event['summary'],
        v2g_event['description'],
        str(v2g_event['target_soc_percent'])
    ])))
    # As it is a key that will be sent in notifications a string is more convenient.
    v2g_event['hash_id'] = str(hash_id)
    return v2g_event


def search_for_soc_target(search_unit: str, string_to_search_in: str) -> int:
    """Search description for the first occurrence of some (integer) number of the search_unit.

    Parameters:
        search_unit (str): The unit to search for, typically %, km or kWh, found directly following the number
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