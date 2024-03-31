from datetime import datetime, timedelta, timezone
import isodate
import pytz
import asyncio
import time
import json
import math
import requests
import constants as c
from v2g_globals import time_round

import appdaemon.plugins.hass.hassapi as hass


class FlexMeasuresClient(hass.Hass):
    """ This class manages the communication with the FlexMeasures platform, which delivers the charging schedules.

    - Saves charging schedule locally (input_text.chargeschedule)
    - Reports on errors via v2g_liberty module handle_no_schedule()

    """

    # Constants
    FM_URL: str
    FM_TIGGER_URL: str
    FM_OPTIMISATION_CONTEXT: dict
    FM_SCHEDULE_DURATION: str
    FM_USER_EMAIL: str
    FM_USER_PASSWORD: str
    MAX_NUMBER_OF_REATTEMPTS: int
    DELAY_FOR_INITIAL_ATTEMPT: int  # number of seconds
    DELAY_FOR_REATTEMPTS: int  # number of seconds
    TZ: timezone

    # A slack for the constraint_relaxation_window in minutes
    WINDOW_SLACK: int = 60

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

    async def initialize(self):
        self.log("Initializing FlexMeasuresClient")

        self.v2g_main_app = await self.get_app("v2g_liberty")

        self.fm_token = ""
        self.fm_busy_getting_schedule = False
        self.fm_date_time_last_schedule = await self.get_now()

        base_url = c.FM_SCHEDULE_URL + str(c.FM_ACCOUNT_POWER_SENSOR_ID)
        self.FM_URL = base_url + c.FM_SCHEDULE_SLUG
        self.FM_TIGGER_URL = base_url + c.FM_SCHEDULE_TRIGGER_SLUG
        self.FM_SCHEDULE_DURATION = self.args["fm_schedule_duration"]
        self.FM_USER_EMAIL = self.args["fm_user_email"]
        self.FM_USER_PASSWORD = self.args["fm_user_password"]
        self.DELAY_FOR_REATTEMPTS = int(self.args["delay_for_reattempts_to_retrieve_schedule"])
        self.MAX_NUMBER_OF_REATTEMPTS = int(self.args["max_number_of_reattempts_to_retrieve_schedule"])
        self.DELAY_FOR_INITIAL_ATTEMPT = int(self.args["delay_for_initial_attempt_to_retrieve_schedule"])
        self.TZ = pytz.timezone(self.get_timezone())

        # Add an extra attempt to prevent the last attempt not being able to finish.
        self.fm_max_seconds_between_schedules = \
            self.DELAY_FOR_REATTEMPTS * (self.MAX_NUMBER_OF_REATTEMPTS + 1) + self.DELAY_FOR_INITIAL_ATTEMPT

        if c.OPTIMISATION_MODE == "price":
            self.FM_OPTIMISATION_CONTEXT = {"consumption-price-sensor": c.FM_PRICE_CONSUMPTION_SENSOR_ID,
                                            "production-price-sensor": c.FM_PRICE_PRODUCTION_SENSOR_ID}
        else:
            # Assumed optimisation = emissions
            self.FM_OPTIMISATION_CONTEXT = {"consumption-price-sensor": c.FM_EMISSIONS_SENSOR_ID,
                                            "production-price-sensor": c.FM_EMISSIONS_SENSOR_ID}
        # self.log(f"Optimisation context: {self.FM_OPTIMISATION_CONTEXT}")
        self.authenticate_with_fm()

        # Ping every half hour. If offline a separate process will run to increase polling frequency.
        self.connection_error_counter = 0
        self.run_every(self.ping_server, "now", 30 * 60)
        self.handle_for_repeater = ""

        self.log("Completed initializing FlexMeasuresClient")

    async def ping_server(self, *args):
        """ Ping function to check if server is alive """
        url = c.FM_PING_URL

        res = requests.get(url)
        if res.status_code == 200:
            if self.connection_error_counter > 0:
                # There was an error before as the counter > 0
                # So a timer must be running, but it is not needed anymore, so cancel it.
                self.cancel_timer(self.handle_for_repeater)
                await self.v2g_main_app.handle_no_new_schedule("no_communication_with_fm", False)
            self.connection_error_counter = 0
        else:
            self.connection_error_counter += 1

        if self.connection_error_counter == 1:
            # A first error occurred, retry in every minute now
            self.handle_for_repeater = self.run_every(self.ping_server, "now+60", 60)
            self.log("No communication with FM! Increase tracking frequency.")
            await self.v2g_main_app.handle_no_new_schedule("no_communication_with_fm", True)

    def authenticate_with_fm(self):
        """Authenticate with the FlexMeasures server and store the returned auth token.

        Hint:
        the lifetime of the token is limited, so also call this method whenever the server returns a 401 status code.
        """
        self.log(f"Authenticating with FlexMeasures on URL '{c.FM_AUTHENTICATION_URL}'.")
        url = c.FM_AUTHENTICATION_URL
        res = requests.post(
            url,
            json=dict(
                email=self.FM_USER_EMAIL,
                password=self.FM_USER_PASSWORD,
            ),
        )
        self.check_deprecation_and_sunset(url, res)
        if not res.status_code == 200:
            self.log_failed_response(res, url)
        self.fm_token = res.json()["auth_token"]

    def log_failed_response(self, res, endpoint: str):
        """Log failed response for a given endpoint."""
        try:
            self.log(f"{endpoint} failed ({res.status_code}) with JSON response {res.json()}")
        except json.decoder.JSONDecodeError:
            self.log(f"{endpoint} failed ({res.status_code}) with response {res}")

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

    async def get_new_schedule(self, target_soc_kwh: float, target_datetime: datetime, current_soc_kwh: float, back_to_max_soc: datetime):
        """Get a new schedule from FlexMeasures.
           But not if still busy with getting previous schedule.
        Trigger a new schedule to be computed and set a timer to retrieve it, by its schedule id.
        Params:
        target_soc_kwh: if no calendar item is present use a SoC that represents 100%
        target_date_time: if no calendar item is present use target in weeks time. It must be snapped to sensor resolution.
        current_soc_kwh: a soc that is as close as possible to the actual state of charge
        back_to_max_soc: if current SoC > Max_SoC this setting informs the schedule when to be back at max soc. Can be None
        """
        # self.log(f"get_new_schedule called with car_soc: {current_soc_kwh}kWh ({type(current_soc_kwh)}, back_to_max:  {back_to_max_soc} ({type(back_to_max_soc)}.")

        now = datetime.now(tz=self.TZ)
        self.log(f"get_new_schedule: nu = {now.isoformat()}, ({type(now)}).")
        if self.fm_busy_getting_schedule:
            self.log(f"get_new_schedule self.fm_date_time_last_schedule = {self.fm_date_time_last_schedule.isoformat()}, ({type(self.fm_date_time_last_schedule)}).")
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
            target_soc_kwh=target_soc_kwh,
            target_datetime=target_datetime,
            current_soc_kwh=current_soc_kwh,
            back_to_max_soc=back_to_max_soc
        )
        # self.log(f"XYZXYZ id={schedule_id}")
        if schedule_id is None:
            self.log("Failed to trigger new schedule, schedule ID is None. Cannot call get_schedule")
            self.fm_busy_getting_schedule = False
            return

        # Set a timer to get the schedule a little later
        self.log(f"Attempting to get schedule (id={schedule_id}) in {self.DELAY_FOR_INITIAL_ATTEMPT} seconds")
        self.run_in(self.get_schedule, delay=self.DELAY_FOR_INITIAL_ATTEMPT, schedule_id=schedule_id)

    async def get_schedule(self, kwargs, **fnc_kwargs):
        """GET a schedule message that has been requested by trigger_schedule.
           The ID for this is schedule_id.
           Then store the retrieved schedule.

        Pass the schedule id using kwargs["schedule_id"]=<schedule_id>.
        """
        # Just to be sure also set this here, it's primary point for setting to true is in get_new_schedule
        self.fm_busy_getting_schedule = True

        schedule_id = kwargs["schedule_id"]
        url = self.FM_URL + schedule_id
        message = {
            "duration": self.FM_SCHEDULE_DURATION,
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
                await self.v2g_main_app.handle_no_new_schedule("timeouts_on_schedule", True)

            return

        self.log(f"GET schedule success: retrieved {res.status_code}")
        self.fm_busy_getting_schedule = False
        await self.v2g_main_app.handle_no_new_schedule("timeouts_on_schedule", False)

        self.fm_date_time_last_schedule = datetime.now(tz=self.TZ)
        self.log(f"get_schedule: self.fm_date_time_last_schedule set to now,"
                 f" {self.fm_date_time_last_schedule.isoformat()}, ({type(self.fm_date_time_last_schedule)}).")

        schedule = res.json()
        self.log(f"Schedule {schedule}")
        # To trigger state change we add the date to the state. State change is not triggered by attributes.
        self.set_state("input_text.chargeschedule",
                       state="ChargeScheduleAvailable" + self.fm_date_time_last_schedule.isoformat(),
                       attributes=schedule)

    async def trigger_schedule(self, *args, **fnc_kwargs) -> str:
        """Request a new schedule to be generated by calling the schedule triggering endpoint, while
        POSTing flex constraints.
        Return the schedule id for later retrieval of the asynchronously computed schedule.
        """

        # Prepare the SoC measurement to be sent along with the scheduling request
        current_soc_kwh = fnc_kwargs["current_soc_kwh"]
        target_soc_kwh = fnc_kwargs["target_soc_kwh"]
        target_datetime = fnc_kwargs["target_datetime"]
        back_to_max_soc = fnc_kwargs["back_to_max_soc"]
        self.log(f"trigger_schedule called with current_soc_kwh: {current_soc_kwh} kWh.")
        url = self.FM_TIGGER_URL


        resolution = timedelta(minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES)
        start_relaxation_window = target_datetime

        # The relaxation window is the period before a calendar item where no
        # soc_maxima should be sent to allow the schedule to reach a target higher
        # than the CAR_MAX_SOC_IN_KWH.
        if target_soc_kwh > c.CAR_MAX_SOC_IN_KWH:
            window_duration = math.ceil((target_soc_kwh - c.CAR_MAX_SOC_IN_KWH) / (c.CHARGER_MAX_CHARGE_POWER / 1000) * 60) + self.WINDOW_SLACK
            start_relaxation_window = time_round((target_datetime - timedelta(minutes=window_duration)), resolution)

        ######## Setting the soc_maxima ##########
        # The soc_maxima are used to set the boundaries for the charge schedule. They are set per interval (resolution),
        # and the schedule cannot go above them at that given interval.
        #
        # Assume:
        # CTM  = Charge Target Moment which is the start of the first upcoming calendar item.
        #        By default if there is no calendar item, the CTM is one week from now. This gives the
        #        schedule enough freedom for the coming 27 hours (total duration of the schedule).
        # SRW  = Start of the relaxation window for the CTM, including the slack of 1 hour.
        #        Only relevant for calendar items with a target SoC above the CAR_MAX_SOC_IN_KWH.
        #        Relaxation refers to the fact that in this window the schedule does not get soc-maxima so that
        #        it can charge above the CAR_MAX_SOC_IN_KWH to reach the higher target SoC.
        #        To keep things simple, the SRW is always based on CAR_MAX_SOC_IN_KWH, even if the current soc is higher.
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
        #    TODO: A drawback of the gradual approach is that there might be discharging with low power which usually is
        #          less efficient. So, if the trigger message could handle the concept "only discharge during this window"
        #          it would result in better schedules. This should then replace the gradually lowered soc_maxima.
        # 2. NOW < SRW < B2MS < CTM and NOW < SRW < CTM < B2MS
        #    In this case, the B2MS and CTM do not play a role. The soc-maxima are based on the current SoC and
        #    run from "now" up to SRW.
        # 3. SRW < NOW < B2MS < CTM and SRW < NOW < CTM < B2MS
        #    Here the priority is to reach the CTM and so not soc-maxima.
        #
        # Note that the situation where CTM < NOW is not relevant anymore and is covered by scenario 1.

        now = datetime.now(tz=self.TZ)
        rounded_now = time_round(now, resolution)
        soc_maxima = []

        if start_relaxation_window < rounded_now:
            # This is when the target SoC cannot be reached at the calendar-item_start,
            # Scenario 3.
            soc_maxima = []
            self.log("Strategy for soc_maxima: Priority for calendar target (Scenario 3), no soc_maxima.")
        else:
            back_to_max_soc = fnc_kwargs["back_to_max_soc"]
            if isinstance(back_to_max_soc, datetime):
                # There is a B2MS
                minimum_discharge_window = math.ceil((current_soc_kwh - c.CAR_MAX_SOC_IN_KWH) / (c.CHARGER_MAX_DIS_CHARGE_POWER / 1000) * 60)
                end_minimum_discharge_window = time_round((rounded_now - timedelta(minutes=minimum_discharge_window)), resolution)
                if end_minimum_discharge_window > back_to_max_soc:
                    # Scenario A.
                    back_to_max_soc = end_minimum_discharge_window
                
                self.log(f"trigger_schedule, back_to_max_soc: '{back_to_max_soc}'.")

                if back_to_max_soc >= start_relaxation_window:
                    # Scenario 2.
                    soc_maxima = [
                        {
                            "value": current_soc_kwh,
                            "datetime": dt.isoformat(),
                        } for dt in [rounded_now + x * resolution for x in range(0, (start_relaxation_window - rounded_now) // resolution) ]
                    ]
                    self.log("Strategy for soc_maxima: Maxima current_soc until Start of relaxation window (Scenario 2).")
                else:
                    # Scenario 1.
                    soc_maxima_higher_max_soc = []
                    number_of_steps = (back_to_max_soc - rounded_now) // resolution
                    if number_of_steps > 0:
                        step_kwh = (current_soc_kwh - c.CAR_MAX_SOC_IN_KWH) / number_of_steps
                        soc_maxima_higher_max_soc += [
                            {
                                "value": current_soc_kwh - (i * step_kwh),
                                "datetime": (rounded_now + i * resolution).isoformat()
                            } for i in range(number_of_steps)
                        ]
                    soc_maxima_original_max_soc = [
                        {
                            "value": c.CAR_MAX_SOC_IN_KWH,
                            "datetime": dt.isoformat(),
                        } for dt in [back_to_max_soc + x * resolution for x in range(0, (start_relaxation_window - back_to_max_soc) // resolution) ]
                    ]
                    soc_maxima = soc_maxima_higher_max_soc + soc_maxima_original_max_soc
                    self.log(f"Strategy for soc_maxima: Gradually decrease SoC to reach {c.CAR_MAX_SOC_IN_KWH}kWh (Scenario 1).")
            else:
                # Scenario 0.
                soc_maxima = [
                    {
                        "value": c.CAR_MAX_SOC_IN_KWH,
                        "datetime": dt.isoformat(),
                    } for dt in [rounded_now + x * resolution for x in range(0, (start_relaxation_window - rounded_now) // resolution) ]
                ]
                self.log(f"Strategy for soc_maxima: Maxima CAR_MAX_SOC_IN_KWH until Start of relaxation window (Scenario 0).")

        message = {
            "start": rounded_now.isoformat(),
            "flex-model": {
                "soc-at-start": current_soc_kwh,
                "soc-unit": "kWh",
                "soc-min": c.CAR_MIN_SOC_IN_KWH,
                "soc-max": c.CAR_MAX_CAPACITY_IN_KWH,
                "soc-minima": [
                    {
                        "value": target_soc_kwh,
                        "datetime": target_datetime.isoformat(),
                    }
                ],
                "soc-maxima": soc_maxima,
                "roundtrip-efficiency": c.CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY,
                "power-capacity": str(c.CHARGER_MAX_CHARGE_POWER) + "W"
            },
            "flex-context": self.FM_OPTIMISATION_CONTEXT,
        }

        res = requests.post(
            url,
            json=message,
            headers={"Authorization": self.fm_token},
        )

        tmp = str(message)
        self.log(f"Trigger_schedule on url '{url}', with message: '{tmp[0:275]} . . . . . {tmp[-275:]}'.")

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
            await self.v2g_main_app.handle_no_new_schedule("timeouts_on_schedule", True)
            return None

        self.log(f"Successfully triggered schedule. Schedule id: {schedule_id}")
        await self.v2g_main_app.handle_no_new_schedule("timeouts_on_schedule", False)
        return schedule_id

    async def try_solve_authentication_error(self, res, url, fnc, *fnc_args, **fnc_kwargs):
        if fnc_kwargs.get("retry_auth_once", True) and res.status_code == 401:
            self.log(f"Call to {url} failed on authorization (possibly the token expired); attempting to "
                     f"reauthenticate once.")
            self.authenticate_with_fm()
            fnc_kwargs["retry_auth_once"] = False
            await fnc(*fnc_args, **fnc_kwargs)