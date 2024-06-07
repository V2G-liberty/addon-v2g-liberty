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
    POLLING_INTERVAL_SECONDS: int = 300
    calender_listener_id: str = ""
    v2g_main_app: object

    async def initialize(self):
        self.log("initialise ReservationsClient")
        self.v2g_main_app = await self.get_app("v2g_liberty")
        self.principal = None
        self.poll_timer_id = ""
        self.v2g_events = []
        await self.initialise_calendar()
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
        # self.log(f"initialise_calendar called")

        if c.CAR_CALENDAR_SOURCE == "Direct caldav source":
            self.log(f"initialise_calendar called - Direct caldav source")

            # Just to be sure: cancel the lister that could be active because the previous setting
            # for c.CAR_CALENDAR_SOURCE was "Home Assistant Integration".
            # For "Direct caldav source" no listener is used as the calendar gets polled.
            if self.calender_listener_id != "":
                await self.cancel_listen_state(self.calender_listener_id)
                self.calender_listener_id = ""

            # A configuration has been made earlier, so it is expected the calendar can be
            # initialised and activated.
            # self.log(f"initialise_calendar, url: {c.CALENDAR_ACCOUNT_INIT_URL}, username: " \
            #          f"{c.CALENDAR_ACCOUNT_USERNAME}, password: {c.CALENDAR_ACCOUNT_PASSWORD}")
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
                self.log(f"initialise_calendar, selected calendar: {c.CAR_CALENDAR_NAME}, activating calendar")
                await self.activate_selected_calendar()
            else:
                self.log(f"initialise_calendar: No calendar selected")
                # TODO: Would be nice if it could say "Please choose a calendar"

            return "Successfully connected"

        else:
            self.log(f"initialise_calendar called - HA Integration")
            if c.INTEGRATION_CALENDAR_ENTITY_NAME is not None and \
                    c.INTEGRATION_CALENDAR_ENTITY_NAME not in ["", "unknown", "Please choose an option"]:
                self.log(f"initialise_calendar, selected calendar integration: "
                         f"{c.INTEGRATION_CALENDAR_ENTITY_NAME}, setting listener")
                self.calender_listener_id = await self.listen_state(
                    self.__handle_calendar_integration_change,
                    c.INTEGRATION_CALENDAR_ENTITY_NAME,
                    attribute="all"
                )
                new = await self.get_state(c.INTEGRATION_CALENDAR_ENTITY_NAME, attribute="all")
                await self.__handle_calendar_integration_change(new=new)
                return "Successfully connected"
            else:
                self.log(f"initialise_calendar, No calendar integrations found")
                return "No calendar integrations found"
        return "initialise_calendar: unexpected exit"

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
            + the next upcoming event gets changed
            Unfortunately the entity only holds one event: the next upcoming.
        """
        self.log(f"__handle_calendar_integration_change called with new: {new}.")
        # TODO: Try get all events from the calendar somewhere along this line:
        # start = await self.get_now()
        # end = (start + timedelta(days=7))
        # calendar = self.get_entity(c.INTEGRATION_CALENDAR_ENTITY_NAME)
        # # This does return a valid calendar entity.
        # # See: https://developers.home-assistant.io/docs/core/entity/calendar/
        # # This does not work yet:
        # events = await calendar.call_service(
        #     "get_events",
        #     start_date=start,
        #     end_date=end,
        # )

        self.v2g_events.clear()
        if new is not None:
            start = new["attributes"].get("start_time", None)
            if start is not None:
                start = start.replace(" ", "T")
                start = isodate.parse_datetime(start).astimezone(c.TZ)

                end = new["attributes"].get("end_time", None)
                if end is None:
                    # Fail-safe, assume a duration of 1 hour
                    end = start + timedelta(hours=1)
                else:
                    end = end.replace(" ", "T")
                    end = isodate.parse_datetime(end).astimezone(c.TZ)

                v2g_event = {
                    'start': start,
                    'end': end,
                    'summary': new["attributes"]["message"],
                    'description': new["attributes"]["description"]
                }
                self.v2g_events.append(v2g_event)
            else:
                self.log("__handle_calendar_integration_change aborted as start is None.")
        else:
            self.log("__handle_calendar_integration_change aborted as new is None.")

        await self.__write_events_in_ui_entity()

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

        if self.v2g_events == remote_v2g_events:
            # Nothing has changed... this will be less relevant once sync_tokens are used.
            return

        self.v2g_events.clear()
        self.v2g_events = remote_v2g_events
        self.log("__poll_calendar changed v2g_events")
        await self.__write_events_in_ui_entity()
        await self.v2g_main_app.set_next_action(v2g_args="changed calendar events")

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
        self.log(f"__write_events_in_ui_entity: {attributes}")

