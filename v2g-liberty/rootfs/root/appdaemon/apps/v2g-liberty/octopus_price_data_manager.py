from datetime import datetime, timedelta
import isodate
import time
import json
import math
import requests
import constants as c
from typing import List, Union
import appdaemon.plugins.hass.hassapi as hass
from v2g_globals import time_round, get_local_now, convert_to_duration_string


class ManageOctopusPriceData(hass.Hass):
    """
    App reads data from (publicly) available price data at:
    https://api.octopus.energy/v1/products/

    This code is based upon:
    https://github.com/badguy99/octoblock

    """
    RESOLUTION_TIMEDELTA: timedelta


    COLLECTION_NAME: str = "results"
    PRICE_LABEL: str = "value_inc_vat"
    START_LABEL: str = "valid_from"
    END_LABEL: str = "valid_to"

    CURRENCY: str = "GBP"
    UOM: str = ""
    EMISSIONS_UOM: str = "kg/MWh"

    # The data comes in pence/kWh and should be sent to FM in pound/MWh
    # Example: 20.5 pence/kWh = 0,205 pound/kWh = 205 pound/MWh, factor is 10
    PENCE_PER_KWH_TO_POUNDS_PER_MWH_FACTOR: int = 10

    daily_timer_id: str = ""

    set_fm_data_module: object = None
    get_fm_data_module: object = None

    # Octopus price urls
    BASE_URL: str = "https://api.octopus.energy/v1/products/"
    import_url: str = ""
    export_url: str = ""

    BASE_EMISSIONS_URL: str = "https://api.carbonintensity.org.uk/regional/intensity/"
    emission_region_slug: str = ""

    first_try_time_get_data : str = ""
    second_try_time_get_data: str = ""

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

    async def initialize(self):
        self.log(f"Initializing ManageOctopusPriceData.")

        ##########################################################################
        # TESTDATA: Currency = EUR, moet GBP zijn! Wordt in class definitie al gezet.
        ##########################################################################
        # self.log(f"initialize, TESTDATA: Currency = EUR, moet GBP zijn!", level="WARNING")
        # self.CURRENCY = "EUR"
        self.UOM = f"{self.CURRENCY}/MWh"

        self.RESOLUTION_TIMEDELTA = timedelta(minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES)

        self.set_fm_data_module = await self.get_app("set_fm_data")
        self.get_fm_data_module = await self.get_app("get_fm_data")

        # Emission data is calculated based on the production data and is therefore available a little later
        # than the price data.
        self.first_try_time_get_data = "16:17:18"
        self.second_try_time_get_data = "17:18:19"

        await self.kick_off_octopus_price_management(initial=True)
        self.log(f"Completed Initializing ManageOctopusPriceData.")

    async def kick_off_octopus_price_management(self, initial: bool = False):
        """
           'Second stage' of initialisation.
           To be called from 'initialize' and from the globals module collective_action()
           when the settings have changed.

            :param initial: Only for the first call from the initialisation of the module.
            :return: Nothing
        """
        if c.ELECTRICITY_PROVIDER != "gb_octopus_energy":
            self.log(f"Not kicking off ManageOctopusPriceData module. Electricity provider is not 'gb_octopus_energy'.")
            return

        un_initiated_values = ["unknown", "", "Please choose an option", None]
        if c.GB_DNO_REGION in un_initiated_values:
            self.log(f"Not kicking off ManageOctopusPriceData module. c.GB_DNO_REGION ('{c.GB_DNO_REGION}') is not "
                     f"populated (yet).")
            return

        if c.OCTOPUS_IMPORT_CODE in un_initiated_values:
            self.log(f"Not kicking off ManageOctopusPriceData module. c.OCTOPUS_IMPORT_CODE "
                     f"('{c.OCTOPUS_IMPORT_CODE}') is not populated (yet).")
            return

        if c.OCTOPUS_EXPORT_CODE in un_initiated_values:
            self.log(f"Not kicking off ManageOctopusPriceData module. c.OCTOPUS_EXPORT_CODE "
                     f"('{c.OCTOPUS_EXPORT_CODE}') is not populated (yet).")
            return

        self.log(f"kick_off_octopus_price_management starting!")

        region_char, region_index = self.gb_dno_regions.get(c.GB_DNO_REGION)

        self.import_url = f"{self.BASE_URL}{c.OCTOPUS_IMPORT_CODE}/electricity-tariffs/" \
                          f"E-1R-{c.OCTOPUS_IMPORT_CODE}-{region_char}/standard-unit-rates/"
        self.log(f"kick_off_octopus_price_management self.import_url: {self.import_url}.")

        self.export_url = f"{self.BASE_URL}{c.OCTOPUS_EXPORT_CODE}/electricity-tariffs/E-1R-" \
                          f"{c.OCTOPUS_EXPORT_CODE}-{region_char}/standard-unit-rates/"
        self.log(f"kick_off_octopus_price_management self.export_url: {self.export_url}.")

        self.emission_region_slug = f"/fw48h/regionid/{region_index}"

        if self.info_timer(self.daily_timer_id):
            await self.cancel_timer(self.daily_timer_id, True)
        self.daily_timer_id = self.run_daily(self.__daily_kickoff_prices_emissions, self.first_try_time_get_data)

        # Always do the kickoff at startup.
        await self.__daily_kickoff_prices_emissions()

        self.log(f"kick_off_octopus_price_management completed")


    async def __daily_kickoff_prices_emissions(self):
        self.log(f"__daily_kickoff_prices_emissions called")
        await self.__get_octopus_import_prices()
        await self.__get_octopus_export_prices()
        await self.__get_gb_region_emissions()
        self.log(f"__daily_kickoff_prices_emissions completed")


    async def __get_octopus_import_prices(self, *args):
        self.log(f"__get_octopus_import_prices called.")
        res = requests.get(self.import_url)
        if res.status_code != 200:
            self.log(f"__get_octopus_import_prices. Error {res.status_code}, res: {res.text}.")
            if self.now_is_between(self.first_try_time_get_data, self.second_try_time_get_data):
                self.log(f"__get_octopus_import_prices, retry once at {self.second_try_time_get_data}.")
                await self.run_at(self.__get_octopus_import_prices, start=self.second_try_time_price_data)
            return

        try:
            # The data structure might not be valid so a little extra care for reading values
            prices = json.loads(res.text)
            prices = prices[self.COLLECTION_NAME]
            prices = list(reversed(prices))
        except Exception as e:
            self.log(f"__get_octopus_import_prices. exception reading JSON: {e}.")
            return

        consumption_prices = []
        for price in prices:
            consumption_prices.append(
                round(float(price[self.PRICE_LABEL]) * self.PENCE_PER_KWH_TO_POUNDS_PER_MWH_FACTOR, 2)
            )

        start = self.parse_to_rounded_local_datetime(prices[0][self.START_LABEL])
        end = self.parse_to_rounded_local_datetime(prices[-1][self.END_LABEL])
        duration = int(float(((end - start).total_seconds() / 60)))
        duration = convert_to_duration_string(duration)
        res = self.set_fm_data_module.post_data(
            fm_entity_address=c.FM_PRICE_CONSUMPTION_ENTITY_ADDRESS,
            values=consumption_prices,
            start=start,
            duration=duration,
            uom=self.UOM
        )
        self.log(f"__get_octopus_import_prices res: {res}.")
        if res:
            if self.get_fm_data_module is not None:
                await self.get_fm_data_module.get_consumption_prices()
            else:
                self.log("__check_for_price_changes. Could not call get_consumption_prices on "
                         "get_fm_data_module as it is None.")

    async def __get_octopus_export_prices(self, *args):
        self.log(f"__get_octopus_export_prices called.")
        res = requests.get(self.export_url)
        if res.status_code != 200:
            self.log(f"__get_octopus_export_prices. Error {res.status_code}, res: {res.text}.")
            if self.now_is_between(self.first_try_time_get_data, self.second_try_time_get_data):
                self.log(f"__get_octopus_export_prices, retry once at {self.second_try_time_get_data}.")
                await self.run_at(self.__get_octopus_export_prices, start=self.second_try_time_price_data)
            return

        try:
            # The data structure might not be valid so a little extra care for reading values
            prices = json.loads(res.text)
            prices = prices[self.COLLECTION_NAME]
            prices = list(reversed(prices))
        except Exception as e:
            self.log(f"__get_octopus_export_prices. exception reading JSON: {e}.")
            return

        production_prices = []
        for price in prices:
            production_prices.append(
                round(float(price[self.PRICE_LABEL]) * self.PENCE_PER_KWH_TO_POUNDS_PER_MWH_FACTOR, 2)
            )

        start = self.parse_to_rounded_local_datetime(prices[0][self.START_LABEL])
        end = self.parse_to_rounded_local_datetime(prices[-1][self.END_LABEL])
        duration = int(float(((end - start).total_seconds() / 60)))
        duration = convert_to_duration_string(duration)
        res = self.set_fm_data_module.post_data(
            fm_entity_address=c.FM_PRICE_PRODUCTION_ENTITY_ADDRESS,
            values=production_prices,
            start=start,
            duration=duration,
            uom=self.UOM
        )
        self.log(f"__get_octopus_export_prices res: {res}.")
        if res:
            if self.get_fm_data_module is not None:
                await self.get_fm_data_module.get_production_prices()
            else:
                self.log("__check_for_price_changes. Could not call get_production_prices on "
                         "get_fm_data_module as it is None.")


    async def __get_gb_region_emissions(self, *args):
        self.log(f"__get_gb_region_emissions called.")

        now = str(get_local_now())
        start = now[:10] + "T00:00:00Z"

        emissions_url = f"{self.BASE_EMISSIONS_URL}{start}{self.emission_region_slug}"
        self.log(f"kick_off_octopus_price_management emissions_url: {emissions_url}.")

        res = requests.get(emissions_url)
        if res.status_code != 200:
            self.log(f"__get_gb_region_emissions. Error {res.status_code}, res: {res.text}.")
            if self.now_is_between(self.first_try_time_get_data, self.second_try_time_get_data):
                self.log(f"__get_gb_region_emissions, retry once at {self.second_try_time_get_data}.")
                await self.run_at(self.__get_gb_region_emissions, start=self.second_try_time_price_data)
            return

        try:
            # The data structure might not be valid so a little extra care for reading values
            emissions = json.loads(res.text)
            emissions = emissions['data']['data']

            # Extracting the 'forecast' values
            emission_intensities = [entry['intensity']['forecast'] for entry in emissions]
        except Exception as e:
            self.log(f"__get_gb_region_emissions. exception reading JSON: {e}.")
            return

        start = self.parse_to_rounded_local_datetime(emissions[0]['from'])
        end = self.parse_to_rounded_local_datetime(emissions[-1]['to'])
        duration = int(float(((end - start).total_seconds() / 60)))
        duration = convert_to_duration_string(duration)

        res = self.set_fm_data_module.post_data(
            fm_entity_address=c.FM_EMISSIONS_ENTITY_ADDRESS,
            values=emission_intensities,
            start=start,
            duration=duration,
            uom=self.EMISSIONS_UOM
        )

        self.log(f"__get_gb_region_emissions res: {res}.")
        if res:
            if self.get_fm_data_module is not None:
                await self.get_fm_data_module.get_emission_intensities()
            else:
                self.log("__get_gb_region_emissions. Could not call get_gb_region_emissions on "
                         "get_fm_data_module as it is None.")


    # TODO: Make generic function in globals, it is copied from Amber module.
    def parse_to_rounded_local_datetime(self, date_time: str) -> datetime:
        date_time = date_time.replace(" ", "T")
        date_time = isodate.parse_datetime(date_time).astimezone(c.TZ)
        date_time = time_round(date_time, self.RESOLUTION_TIMEDELTA)
        return date_time