from datetime import datetime, timedelta, timezone
import isodate
import pytz
import asyncio
import time
import json
import math
import requests
import constants as c
from v2g_globals import time_round, get_local_now
import appdaemon.plugins.hass.hassapi as hass


class FMClient(hass.Hass):
    """ This class manages the communication with the FlexMeasures platform, which delivers the charging schedules.

    - Saves charging schedule locally (input_text.chargeschedule)
    - Reports on errors via v2g_liberty module handle_no_schedule()

    """

    # Constants
    FM_SCHEDULE_DURATION: datetime
    FM_SCHEDULE_DURATION_STR: str
    MAX_NUMBER_OF_REATTEMPTS: int
    DELAY_FOR_INITIAL_ATTEMPT: int  # number of seconds
    DELAY_FOR_REATTEMPTS: int  # number of seconds

    # A slack for the constraint_relaxation_window in minutes
    WINDOW_SLACK_IN_MINUTES: int

    # FM Authentication token
    fm_token: str
    # Helper to prevent parallel calls to FM for getting a schedule
    fm_busy_getting_schedule: bool
    # Helper to prevent blocking the sequence of getting schedules.
    # Sometimes the previous bool is not reset (why we don't know), then it needs a timed reset.
    # stores the date_time of the last successful received schedule
    fm_date_time_last_schedule: datetime
    fm_max_seconds_between_schedules: int

    # Helper to see if FM connection/ping has too many errors
    connection_error_counter: int
    handle_for_repeater: str
    connection_ping_interval: int
    errored_connection_ping_interval: int

    # For sending notifications to the user.
    v2g_main_app: object

    client: object
    resolution: timedelta

    async def initialize(self):
        self.log("Initializing FlexMeasuresClient")

        self.v2g_main_app = await self.get_app("v2g_liberty")

        self.fm_token = ""
        self.fm_busy_getting_schedule = False
        self.fm_date_time_last_schedule = get_local_now()
        self.resolution = timedelta(minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES)

        # Maybe add these to constants / settings?
        self.FM_SCHEDULE_DURATION_STR = "PT27H"
        self.FM_SCHEDULE_DURATION = isodate.parse_duration(self.FM_SCHEDULE_DURATION_STR)
        self.DELAY_FOR_REATTEMPTS = 6
        self.MAX_NUMBER_OF_REATTEMPTS = 15
        self.DELAY_FOR_INITIAL_ATTEMPT = 20
        self.WINDOW_SLACK_IN_MINUTES = 60

        # Add an extra attempt to prevent the last attempt not being able to finish.
        self.fm_max_seconds_between_schedules = \
            self.DELAY_FOR_REATTEMPTS * (self.MAX_NUMBER_OF_REATTEMPTS + 1) + self.DELAY_FOR_INITIAL_ATTEMPT

        await self.initialise_and_test_fm_client()

        # Ping every half hour. If offline a separate process will run to increase polling frequency.
        self.connection_error_counter = 0
        # self.run_every(self.ping_server, "now", 30 * 60)
        self.handle_for_repeater = ""
        self.log("Completed initializing FlexMeasuresClient")


    async def initialise_and_test_fm_client(self) -> str:
        self.log("initialise_and_test_fm_client called")
        # Unusual place for the import, but it has to be in an async method otherwise it errors out
        # with problems with the async loop.
        from flexmeasures_client import FlexMeasuresClient
        from flexmeasures_client.exceptions import EmailValidationError

        host, ssl = get_host_and_ssl_from_url(c.FM_BASE_URL)
        self.log(f"initialise_and_test_fm_client, host: '{host}', ssl: '{ssl}'.")
        try:
            self.client = FlexMeasuresClient(
                host = host,
                email = c.FM_ACCOUNT_USERNAME,
                password = c.FM_ACCOUNT_PASSWORD,
                ssl = ssl,
            )
        except ValueError as ve:
            self.log(f"initialise_and_test_fm_client, CLIENT ERROR: {ve}.")
            # ValueErrors:
            # 'xxx'' is not an email address format string (= also for empty email)
            # password cannot be empty
            return ve
        except EmailValidationError as eve:
            self.log(f"initialise_and_test_fm_client, CLIENT ERROR: {eve}.")
            return eve

        self.log(f"initialise_and_test_fm_client, successfully initialised flexmeasures client")

        try:
            self.log("initialise_and_test_fm_client, getting access token...")
            await self.client.get_access_token()
        except ValueError as ve:
            self.log(f"V2G ERROR: {ve}")
            # ValueErrors:
            # User with email 'xxx' does not exist
            # User password does not match
            return ve
        except ConnectionError as ce:
            self.log(f"CLIENT ERROR: {ce}")
            # CommunicationErrors:
            # Error occurred while communicating with the API
            # = for invalid URL and none-fm-url
            return "Communication error. Wrong URL?"

        if self.client.access_token is None:
            self.log("initialise_and_test_fm_client, access token is None")
            return "Unknown error with FlexMeasures"
        self.log(f"initialise_and_test_fm_client, access token: {self.client.access_token}, "
                 f"returning 'Successfully connected'.")

        return "Successfully connected"

    async def get_fm_assets(self):
        self.log("get_fm_assets called")
        return await self.client.get_assets()

    async def get_fm_sensors(self, asset_id: int):
        self.log("get_fm_sensors called")
        fm_sensors = await self.client.get_sensors()
        # Filter sensors to match the assets_id
        sensors = [sensor for sensor in fm_sensors if sensor["generic_asset_id"] == asset_id]
        return sensors

    # async def ping_server(self, *args):
        # """ Ping function to check if server is alive """
        # res = requests.get(c.FM_PING_URL)
        # if res.status_code == 200:
        #     if self.connection_error_counter > 0:
        #         # There was an error before as the counter > 0
        #         # So a timer must be running, but it is not needed any more, so cancel it.
        #         await self.cancel_timer(self.handle_for_repeater, True)
        #         await self.v2g_main_app.handle_no_new_schedule("no_communication_with_fm", False)
        #     self.connection_error_counter = 0
        # else:
        #     self.connection_error_counter += 1
        #
        # if self.connection_error_counter == 1:
        #     # A first error occurred, retry in every minute now
        #     self.handle_for_repeater = self.run_every(self.ping_server, "now+60", 60)
        #     self.log("No communication with FM! Increase tracking frequency.")
        #     await self.v2g_main_app.handle_no_new_schedule("no_communication_with_fm", True)

    def authenticate_with_fm(self):
        """Authenticate with the FlexMeasures server and store the returned auth token.

        Hint:
        the lifetime of the token is limited, so also call this method whenever the server returns a 401 status code.
        """
        # self.log(f"Authenticating with FlexMeasures on URL '{c.FM_AUTHENTICATION_URL}'.")
        url = c.FM_AUTHENTICATION_URL
        res = requests.post(
            url,
            json=dict(
                email=c.FM_ACCOUNT_USERNAME,
                password=c.FM_ACCOUNT_PASSWORD,
            ),
        )
        now = get_local_now().strftime(c.DATE_TIME_FORMAT)
        if not res.status_code == 200:
            self.log_failed_response(res, url)
            message = f"Failed to connect/login at {now}."
            self.set_state("input_text.fm_connection_status", state=message)
            return False
        json_response = res.json()
        if json_response is None:
            self.log(f"Authenticating failed, no valid json response.")
            return False

        self.fm_token = json_response.get("auth_token", None)
        if self.fm_token is None:
            self.log(f"Authenticating failed, no auth_token in json response: '{json_response}'.")
            return False
        message = f"Successful connect+login"
        self.set_state("input_text.fm_connection_status", state=message)
        return True

    def log_failed_response(self, res, endpoint: str):
        """Log failed response for a given endpoint."""
        try:
            tmp= str(res.json())
            if len(tmp) > 500:
                tmp = f"{tmp[0:350]}.....{tmp[-150:]}"
            self.log(f'{endpoint} failed ({res.status_code}) with JSON response "{tmp}"')
        except json.decoder.JSONDecodeError:
            self.log(f"{endpoint} failed ({tmp.status_code}) with response {tmp}")

    def check_deprecation_and_sunset(self, url, res):
        """Log deprecation and sunset headers, along with info links.

        Reference
        ---------
        https://flexmeasures.readthedocs.io/en/latest/api/introduction.html#deprecation-and-sunset
        """
        warnings = res.headers.get("Deprecation") or res.headers.get("Sunset")
        if warnings:
            message = f"Your request to {url} returned a warning."
            # Go through the response headers in their given order
            for header, content in res.headers.items():
                if header == "Deprecation":
                    message += f"\nDeprecation: {content}."
                elif header == "Sunset":
                    message += f"\nSunset: {content}."
                elif header == "Link" and ('rel="deprecation";' in content or 'rel="sunset";' in content):
                    message += f" Link for further info: {content}"
            self.log(message)

    async def get_new_schedule(self, targets: list, current_soc_kwh: float, back_to_max_soc: datetime):
        """Get a new schedule from FlexMeasures.
           But not if still busy with getting previous schedule.
        Trigger a new schedule to be computed and set a timer to retrieve it, by its schedule id.
        Params:
        targets: a list of targets (=dict with start, end, soc)
        back_to_max_soc: if current SoC > Max_SoC this setting informs the schedule when to be back at max soc.
        Can be None.
        """
        self.log(f"get_new_schedule called.")
        now = get_local_now()
        if self.fm_busy_getting_schedule:
            self.log(f"get_new_schedule self.fm_date_time_last_schedule {self.fm_date_time_last_schedule.isoformat()}.")
            seconds_since_last_schedule = int((now - self.fm_date_time_last_schedule).total_seconds())
            if seconds_since_last_schedule > self.fm_max_seconds_between_schedules:
                self.log(f"Retrieving previous schedule is taking too long ({seconds_since_last_schedule} sec.),"
                         " assuming call got 'lost'. Getting new schedule.")
            else:
                self.log("Not getting new schedule, still processing previous request.")
                return
        else:
            self.log("get_new_schedule: Was not busy getting schedule, but i am now!")

        # This has to be set here instead of in get_schedule because that function is called with a delay
        # and during this delay this get_new_schedule could be called.
        self.fm_busy_getting_schedule = True

        # Ask to compute a new schedule by posting flex constraints while triggering the scheduler
        schedule_id = await self.trigger_schedule(
            targets = targets,
            current_soc_kwh = current_soc_kwh,
            back_to_max_soc = back_to_max_soc
        )
        if schedule_id is None:
            self.log("Failed to trigger new schedule, schedule ID is None. Cannot call get_schedule")
            self.fm_busy_getting_schedule = False
            return

        # Set a timer to get the schedule a little later
        self.log(f"Attempting to get schedule (id={schedule_id}) in {self.DELAY_FOR_INITIAL_ATTEMPT} seconds")
        self.run_in(self.get_schedule, delay=self.DELAY_FOR_INITIAL_ATTEMPT, schedule_id=schedule_id)


    async def trigger_schedule(self, *args, **fnc_kwargs) -> str:
        """Request a new schedule to be generated by calling the schedule triggering endpoint, while
        POSTing flex constraints.
        Return the schedule id for later retrieval of the asynchronously computed schedule.
        """
        current_soc_kwh = fnc_kwargs.get("current_soc_kwh", None)
        if current_soc_kwh is None:
            self.log(f"trigger_schedule: aborting as there is no current_soc_kwh.")
            return

        targets = fnc_kwargs.get("targets", None)
        back_to_max_soc = fnc_kwargs.get("back_to_max_soc", None)
        resolution = timedelta(minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES)
        rounded_now = time_round(get_local_now(), resolution)

        # The schedule duration, usually just over a day long.
        schedule_end = time_round(rounded_now + self.FM_SCHEDULE_DURATION, resolution)

        # Add a placeholder target with soc CAR_MAX_SOC_IN_KWH (usually ≈80%) one week from now.
        # FM needs this to be able to produce a schedule whereby the influence of this placeholder is none.
        # Added to the soc_minima later on
        end_of_schedule_input_period = (rounded_now + timedelta(days=7))

        soc_minima = []

        # TODO: Add "soc-usage": soc_usage
        # soc_usage = []
        # The basis usage is 0 and can be lifted by adding other usage
        # soc_usage.append({
        #     "value": 0,
        #     "start": rounded_now,
        #     "end": schedule_end,
        # })

        if targets is not None:
            # TODO: Add "soc-usage"
            # usage_per_event_time_interval = f"{str(c.USAGE_PER_EVENT_TIME_INTERVAL)} kW"

            ##################################
            #    Make a list of soc_minima   #
            ##################################
            self.log(f"Trigger_schedule. start generating soc_minima'.")
            for target in targets:
                target_start = target["start"]
                target_end = target["end"]
                # Only add targets with a start in the schedule duration
                if target_start > schedule_end:
                    # Assuming the targets list is sorted we can break (instead of continue)
                    break
                target_soc_kwh = target["target_soc_kwh"]
                soc_minima.append({
                    "value": target_soc_kwh,
                    "start": target_start,
                    "end": target_end,
                })
                # TODO: soc_usage
                # soc_usage.append({
                #     "value": c.USAGE_PER_EVENT_TIME_INTERVAL,
                #     "start": target_start,
                #     "end": target_end,
                # })
            # Always add a future target for scheduling to be made possible.
            soc_minima.append({
                'value': c.CAR_MAX_SOC_IN_KWH,
                'start': end_of_schedule_input_period - self.resolution,
                'end': end_of_schedule_input_period,
            })
            # Remove any overlap and use the maximum in overlapping periods.
            soc_minima = self.consolidate_time_ranges(soc_minima)

            # TODO: soc_usage
            # soc_usage = self.consolidate_time_ranges(soc_usage)


            ##################################
            #   Make a list of soc_maxima    #
            ##################################
            self.log(f"Trigger_schedule. start generating soc_maxima'.")

            # The basis maxima that can be lifted by adding other maxima
            soc_maxima = [{
                "value": c.CAR_MAX_SOC_IN_KWH,
                "start": rounded_now,
                "end": end_of_schedule_input_period,
            }]

            first_b2ms_reset_moment = schedule_end
            for soc_minimum in soc_minima:
                # Does this target need a relaxation window? This is the period before a calendar item where
                # soc_maxima should be set to the value (target_soc_kwh to allow the schedule to reach a target higher
                # than the CAR_MAX_SOC_IN_KWH.
                soc_minimum_start = soc_minimum["start"]
                soc_minimum_end = soc_minimum["end"]
                minimum_soc_kwh = soc_minimum["value"]

                if minimum_soc_kwh > c.CAR_MAX_SOC_IN_KWH:
                    window_duration = math.ceil((minimum_soc_kwh - c.CAR_MAX_SOC_IN_KWH) /
                                      (c.CHARGER_MAX_CHARGE_POWER / 1000) * 60) + self.WINDOW_SLACK_IN_MINUTES
                    self.log(f"trigger_schedule window_duration: {window_duration}.")
                    # srw = start_relaxation_window, erw = end_relaxation_window
                    srw = time_round((soc_minimum_start - timedelta(minutes=window_duration)), resolution)
                    erw = time_round((soc_minimum_end + timedelta(minutes=window_duration)), resolution)
                    if srw < rounded_now:
                        # This is when the target SoC cannot be reached at the calendar-item_start, Scenario 3.
                        srw = rounded_now
                        # In this case it is never relevant to go back to max_soc
                        back_to_max_soc = None
                        self.log("Strategy for soc_maxima: Priority for calendar target (Scenario 3).")
                    if srw < first_b2ms_reset_moment:
                        first_b2ms_reset_moment = srw
                    self.log(f"trigger_schedule srw: {srw}.")

                    soc_maxima.append({
                        "value": minimum_soc_kwh,
                        "start": srw,
                        "end": erw,
                    })
                self.log(f"Soc_minima processed, first_b2ms_reset_moment: {first_b2ms_reset_moment.isoformat()}.")


        # Range where schedule should only discharge. This when the SoC is above the max (80%).
        # This can be due to returning home and connection with a high SoC or when the car did not
        # get disconnected during a reservation with a target > max_soc and the calendar item is dismissed.
        # It is only added at the start (now) and not after future targets as we expect these to lead to a
        # SoC below the max as the car is used for driving and thus losing SoC.

        # If the current_soc is above the CAR_MAX_SOC_IN_KWH it need to be brought back below this max.
        # The back_to_max_soc parameter indicates when this task should be finished.
        #
        # Assume:
        # SRW  = Start of the relaxation window for the CTM, including the slack of 1 hour.
        #        Only relevant for calendar items with a target SoC above the CAR_MAX_SOC_IN_KWH.
        #        Relaxation refers to the fact that in this window the schedule does not get soc-maxima so that
        #        it can charge above the CAR_MAX_SOC_IN_KWH to reach the higher target SoC.
        #        To keep things simple, the SRW is always based on CAR_MAX_SOC_IN_KWH, even if the current soc is higher.
        # CTM  = Charge Target Moment which is the start of the first upcoming calendar item.
        #        By default, if there is no calendar item, the CTM is one week from now. This gives the
        #        schedule enough freedom for the coming 27 hours (total duration of the schedule).
        # B2MS = The datetime at which the ALLOWED_DURATION_ABOVE_MAX_SOC ends, it cannot be in the past.
        #        It serves as a target with a maximum SoC (where regular targets have a minimum).
        #        The CTM has a higher priority than the B2MS.
        # EMDW = End of Minimum Discharge Window. Minimum Discharge Window (MDW) = time needed to discharge from current
        #        SoC to CAR_MAX_SOC_IN_KWH with available discharge power. EMDW = Now + MDW.
        #        Scenario A: In case of EMDW > B2MS then the latter is extended to EMDW.
        #
        # The following scenarios need to be handled, they might in time flow from one into the other:
        # 0. No B2MS
        #    The soc-maxima are based on the CAR_MAX_SOC_IN_KWH and run from "now" up to SRW.
        # 1. NOW < B2MS < SRW < CTM
        #    The B2MS is not influenced by the first calendar item (or there is none)
        #    SoC maxima are gradually lowered from current soc until B2MS from where they are set to CAR_MAX_SOC_IN_KWH.
        # 2. NOW < SRW < B2MS < CTM and NOW < SRW < CTM < B2MS
        #    In this case, the B2MS and CTM do not play a role. The soc-maxima are based on the current SoC and
        #    run from "now" up to SRW.
        # 3. SRW < NOW < B2MS < CTM and SRW < NOW < CTM < B2MS
        #    Here the priority is to reach the CTM and so do not set soc-maxima.
        #
        # Note that the situation where CTM < NOW is not relevant anymore and is covered by scenario 1.

        b2ms_maxima = []
        if back_to_max_soc is not None and isinstance(back_to_max_soc, datetime):
            self.log(f"trigger_schedule, back_to_max_soc: '{back_to_max_soc}'.")

            # Postpone discharge till after current calendar item if target soc has not been fulfilled
            start_b2ms = None
            if targets is not None:
                first_target = targets[0]
                start = first_target.get('start', None)
                end = first_target.get('end', None)
                if start < rounded_now < end:
                    target_soc_kwh = first_target.get('target_soc_kwh', None)
                    if target_soc_kwh is not None and target_soc_kwh >= current_soc_kwh:
                        start_b2ms = end
            if start_b2ms is None:
                start_b2ms = rounded_now

            # Handle too high current_soc.
            minimum_discharge_window = math.ceil(
                (current_soc_kwh - c.CAR_MAX_SOC_IN_KWH) / (c.CHARGER_MAX_DISCHARGE_POWER / 1000) * 60)
            end_minimum_discharge_window = time_round((start_b2ms - timedelta(minutes=minimum_discharge_window)),
                                                      resolution)
            if end_minimum_discharge_window > back_to_max_soc:
                # Scenario A.
                back_to_max_soc = end_minimum_discharge_window

            if back_to_max_soc >= first_b2ms_reset_moment:
                # Scenario 2.
                soc_maxima.append({
                    "value": current_soc_kwh,
                    "start": rounded_now,
                    "end": first_b2ms_reset_moment,
                })
                self.log("Strategy for soc_maxima: Maxima current_soc until Start of relaxation window (Scenario 2).")
            else:
                # Scenario 1: Gradually decrease SoC to reach c.CAR_MAX_SOC_IN_KWH by means of setting soc_maxima
                # TODO: A drawback of the gradual approach is that there might be discharging with low power which
                #       usually is less efficient. So, if the trigger message could handle the concept
                #       "only discharge during this window" it would result in better schedules.
                #       This should then replace the gradually lowered soc_maxima.
                #       Use target = c.CAR_MAX_SOC_IN_KWH at back_to_max_soc in combination with dynamic power
                #       constraint, production_power=0. See https://github.com/V2G-liberty/addon-v2g-liberty/issues/41.
                number_of_steps = (back_to_max_soc - rounded_now) // resolution
                if number_of_steps > 0:
                    step_kwh = (current_soc_kwh - c.CAR_MAX_SOC_IN_KWH) / number_of_steps
                    for i in range(number_of_steps):
                        b2ms_maxima.append({
                            "value": current_soc_kwh - (i * step_kwh),
                            "datetime": (rounded_now + i * resolution).isoformat()
                        })
                self.log(f"Strategy for soc_maxima: Gradually decrease SoC to "
                         f"reach {c.CAR_MAX_SOC_IN_KWH}kWh (Scenario 1).")

        soc_maxima = self.consolidate_time_ranges(soc_maxima)
        soc_maxima = convert_dates_to_iso_format(soc_maxima)
        soc_maxima += b2ms_maxima

        soc_minima = convert_dates_to_iso_format(soc_minima)

        # TODO: Add "soc-usage"
        # soc_usage = convert_dates_to_iso_format(soc_usage)

        message = {
            "start": rounded_now.isoformat(),
            "flex-model": {
                "soc-at-start": current_soc_kwh,
                "soc-unit": "kWh",
                "soc-min": c.CAR_MIN_SOC_IN_KWH,
                "soc-max": c.CAR_MAX_CAPACITY_IN_KWH,
                "soc-minima": soc_minima,
                "soc-maxima": soc_maxima,
                "roundtrip-efficiency": c.ROUNDTRIP_EFFICIENCY_FACTOR,
                "power-capacity": str(c.CHARGER_MAX_CHARGE_POWER) + "W"
            },
            "flex-context": c.FM_OPTIMISATION_CONTEXT,
        }

        url = c.FM_TRIGGER_URL
        tmp = str(message)
        self.log(f"Trigger_schedule with message: '{tmp}'.")
        # self.log(f"Trigger_schedule on url '{url}', with message: '{tmp[0:300]} . . . . . {tmp[-250:]}'.")
        res = requests.post(
            url,
            json=message,
            headers={"Authorization": self.fm_token},
        )

        self.check_deprecation_and_sunset(url, res)

        if res.status_code == 401:
            self.log_failed_response(res, url)
            await self.try_solve_authentication_error(res, url, self.trigger_schedule, *args, **fnc_kwargs)
            return None

        schedule_id = None
        if res.status_code == 200:
            schedule_id = res.json()["schedule"]  # can still be None in case something went wong

        if schedule_id is None:
            self.log_failed_response(res, url)
            if self.v2g_main_app is not None:
                await self.v2g_main_app.handle_no_new_schedule("timeouts_on_schedule", True)
            else:
                self.log(f"get_schedule. Could not call v2g_main_app.handle_no_new_schedule (3). Exception: {e}.")

            return None

        self.log(f"Successfully triggered schedule. Schedule id: {schedule_id}")
        if self.v2g_main_app is not None:
            await self.v2g_main_app.handle_no_new_schedule("timeouts_on_schedule", False)
        else:
            self.log(f"get_schedule. Could not call handle_no_new_schedule (4) on v2g_main_app as it is None.")

        return schedule_id


    def consolidate_time_ranges(self, ranges):
        """ Processes a list of time-value ranges, merging overlapping intervals """
        if len(ranges) == 0:
            self.log("consolidate_time_ranges, ranges = [], aborting")
            return []
        elif len(ranges) == 1:
            self.log("consolidate_time_ranges, only one range so nothing to consolidate, returning ranges untouched.")
            return ranges
        time_slots = self.generate_time_slots(ranges)
        combined_ranges = self.combine_time_slots(time_slots)
        return combined_ranges

    def generate_time_slots(self, ranges):
        """Generates a dictionary of time_slots at resolution with the maximum value for each time_slot."""
        time_slots = {}
        for time_range in ranges:
            current_time = time_range['start']
            end_time = time_range['end']
            while current_time <= end_time:
                if current_time not in time_slots:
                    time_slots[current_time] = time_range['value']
                else:
                    time_slots[current_time] = max(time_slots[current_time], time_range['value'])
                current_time += self.resolution
        return time_slots

    def combine_time_slots(self, time_slots):
        """Merges time slots into non-overlapping intervals with the same value."""

        combined_ranges = []
        sorted_times = sorted(time_slots.keys())
        current_start = sorted_times[0]
        current_value = time_slots[current_start]

        for i in range(1, len(sorted_times)):
            current_time = sorted_times[i]
            expected_time = sorted_times[i - 1] + self.resolution
            # self.log(f"s: {current_start}, t: {current_time}, e: {expected_time}, c: {current_value}, v: {time_slots[current_time]}.")
            if current_time != expected_time or time_slots[current_time] != current_value:
                combined_ranges.append({
                    'value': current_value,
                    'start': current_start,
                    'end': sorted_times[i - 1]
                })
                current_start = current_time
                current_value = time_slots[current_time]

        # Add the last range
        combined_ranges.append({
            'value': current_value,
            'start': current_start,
            'end': sorted_times[-1]
        })
        return combined_ranges

    async def get_schedule(self, kwargs, **fnc_kwargs):
        """GET a schedule message that has been requested by trigger_schedule.
           The ID for this is schedule_id.
           Then store the retrieved schedule.

        Pass the schedule id using kwargs["schedule_id"]=<schedule_id>.
        """
        # Just to be sure also set this here, it's primary point for setting to true is in get_new_schedule
        self.fm_busy_getting_schedule = True

        schedule_id = kwargs["schedule_id"]
        url = c.FM_GET_SCHEDULE_URL + schedule_id
        message = {
            "duration": self.FM_SCHEDULE_DURATION_STR,
        }
        res = requests.get(
            url,
            params=message,
            headers={"Authorization": self.fm_token},
        )
        self.check_deprecation_and_sunset(url, res)
        if res.status_code == 303:
            new_url = res.headers.get("location")
            if new_url is not None:
                self.log(f"Redirecting from {url} to {new_url}")
                url = new_url
                res = requests.get(
                    url,
                    params=message,
                    headers={"Authorization": self.fm_token},
                )

        if (res.status_code != 200) or (res.json is None):
            self.log_failed_response(res, url)
            s = self.DELAY_FOR_REATTEMPTS
            attempts_left = kwargs.get("attempts_left", self.MAX_NUMBER_OF_REATTEMPTS)
            if attempts_left >= 1:
                self.log(f"Reattempting to get schedule in {s} seconds (attempts left: {attempts_left})")
                self.run_in(self.get_schedule, delay=s, attempts_left=attempts_left - 1,
                            schedule_id=schedule_id)
            else:
                self.log("Schedule cannot be retrieved. Any previous charging schedule will keep being followed.")
                self.fm_busy_getting_schedule = False

                if self.v2g_main_app is not None:
                    await self.v2g_main_app.handle_no_new_schedule("timeouts_on_schedule", True)
                else:
                    self.log(f"get_schedule. Could not call handle_no_new_schedule (1) on v2g_main_app as it is None.")

            return False
        # self.log(f"get_schedule. successfully retrieved {res.status_code}")
        self.fm_busy_getting_schedule = False
        if self.v2g_main_app is not None:
            await self.v2g_main_app.handle_no_new_schedule("timeouts_on_schedule", False)
        else:
            self.log(f"get_schedule. Could not call handle_no_new_schedule (2) on v2g_main_app as it is None.")

        self.fm_date_time_last_schedule = get_local_now()
        self.log(f"get_schedule: self.fm_date_time_last_schedule set to now,"
                 f" {self.fm_date_time_last_schedule.isoformat()}, ({type(self.fm_date_time_last_schedule)}).")

        schedule = res.json()
        self.log(f"Schedule {schedule}")
        # To trigger state change we add the date to the state. State change is not triggered by attributes.
        self.set_state("input_text.chargeschedule",
                       state="ChargeScheduleAvailable" + self.fm_date_time_last_schedule.isoformat(),
                       attributes=schedule)


    async def try_solve_authentication_error(self, res, url, fnc, *fnc_args, **fnc_kwargs):
        if fnc_kwargs.get("retry_auth_once", True) and res.status_code == 401:
            self.log(f"Call to {url} failed on authorization (possibly the token expired); attempting to "
                     f"reauthenticate once.")
            self.authenticate_with_fm()
            fnc_kwargs["retry_auth_once"] = False
            await fnc(*fnc_args, **fnc_kwargs)


def get_host_and_ssl_from_url(url: str) -> tuple[str, bool]:
    """Get the host and ssl from the url."""
    if url.startswith("http://"):
        ssl = False
        host = url.removeprefix("http://")
    elif url.startswith("https://"):
        ssl = True
        host = url.removeprefix("https://")
    else:
        # If no prefix is given in url
        ssl = True
        host = url

    return host, ssl


def get_keepalive():
    now = get_local_now().strftime(c.DATE_TIME_FORMAT)
    return {"keep_alive": now}


def convert_dates_to_iso_format(data):
    for entry in data:
        dts = entry.get('start', None)
        if dts is not None and isinstance(dts, datetime):
            entry['start'] = dts.isoformat()
        dte = entry.get('end', None)
        if dte is not None and isinstance(dte, datetime):
            entry['end'] = dte.isoformat()
    return data

