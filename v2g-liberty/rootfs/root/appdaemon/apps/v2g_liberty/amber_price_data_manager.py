"""Module for reading and transforming Amber price data and making it available to fm_client."""

from datetime import datetime
import isodate
import constants as c
import log_wrapper
from v2g_globals import time_round, convert_to_duration_string
from appdaemon.plugins.hass.hassapi import Hass


class ManageAmberPriceData:
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

    POLLING_INTERVAL_SECONDS: int
    CURRENCY: str = "AUD"

    KWH_MWH_FACTOR: int = 1000

    last_consumption_prices: list = []
    last_production_prices: list = []
    last_emissions: list = []

    poll_timer_handle: object

    v2g_main_app: object = None
    get_fm_data_module: object = None
    fm_client_app: object = None
    hass: Hass = None

    def __init__(self, hass: Hass):
        self.hass = hass
        self.__log = log_wrapper.get_class_method_logger(hass.log)

    async def initialize(self):
        self.__log("ManageAmberPriceData.")

        ##########################################################################
        # TESTDATA: Currency = EUR, moet AUD zijn! Wordt in class definitie al gezet.
        ##########################################################################
        # self.log(f"initialize, TESTDATA: Currency = EUR, moet AUD zijn!", level="WARNING")
        # self.CURRENCY = "EUR"

        self.POLLING_INTERVAL_SECONDS = c.FM_EVENT_RESOLUTION_IN_MINUTES * 60
        self.poll_timer_handle = None
        await self.kick_off_amber_price_management(initial=True)
        self.__log("Completed Initializing ManageAmberPriceData.")

    async def kick_off_amber_price_management(self, initial: bool = False):
        """
        'Second stage' of initialisation.
        To be called from 'initialize' and from the globals module collective_action()
        when the settings have changed.

         :param initial: Only for the first call from the initialisation of the module.
         :return: Nothing
        """
        if c.ELECTRICITY_PROVIDER != "au_amber_electric":
            self.__log(
                "Not kicking off ManageAmberPriceData module. Electricity provider is not 'au_amber_electric'."
            )
            return

        if (
            c.HA_OWN_CONSUMPTION_PRICE_ENTITY_ID is None
            or c.HA_OWN_PRODUCTION_PRICE_ENTITY_ID is None
        ):
            self.__log(
                "Not kicking off ManageAmberPriceData module, price entity_id's are not populated (yet)."
            )
            return

        self.__log("starting!")

        if initial:
            await self.__check_for_price_changes({"forced": True})

        if self.hass.timer_running(self.poll_timer_handle):
            silent = True  # Does not really work
            await self.hass.cancel_timer(self.poll_timer_handle, silent)

        self.poll_timer_handle = await self.hass.run_every(
            self.__check_for_price_changes,
            start="now+2",
            interval=self.POLLING_INTERVAL_SECONDS,
        )

        self.__log("completed")

    async def __check_for_price_changes(self, kwargs):
        """Checks if prices have changed.
        To be called every 5 minutes, not at on_change events of price_entities, these are too volatile.
        If any changes:
        + Send changed prices and/or emissions to FM
        + Request a new schedule
        """
        forced = kwargs.get("forced", False)
        self.__log(f"forced: {forced}.")

        new_schedule_needed = False

        #### Consumption prices (& emissions) ####
        consumption_prices = []
        emissions = []

        state = await self.hass.get_state(
            c.HA_OWN_CONSUMPTION_PRICE_ENTITY_ID, attribute="all"
        )
        if state is None:
            self.__log("no (data in) price_entity (yet), aborting.")
            return

        collection_cpf = state["attributes"][self.COLLECTION_NAME]
        for item in collection_cpf:
            consumption_prices.append(
                round(float(item[self.PRICE_LABEL]) * self.KWH_MWH_FACTOR, 2)
            )
            # Emissions are same for consumption- and production prices, only processed here.
            # Amber has % renewables, FlexMeasures uses % emissions as it runs a cost minimisation.
            # So, emissions % = 100 - renewables %.
            emissions.append(100 - int(float(item[self.EMISSION_LABEL])))

        if consumption_prices != self.last_consumption_prices or forced:
            self.__log("consumption_prices changed")
            start_cpf = parse_to_rounded_local_datetime(
                collection_cpf[0][self.START_LABEL]
            )
            end_cpf = parse_to_rounded_local_datetime(
                collection_cpf[-1][self.END_LABEL]
            )
            duration_cpf = int(float(((end_cpf - start_cpf).total_seconds() / 60)))
            duration_cpf = convert_to_duration_string(duration_cpf)
            self.last_consumption_prices = list(consumption_prices)
            uom = f"{self.CURRENCY}/MWh"

            if self.fm_client_app is not None:
                res = await self.fm_client_app.post_measurements(
                    sensor_id=c.FM_PRICE_CONSUMPTION_SENSOR_ID,
                    values=consumption_prices,
                    start=start_cpf,
                    duration=duration_cpf,
                    uom=uom,
                )
            else:
                self.__log(
                    "1 Could not call post_measurements on fm_client_app as it is None."
                )
                res = False

            if res:
                if self.get_fm_data_module is not None:
                    parameters = {"price_type": "consumption"}
                    await self.get_fm_data_module.get_prices(parameters)
                else:
                    self.__log(
                        "Could not call get_consumption_prices on "
                        "get_fm_data_module as it is None."
                    )
                if c.OPTIMISATION_MODE == "price":
                    new_schedule_needed = True
                self.__log(
                    f"res: {res}, "
                    f"opt_mod: {c.OPTIMISATION_MODE}, new_schedule: {new_schedule_needed}"
                )

        if emissions != self.last_emissions or forced:
            self.__log("emissions changed")
            # TODO: copied code from previous block, please prevent this.
            start_cpf = parse_to_rounded_local_datetime(
                collection_cpf[0][self.START_LABEL]
            )
            end_cpf = parse_to_rounded_local_datetime(
                collection_cpf[-1][self.END_LABEL]
            )
            duration_cpf = int(
                float(((end_cpf - start_cpf).total_seconds() / 60))
            )  # convert sec. to min.
            duration_cpf = convert_to_duration_string(duration_cpf)
            self.last_emissions = list(emissions)

            if self.fm_client_app is not None:
                res = await self.fm_client_app.post_measurements(
                    sensor_id=c.FM_EMISSIONS_SENSOR_ID,
                    values=emissions,
                    start=start_cpf,
                    duration=duration_cpf,
                    uom="%",
                )
            else:
                self.__log(
                    "1 Could not call post_measurements on fm_client_app as it is None."
                )
                res = False

            if res:
                if c.OPTIMISATION_MODE != "price":
                    new_schedule_needed = True

                if self.get_fm_data_module is not None:
                    await self.get_fm_data_module.get_emission_intensities()
                else:
                    self.__log(
                        "Could not call get_emission_intensities on "
                        "get_fm_data_module as it is None."
                    )

            self.__log(
                f"res: {res}, "
                f"opt_mod: {c.OPTIMISATION_MODE}, new_schedule: {new_schedule_needed}"
            )

        #### Production prices ####
        production_prices = []
        state = await self.hass.get_state(
            c.HA_OWN_PRODUCTION_PRICE_ENTITY_ID, attribute="all"
        )
        collection_ppf = state["attributes"][self.COLLECTION_NAME]
        for item in collection_ppf:
            production_prices.append(
                round(float(item[self.PRICE_LABEL]) * self.KWH_MWH_FACTOR, 2)
            )

        if production_prices != self.last_production_prices or forced:
            self.__log("production_prices changed")
            self.last_production_prices = list(production_prices)
            start_ppf = parse_to_rounded_local_datetime(
                collection_ppf[0][self.START_LABEL]
            )
            end_ppf = parse_to_rounded_local_datetime(
                collection_ppf[-1][self.END_LABEL]
            )
            duration_ppf = int(float(((end_ppf - start_ppf).total_seconds() / 60)))
            duration_ppf = convert_to_duration_string(duration_ppf)
            uom = f"{self.CURRENCY}/MWh"

            if self.fm_client_app is not None:
                res = await self.fm_client_app.post_measurements(
                    sensor_id=c.FM_PRICE_PRODUCTION_SENSOR_ID,
                    values=production_prices,
                    start=start_ppf,
                    duration=duration_ppf,
                    uom=uom,
                )
            else:
                self.__log(
                    "2 Could not call post_measurements on fm_client_app as it is None."
                )
                res = False

            if res:
                if self.get_fm_data_module is not None:
                    parameters = {"price_type": "production"}
                    await self.get_fm_data_module.get_prices(parameters)
                else:
                    self.__log(
                        "Could not call get_production_prices on "
                        "get_fm_data_module as it is None."
                    )
                if c.OPTIMISATION_MODE == "price":
                    new_schedule_needed = True
            self.__log(
                f"res: {res}, opt_mod: {c.OPTIMISATION_MODE}, "
                f"new_schedule: {new_schedule_needed}"
            )

        if not new_schedule_needed:
            self.__log("not any changes")
            return

        msg = f"changed Amber {c.OPTIMISATION_MODE}s"
        if self.v2g_main_app is not None:
            await self.v2g_main_app.set_next_action(v2g_args=msg)
        else:
            self.__log("Could not call set_next_action on v2g_main_app as it is None.")


# TODO: Make generic function in globals, it is copied to Octopus module.
def parse_to_rounded_local_datetime(date_time: str) -> datetime:
    date_time = date_time.replace(" ", "T")
    date_time = isodate.parse_datetime(date_time).astimezone(c.TZ)
    date_time = time_round(date_time, c.EVENT_RESOLUTION)
    return date_time
