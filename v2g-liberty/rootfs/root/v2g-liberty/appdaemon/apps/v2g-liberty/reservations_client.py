from datetime import datetime, timedelta
import isodate
import time
import asyncio
import adbase as ad
import requests
import constants as c
import caldav

import appdaemon.plugins.hass.hassapi as hass


class ReservationsClient(hass.Hass):
    cal_client: caldav.DAVClient
    principal: object
    car_reservation_calendar: object

    # TODO:
    # When a calender event is 'received' (new/changed) parse it right away and add to the v2g_event:
    #     target_value_km: int
    #     target_value_percent: int
    #     target_value_kwh: int
    # So that v2g_liberty can use this right away and does not have to process this every time while getting a schedule
    # If it finds target_value_kwh empty it can set is for later use.

    v2g_events: list = []
    poll_timer_id: str = ""
    initialised: bool = False
    POLLING_INTERVAL_SECONDS: int = 300
    calender_listener_id: str = ""

    async def initialize(self):
        self.log("initialise ReservationsClient")
        self.principal = None
        self.poll_timer_id = ""
        self.v2g_events = []
        await self.initialise_calendar()
        self.initialised = True
        self.log(f"Completed initialise ReservationsClient")

    ######################################################################
    #                         PUBLIC FUNCTIONS                           #
    ######################################################################

    async def get_v2g_events(self):
        return self.v2g_events

    async def initialise_calendar(self):
        # Called by globals when:
        # + constants have been loaded from config
        # + constants have changed from UI.
        self.log(f"initialise_calendar")

        if c.CAR_CALENDAR_SOURCE == "Direct caldav source":
            if c.CAR_CALENDAR_NAME is not None and c.CAR_CALENDAR_NAME not in ["", "unknown", "Please choose an option"]:
                # A configuration has been made earlier, so it is expected the calendar can be
                # initialised and activated.
                # self.log(f"initialise ReservationsClient, c.CAR_CALENDAR_NAME "
                #          f"known: {c.CAR_CALENDAR_NAME}, initialising calendar")
                self.cal_client = caldav.DAVClient(
                    url=c.CALENDAR_ACCOUNT_INIT_URL,
                    username=c.CALENDAR_ACCOUNT_USERNAME,
                    password=c.CALENDAR_ACCOUNT_PASSWORD
                )
                try:
                    self.principal = self.cal_client.principal()
                except caldav.lib.error.AuthorizationError:
                    return "Authorization error"
                except requests.exceptions.ConnectionError:
                    return "Connection error"

                if self.principal is None:
                    return "No calendars found"
                await self.activate_selected_calendar()
            if self.calender_listener_id != "":
                await self.cancel_listen_state(self.calender_listener_id)
                self.calender_listener_id = ""
        else:
            if c.INTEGRATION_CALENDAR_ENTITY_NAME is not None and \
               c.INTEGRATION_CALENDAR_ENTITY_NAME not in ["", "unknown", "Please choose an option"]:
                self.calender_listener_id = self.listen_state(
                    self.__handle_calendar_integration_change,
                    c.INTEGRATION_CALENDAR_ENTITY_NAME,
                    attribute="all"
                )
                new = self.get_state(c.INTEGRATION_CALENDAR_ENTITY_NAME, attribute="all")
                await self.__handle_calendar_integration_change(new=new)
            else:
                return "No calendar integrations found"
        return "success"

    async def get_calendar_names(self):
        # Called by globals to populate the input_select.
        self.log("get_calendar_names called")
        cal_names = []
        if c.CAR_CALENDAR_SOURCE == "Direct caldav source":
            if self.principal is None:
                return cal_names
            for calendar in self.principal.calendars():
                cal_names.append(calendar.name)
        else:
            calendar_states = self.get_state("calendar")
            self.log(f"get_calendar_names calendar states: {calendar_states}")
            for calendar in calendar_states:
                cal_names.append(calendar.entity_id)

        self.log(f"get_calendar_names, returning calendars: {cal_names}")
        return cal_names

    async def activate_selected_calendar(self):
        # Only used for "Direct caldav source"
        self.log("activate_selected_calendar called")
        if not self.initialised:
            self.log("activate_selected_calendar called before initialise is finished.")

        if c.CAR_CALENDAR_NAME is None or c.CAR_CALENDAR_NAME in ["", "unknown", "Please choose an option"]:
            self.log(f"activate_selected_calendar empty c.CAR_CALENDAR_NAME {c.CAR_CALENDAR_NAME}, not activating.")
            # TODO: Check if this ever occurs, it is tested at initialisation already...
            # TODO: Create persistent notification
            return False

        self.car_reservation_calendar = self.principal.calendar(name=c.CAR_CALENDAR_NAME)

        if self.poll_timer_id != "" and self.timer_running(self.poll_timer_id):
            # Making sure there won't be parallel polling treads.
            self.log(f"activate_selected_calendar  there seems to be a poll-timer already: {self.poll_timer_id}.")
            await self.cancel_timer(self.poll_timer_id)
            self.poll_timer_id = ""

        self.poll_timer_id = await self.run_every(self.__poll_calendar, "now", self.POLLING_INTERVAL_SECONDS)
        self.log(f"activate_selected_calendar started polling_time {self.poll_timer_id} "
                 f"every {self.POLLING_INTERVAL_SECONDS} sec.")
        self.log("Completed activate_selected_calendar")


    ######################################################################
    #                   PRIVATE (CALLBACK) FUNCTIONS                     #
    ######################################################################

    async def __handle_calendar_integration_change(self, entity=None, attribute=None, old=None, new=None, kwargs=None):
        """For the situation where a calendar integration is used (and not a direct caldav online calendar).
            It is expected that this listener is only called when:
            + the previous event has passed
            + the next upcoming event gets changed
            Unfortunately the entity only holds one event: the next upcoming.
        """
        if new is None:
            return
        start = new["attributes"].get("start_time", None)
        if start is None:
            return
        start = start.replace(" ", "T")
        start = isodate.parse_datetime(start).astimezone(c.TZ)

        end = new["attributes"].get("end_time", None)
        if end is None:
            # Fail-safe, assume a duration of 1 hour
            end = start + timedelta(hours = 1)
        else:
            end = end.replace(" ", "T")
            end = isodate.parse_datetime(end).astimezone(c.TZ)

        v2g_event = {
            'start': start,
            'end': end,
            'summary': new["attributes"]["message"],
            'description': new["attributes"]["description"]
        }
        self.v2g_events.clear()
        self.v2g_events.append(v2g_event)
        self.log(f"__handle_calendar_integration_change v2g_events: {self.v2g_events}")
        self.__write_events_in_ui_entity()


    async def __poll_calendar(self, kwargs=None):
        # Get the items in from now to the next week from the calendar
        start = await self.get_now()
        end = (start + timedelta(days=7))
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
            # Strip all caldav related 'fluff'
            cdi = caldav_event.icalendar_component
            v2g_event = {
                'start': cdi['dtstart'].dt.astimezone(c.TZ),
                'end': cdi['dtend'].dt.astimezone(c.TZ),
                'summary': str(cdi.get('summary', "")),
                'description': str(cdi.get('description', ""))
            }
            # self.log(f"__poll_calendar v2g_event: {v2g_event}")
            remote_v2g_events.append(v2g_event)

        attributes = {"keep_alive": start}
        await self.set_state(
            "input_text.calendar_account_connection_status",
            state="Successfully connected",
            attributes=attributes
        )

        # self.log(f"__poll_calendar v2g_events: {self.v2g_events}")
        # self.log(f"__poll_calendar rem_events: {remote_v2g_events}")
        if self.v2g_events == remote_v2g_events:
            self.log("__poll_calendar v2g_events and rem_events are equal")
            # Nothing has changed... this will be less relevant once sync_tokens are used.
            return
        # TODO: Call XYZ on v2g_main app to kick off set_next_action?
        self.v2g_events = remote_v2g_events
        self.log("__poll_calendar v2g_events and rem_events different")

        await self.__write_events_in_ui_entity()

    async def __write_events_in_ui_entity(self):
        # Prepare for rendering in the UI
        start = await self.get_now()
        v2g_ui_event_calendar = []
        for n in range(7):
            calendar_date = start + timedelta(days=n)
            events_in_day = []
            for v2g_ui_event in self.v2g_events:
                if v2g_ui_event["start"].date() == calendar_date.date():
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
