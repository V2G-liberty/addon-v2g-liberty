"""Module to read data from (publicly) available Britisch electricity price data"""

from datetime import datetime
import json
import isodate
import aiohttp
import asyncio
from aiohttp import ClientTimeout, ClientError
import constants as c
import log_wrapper
from v2g_globals import (
    time_round,
    get_local_now,
    convert_to_duration_string,
    is_local_now_between,
)

from appdaemon.plugins.hass.hassapi import Hass


class ManageOctopusPriceData:
    """
    Class reads data from (publicly) available price data at:
    https://api.octopus.energy/v1/products/

    This code is based upon:
    https://github.com/badguy99/octoblock

    """

    COLLECTION_NAME: str = "results"
    PRICE_LABEL: str = "value_inc_vat"
    START_LABEL: str = "valid_from"
    END_LABEL: str = "valid_to"

    CURRENCY: str = "GBP"
    UOM: str = "GBP/MWh"
    EMISSIONS_UOM: str = "kg/MWh"

    # The data comes in pence/kWh and should be sent to FM in pound/MWh
    # Example: 20.5 pence/kWh = 0,205 pound/kWh = 205 pound/MWh, factor is 10
    PENCE_PER_KWH_TO_POUNDS_PER_MWH_FACTOR: int = 10

    daily_timer_id: str = ""

    fm_client_app: object = None
    get_fm_data_module: object = None

    # Octopus price urls
    BASE_URL: str = "https://api.octopus.energy/v1/products/"
    import_url: str = ""
    export_url: str = ""

    BASE_EMISSIONS_URL: str = "https://api.carbonintensity.org.uk/regional/intensity/"
    emission_region_slug: str = ""

    # Octopus publishes price data around 16:00, add a little slack.
    FIRST_TRY_TIME_GET_DATA: str = "16:15:14"

    # If not successful retry every x minutes until this time (the next day)
    CHECK_RESOLUTION_SECONDS: int = 30 * 60
    TRY_UNTIL: str = "12:34:50"

    # For more details about the regions see:
    # https://energy-stats.uk/dno-region-codes-explained/
    # https://carbon-intensity.github.io/api-definitions/#region-list
    # A..P = DNO region letter, 1..14 = carbon_intensity region number
    gb_dno_regions = {
        "Eastern England": ("A", 10),
        "East Midlands": ("B", 9),
        "London": ("C", 13),
        "Merseyside and Northern Wales": ("D", 6),
        "West Midlands": ("E", 8),
        "North Eastern England": ("F", 4),
        "North Western England": ("G", 3),
        "Southern England": ("H", 12),
        "South Eastern England": ("J", 14),
        "Southern Wales": ("K", 7),
        "South Western England": ("L", 11),
        "Yorkshire": ("M", 5),
        "Southern Scotland": ("N", 2),
        "Northern Scotland": ("P", 1),
    }

    hass: Hass = None

    def __init__(self, hass: Hass):
        self.hass = hass
        self.__log = log_wrapper.get_class_method_logger(hass.log)

    async def initialize(self):
        self.__log("Initializing")

        ##########################################################################
        # TESTDATA: Currency = EUR, should be GBP! Is set in class definition.
        ##########################################################################
        # self.__log("TESTDATA: Currency = EUR, should be GBP!", level="WARNING")
        # self.UOM = "EUR/MWh"

        await self.kick_off_octopus_price_management(initial=True)
        self.__log("Completed")

    async def kick_off_octopus_price_management(self, initial: bool = False):
        """
        'Second stage' of initialisation.
        To be called from 'initialize' and from the globals module collective_action()
        when the settings have changed.

         :param initial: Only for the first call from the initialisation of the module.
         :return: Nothing
        """
        if c.ELECTRICITY_PROVIDER != "gb_octopus_energy":
            self.__log(
                "Not kicking off ManageOctopusPriceData module."
                "Electricity provider is not 'gb_octopus_energy'."
            )
            return

        un_initiated_values = ["unknown", "", "Please choose an option", None]
        if c.GB_DNO_REGION in un_initiated_values:
            self.__log(
                f"Not kicking off ManageOctopusPriceData module."
                f" c.GB_DNO_REGION ('{c.GB_DNO_REGION}') is not populated (yet)."
            )
            return

        if c.OCTOPUS_IMPORT_CODE in un_initiated_values:
            self.__log(
                f"Not kicking off ManageOctopusPriceData module. c.OCTOPUS_IMPORT_CODE "
                f"('{c.OCTOPUS_IMPORT_CODE}') is not populated (yet)."
            )
            return

        if c.OCTOPUS_EXPORT_CODE in un_initiated_values:
            self.__log(
                f"Not kicking off ManageOctopusPriceData module. c.OCTOPUS_EXPORT_CODE "
                f"('{c.OCTOPUS_EXPORT_CODE}') is not populated (yet)."
            )
            return

        self.__log("starting")

        region_char, region_index = self.gb_dno_regions.get(c.GB_DNO_REGION)

        self.import_url = (
            f"{self.BASE_URL}{c.OCTOPUS_IMPORT_CODE}/electricity-tariffs/"
            f"E-1R-{c.OCTOPUS_IMPORT_CODE}-{region_char}/standard-unit-rates/"
        )
        self.__log(f"self.import_url: {self.import_url}.")

        self.export_url = (
            f"{self.BASE_URL}{c.OCTOPUS_EXPORT_CODE}/electricity-tariffs/E-1R-"
            f"{c.OCTOPUS_EXPORT_CODE}-{region_char}/standard-unit-rates/"
        )
        self.__log(f"self.export_url: {self.export_url}.")

        self.emission_region_slug = f"/fw48h/regionid/{region_index}"

        if self.hass.timer_running(self.daily_timer_id):
            await self.hass.cancel_timer(self.daily_timer_id, silent=True)
        self.daily_timer_id = self.hass.run_daily(
            self.__daily_kickoff_prices_emissions, start=self.FIRST_TRY_TIME_GET_DATA
        )

        # Always do the kickoff at startup.
        await self.__daily_kickoff_prices_emissions()

        self.__log("completed")

    async def __daily_kickoff_prices_emissions(self, *args):
        self.__log("called")
        await self.__get_octopus_import_prices()
        await self.__get_octopus_export_prices()
        await self.__get_gb_region_emissions()
        self.__log("completed")

    async def __get_octopus_import_prices(self, *args):
        """Get import (consumption) price data from Octopus and send then to FlexMeasures"""
        self.__log("called")
        res = await self.__request_data_aiohttp(self.import_url)
        if res is None:
            self.__log(
                f"Get data failed, retry in {self.CHECK_RESOLUTION_SECONDS} sec."
            )
            if is_local_now_between(self.FIRST_TRY_TIME_GET_DATA, self.TRY_UNTIL):
                await self.hass.run_in(
                    self.__get_octopus_import_prices,
                    delay=self.CHECK_RESOLUTION_SECONDS,
                )
            return

        prices = res.get(self.COLLECTION_NAME, None)
        if prices is None:
            self.__log(
                f"Could not get '{self.COLLECTION_NAME}' from data '{res}', aborting.",
                level="WARNING",
            )
            return
        prices = list(reversed(prices))

        consumption_prices = []
        for price in prices:
            consumption_prices.append(
                round(
                    float(price[self.PRICE_LABEL])
                    * self.PENCE_PER_KWH_TO_POUNDS_PER_MWH_FACTOR,
                    2,
                )
            )

        start = parse_to_rounded_local_datetime(prices[0][self.START_LABEL])
        end = parse_to_rounded_local_datetime(prices[-1][self.END_LABEL])
        duration = int(float(((end - start).total_seconds() / 60)))
        duration = convert_to_duration_string(duration)

        if self.fm_client_app is not None:
            res = await self.fm_client_app.post_measurements(
                sensor_id=c.FM_PRICE_CONSUMPTION_SENSOR_ID,
                values=consumption_prices,
                start=start,
                duration=duration,
                uom=self.UOM,
            )
        else:
            self.__log(
                "Could not call post_measurements on fm_client_app as it is None."
            )
            res = False

        self.__log(f"res: {res}.")
        if res:
            if self.get_fm_data_module is not None:
                parameters = {"price_type": "consumption"}
                await self.get_fm_data_module.get_prices(parameters)
            else:
                self.__log(
                    "Could not call get_consumption_prices on get_fm_data_module as it is None."
                )

    async def __get_octopus_export_prices(self, *args):
        """Get export (production) price data from Octopus and send then to FlexMeasures"""
        self.__log("called")
        res = await self.__request_data_aiohttp(self.export_url)
        if res is None:
            self.__log(
                f"Get data failed, retry in {self.CHECK_RESOLUTION_SECONDS} sec."
            )
            if is_local_now_between(self.FIRST_TRY_TIME_GET_DATA, self.TRY_UNTIL):
                await self.hass.run_in(
                    self.__get_octopus_export_prices,
                    delay=self.CHECK_RESOLUTION_SECONDS,
                )
            return

        prices = res.get(self.COLLECTION_NAME, None)
        if prices is None:
            self.__log(
                f"Could not get '{self.COLLECTION_NAME}' from data '{res}', aborting.",
                level="WARNING",
            )
            return

        production_prices = []
        for price in prices:
            production_prices.append(
                round(
                    float(price[self.PRICE_LABEL])
                    * self.PENCE_PER_KWH_TO_POUNDS_PER_MWH_FACTOR,
                    2,
                )
            )

        start = parse_to_rounded_local_datetime(prices[0][self.START_LABEL])
        end = parse_to_rounded_local_datetime(prices[-1][self.END_LABEL])
        duration = int(float(((end - start).total_seconds() / 60)))
        duration = convert_to_duration_string(duration)

        if self.fm_client_app is not None:
            res = await self.fm_client_app.post_measurements(
                sensor_id=c.FM_PRICE_PRODUCTION_SENSOR_ID,
                values=production_prices,
                start=start,
                duration=duration,
                uom=self.UOM,
            )
        else:
            self.__log(
                "Could not call post_measurements on fm_client_app as it is None."
            )
            res = False

        self.__log(f"res: {res}.")
        if res:
            if self.get_fm_data_module is not None:
                parameters = {"price_type": "production"}
                await self.get_fm_data_module.get_prices(parameters)
            else:
                self.__log(
                    "Could not call get_production_prices on get_fm_data_module as it is None."
                )

    async def __get_gb_region_emissions(self, *args):
        """Get emission data for region and send then to FlexMeasures"""
        self.__log("called")

        now = str(get_local_now())
        start = now[:10] + "T00:00:00Z"

        emissions_url = f"{self.BASE_EMISSIONS_URL}{start}{self.emission_region_slug}"
        self.__log(f"emissions_url: {emissions_url}.")

        res = await self.__request_data_aiohttp(emissions_url)
        if res is None:
            if self.hass.now_is_between(self.FIRST_TRY_TIME_GET_DATA, self.TRY_UNTIL):
                await self.hass.run_in(
                    self.__get_gb_region_emissions,
                    delay=self.CHECK_RESOLUTION_SECONDS,
                )
            return

        emissions = res.get("data", None)
        if emissions is not None:
            emissions = emissions.get("data", None)
        if emissions is None:
            self.__log(
                f"Could not get 'data', 'data' from data '{res}', aborting.",
                level="WARNING",
            )
            return

        # Extracting the 'forecast' values
        emission_intensities = [entry["intensity"]["forecast"] for entry in emissions]

        start = parse_to_rounded_local_datetime(emissions[0]["from"])
        end = parse_to_rounded_local_datetime(emissions[-1]["to"])
        duration = int(float(((end - start).total_seconds() / 60)))
        duration = convert_to_duration_string(duration)

        ##########################################################################
        # TESTDATA: EMISSIONS_UOM = "%", should be kg/MWh
        ##########################################################################
        # self.__log(
        #     "TESTDATA: self.EMISSIONS_UOM = %, moet kg/MWh zijn!", level="WARNING"
        # )
        # self.EMISSIONS_UOM = "%"

        if self.fm_client_app is not None:
            res = await self.fm_client_app.post_measurements(
                sensor_id=c.FM_EMISSIONS_SENSOR_ID,
                values=emission_intensities,
                start=start,
                duration=duration,
                uom=self.EMISSIONS_UOM,
            )
        else:
            self.__log(
                "Could not call post_measurements on fm_client_app as it is None."
            )
            res = False

        self.__log(f"res: {res}.")
        if res:
            if self.get_fm_data_module is not None:
                await self.get_fm_data_module.get_emission_intensities()
            else:
                self.__log(
                    "Could not call get_gb_region_emissions on get_fm_data_module as it is None."
                )

    async def __request_data_aiohttp(self, url: str):
        timeout = ClientTimeout(total=13, connect=3, sock_read=10)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    # Check status code first
                    if response.status != 200:
                        self.__log(
                            f"HTTP {response.status} error from url {url}.",
                            level="WARNING",
                        )
                        return None
                    try:
                        return await response.json()
                    except aiohttp.ContentTypeError:
                        # Fallback to manual JSON parsing if content-type is wrong
                        text = await response.text()
                        return json.loads(text)
                    except json.JSONDecodeError as e:
                        self.__log(
                            f"Exception reading JSON: {e} from url {url}.",
                            level="WARNING",
                        )
                    except Exception as e:
                        self.__log(
                            f"Exception reading JSON: {e} from url {url}.",
                            level="WARNING",
                        )
        except asyncio.TimeoutError:
            self.__log(f"Timeout on url: {url}.", level="WARNING")
        except aiohttp.ClientConnectorError:
            self.__log(f"Connection error on url: {url}.", level="WARNING")
        except ClientError as e:
            self.__log(f"Client error: {e} on url: {url}.", level="WARNING")
        except Exception as e:
            self.__log(f"Unexpected error: {e} on url: {url}.", level="WARNING")


# TODO: Make generic function in globals, it is copied from Amber module.
def parse_to_rounded_local_datetime(date_time: str) -> datetime:
    date_time = date_time.replace(" ", "T")
    date_time = isodate.parse_datetime(date_time).astimezone(c.TZ)
    date_time = time_round(date_time, c.EVENT_RESOLUTION)
    return date_time
