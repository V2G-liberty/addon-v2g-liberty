from datetime import datetime, timedelta
import isodate
import math
import constants as c
import log_wrapper
from .v2g_globals import time_round, get_local_now
from .time_slot_util import generate_time_slots
from appdaemon.plugins.hass.hassapi import Hass


class FMClient:
    """This class manages the communication with the FlexMeasures platform, which delivers the
    charging schedules.
    - Saves charging schedule locally (sensor.charge_schedule)
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
    fm_busy_getting_schedule: bool = False
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

    # For calling handle_no_new_schedule
    v2g_main_app: object
    hass: Hass = None

    client: object  # Should be FlexMeasuresClient but (early) import statement gives errors..

    def __init__(self, hass: Hass):
        self.hass = hass
        self.__log = log_wrapper.get_class_method_logger(hass.log)

        # Maybe add these to constants / settings?
        self.FM_SCHEDULE_DURATION_STR = "PT27H"
        self.FM_SCHEDULE_DURATION = isodate.parse_duration(
            self.FM_SCHEDULE_DURATION_STR
        )
        self.DELAY_FOR_REATTEMPTS = 6
        self.MAX_NUMBER_OF_REATTEMPTS = 15
        self.DELAY_FOR_INITIAL_ATTEMPT = 20
        self.WINDOW_SLACK_IN_MINUTES = 60

        # Add an extra attempt to prevent the last attempt not being able to finish.
        self.fm_max_seconds_between_schedules = (
            self.DELAY_FOR_REATTEMPTS * (self.MAX_NUMBER_OF_REATTEMPTS + 1)
            + self.DELAY_FOR_INITIAL_ATTEMPT
        )

        # Ping every half hour. If offline, a separate process will run to increase frequency.
        self.connection_error_counter = 0
        # self.run_every(self.ping_server, "now", 30 * 60)
        self.handle_for_repeater = ""

    async def test_fm_connection(self, host_url, username, password):
        """Test if we can connect with given FlexMeasures host and port.
        Used from UI dialog flow.

        Args:
            host_url (str): FlexMeasures URL
            username (str): username (e-mail address)
            password (str): password

        Raises:
            ve: Value Error
            eve: Email Validation Error

        Returns:
            assets (list): list of asset names
        """

        # TODO: Fix this
        from flexmeasures_client import FlexMeasuresClient
        from flexmeasures_client.exceptions import EmailValidationError

        host, ssl = get_host_and_ssl_from_url(host_url)
        self.__log("host: '{host}', ssl: '{ssl}'.")

        try:
            client = FlexMeasuresClient(
                host=host,
                email=username,
                password=password,
                ssl=ssl,
            )
        except ValueError as ve:
            self.__log("CLIENT ERROR: {ve}.", level="WARNING")
            # ValueErrors:
            # 'xxx' is not an email address format string (= also for empty email)
            # password cannot be empty
            raise ve
        except EmailValidationError as eve:
            self.__log("CLIENT ERROR: {eve}.", level="WARNING")
            raise eve

        self.__log("successfully connect to flexmeasures")
        try:
            assets = await client.get_assets()
            await self.set_fm_connection_status(connected=True)
            return assets
        except Exception as e:
            self.__log(
                f"Could not get assets from fm_client. Exception: {e}.", level="WARNING"
            )
            await self.set_fm_connection_status(connected=False)
            raise e
        finally:
            await client.close()

    async def initialise_and_test_fm_client(self) -> str:
        """Initialise at startup"""
        self.__log("called")
        # Unusual place for the import, but it has to be in an async method otherwise it errors out
        # with problems with the async loop.
        from flexmeasures_client import FlexMeasuresClient
        from flexmeasures_client.exceptions import EmailValidationError

        self.fm_token = ""
        self.fm_busy_getting_schedule = False
        self.fm_date_time_last_schedule = get_local_now()

        host, ssl = get_host_and_ssl_from_url(c.FM_BASE_URL)
        self.__log(f"host: '{host}', ssl: '{ssl}'.")
        try:
            self.client = FlexMeasuresClient(
                host=host,
                email=c.FM_ACCOUNT_USERNAME,
                password=c.FM_ACCOUNT_PASSWORD,
                ssl=ssl,
            )
        except ValueError as ve:
            self.__log(f"CLIENT ERROR: {ve}.", level="WARNING")
            # ValueErrors:
            # 'xxx' is not an email address format string (= also for empty email)
            # password cannot be empty
            return ve
        except EmailValidationError as eve:
            self.__log(f"CLIENT ERROR: {eve}.", level="WARNING")
            return eve

        self.__log("successfully initialised flexmeasures client")

        try:
            self.__log("getting access token...")
            await self.client.get_access_token()
        except ValueError as ve:
            self.__log(f"V2G ERROR: {ve}")
            # ValueErrors:
            # User with email 'xxx' does not exist
            # User password does not match
            return ve
        except ConnectionError as ce:
            self.__log(f"CLIENT ERROR: {ce}")
            # CommunicationErrors:
            # Error occurred while communicating with the API
            # = for invalid URL and none-fm-url
            return "Communication error. Wrong URL?"

        if self.client.access_token is None:
            self.__log("access token is None", level="WARNING")
            return "Unknown error with FlexMeasures"
        self.__log(
            f"access token: {self.client.access_token}, returning 'Successfully connected'."
        )

        await self.set_fm_connection_status(connected=True)
        return "Successfully connected"

    async def get_fm_sensors_by_asset_name(self, asset_name: str):
        assets = await self.client.get_assets()
        for asset in assets:
            if asset["name"] == asset_name:
                sensors = [sensor for sensor in asset["sensors"]]
                return sensors
        return []

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
    #     self.__log("No communication with FM! Increase tracking frequency.")
    #     await self.v2g_main_app.handle_no_new_schedule("no_communication_with_fm", True)

    async def get_sensor_data(
        self,
        sensor_id: int,
        start: str | datetime,
        duration: str | timedelta,
        uom: str,
        resolution: str | timedelta,
    ) -> dict:
        """
        :param sensor_id: fm sensor id
        :param start:
        :param duration:
        :param uom: unit of measurement
        :param resolution:
        :return: sensor data as dictionary, for example:
                {
                    'values': [2.15, 3, 2],
                    'start': '2015-06-02T10:00:00+00:00',
                    'duration': 'PT45M',
                    'unit': 'MW'
                }
        """
        self.__log(f"called for sensor_id: {sensor_id}.")
        try:
            res = await self.client.get_sensor_data(
                sensor_id=sensor_id,
                start=start,
                duration=duration,
                unit=uom,
                resolution=resolution,
            )
        except Exception as e:
            # ContentTypeError, ValueError, timeout, no data??:
            self.__log(
                f"for sensor_id: '{sensor_id}', start: '{start}', duration: '{duration}', unit: "
                f"'{uom}',  resolution: '{resolution}' failed, client returned exception: '{e}'."
            )
            return None

        return res

    async def post_measurements(
        self,
        sensor_id: int,
        values: list[float],
        start: datetime,
        duration: str,
        uom: str,
    ) -> bool:
        """General function to post data to FM.

        Args:
            sensor_id (str): FM sensor id that the measurements get posted to
            values (list): list of values to send
            start (datetime): start date-time of first value
            duration (str): duration for which the values are relevant in iso format e.g. PT8H45M
            uom (str): unit of measure, eg MW, EUR/kWh, etc.

        Returns:
            bool: weather or not sending was successful
        """
        self.__log("post_measurements called.")
        if len(values) == 0:
            self.__log(
                f"value list 0 length, not sending data to sensor_id '{sensor_id}'."
            )
            return False

        try:
            await self.client.post_measurements(
                sensor_id=sensor_id,
                values=values,
                start=start,
                duration=duration,
                unit=uom,
            )
        except Exception as e:
            # ContentTypeError, ValueError, timeout??:
            self.__log(
                f"failed | sensor_id: '{sensor_id}', values: '{values}', start: '{start}', "
                f"duration: '{duration}', unit: '{uom}', fm_client returned exception: '{e}'."
            )
            await self.set_fm_connection_status(connected=False)
            return False

        return True

    async def get_new_schedule(
        self, targets: list, current_soc_kwh: float, back_to_max_soc: datetime
    ):
        """Get a new schedule from FlexMeasures.
        But not if still busy with getting previous schedule.
        Trigger a new schedule to be computed and set a timer to retrieve it, by its schedule id.
        Params:
        targets: a list of targets (=dict with start, end, soc)
        back_to_max_soc: if current SoC > Max_SoC this setting informs the schedule when to be back
                         at max soc. Can be None.
        """
        self.__log("called.")
        now = get_local_now()
        if self.fm_busy_getting_schedule:
            self.__log(
                f"busy with prior request since: {self.fm_date_time_last_schedule.isoformat()}."
            )
            seconds_since_last_schedule = int(
                (now - self.fm_date_time_last_schedule).total_seconds()
            )
            if seconds_since_last_schedule > self.fm_max_seconds_between_schedules:
                self.__log(
                    f"Retrieving previous schedule is taking too long "
                    f"({seconds_since_last_schedule} sec.), assuming call got 'lost'. "
                    f"Getting new schedule."
                )
            else:
                self.__log(
                    "Not getting new schedule, still processing previous request."
                )
                return
        else:
            self.__log("Was not busy getting schedule, but i am now!")

        # This has to be set here instead of in get_schedule because that function is called with a
        # delay and during this delay this get_new_schedule could be called.
        self.fm_busy_getting_schedule = True

        rounded_now = time_round(now, c.EVENT_RESOLUTION)

        # The schedule duration, usually just over a day long.
        schedule_end = time_round(
            rounded_now + self.FM_SCHEDULE_DURATION, c.EVENT_RESOLUTION
        )

        # Add a placeholder target with soc CAR_MAX_SOC_IN_KWH (usually ≈80%) one week from now.
        # FM needs this to be able to produce a schedule whereby the influence of this placeholder
        # is none. Added to the soc_minima later on
        end_of_schedule_input_period = rounded_now + timedelta(days=7)

        soc_minima = []

        # TODO: Add "soc-usage": soc_usage
        # soc_usage = []
        # The basis usage is 0 and can be lifted by adding other usage
        # soc_usage.append({
        #     "value": 0,
        #     "start": rounded_now,
        #     "end": schedule_end,
        # })

        # The schedule should not take into account that during calendar items it cannot
        # charge/discharge.
        # Further, if a b2ms is set, the schedule should not charge (but can dis-charge) until b2ms.
        # These power capacities are collected in the max_consumption_power_ranges and
        # idle_ranges_production.
        max_consumption_power_ranges = [
            {
                "value": c.CHARGER_MAX_CHARGE_POWER,
                "start": rounded_now,
                "end": schedule_end,
            }
        ]
        max_production_power_ranges = [
            {
                "value": c.CHARGER_MAX_DISCHARGE_POWER,
                "start": rounded_now,
                "end": schedule_end,
            }
        ]

        if targets is not None:
            # TODO: Add "soc-usage"
            # usage_per_event_time_interval = f"{str(c.USAGE_PER_EVENT_TIME_INTERVAL)} kW"

            ##################################
            #    Make a list of soc_minima   #
            ##################################
            self.__log("start generating soc_minima")
            for target in targets:
                target_start = target["start"]
                target_end = target["end"]
                # Only add targets with a start in the schedule duration
                if target_start > schedule_end:
                    # Assuming the targets list is sorted we can break (instead of continue)
                    break
                target_soc_kwh = target["target_soc_kwh"]
                soc_minima.append(
                    {
                        "value": target_soc_kwh,
                        "start": target_start,
                        "end": target_end,
                    }
                )
                max_consumption_power_ranges.append(
                    {
                        "value": 0,
                        "start": target_start,
                        "end": target_end,
                    }
                )
                max_production_power_ranges.append(
                    {
                        "value": 0,
                        "start": target_start,
                        "end": target_end,
                    }
                )

                # TODO: soc_usage
                # soc_usage.append({
                #     "value": c.USAGE_PER_EVENT_TIME_INTERVAL,
                #     "start": target_start,
                #     "end": target_end,
                # })
            # -- End for target in targets --

            # Always add a future target for scheduling to be made possible.
            soc_minima.append(
                {
                    "value": c.CAR_MAX_SOC_IN_KWH,
                    "start": end_of_schedule_input_period - c.EVENT_RESOLUTION,
                    "end": end_of_schedule_input_period,
                }
            )
            # Remove any overlap and use the maximum in overlapping periods.
            soc_minima = consolidate_time_ranges(soc_minima)

            # TODO: soc_usage
            # soc_usage = self.consolidate_time_ranges(soc_usage)

            ##################################
            #   Make a list of soc_maxima    #
            ##################################
            self.__log("start generating soc_maxima")

            # The basis maxima that can be lifted by adding other maxima
            soc_maxima = [
                {
                    "value": c.CAR_MAX_SOC_IN_KWH,
                    "start": rounded_now,
                    "end": end_of_schedule_input_period,
                }
            ]

            first_b2ms_reset_moment = schedule_end
            for soc_minimum in soc_minima:
                # Does this target need a relaxation window? This is the period before a calendar
                # item where soc_maxima should be set to the value target_soc_kwh to allow the
                # schedule to reach a target higher than the CAR_MAX_SOC_IN_KWH.
                soc_minimum_start = soc_minimum["start"]
                soc_minimum_end = soc_minimum["end"]
                minimum_soc_kwh = soc_minimum["value"]

                if minimum_soc_kwh > c.CAR_MAX_SOC_IN_KWH:
                    window_duration = (
                        math.ceil(
                            (minimum_soc_kwh - c.CAR_MAX_SOC_IN_KWH)
                            / (c.CHARGER_MAX_CHARGE_POWER / 1000)
                            * 60
                        )
                        + self.WINDOW_SLACK_IN_MINUTES
                    )
                    self.__log("window_duration: {window_duration}.")
                    # srw = start_relaxation_window, erw = end_relaxation_window
                    srw = time_round(
                        (soc_minimum_start - timedelta(minutes=window_duration)),
                        c.EVENT_RESOLUTION,
                    )
                    erw = time_round(
                        (soc_minimum_end + timedelta(minutes=window_duration)),
                        c.EVENT_RESOLUTION,
                    )
                    if srw < rounded_now:
                        # This is when the target SoC cannot be reached at the calendar-item_start,
                        # Scenario 3.
                        srw = rounded_now
                        # In this case it is never relevant to go back to max_soc
                        back_to_max_soc = None
                        self.__log(
                            "strategy for soc_maxima: Priority for calendar target (Scenario 3)."
                        )
                    if srw < first_b2ms_reset_moment:
                        first_b2ms_reset_moment = srw
                    self.__log(f"start relaxation window (srw): {srw}.")

                    soc_maxima.append(
                        {
                            "value": minimum_soc_kwh,
                            "start": srw,
                            "end": erw,
                        }
                    )
                self.__log(
                    f"soc_minima processed - "
                    f"first_b2ms_reset_moment: {first_b2ms_reset_moment.isoformat()}."
                )
            # -- End for soc_minimum in soc_minima --
        # -- End if targets not None --

        # Range where schedule should only discharge. This when the SoC is above the max (80%).
        # This can be due to returning home and connection with a high SoC or when the car did not
        # get disconnected during a reservation with a target > max_soc and the calendar item is
        # dismissed. It is only added at the start (now) and not after future targets as we expect
        # these to lead to a SoC below the max as the car is used for driving and thus losing SoC.

        # If the current_soc is above the CAR_MAX_SOC_IN_KWH it need to be brought back below this
        # max. The back_to_max_soc parameter indicates when this task should be finished.
        #
        # Assume:
        # SRW  = Start of the relaxation window for the CTM, including the slack of 1 hour.
        #        Only relevant for calendar items with a target SoC above the CAR_MAX_SOC_IN_KWH.
        #        Relaxation refers to the fact that in this window the schedule does not get
        #        soc-maxima so that it can charge above the CAR_MAX_SOC_IN_KWH to reach the higher
        #        target SoC. To keep things simple, the SRW is always based on CAR_MAX_SOC_IN_KWH,
        #        even if the current soc is higher.
        # CTM  = Charge Target Moment which is the start of the first upcoming calendar item.
        #        By default, if there is no calendar item, the CTM is one week from now. This gives
        #        the schedule enough freedom for the coming 27 hours (total schedule duration).
        # B2MS = The datetime at which the ALLOWED_DURATION_ABOVE_MAX_SOC ends, it cannot be in the
        #        past. It serves as a target with a maximum SoC (where regular targets have a
        #        minimum). The CTM has a higher priority than the B2MS.
        # EMDW = End of Minimum Discharge Window. Minimum Discharge Window (MDW) = time needed to
        #        discharge from current SoC to CAR_MAX_SOC_IN_KWH with available discharge power.
        #        EMDW = Now + MDW.
        #        Scenario A: In case of EMDW > B2MS then the latter is extended to EMDW.
        #
        # These scenarios need to be handled, they might in time flow from one into the other:
        # 0. No B2MS
        #    The soc-maxima are based on the CAR_MAX_SOC_IN_KWH and run from "now" up to SRW.
        # 1. NOW < B2MS < SRW < CTM
        #    The B2MS is not influenced by the first calendar item (or there is none)
        #    The SoC maxima are based upon the CURRENT_SOC and run from "now" up to B2MS, from where
        #    they are set to CAR_MAX_SOC_IN_KWH. Furthermore, the schedule should not charge during
        #    this period. So this period should be added to the max_consumption_power_ranges.
        # 2. NOW < SRW < B2MS < CTM and NOW < SRW < CTM < B2MS
        #    In this case, the B2MS and CTM do not play a role. The soc-maxima are based on the
        #    current SoC and run from "now" up to SRW.
        # 3. SRW < NOW < B2MS < CTM and SRW < NOW < CTM < B2MS
        #    Here the priority is to reach the CTM and so do not set soc-maxima.
        #
        # The situation where CTM < NOW is not relevant anymore and is covered by scenario 1.

        if back_to_max_soc is not None and isinstance(back_to_max_soc, datetime):
            self.__log(f"back_to_max_soc: '{back_to_max_soc}'.")

            # Postpone discharge till after current calendar item if target soc has not been
            # fulfilled.
            start_b2ms = None
            if targets is not None and len(targets) > 0:
                first_target = targets[0]
                start = first_target.get("start", None)
                end = first_target.get("end", None)
                if start < rounded_now < end:
                    target_soc_kwh = first_target.get("target_soc_kwh", None)
                    if target_soc_kwh is not None and target_soc_kwh >= current_soc_kwh:
                        start_b2ms = end
            if start_b2ms is None:
                start_b2ms = rounded_now

            # Handle too high current_soc.
            minimum_discharge_window = math.ceil(
                (current_soc_kwh - c.CAR_MAX_SOC_IN_KWH)
                / (c.CHARGER_MAX_DISCHARGE_POWER / 1000)
                * 60
            )
            end_minimum_discharge_window = time_round(
                (start_b2ms - timedelta(minutes=minimum_discharge_window)),
                c.EVENT_RESOLUTION,
            )
            if end_minimum_discharge_window > back_to_max_soc:
                # Scenario A.
                back_to_max_soc = end_minimum_discharge_window

            if back_to_max_soc >= first_b2ms_reset_moment:
                # Scenario 2.
                soc_maxima.append(
                    {
                        "value": current_soc_kwh,
                        "start": rounded_now,
                        "end": first_b2ms_reset_moment,
                    }
                )
                self.__log(
                    "strategy for soc_maxima: "
                    "Maxima current_soc until Start of relaxation window (Scenario 2)."
                )
            else:
                # Scenario 1:
                soc_maxima.append(
                    {
                        "value": current_soc_kwh,
                        "start": rounded_now,
                        "end": back_to_max_soc,
                    }
                )
                max_consumption_power_ranges.append(
                    {
                        "value": 0,
                        "start": rounded_now,
                        "end": back_to_max_soc,
                    }
                )
        # -- End if back_to_max_soc is not None and isinstance(back_to_max_soc, datetime) --

        soc_maxima = consolidate_time_ranges(soc_maxima)
        soc_maxima = convert_dates_to_iso_format(soc_maxima)

        soc_minima = convert_dates_to_iso_format(soc_minima)

        max_consumption_power_ranges = consolidate_time_ranges(
            max_consumption_power_ranges, min_or_max="min"
        )
        max_consumption_power_ranges = add_unit_to_values(
            max_consumption_power_ranges, unit="W"
        )
        max_consumption_power_ranges = convert_dates_to_iso_format(
            max_consumption_power_ranges
        )

        max_production_power_ranges = consolidate_time_ranges(
            max_production_power_ranges, min_or_max="min"
        )
        max_production_power_ranges = add_unit_to_values(
            max_production_power_ranges, unit="W"
        )
        max_production_power_ranges = convert_dates_to_iso_format(
            max_production_power_ranges
        )

        # TODO: convert soc_usage
        # soc_usage = convert_dates_to_iso_format(soc_usage)

        # TODO: Add "soc-usage"
        flex_model = {
            "soc-at-start": current_soc_kwh,
            "soc-unit": "kWh",
            "soc-min": c.CAR_MIN_SOC_IN_KWH,
            "soc-max": c.CAR_MAX_CAPACITY_IN_KWH,
            "soc-minima": soc_minima,
            "soc-maxima": soc_maxima,
            "roundtrip-efficiency": c.ROUNDTRIP_EFFICIENCY_FACTOR,
            "consumption-capacity": max_consumption_power_ranges,
            "production-capacity": max_production_power_ranges,
        }
        self.__log(f"flex_model: {flex_model}.")
        schedule = {}
        try:
            schedule = await self.client.trigger_and_get_schedule(
                sensor_id=c.FM_ACCOUNT_POWER_SENSOR_ID,
                duration=self.FM_SCHEDULE_DURATION_STR,
                start=rounded_now.isoformat(),
                flex_model=flex_model,
                flex_context=c.FM_OPTIMISATION_CONTEXT,
            )
        except Exception as e:
            # ContentTypeError, ValueError, timeout??:
            self.__log(
                f"failed to get schedule, client returned exception: {e}.",
                level="WARNING",
            )
            self.fm_busy_getting_schedule = False
            await self.v2g_main_app.handle_no_new_schedule("timeouts_on_schedule", True)
            return

        self.fm_busy_getting_schedule = False

        if schedule == {}:
            self.__log("schedule is empty")
            await self.v2g_main_app.handle_no_new_schedule("timeouts_on_schedule", True)
            return

        await self.v2g_main_app.handle_no_new_schedule("timeouts_on_schedule", False)
        self.fm_date_time_last_schedule = get_local_now()
        self.__log(f"schedule: {schedule}")

        # To trigger state change we add the date to the state. State change is not triggered by
        # attributes.
        await self.hass.set_state(
            entity_id="sensor.charge_schedule",
            state="Charge schedule available "
            + self.fm_date_time_last_schedule.isoformat(),
            attributes=schedule,
        )
        await self.set_fm_connection_status(connected=True)

    async def set_fm_connection_status(self, connected: bool, error_message: str = ""):
        """Helper to set fm connection status in HA entity"""
        if connected:
            state = "Successfully connected"
        else:
            if error_message != "":
                state = error_message
            else:
                state = "Error"
            self.__log(f"Could not connect to FM: '{state}'.")

        # Force a changed trigger even if the state does not change
        keep_alive = {"keep_alive": get_local_now().strftime(c.DATE_TIME_FORMAT)}

        await self.hass.set_state(
            "sensor.fm_connection_status", state=state, attributes=keep_alive
        )


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


# See separate unit tests
def consolidate_time_ranges(ranges, min_or_max: str = "max"):
    """
    Make ranges non-overlapping and, for the overlapping parts, use min/max value from the ranges.

    :param ranges: dicts with start(datetime), end(datetime) and value (int)
                   The start and end must be snapped to the resolution.
    :param min_or_max: str, "min" or "max" (default) to indicate if the minimum or maximum values
                       should be used for the overlapping parts.
    :return: a list of dicts with start(datetime), end(datetime) and value (int) that are
             none-overlapping, but possibly 'touching' (end of A = start of B).

    Note!
    It is not possible yet to correctly process non-overlapping ranges with a one-resolution
    distance. As this is very rare in this context, and it's impact relatively small it has not been
    solved yet and accepted as a not-perfect output.
    """
    if len(ranges) == 0:
        return []
    elif len(ranges) == 1:
        return ranges

    generated_slots = generate_time_slots(ranges, c.EVENT_RESOLUTION)
    return __combine_time_slots(generated_slots, min_or_max=min_or_max)


# See separate unit tests
def __combine_time_slots(time_slots: dict, min_or_max: str = "max"):
    """
    Merges time slots into ranges with a constant value. The value to use is based on min_or_max
    parameter.

    :param time_slots: dict, with the format:
                       key: [min, max] where key is a datetime and min/max are int values
    :param min_or_max: str, "min" or "max" (default) to indicate if the minimum or maximum values
                       should be used for the overlapping parts.
    :return: dicts with start(datetime), end(datetime) and value (int) that are none-overlapping,
             but possibly 'touching' (end of A = start of B).
    """

    combined_ranges = []
    sorted_times = sorted(time_slots.keys())

    min_max_index = 1 if min_or_max == "max" else 0

    # Initialize the first time slot
    current_range_start = sorted_times[0]

    # Choose the first value based on min or max
    current_range_value = time_slots[current_range_start][min_max_index]

    for i in range(1, len(sorted_times)):
        current_time = sorted_times[i]
        expected_time = sorted_times[i - 1] + c.EVENT_RESOLUTION

        # Determine the current value based on min or max
        time_slot_value = time_slots[current_time][min_max_index]

        # If there's a break in the range times, close the current range
        if current_time != expected_time:
            combined_ranges.append(
                {
                    "start": current_range_start,
                    "end": sorted_times[i - 1],
                    "value": current_range_value,
                }
            )
            # Start a new range
            current_range_start = current_time
            current_range_value = time_slot_value

        # If there's a break in the range value changes, close the current range
        elif time_slot_value != current_range_value:
            range_end_time = current_time
            if (min_or_max != "max" and time_slot_value > current_range_value) or (
                min_or_max == "max" and time_slot_value < current_range_value
            ):
                range_end_time = sorted_times[i - 1]

            combined_ranges.append(
                {
                    "start": current_range_start,
                    "end": range_end_time,
                    "value": current_range_value,
                }
            )
            # Start a new range
            current_range_start = range_end_time
            current_range_value = time_slot_value

    # Add the last range
    combined_ranges.append(
        {
            "start": current_range_start,
            "end": sorted_times[-1],
            "value": current_range_value,
        }
    )

    return combined_ranges


def convert_dates_to_iso_format(data):
    for entry in data:
        dts = entry.get("start", None)
        if dts is not None and isinstance(dts, datetime):
            entry["start"] = dts.isoformat()
        dte = entry.get("end", None)
        if dte is not None and isinstance(dte, datetime):
            entry["end"] = dte.isoformat()
    return data


def add_unit_to_values(data, unit: str):
    for entry in data:
        value = entry.get("value", None)
        if value is not None:
            entry["value"] = f"{value} {unit}"
    return data
