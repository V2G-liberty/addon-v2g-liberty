from datetime import datetime, timedelta
import isodate
import time
import json
import math
import requests
import constants as c
from typing import List, Union
import appdaemon.plugins.hass.hassapi as hass
from v2g_globals import time_round, convert_to_duration_string


class ManageAmberPriceData(hass.Hass):
    """
    App reads data from the entities that the integration of the Amber Electric power-company populates.

    It is expected that there are:
    Forecast entities for consumption- and production prices per kWh for fixed intervals in
    a longer period ahead (e.g. 12 or 24 hrs). These also include an indication of the
    % renewables:
    The data is expected to have the following structure:
    forecasts:
      - duration: 30
        date: "2024-03-23"
        nem_date: "2024-03-23T08:00:00+10:00"
        per_kwh: 0.09
        spot_per_kwh: 0.09
        start_time: "2024-03-22T21:30:01+00:00"
        end_time: "2024-03-22T22:00:00+00:00"
        renewables: 32
        spike_status: none
        descriptor: high

    This data is updated frequently (upto minutely) at irregular intervals.
    This is why it is queried locally at fix interval of 5 minutes.
    """

    # To extract the right data the following constants are used.
    COLLECTION_NAME: str = "forecasts"
    PRICE_LABEL: str = "per_kwh"
    EMISSION_LABEL: str = "renewables"
    # UOM_LABEL: str  # currently un-used because Amber does not use the TLA but the general
    START_LABEL: str = "start_time"
    END_LABEL: str = "end_time"

    RESOLUTION_TIMEDELTA: timedelta
    POLLING_INTERVAL_SECONDS: int
    CURRENCY: str = "AUD"

    KWH_MWH_FACTOR: int = 1000

    last_consumption_prices: list = []
    last_production_prices: list = []
    last_emissions: list = []

    poll_timer_handle: object

    set_fm_data_module: object
    v2g_main_app: object
    get_fm_data_module: object

    async def initialize(self):
        self.log(f"Initializing ManageAmberPriceData.")

        ##########################################################################
        # TESTDATA: Currency = EUR, moet AUD zijn! Wordt in class definitie al gezet.
        ##########################################################################
        # self.log(f"initialize, TESTDATA: Currency = EUR, moet AUD zijn!")
        # self.CURRENCY = "EUR"

        self.RESOLUTION_TIMEDELTA = timedelta(minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES)
        self.POLLING_INTERVAL_SECONDS = c.FM_EVENT_RESOLUTION_IN_MINUTES * 60
        self.set_fm_data_module = await self.get_app("set_fm_data")
        self.v2g_main_app = await self.get_app("v2g_liberty")
        self.get_fm_data_module = await self.get_app("get_fm_data")
        self.poll_timer_handle = None
        await self.kick_off_amber_price_management(initial=True)
        self.log(f"Completed Initializing ManageAmberPriceData.")

    async def kick_off_amber_price_management(self, initial: bool = False):
        """
           'Second stage' of initialisation.
           To be called from 'initialize' and from the globals module collective_action()
           when the settings have changed.

            :param initial: Only for the first call from the initialisation of the module.
            :return: Nothing
        """
        if c.ELECTRICITY_PROVIDER != "au_amber_electric" and \
           c.HA_OWN_CONSUMPTION_PRICE_ENTITY_ID is not None and \
           c.HA_OWN_PRODUCTION_PRICE_ENTITY_ID is not None:
            self.log(f"Not kicking off ManageAmberPriceData module. Electricity provider is not "
                     f"'au_amber_electric' and/or price entity_id's are not populated (yet).")
            return
        else:
            self.log(f"kick_off_amber_price_management starting!")

        if initial:
            self.__check_for_price_changes({'forced': True})

        if self.info_timer(self.poll_timer_handle):
            silent = True  # Does not really work
            await self.cancel_timer(self.poll_timer_handle, silent)

        self.poll_timer_handle = await self.run_every(
            self.__check_for_price_changes,
            start = "now+2",
            interval = self.POLLING_INTERVAL_SECONDS
        )

        self.log(f"kick_off_amber_price_management completed")


    # TODO: Make generic function in globals, it is copied to Octopus module.
    def parse_to_rounded_local_datetime(self, date_time: str) -> datetime:
        # self.log(f"parse_to_rounded_local_datetime, original: {date_time}.")
        date_time = date_time.replace(" ", "T")
        date_time = isodate.parse_datetime(date_time).astimezone(c.TZ)
        date_time = time_round(date_time, self.RESOLUTION_TIMEDELTA)
        # self.log(f"parse_to_rounded_local_datetime, with  TZ: {date_time.isoformat()}.")
        return date_time

    async def __check_for_price_changes(self, kwargs):
        """ Checks if prices have changed.
            To be called every 5 minutes, not at on_change events of price_entities, these are too volatile.
            If any changes:
            + Send changed prices and/or emissions to FM
            + Request a new schedule
        """
        forced = kwargs.get('forced', False)
        self.log(f"__check_for_price_changes, forced: {forced}.")

        new_schedule_needed = False

        #### Consumption prices (& emissions) ####
        consumption_prices = []
        emissions = []

        state = await self.get_state(c.HA_OWN_CONSUMPTION_PRICE_ENTITY_ID, attribute="all")
        collection_cpf = state["attributes"][self.COLLECTION_NAME]
        for item in collection_cpf:
            consumption_prices.append(round(float(item[self.PRICE_LABEL]) * self.KWH_MWH_FACTOR, 2))
            # Emissions are same for consumption- and production prices, only processed here.
            # Amber has % renewables, FlexMeasures uses % emissions as it runs a cost minimisation.
            # So, emissions % = 100 - renewables %.
            emissions.append(100 - int(float(item[self.EMISSION_LABEL])))

        if consumption_prices != self.last_consumption_prices or forced:
            self.log("__check_for_price_changes: consumption_prices changed")
            start_cpf = self.parse_to_rounded_local_datetime(collection_cpf[0][self.START_LABEL])
            end_cpf = self.parse_to_rounded_local_datetime(collection_cpf[-1][self.END_LABEL])
            duration_cpf = int(float(((end_cpf - start_cpf).total_seconds() / 60)))
            duration_cpf = convert_to_duration_string(duration_cpf)
            self.last_consumption_prices = list(consumption_prices)
            uom = f"{self.CURRENCY}/MWh"
            res = self.set_fm_data_module.post_data(
                fm_entity_address=c.FM_PRICE_CONSUMPTION_ENTITY_ADDRESS,
                values=consumption_prices,
                start=start_cpf,
                duration=duration_cpf,
                uom=uom
            )
            if res:
                if self.get_fm_data_module is not None:
                    await self.get_fm_data_module.get_consumption_prices()
                else:
                    self.log("__check_for_price_changes. Could not call get_consumption_prices on "
                             "get_fm_data_module as it is None.")
                if c.OPTIMISATION_MODE == "price":
                    new_schedule_needed = True
                self.log(f"__check_for_price_changes, res: {res}, "
                         f"opt_mod: {c.OPTIMISATION_MODE}, new_schedule: {new_schedule_needed}")

        if emissions != self.last_emissions or forced:
            self.log("__check_for_price_changes: emissions changed")
            # TODO: copied code from previous block, please prevent this.
            start_cpf = self.parse_to_rounded_local_datetime(collection_cpf[0][self.START_LABEL])
            end_cpf = self.parse_to_rounded_local_datetime(collection_cpf[-1][self.END_LABEL])
            duration_cpf = int(float(((end_cpf - start_cpf).total_seconds() / 60)))  # convert sec. to min.
            duration_cpf = convert_to_duration_string(duration_cpf)
            self.last_emissions = list(emissions)
            res = self.set_fm_data_module.post_data(
                fm_entity_address=c.FM_EMISSIONS_ENTITY_ADDRESS,
                values=emissions,
                start=start_cpf,
                duration=duration_cpf,
                uom="%"
            )
            if res:
                if c.OPTIMISATION_MODE != "price":
                    new_schedule_needed = True

                if self.get_fm_data_module is not None:
                    await self.get_fm_data_module.get_emission_intensities()
                else:
                    self.log("__check_for_price_changes. Could not call get_emission_intensities on "
                             "get_fm_data_module as it is None.")

            self.log(f"__check_for_price_changes, res: {res}, "
                     f"opt_mod: {c.OPTIMISATION_MODE}, new_schedule: {new_schedule_needed}")

        #### Production prices ####
        production_prices = []
        state = await self.get_state(c.HA_OWN_PRODUCTION_PRICE_ENTITY_ID, attribute="all")
        collection_ppf = state["attributes"][self.COLLECTION_NAME]
        for item in collection_ppf:
            production_prices.append(round(float(item[self.PRICE_LABEL]) * self.KWH_MWH_FACTOR, 2))

        if production_prices != self.last_production_prices or forced:
            self.log("__check_for_price_changes: production_prices changed")
            self.last_production_prices = list(production_prices)
            start_ppf = self.parse_to_rounded_local_datetime(collection_ppf[0][self.START_LABEL])
            end_ppf = self.parse_to_rounded_local_datetime(collection_ppf[-1][self.END_LABEL])
            duration_ppf = int(float(((end_ppf - start_ppf).total_seconds() / 60)))
            duration_ppf = convert_to_duration_string(duration_ppf)
            uom = f"{self.CURRENCY}/MWh"
            res = self.set_fm_data_module.post_data(
                fm_entity_address=c.FM_PRICE_PRODUCTION_ENTITY_ADDRESS,
                values=production_prices,
                start=start_ppf,
                duration=duration_ppf,
                uom=uom
            )
            if res:
                if self.get_fm_data_module is not None:
                    await self.get_fm_data_module.get_production_prices()
                else:
                    self.log("__check_for_price_changes. Could not call get_production_prices on "
                             "get_fm_data_module as it is None.")
                if c.OPTIMISATION_MODE == "price":
                    new_schedule_needed = True
            self.log(f"__check_for_price_changes, res: {res}, opt_mod: {c.OPTIMISATION_MODE}, "
                     f"new_schedule: {new_schedule_needed}")

        if not new_schedule_needed:
            self.log("__check_for_price_changes: not any changes")
            return

        msg = f"changed Amber {c.OPTIMISATION_MODE}s"
        if self.v2g_main_app is not None:
            await self.v2g_main_app.set_next_action(v2g_args=msg)
        else:
            self.log("__check_for_price_changes. Could not call set_next_action on v2g_main_app as it is None.")

