from datetime import datetime, timedelta
from pyee.asyncio import AsyncIOEventEmitter
import isodate
import math
import constants as c
import log_wrapper
from v2g_globals import time_round, time_ceil, get_local_now
from time_range_util import (
    consolidate_time_ranges,
    convert_dates_to_iso_format,
    add_unit_to_values,
)
from appdaemon.plugins.hass.hassapi import Hass
from event_bus import EventBus


class FMClient(AsyncIOEventEmitter):
    """This class manages the communication with the FlexMeasures platform, which delivers the
    charging schedules.
    - Saves charging schedule locally (sensor.charge_schedule)
    - Reports on errors via v2g_liberty module handle_no_schedule()
    """

    event_bus: EventBus = None

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
    hass: Hass = None
    client: object  # Should be FlexMeasuresClient but (early) import statement gives errors..

    def __init__(self, hass: Hass, event_bus: EventBus):
        super().__init__()
        self.hass = hass
        self.__log = log_wrapper.get_class_method_logger(hass.log)

        self.event_bus = event_bus

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

    async def log_version(self, v2g_liberty_version: str):
        """Log V2G Liberty version number in the asset on FlexMeasures"""
        asset_id = await self.__get_asset_id_by_name(c.FM_ASSET_NAME)

        await self.__set_asset_attribute(
            asset_id=asset_id,
            attribute_name="v2g-liberty-version",
            attribute_value=v2g_liberty_version,
        )

    async def __set_asset_attribute(
        self, asset_id: int, attribute_name: str, attribute_value: str
    ):
        """Set attribute on asset in FM"""
        if attribute_name is None or asset_id is None:
            self.__log(
                f"{asset_id=} or {attribute_name=} is None, abort.", level="WARNING"
            )
            return

        inner_attributes = dict({attribute_name: attribute_value})
        asset_attributes = dict(attributes=inner_attributes)

        try:
            res = await self.client.update_asset(asset_id, asset_attributes)
        except Exception as e:
            self.__log(
                f"Update for {asset_id=}, {attribute_name=}, {attribute_value=} "
                f"failed, client returned exception: '{e}'.",
                level="WARNING",
            )

    async def __get_asset_id_by_name(self, asset_name: str):
        # TODO: The asset_id is known already at first configuration time so could be stored then.
        # See globals module where c.FM_ASSET_NAME is set.
        assets = await self.client.get_assets()
        for asset in assets:
            if asset["name"] == asset_name:
                return asset["id"]
        return None

    async def get_fm_sensors_by_asset_name(self, asset_name: str):
        assets = await self.client.get_assets()
        for asset in assets:
            if asset["name"] == asset_name:
                sensors = [sensor for sensor in asset["sensors"]]
                return sensors
        return []

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

        param: targets (list) a list of targets (=dict with start, end, soc)
        param: current_soc_kwh (float), the state of charge at the moment the schedule is requested
        param: back_to_max_soc (datetime) if current SoC > Max_SoC this setting informs the schedule
               when to be back at max soc. Can be None.
        """
        self.__log("called.")
        now = get_local_now()
        if self.client is None:
            # We are assuming this is a temporary situation and will be resolved soon,
            # not notifying the user.
            self.__log(
                "Abort getting schedule, client not initialised (yet).", level="WARNING"
            )
            return

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

        # Always add a placeholder target with soc CAR_MAX_SOC_IN_KWH one week from now.
        # FM needs this to be able to produce a schedule whereby the influence of this placeholder
        # is none.
        end_of_schedule_input_period = rounded_now + timedelta(days=7)
        soc_minima = [
            {
                "value": c.CAR_MAX_SOC_IN_KWH,
                "start": end_of_schedule_input_period - c.EVENT_RESOLUTION,
                "end": end_of_schedule_input_period,
            }
        ]

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
            ########################################################################################
            #    Make a list of soc_minima, max_power_ranges for consumption and production        #
            ########################################################################################
            self.__log("start generating soc_minima")
            for i, target in enumerate(targets):
                target_start = target["start"]
                target_end = target["end"]
                # Only add targets with a start in the schedule duration
                if target_start > schedule_end:
                    # Assuming the targets list is sorted we can break (instead of continue)
                    break
                target_soc_kwh = target["target_soc_kwh"]

                if i == 0:
                    # Only for the first v2g_event check if the target soc can be reached in time.
                    delta_to_target = target_soc_kwh - current_soc_kwh
                    if delta_to_target > 0:
                        min_charge_time = math.ceil(
                            delta_to_target
                            / (c.ROUNDTRIP_EFFICIENCY_FACTOR**0.5)
                            / (c.CHARGER_MAX_CHARGE_POWER / 1000)
                            * 60
                        )
                        soonest_at_target = time_ceil(
                            rounded_now + timedelta(minutes=min_charge_time),
                            c.EVENT_RESOLUTION,
                        )
                        if soonest_at_target > target_start:
                            # Target soc cannot be reached at start of v2g_event,
                            # relax the start time.
                            virtual_target_end = target_end - c.EVENT_RESOLUTION
                            max_target = None
                            if virtual_target_end < soonest_at_target:
                                # Target % cannot be reached within duration of v2g_event,
                                # relax the target %.
                                soonest_at_target = virtual_target_end
                                # Calculate highest possible target soc
                                minutes_left_to_charge = (
                                    virtual_target_end - rounded_now
                                ).total_seconds() / 60
                                self.__log(
                                    f"minutes_left_to_charge: '{minutes_left_to_charge}'."
                                )
                                max_target = current_soc_kwh + (
                                    minutes_left_to_charge
                                    / 60
                                    * c.ROUNDTRIP_EFFICIENCY_FACTOR**0.5
                                    * (c.CHARGER_MAX_CHARGE_POWER / 1000)
                                )
                                target_soc_kwh = max_target
                                # Communicate the target soc to user in %
                                max_target = int(
                                    round(
                                        (max_target / c.CAR_MAX_CAPACITY_IN_KWH) * 100,
                                        0,
                                    )
                                )
                            target_start = soonest_at_target
                            self.emit(
                                "unreachable_target",
                                soonest_at_target=soonest_at_target,
                                max_target=max_target,
                            )
                            await self.wait_for_complete()

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
            # -- End for target in targets --

            # Remove any overlap and use the maximum in overlapping periods.
            soc_minima = consolidate_time_ranges(soc_minima, c.EVENT_RESOLUTION)

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
                    self.__log("window_duration: {window_duration} minutes.")
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

        soc_maxima = consolidate_time_ranges(soc_maxima, c.EVENT_RESOLUTION)
        soc_maxima = convert_dates_to_iso_format(soc_maxima)

        soc_minima = convert_dates_to_iso_format(soc_minima)

        max_consumption_power_ranges = consolidate_time_ranges(
            max_consumption_power_ranges, c.EVENT_RESOLUTION, min_or_max="min"
        )
        max_consumption_power_ranges = add_unit_to_values(
            max_consumption_power_ranges, unit="W"
        )
        max_consumption_power_ranges = convert_dates_to_iso_format(
            max_consumption_power_ranges
        )

        max_production_power_ranges = consolidate_time_ranges(
            max_production_power_ranges, c.EVENT_RESOLUTION, min_or_max="min"
        )
        max_production_power_ranges = add_unit_to_values(
            max_production_power_ranges, unit="W"
        )
        max_production_power_ranges = convert_dates_to_iso_format(
            max_production_power_ranges
        )

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
        max_retries = 2
        for attempt in range(max_retries + 1):
            # Preferably the retry mechanism would be incorporated in the flexmeasures_client.
            # But this seems to make the system much more relyable so it is implemented here
            # until the fm client implements it.
            try:
                schedule = await self.client.trigger_and_get_schedule(
                    sensor_id=c.FM_ACCOUNT_POWER_SENSOR_ID,
                    duration=self.FM_SCHEDULE_DURATION_STR,
                    start=rounded_now.isoformat(),
                    flex_model=flex_model,
                    flex_context=c.FM_OPTIMISATION_CONTEXT,
                )
                break
            except Exception as e:
                if attempt < max_retries:
                    self.__log(
                        f"trigger_and_get_schedule attempt {attempt + 1} failed, retrying. "
                        f"Client exception: {e}.",
                        level="WARNING",
                    )
                    await self.hass.sleep(1)
                else:
                    self.__log(
                        f"trigger_and_get_schedule failed after {attempt + 1} attempts. "
                        f"Client exception: {e}.",
                        level="WARNING",
                    )
                    self.fm_busy_getting_schedule = False
                    self.emit(
                        "no_new_schedule", "timeouts_on_schedule", error_state=True
                    )
                    await self.wait_for_complete()
                    return

        self.fm_busy_getting_schedule = False

        if schedule == {}:
            self.__log("schedule is empty")
            self.emit("no_new_schedule", "timeouts_on_schedule", error_state=True)
            await self.wait_for_complete()
            return

        self.fm_date_time_last_schedule = get_local_now()
        self.emit("no_new_schedule", "timeouts_on_schedule", error_state=False)
        await self.wait_for_complete()
        await self.set_fm_connection_status(connected=True)
        return schedule

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
        self.event_bus.emit_event("fm_connection_status", state=state)


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
