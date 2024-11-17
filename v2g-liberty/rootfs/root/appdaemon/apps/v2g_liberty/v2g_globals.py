from datetime import datetime, timedelta
import pytz
import time
import asyncio
import json
import os
import math
from appdaemon.plugins.hass.hassapi import Hass
import constants as c
import log_wrapper
from service_response_app import ServiceResponseApp
from settings_manager import SettingsManager


class V2GLibertyGlobals(ServiceResponseApp):
    v2g_settings: SettingsManager
    settings_file_path = "/data/v2g_liberty_settings.json"
    v2g_main_app: object
    evse_client_app: object
    fm_client_app: object
    calendar_client: object
    fm_data_retrieve_client: object
    amber_price_data_manager: object
    octopus_price_data_manager: object

    # Settings related to FlexMeasures
    SETTING_FM_ACCOUNT_USERNAME = {
        "entity_name": "fm_account_username",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
        "listener_id": None,
    }
    SETTING_FM_ACCOUNT_PASSWORD = {
        "entity_name": "fm_account_password",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
        "listener_id": None,
    }
    SETTING_USE_OTHER_FM_BASE_URL = {
        "entity_name": "fm_show_option_to_change_url",
        "entity_type": "input_boolean",
        "value_type": "bool",
        "factory_default": False,
        "listener_id": None,
    }
    SETTING_FM_BASE_URL = {
        "entity_name": "fm_host_url",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": "https://seita.energy",
        "listener_id": None,
    }
    SETTING_FM_ASSET = {
        "entity_name": "fm_asset",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": None,
        "listener_id": None,
    }

    # Settings related to optimisation
    SETTING_OPTIMISATION_MODE = {
        "entity_name": "optimisation_mode",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": "price",
        "listener_id": None,
    }
    SETTING_ELECTRICITY_PROVIDER = {
        "entity_name": "electricity_provider",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": "nl_generic",
        "listener_id": None,
    }

    # The entity_name's to which the third party integration
    # writes the Consumption- and Production Price (Forecasts)
    SETTING_OWN_PRODUCTION_PRICE_ENTITY_ID = {
        "entity_name": "own_production_price_entity_id",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
        "listener_id": None,
    }
    SETTING_OWN_CONSUMPTION_PRICE_ENTITY_ID = {
        "entity_name": "own_consumption_price_entity_id",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
        "listener_id": None,
    }

    # Settings related to Octopus Agile contracts
    SETTING_OCTOPUS_IMPORT_CODE = {
        "entity_name": "octopus_import_code",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
        "listener_id": None,
    }
    SETTING_OCTOPUS_EXPORT_CODE = {
        "entity_name": "octopus_export_code",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
        "listener_id": None,
    }
    SETTING_GB_DNO_REGION = {
        "entity_name": "gb_dno_region",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": None,
        "listener_id": None,
    }

    # Settings related to charger
    SETTING_CHARGER_HOST_URL = {
        "entity_name": "charger_host_url",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
        "listener_id": None,
    }
    SETTING_CHARGER_PORT = {
        "entity_name": "charger_port",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 502,
        "listener_id": None,
    }

    # Settings related to car
    SETTING_CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY = {
        "entity_name": "charger_plus_car_roundtrip_efficiency",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 85,
        "listener_id": None,
    }
    SETTING_CAR_MAX_CAPACITY_IN_KWH = {
        "entity_name": "car_max_capacity_in_kwh",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 24,
        "listener_id": None,
    }
    SETTING_CAR_CONSUMPTION_WH_PER_KM = {
        "entity_name": "car_consumption_wh_per_km",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 175,
        "listener_id": None,
    }

    # Settings related to optimisation
    SETTING_CAR_MIN_SOC_IN_PERCENT = {
        "entity_name": "car_min_soc_in_percent",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 20,
        "listener_id": None,
    }
    SETTING_CAR_MAX_SOC_IN_PERCENT = {
        "entity_name": "car_max_soc_in_percent",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 80,
        "listener_id": None,
    }
    SETTING_ALLOWED_DURATION_ABOVE_MAX_SOC_IN_HRS = {
        "entity_name": "allowed_duration_above_max_soc_in_hrs",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 4,
        "min": 1,
        "max": 12,
        "listener_id": None,
    }
    SETTING_USE_REDUCED_MAX_CHARGE_POWER = {
        "entity_name": "use_reduced_max_charge_power",
        "entity_type": "input_boolean",
        "value_type": "bool",
        "factory_default": False,
        "listener_id": None,
    }
    SETTING_CHARGER_MAX_CHARGE_POWER = {
        "entity_name": "charger_max_charging_power",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 1380,
        "min": 1380,
        "max": 25000,
        "listener_id": None,
    }
    SETTING_CHARGER_MAX_DISCHARGE_POWER = {
        "entity_name": "charger_max_discharging_power",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 1380,
        "min": 1380,
        "max": 25000,
        "listener_id": None,
    }

    # Settings related to showing prices
    SETTING_ENERGY_PRICE_VAT = {
        "entity_name": "energy_price_vat",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 0,
        "listener_id": None,
    }
    SETTING_ENERGY_PRICE_MARKUP_PER_KWH = {
        "entity_name": "energy_price_markup_per_kwh",
        "entity_type": "input_number",
        "value_type": "float",
        "factory_default": 0,
        "listener_id": None,
    }
    SETTING_USE_VAT_AND_MARKUP = {
        "entity_name": "use_vat_and_markup",
        "entity_type": "input_boolean",
        "value_type": "bool",
        "factory_default": False,
        "listener_id": None,
    }

    # Settings related to notifications
    SETTING_ADMIN_MOBILE_NAME = {
        "entity_name": "admin_mobile_name",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": None,
        "listener_id": None,
    }
    SETTING_ADMIN_MOBILE_PLATFORM = {
        "entity_name": "admin_mobile_platform",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": "ios",
        "listener_id": None,
    }

    # Settings related to calendar
    SETTING_CAR_CALENDAR_SOURCE = {
        "entity_name": "car_calendar_source",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": "Direct caldav source",
        "listener_id": None,
    }
    SETTING_INTEGRATION_CALENDAR_ENTITY_NAME = {
        "entity_name": "integration_calendar_entity_name",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": "",
        "listener_id": None,
    }
    SETTING_CALENDAR_ACCOUNT_INIT_URL = {
        "entity_name": "calendar_account_init_url",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
        "listener_id": None,
    }
    SETTING_CALENDAR_ACCOUNT_USERNAME = {
        "entity_name": "calendar_account_username",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
        "listener_id": None,
    }
    SETTING_CALENDAR_ACCOUNT_PASSWORD = {
        "entity_name": "calendar_account_password",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
        "listener_id": None,
    }
    SETTING_CAR_CALENDAR_NAME = {
        "entity_name": "car_calendar_name",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": None,
        "listener_id": None,
    }

    # Used by method __collect_action_triggers
    collect_action_handle = None

    fm_assets: dict = {}

    hass: Hass = None

    def __init__(self, hass: Hass):
        self.hass = hass
        self.__log = log_wrapper.get_class_method_logger(hass.log)
        self.v2g_settings = SettingsManager(log=self.__log)

    async def initialize(self):
        self.__log("Initializing V2GLibertyGlobals")
        config = await self.hass.get_plugin_config()
        # Use the HA time_zone, and not the TZ from appdaemon.yaml that AD uses.
        c.TZ = pytz.timezone(config["time_zone"])
        # For footer of notifications
        c.HA_NAME = config["location_name"]
        # The currency is dictated by the energy provider so it is not retrieved from the config here.
        self.__log(f"initialize | {c.HA_NAME=}, {c.TZ=}, local_now: {get_local_now()}.")

        c.EVENT_RESOLUTION = timedelta(minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES)

        # It is recommended to always use the utility function get_local_now() from this module and
        # not use self.get_now() as this depends on AppDaemon OS timezone,
        # and that we have not been able to set from this code.

        await self.__kick_off_settings()

        # Listen to [TEST] buttons
        self.hass.listen_event(
            self.__test_charger_connection, "TEST_CHARGER_CONNECTION"
        )
        self.hass.listen_event(self.__init_caldav_calendar, "TEST_CALENDAR_CONNECTION")
        self.hass.listen_event(self.__test_fm_connection, "TEST_FM_CONNECTION")
        self.hass.listen_event(
            self.__reset_to_factory_defaults, "RESET_TO_FACTORY_DEFAULTS"
        )
        self.hass.listen_event(self.restart_v2g_liberty, "RESTART_HA")

        # Was None, which blocks processing during initialisation
        self.collect_action_handle = ""
        self.__log("Completed initializing V2GLibertyGlobals")

    ######################################################################
    #                         PUBLIC METHODS                             #
    ######################################################################

    async def process_max_power_settings(
        self, min_acceptable_charge_power: int, max_available_charge_power: int
    ):
        """To be called from modbus_evse_client to check if setting in the charger
        is lower than the setting by the user.
        """
        self.__log(
            f"process_max_power_settings called with power {max_available_charge_power}."
        )
        self.SETTING_CHARGER_MAX_CHARGE_POWER["max"] = max_available_charge_power
        self.SETTING_CHARGER_MAX_CHARGE_POWER["min"] = min_acceptable_charge_power
        self.SETTING_CHARGER_MAX_DISCHARGE_POWER["max"] = max_available_charge_power
        self.SETTING_CHARGER_MAX_DISCHARGE_POWER["min"] = min_acceptable_charge_power

        # For showing this maximum in the UI.
        await self.hass.set_state(
            "input_text.charger_max_available_power", state=max_available_charge_power
        )

        kwargs = {"run_once": True}
        await self.__read_and_process_charger_settings(kwargs=kwargs)

    ######################################################################
    #                    INITIALISATION METHODS                          #
    ######################################################################

    async def __kick_off_settings(self):
        # To be called from initialise or restart event
        self.__log("__kick_off_settings called")

        self.v2g_settings.retrieve_settings()
        # TODO: Add a listener for changes in registered devices (smartphones with HA installed)?
        await self.__initialise_devices()
        await self.__read_and_process_notification_settings()

        await self.__populate_select_with_local_calendars()

        await self.__read_and_process_charger_settings()
        await self.__read_and_process_optimisation_settings()
        await self.__read_and_process_calendar_settings()
        await self.__read_and_process_general_settings()
        # FlexMeasures settings are influenced by the optimisation_ and general_settings.
        await self.__read_and_process_fm_client_settings()

    async def __populate_select_with_local_calendars(self):
        self.__log("__populate_select_with_local_calendars called")
        if self.calendar_client is not None:
            calendar_names = await self.calendar_client.get_ha_calendar_names()
        else:
            self.__log(
                "__populate_select_with_local_calendars. "
                "Could not call calendar_client.get_ha_calendar_names"
            )
            return

        # At init this method is always called.
        # Notify only when the source is actually "Home Assistant integration"
        if (
            len(calendar_names) == 0
            and c.CAR_CALENDAR_SOURCE == "Home Assistant integration"
        ):
            message = (
                f"No calendars from integration available. "
                f"A car reservation calendar is essential for V2G Liberty. "
                f"Please arrange for one.<br/>"
            )
            self.__log(f"Configuration error: {message}.")
            # TODO: Research if showing this only to admin users is possible.
            await self.create_persistent_notification(
                title="Configuration error",
                message=message,
                notification_id="calendar_config_error",
            )

        elif len(calendar_names) == 1:
            self.__log(
                f"__populate_select_with_local_calendars one calendar found: '{calendar_names[0]}', "
                f"this will be used."
            )
            await self.__set_select_options(
                entity_id="input_select.integration_calendar_entity_name",
                options=calendar_names,
                option_to_select=calendar_names[0],
            )
            c.INTEGRATION_CALENDAR_ENTITY_NAME = calendar_names[0]

        else:
            self.__log(
                f"__populate_select_with_local_calendars > 1 calendar found: populate select."
            )
            await self.__set_select_options(
                entity_id="input_select.integration_calendar_entity_name",
                options=calendar_names,
            )

    async def __initialise_devices(self):
        # List of all the recipients to notify
        # Check if Admin is configured correctly
        # Warn user about bad config with persistent notification in UI.
        self.__log("Initializing devices configuration")

        c.NOTIFICATION_RECIPIENTS.clear()
        # Service "mobile_app_" seems more reliable than using get_trackers,
        # as these names do not always match with the service.
        for service in self.hass.list_services():
            if service["service"].startswith("mobile_app_"):
                c.NOTIFICATION_RECIPIENTS.append(
                    service["service"].replace("mobile_app_", "")
                )

        if len(c.NOTIFICATION_RECIPIENTS) == 0:
            message = (
                f"No mobile devices (e.g. phone, tablet, etc.) have been registered in Home Assistant "
                f"for notifications.<br/>"
                f"It is highly recommended to do so. Please install the HA companion app on your mobile device "
                f"and connect it to Home Assistant. Then restart Home Assistant and the V2G Liberty add-on."
            )
            self.__log(f"Configuration error: {message}.")
            # TODO: Research if showing this only to admin users is possible.
            await self.create_persistent_notification(
                title="Configuration error",
                message=message,
                notification_id="notification_config_error",
            )
        else:
            c.NOTIFICATION_RECIPIENTS = list(
                set(c.NOTIFICATION_RECIPIENTS)
            )  # Remove duplicates
            c.NOTIFICATION_RECIPIENTS.sort()
            self.__log(
                f"__initialise_devices - recipients for notifications: "
                f"{c.NOTIFICATION_RECIPIENTS}."
            )
            await self.__set_select_options(
                entity_id="input_select.admin_mobile_name",
                options=c.NOTIFICATION_RECIPIENTS,
            )

        self.__log("Completed Initializing devices configuration")

    ######################################################################
    #                    CALLBACK METHODS FROM UI                        #
    ######################################################################

    async def __init_caldav_calendar(self, event=None, data=None, kwargs=None):
        # Should only be called when c.CAR_CALENDAR_SOURCE == "Direct caldav source"
        # Get the possible calendars from the validated account
        # if 0 set persistent notification
        # if current one is not in list set persistent notification
        # Populate the input_select input_select.car_calendar_name with possible calendars
        # Select the right option
        # Add a listener
        self.__log("__init_caldav_calendar called")

        await self.hass.set_state(
            "input_text.calendar_account_connection_status",
            state="Getting calendars...",
        )

        if self.calendar_client is not None:
            res = await self.calendar_client.initialise_calendar()
        else:
            self.__log(
                "__populate_select_with_local_calendars. Could not call calendar_client.initialise_calendar"
            )
            res = "Internal error"

        if res != "Successfully connected":
            await self.hass.set_state(
                "input_text.calendar_account_connection_status", state=res
            )
            self.__log(f"__init_caldav_calendar, res: {res}.")
            return

        # reset options in calendar_name select
        calendar_names = []

        # A conditional card in the dashboard is dependent on exactly the text "Successfully connected".
        await self.hass.set_state(
            "input_text.calendar_account_connection_status",
            state="Successfully connected",
        )

        calendar_names = await self.calendar_client.get_dav_calendar_names()
        self.__log(f"__init_caldav_calendar, calendar_names: {calendar_names}.")
        if len(calendar_names) == 0:
            message = (
                f"No calendars available on {c.CALENDAR_ACCOUNT_INIT_URL} "
                f"A car reservation calendar is essential for V2G Liberty. "
                f"Please arrange for one.<br/>"
            )
            self.__log(f"Configuration error: {message}.")
            # TODO: Research if showing this only to admin users is possible.
            await self.create_persistent_notification(
                title="Configuration error",
                message=message,
                notification_id="calendar_config_error",
            )
            calendar_names = ["No calenders found"]

        await self.__set_select_options(
            entity_id="input_select.car_calendar_name", options=calendar_names
        )
        # TODO: If no stored_setting is found:
        # try guess a good default by selecting the first option that has "car" or "auto" in it's name.
        c.CAR_CALENDAR_NAME = await self.__process_setting(
            setting_object=self.SETTING_CAR_CALENDAR_NAME,
            callback=self.__read_and_process_calendar_settings,
        )
        await self.calendar_client.activate_selected_calendar()
        self.__log("Completed __init_caldav_calendar")

    async def __reset_to_factory_defaults(self, event=None, data=None, kwargs=None):
        """Reset to factory defaults by emptying the settings file"""
        self.__log("__reset_to_factory_defaults called")
        self.v2g_settings.reset()
        await self.restart_v2g_liberty()

    async def restart_v2g_liberty(self, event=None, data=None, kwargs=None):
        self.__log("restart_v2g_liberty called")
        await self.hass.call_service("homeassistant/restart")
        # This also results in the V2G Liberty python modules to be reloaded (not a restart of appdaemon).

    async def __test_charger_connection(self, event, data, kwargs):
        """Tests the connection with the charger and processes the maximum charge power read from the charger
        Called from the settings page."""
        self.__log("__test_charger_connection called")
        # The url and port settings have been changed via the listener
        await self.hass.set_state(
            "input_text.charger_connection_status", state="Trying to connect..."
        )
        if not await self.evse_client_app.initialise_charger():
            msg = "Failed to connect"
        else:
            msg = "Successfully connected"
        # min/max power is set self.evse_client_app.initialise_charger()
        # A conditional card in the dashboard is dependent on exactly the text "Successfully connected".
        await self.hass.set_state("input_text.charger_connection_status", state=msg)

    async def __test_fm_connection(self, event=None, data=None, kwargs=None):
        # Tests the connection with FlexMeasures
        # to be called at initialisation and from the event "TEST_FM_CONNECTION" UI event

        self.__log("__test_fm_connection called")
        await self.__set_fm_connection_status("Testing connection...")

        if self.fm_client_app is not None:
            res = await self.fm_client_app.initialise_and_test_fm_client()
        else:
            res = "Error: no fm_client_app available, please try again."
            self.__log(
                "__test_fm_connection. Could not call initialise_and_test_fm_client on fm_client_app as it is None."
            )

        if res != "Successfully connected":
            await self.__set_fm_connection_status(res)
            return

        assets = await self.fm_client_app.get_fm_assets()
        if assets is None:
            self.__log(
                f"__test_fm_connection. Could not call fm_client_app.get_fm_assets"
            )
            await self.__set_fm_connection_status(
                "Problem getting asset(s) from FlexMeasures, please try again."
            )
            return
        if len(assets) == 0:
            await self.__set_fm_connection_status(
                "Not assets in account, please check with administrator."
            )
            return

        asset_entity_id = f"{self.SETTING_FM_ASSET['entity_type']}.{self.SETTING_FM_ASSET['entity_name']}"
        current_asset_setting = self.v2g_settings.get("asset_entity_id")
        self.__log(
            f"__test_fm_connection, current_asset_setting: {current_asset_setting} "
            f"(asset_entity_id={asset_entity_id})."
        )
        if len(assets) == 1:
            # Most common scenario
            asset_name = assets[0]["name"]
            asset_id = assets[0]["id"]
            self.__log(
                f"__test_fm_connection, found one asset in FM: {asset_name}, id: {asset_id}."
            )
            if current_asset_setting != asset_name:
                self.__log(
                    f"__test_fm_connection: v2g_stored_asset_name and "
                    f"fm_retrieved_asset_name differ."
                )
                # TODO: Create persistent notification?
                await self.__set_fm_connection_status(
                    f"Succes! Using new asset '{asset_name}'."
                )
            else:
                await self.__set_fm_connection_status(f"Succes! Using '{asset_name}'.")
            self.__store_setting(asset_entity_id, asset_name)
            await self.__get_and_process_fm_sensors(asset_id=asset_id)
        else:
            # Populate and show input_select and let user pick
            for asset_specs in assets:
                self.fm_assets[asset_specs["name"]] = asset_specs["id"]
            self.__log(f"__test_fm_connection, > 1 assets: {self.fm_assets}")
            asset_options = list(self.fm_assets.keys())
            if current_asset_setting not in asset_options:
                self.__log(
                    f"__test_fm_connection, current_asset: {current_asset_setting} not in assets"
                )
                current_asset_setting = None
            await self.__set_select_options(
                entity_id=asset_entity_id,
                options=asset_options,
                option_to_select=current_asset_setting,
                pcao=True,
            )
            await self.__read_and_process_fm_asset()
            await self.__set_fm_connection_status("Please select an asset")
        self.__log("__test_fm_connection completed")

    async def __read_and_process_fm_asset(
        self, entity=None, attribute=None, old=None, new=None, kwargs=None
    ):
        # Split from __read_and_process_fm_client_settings because this is only optional for situation where > 1
        # assets are registered in FM.
        self.__log("__read_and_process_fm_asset called")

        callback_method = self.__read_and_process_fm_asset
        asset_name = await self.__process_setting(
            setting_object=self.SETTING_FM_ASSET, callback=callback_method
        )
        asset_id = self.fm_assets.get(asset_name, None)
        if asset_id is None:
            self.__log(
                f"__read_and_process_fm_asset aborted, asset_name '{asset_name}' "
                f"not in fm_assets {self.fm_assets}, could not get asset_id."
            )
            return
        else:
            asset_id = int(float(asset_id))

        await self.__get_and_process_fm_sensors(asset_id=asset_id)
        self.__log("__read_and_process_fm_asset completed")

    async def __get_and_process_fm_sensors(self, asset_id: int):
        self.__log("__get_and_process_fm_sensors called")
        if self.fm_client_app is not None:
            sensors = await self.fm_client_app.get_fm_sensors(asset_id)
        else:
            self.__log(
                "__get_and_process_fm_sensors. Could not call get_fm_sensors on fm_client_app as it is None."
            )
            await self.__set_fm_connection_status(
                state="Problem getting sensors from FlexMeasures, please try again."
            )
            return

        if sensors is None:
            self.__log(
                "__get_and_process_fm_sensors. get_fm_sensors('{}') returned None."
            )
            await self.__set_fm_connection_status(
                state="Problem getting sensors from FlexMeasures, please try again."
            )
            return

        for sensor in sensors:
            sensor_name = sensor["name"].lower()
            # self.__log(f"__get_and_process_fm_sensors, name: {sensor_name}.")
            if "power" in sensor_name and "aggregate" not in sensor_name:
                # E.g. "aggregate power" and "Nissan Leaf Power"
                c.FM_ACCOUNT_POWER_SENSOR_ID = sensor["id"]
            elif "availability" in sensor_name:
                c.FM_ACCOUNT_AVAILABILITY_SENSOR_ID = sensor["id"]
            elif "state of charge" in sensor_name:
                c.FM_ACCOUNT_SOC_SENSOR_ID = sensor["id"]
            elif "charging cost" in sensor_name:
                c.FM_ACCOUNT_COST_SENSOR_ID = sensor["id"]

            if c.ELECTRICITY_PROVIDER in ["au_amber_electric", "gb_octopus_energy"]:
                # self.__log(f"__get_and_process_fm_sensors for au_amber_electric/gb_octopus_energy, sensor: '{sensor}'.")
                if "consumption" in sensor_name:
                    # E.g. 'consumption price' or 'consumption tariff'
                    c.FM_PRICE_CONSUMPTION_SENSOR_ID = sensor["id"]
                elif "production" in sensor_name:
                    # E.g. 'production price' or 'production tariff'
                    c.FM_PRICE_PRODUCTION_SENSOR_ID = sensor["id"]
                elif "intensity" in sensor_name:
                    # E.g. 'Amber CO₂ intensity'
                    c.FM_EMISSIONS_SENSOR_ID = sensor["id"]

        self.__log(
            f"__get_and_process_fm_sensors: \n"
            f"    c.FM_ACCOUNT_POWER_SENSOR_ID: {c.FM_ACCOUNT_POWER_SENSOR_ID}. \n"
            f"    c.FM_ACCOUNT_AVAILABILITY_SENSOR_ID: {c.FM_ACCOUNT_AVAILABILITY_SENSOR_ID}. \n"
            f"    c.FM_ACCOUNT_SOC_SENSOR_ID: {c.FM_ACCOUNT_SOC_SENSOR_ID}. \n"
            f"    c.FM_ACCOUNT_COST_SENSOR_ID: {c.FM_ACCOUNT_COST_SENSOR_ID}."
        )

        if c.ELECTRICITY_PROVIDER in ["au_amber_electric", "gb_octopus_energy"]:
            self.__log(
                f"__get_and_process_fm_sensors, (own_prices): \n"
                f"    c.FM_PRICE_CONSUMPTION_SENSOR_ID:  {c.FM_PRICE_CONSUMPTION_SENSOR_ID}. \n"
                f"    c.FM_PRICE_PRODUCTION_SENSOR_ID:  {c.FM_PRICE_PRODUCTION_SENSOR_ID}. \n"
                f"    c.FM_EMISSIONS_SENSOR_ID: {c.FM_EMISSIONS_SENSOR_ID}."
            )

        await self.__set_fm_optimisation_context()
        await self.__collect_action_triggers(source="changed FM sensors")
        self.__log("__get_and_process_fm_sensors completed")

    ######################################################################
    #                            HA METHODS                              #
    ######################################################################
    async def __set_fm_connection_status(self, state: str):
        self.__log(f"__set_fm_connection_status, state: {state}.")
        await self.hass.set_state(
            "input_text.fm_connection_status", state=state, attributes=get_keepalive()
        )

    async def create_persistent_notification(
        self, message: str, title: str, notification_id: str
    ):
        try:
            await self.hass.call_service(
                service="persistent_notification/create",
                title=title,
                message=message,
                notification_id=notification_id,
            )
        except Exception as e:
            self.__log(f"create_persistent_notification failed! Exception: '{e}'")

    async def __write_setting_to_ha(
        self,
        setting: dict,
        setting_value: any,
        source: str,
        min_allowed_value: int | float = None,
        max_allowed_value: int | float = None,
    ):
        """
        This method writes the value to the HA entity.
        :param setting: A setting object, o.a. containing the entity_id
        :param setting_value: The actual value to write
        :param source: user_input, settings, factory_default, ha. Needed for the "initialised" attribute in the entity.
        :param min_allowed_value:
        :param max_allowed_value:
        :return: Nothing
        """
        entity_name = setting["entity_name"]
        entity_type = setting["entity_type"]
        entity_id = f"{entity_type}.{entity_name}"
        # self.__log(f"__write_setting_to_ha called with value '{setting_value}' for entity '{entity_id}'.")

        if setting_value is not None:
            # setting_value has a relevant setting_value to set to HA
            if entity_type == "input_select":
                await self.__select_option(entity_id, setting_value)
            elif entity_type == "input_boolean":
                if setting_value is True:
                    await self.hass.turn_on(entity_id)
                else:
                    await self.hass.turn_off(entity_id)
            else:
                initialised_sourced = ["user_input", "settings"]
                new_attributes = {"initialised": (source in initialised_sourced)}

                # Unfortunately the UI does not pick up these new limits from the attributes (maybe in a new version?),
                # so need to check locally also.
                if min_allowed_value:
                    new_attributes["min"] = min_allowed_value
                if max_allowed_value:
                    new_attributes["max"] = max_allowed_value
                # self.__log(f"__write_setting_to_ha, attributes: {new_attributes}.")
                await self.hass.set_state(
                    entity_id, state=setting_value, attributes=new_attributes
                )

    async def __select_option(self, entity_id: str, option: str):
        """Helper function to select an option in an input_select. It should be used instead of self.hass.select_option.
           It overcomes the problem whereby an error is raised if the option is not available.
           This sometimes kills the (web) server.

        Args:
            entity_id (str): full entity_id, must of course be input_select.xyz
            option (str): The option to select.

        Returns:
            bool: If option was successfully selected or not
        """

        self.__log(f"__select_option called")

        if option == "Please choose an option":
            self.__log(
                f"__select_option - option to select == 'Please choose an option'.",
                level="WARNING",
            )
            return False
        if entity_id is None or entity_id[:13] != "input_select.":
            self.__log(
                f"__select_option aborted - entity type is not input_select: '{entity_id[:13]}'."
            )
            return False
        if not self.hass.entity_exists(entity_id):
            self.__log(
                f"__select_option aborted - entity_id does not exist: '{entity_id}'."
            )
            return False
        res = await self.hass.get_state(entity_id=entity_id, attribute="options")
        if res is None or option not in res:
            self.__log(f"__select_option, option '{option}' not in options {res}.")
            # This is the only way of handling this error situation, try - except fails...
            # As we expect this to be a sort of race condition we just add this one option and
            # assume the list will be completed later with this option selected. Risky?
            await self.__set_select_options(
                entity_id=entity_id, options=[option], option_to_select=option
            )
        else:
            # self.__log(f"__select_option, option '{option}' selected.")
            await self.hass.select_option(entity_id=entity_id, option=option)
        return True

    async def __set_select_options(
        self,
        entity_id: str,
        options: list,
        option_to_select: str = "",
        pcao: bool = None,
    ):
        """Helper method to fill a select with options.
            It overcomes the problem whereby an error is raised for the currently selected option is
            not in the new options list.
            It replaces the current options in the list by the new options list.
            It also sorts the list, removes duplicates and None values.

        Args:
            + entity_id (str): full entity_id, must of course be input_select.xyz
            + options (list): list of options (strings) with minimal 1 item.
            + option_to_select (str, optional):
              The option to select after the options have been added.
              If none is given or the given option is not in the list of options the first option will be selected.
            + pcao (bool, optional):
              pcao is an acronym for "Please choose an option", if:
               = True, a pcao option will be added
               = False, will be removed (if existing)
               = None, leave list untouched
        """
        self.__log(
            f"__set_select_options called, entity_id: '{entity_id}', options: {options},"
            f"option_to_select: {option_to_select}, pcao: {pcao}."
        )
        if entity_id is None or entity_id[:13] != "input_select.":
            self.__log(
                f"__set_select_options - entity type is not input_select: '{entity_id[:13]}'.",
                level="WARNING",
            )
            return False
        if not self.hass.entity_exists(entity_id):
            self.__log(
                f"__set_select_options - entity_id does not exist: '{entity_id}'.",
                level="WARNING",
            )
            return False
        if options is None or len(options) == 0:
            self.__log(
                f"__set_select_options - invalid options: '{options}'.", level="WARNING"
            )
            return False

        current_selected_option = await self.hass.get_state(entity_id, None)
        self.__log(
            f"__set_select_options - current_selected_option: '{current_selected_option}'."
        )

        # The list needs to be sorted alphabetically and should not have duplicates
        # The list should not contain None options
        # If a pcao option is required it has to be the first option.
        pcao_option = "Please choose an option"
        if pcao is None and (pcao_option in options):
            self.__log(
                f"__set_select_options pcao is None but in current options, keeping it."
            )
            pcao = True
        options = list(set(options))  # Remove duplicates and sort
        options.sort()  # set() does not sort properly

        if None in options:
            options.remove(None)
        if pcao_option in options:
            options.remove(pcao_option)
        self.__log(
            f"__set_select_options options, removed duplicates, pcao and None values"
            f" and sorted: {options}"
        )

        if pcao:
            pass
            # options.insert(0, pcao_option)
            # self.__log(f"__set_select_options options, added pcao again: {options}")

        if current_selected_option == pcao_option:
            self.__log(
                f"__set_select_options temporary BUGFIX, "
                f"hard remove 'Please choose an option' option.",
                level="WARNING",
            )
            current_selected_option = options[0]

        tmp = ""
        if (
            current_selected_option is not None
            and current_selected_option not in options
        ):
            tmp = options.append(current_selected_option)
            # Set a new list with the old option selected to prevent an error, it will be removed later.
            await self.hass.call_service(
                "input_select/set_options", entity_id=entity_id, options=tmp
            )
            self.__log(
                f"__set_select_options options, current_selected_option {current_selected_option} added "
                f"to prevent HA error."
            )
        else:
            await self.hass.call_service(
                "input_select/set_options", entity_id=entity_id, options=options
            )
            self.__log(f"__set_select_options options, new options set in select.")

        if option_to_select in options:
            so = option_to_select
        elif current_selected_option in options:
            so = current_selected_option
        else:
            so = options[0]
        # self.__log(f"__set_select_options options, to select option is: {so}")

        # Select the desired option.
        await self.hass.select_option(entity_id=entity_id, option=so)

        if tmp != "":
            # If added, remove the current_selected_option
            await self.hass.call_service(
                "input_select/set_options", entity_id=entity_id, options=options
            )
            # self.__log(f"__set_select_options options, removed original selected option")

        return True

    ######################################################################
    #                METHODS FOR IO of SETTINGS FILE                     #
    ######################################################################

    def __store_setting(self, entity_id: str, setting_value: any):
        """Store (overwrite or create) a setting in settings file.

        Args:
            entity_id (str): setting name = the full entity_id from HA
            setting_value: the value to set.
        """
        # self.__log(f"__store_setting, entity_id: '{entity_id}' to value '{setting_value}'.")
        if setting_value in ["unknown", "Please choose an option"]:
            return False
        self.v2g_settings.store_setting(entity_id, setting_value)
        return True

    ######################################################################
    #                           CORE METHODS                             #
    ######################################################################

    async def __process_setting(self, setting_object: dict, callback):
        """
        This method checks if the setting-entity is empty, if so:
        - set the setting-entity setting_value to the default that is set in constants
        if not empty:
        - return the setting_value of the setting-entity
        """
        entity_name = setting_object["entity_name"]
        entity_type = setting_object["entity_type"]
        entity_id = f"{entity_type}.{entity_name}"
        setting_entity = await self.hass.get_state(entity_id, attribute="all")

        # Get the setting from store
        stored_setting_value = self.v2g_settings.get(entity_id)

        # At first initial run the listener_id is filled with a callback handler.
        if setting_object["listener_id"] is None:
            # read the setting from store, write to UI and return it so the constant can be set.
            return_value = ""
            if stored_setting_value is None:
                # v2g_setting was empty, populate it with the factory default from settings_object
                factory_default = setting_object["factory_default"]
                if factory_default is not None and factory_default != "":
                    return_value, has_changed = await self.__check_and_convert_value(
                        setting_object, factory_default
                    )
                    # self.__log(f"__process_setting, Initial call. No relevant v2g_setting. "
                    #          f"Set constant and UI to factory_default: {return_value} "
                    #          f"{type(return_value)}.")
                    self.__store_setting(
                        entity_id=entity_id, setting_value=return_value
                    )
                    await self.__write_setting_to_ha(
                        setting=setting_object,
                        setting_value=return_value,
                        source="factory_default",
                    )
                else:
                    # This most likely is the situation after a re-install or "reset to factory defaults": no stored
                    # setting. Then there might be relevant information stored in the entity (in the UI).
                    # Store this setting, but not if it is empty or "unknown" or "Please choose an option" (the latter
                    # for input_select entities).
                    return_value = setting_entity.get("state", None)
                    if return_value is not None and return_value not in [
                        "",
                        "unknown",
                        "Please choose an option",
                    ]:
                        self.__store_setting(
                            entity_id=entity_id, setting_value=return_value
                        )
                    else:
                        # There is no relevant default to set...
                        self.__log(
                            f"__process_setting: setting '{entity_id}' has no stored value, "
                            f"no factory_default, no value in UI."
                        )
            else:
                # Initial call with relevant v2g_setting.
                # Write that to HA for UI and return this value to set in constants
                return_value, has_changed = await self.__check_and_convert_value(
                    setting_object, stored_setting_value
                )
                # self.__log(f"__process_setting, Initial call. Relevant v2g_setting: {return_value}, "
                #          f"write this to HA entity '{entity_id}'.")
                await self.__write_setting_to_ha(
                    setting=setting_object,
                    setting_value=return_value,
                    source="settings",
                )

            if callback is not None:
                setting_object["listener_id"] = self.hass.listen_state(
                    callback, entity_id, attribute="all"
                )

        else:
            # Not the initial call, so this is triggered by changed value in UI
            # Write value from HA to store and constant
            state = setting_entity.get("state", None)
            return_value, has_changed = await self.__check_and_convert_value(
                setting_object, state
            )
            # self.__log(f"__process_setting. Triggered by changes in UI. "
            #          f"Write value '{return_value}' to store '{entity_id}'.")
            # We need to write to HA entity even if has_changed == False as we have to add the source.
            await self.__write_setting_to_ha(
                setting=setting_object, setting_value=return_value, source="user_input"
            )
            self.__store_setting(entity_id=entity_id, setting_value=return_value)

        # Just for logging
        # Not an exact match of the constant name but good enough for logging
        message = f"v2g_globals, __process_setting set c.{entity_name.upper()} to"
        mode = setting_entity["attributes"].get("mode", "none").lower()
        if mode == "password":
            message = f"{message} ********"
        else:
            message = f"{message} '{return_value}'"

        uom = setting_entity["attributes"].get("unit_of_measurement")
        if uom:
            message = f"{message} {uom}."
        else:
            message = f"{message}."
        self.__log(message)

        return return_value

    async def __read_and_process_charger_settings(
        self, entity=None, attribute=None, old=None, new=None, kwargs=None
    ):
        self.__log("__read_and_process_charger_settings called")

        callback_method = self.__read_and_process_charger_settings

        # If the modbus_evse_client has read the max_charge_power from the charger (in process_max_power_settings()
        # method), it calls this method. Then the max_power has been processed.
        max_power_processed = kwargs is not None and kwargs.get("run_once", False)

        c.CHARGER_HOST_URL = await self.__process_setting(
            setting_object=self.SETTING_CHARGER_HOST_URL, callback=callback_method
        )
        c.CHARGER_PORT = await self.__process_setting(
            setting_object=self.SETTING_CHARGER_PORT, callback=callback_method
        )
        c.CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY = await self.__process_setting(
            setting_object=self.SETTING_CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY,
            callback=callback_method,
        )
        c.ROUNDTRIP_EFFICIENCY_FACTOR = c.CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY / 100

        use_reduced_max_charge_power = False
        use_reduced_max_charge_power = await self.__process_setting(
            setting_object=self.SETTING_USE_REDUCED_MAX_CHARGE_POWER,
            callback=callback_method,
        )
        if use_reduced_max_charge_power:
            # set c.CHARGER_MAX_CHARGE_POWER and c.CHARGER_MAX_DISCHARGE_POWER to max from settings page
            c.CHARGER_MAX_CHARGE_POWER = await self.__process_setting(
                setting_object=self.SETTING_CHARGER_MAX_CHARGE_POWER,
                callback=callback_method,
            )
            c.CHARGER_MAX_DISCHARGE_POWER = await self.__process_setting(
                setting_object=self.SETTING_CHARGER_MAX_DISCHARGE_POWER,
                callback=callback_method,
            )
        else:
            # set c.CHARGER_MAX_CHARGE_POWER and c.CHARGER_MAX_DISCHARGE_POWER to max from charger
            # cancel callbacks for SETTINGS.
            await self.__cancel_setting_listener(self.SETTING_CHARGER_MAX_CHARGE_POWER)
            await self.__cancel_setting_listener(
                self.SETTING_CHARGER_MAX_DISCHARGE_POWER
            )
            if max_power_processed:
                c.CHARGER_MAX_CHARGE_POWER = self.SETTING_CHARGER_MAX_CHARGE_POWER[
                    "max"
                ]
                c.CHARGER_MAX_DISCHARGE_POWER = (
                    self.SETTING_CHARGER_MAX_DISCHARGE_POWER["max"]
                )
                self.__log(
                    f"__read_and_process_charger_settings \n"
                    f"    c.CHARGER_MAX_CHARGE_POWER: {c.CHARGER_MAX_CHARGE_POWER}.\n"
                    f"    c.CHARGER_MAX_DISCHARGE_POWER: {c.CHARGER_MAX_DISCHARGE_POWER}."
                )
            else:
                # This normally is temporary (fallback) and so it is not logged.
                c.CHARGER_MAX_CHARGE_POWER = self.SETTING_CHARGER_MAX_CHARGE_POWER[
                    "factory_default"
                ]
                c.CHARGER_MAX_DISCHARGE_POWER = (
                    self.SETTING_CHARGER_MAX_DISCHARGE_POWER["factory_default"]
                )

        if max_power_processed:
            # To prevent a loop
            return
        await self.__collect_action_triggers(source="changed charger_settings")

    async def __read_and_process_notification_settings(
        self, entity=None, attribute=None, old=None, new=None, kwargs=None
    ):
        self.__log("__read_and_process_notification_settings called")
        callback_method = self.__read_and_process_notification_settings

        if len(c.NOTIFICATION_RECIPIENTS) == 0:
            # Persistent notification is set bij __init_devices
            c.ADMIN_MOBILE_NAME = ""
        else:
            c.ADMIN_MOBILE_NAME = await self.__process_setting(
                setting_object=self.SETTING_ADMIN_MOBILE_NAME, callback=callback_method
            )
            # if c.ADMIN_MOBILE_NAME not in c.NOTIFICATION_RECIPIENTS:
            #     tmp = c.NOTIFICATION_RECIPIENTS[0]
            #     message = f"The admin mobile name ***{c.ADMIN_MOBILE_NAME}*** in configuration is not found in" \
            #               f"available mobiles for notification, instead ***{tmp}*** is used.<br/>" \
            #               f"Please go to the settings view and choose one from the list."
            #     self.__log(f"Configuration error: admin mobile name not found.")
            #     # TODO: Research if showing this only to admin users is possible.
            #     await self.create_persistent_notification(
            #         title="Configuration error",
            #         message=message,
            #         notification_id="notification_config_error_no_admin"
            #     )
            #     c.ADMIN_MOBILE_NAME = tmp

        c.ADMIN_MOBILE_PLATFORM = await self.__process_setting(
            setting_object=self.SETTING_ADMIN_MOBILE_PLATFORM, callback=callback_method
        )
        # Assume iOS as standard
        c.PRIORITY_NOTIFICATION_CONFIG = {
            "push": {"sound": {"critical": 1, "name": "default", "volume": 0.9}}
        }
        if c.ADMIN_MOBILE_PLATFORM.lower() == "android":
            c.PRIORITY_NOTIFICATION_CONFIG = {"ttl": 0, "priority": "high"}

        # These settings do not require any re-init, so do not call __collect_action_triggers()
        self.__log("__read_and_process_notification_settings completed")

    async def __read_and_process_general_settings(
        self, entity=None, attribute=None, old=None, new=None, kwargs=None
    ):
        callback_method = self.__read_and_process_general_settings
        self.__log("__read_and_process_general_settings called")

        c.CAR_CONSUMPTION_WH_PER_KM = await self.__process_setting(
            setting_object=self.SETTING_CAR_CONSUMPTION_WH_PER_KM,
            callback=callback_method,
        )
        c.CAR_MAX_CAPACITY_IN_KWH = await self.__process_setting(
            setting_object=self.SETTING_CAR_MAX_CAPACITY_IN_KWH,
            callback=callback_method,
        )
        c.CAR_MIN_SOC_IN_PERCENT = await self.__process_setting(
            setting_object=self.SETTING_CAR_MIN_SOC_IN_PERCENT, callback=callback_method
        )
        c.CAR_MAX_SOC_IN_PERCENT = await self.__process_setting(
            setting_object=self.SETTING_CAR_MAX_SOC_IN_PERCENT, callback=callback_method
        )
        c.ALLOWED_DURATION_ABOVE_MAX_SOC = await self.__process_setting(
            setting_object=self.SETTING_ALLOWED_DURATION_ABOVE_MAX_SOC_IN_HRS,
            callback=callback_method,
        )

        c.CAR_MIN_SOC_IN_KWH = (
            c.CAR_MAX_CAPACITY_IN_KWH * c.CAR_MIN_SOC_IN_PERCENT / 100
        )
        c.CAR_MAX_SOC_IN_KWH = (
            c.CAR_MAX_CAPACITY_IN_KWH * c.CAR_MAX_SOC_IN_PERCENT / 100
        )

        c.USAGE_PER_EVENT_TIME_INTERVAL = (
            c.KM_PER_HOUR_OF_CALENDAR_ITEM * c.CAR_CONSUMPTION_WH_PER_KM / 1000
        ) / (60 / c.FM_EVENT_RESOLUTION_IN_MINUTES)

        await self.__collect_action_triggers(source="changed general_settings")
        self.__log("__read_and_process_general_settings completed")

    async def __read_and_process_calendar_settings(
        self, entity=None, attribute=None, old=None, new=None, kwargs=None
    ):
        self.__log("__read_and_process_calendar_settings called")

        callback_method = self.__read_and_process_calendar_settings

        tmp = await self.__process_setting(
            setting_object=self.SETTING_CAR_CALENDAR_SOURCE, callback=callback_method
        )
        if (
            c.CAR_CALENDAR_SOURCE != ""
            and c.CAR_CALENDAR_SOURCE is not None
            and c.CAR_CALENDAR_SOURCE != tmp
        ):
            self.__log(
                f"__read_and_process_calendar_settings: Calendar source has changed to '{tmp}'."
            )
            if tmp == "Direct caldav source":
                await self.__cancel_setting_listener(
                    self.SETTING_INTEGRATION_CALENDAR_ENTITY_NAME
                )
            else:
                await self.__populate_select_with_local_calendars()

                await self.__cancel_setting_listener(
                    self.SETTING_CALENDAR_ACCOUNT_INIT_URL
                )
                await self.__cancel_setting_listener(
                    self.SETTING_CALENDAR_ACCOUNT_USERNAME
                )
                await self.__cancel_setting_listener(
                    self.SETTING_CALENDAR_ACCOUNT_PASSWORD
                )
                await self.__cancel_setting_listener(self.SETTING_CAR_CALENDAR_NAME)

        c.CAR_CALENDAR_SOURCE = tmp

        if c.CAR_CALENDAR_SOURCE == "Direct caldav source":
            c.CALENDAR_ACCOUNT_INIT_URL = await self.__process_setting(
                setting_object=self.SETTING_CALENDAR_ACCOUNT_INIT_URL,
                callback=callback_method,
            )
            c.CALENDAR_ACCOUNT_USERNAME = await self.__process_setting(
                setting_object=self.SETTING_CALENDAR_ACCOUNT_USERNAME,
                callback=callback_method,
            )
            c.CALENDAR_ACCOUNT_PASSWORD = await self.__process_setting(
                setting_object=self.SETTING_CALENDAR_ACCOUNT_PASSWORD,
                callback=callback_method,
            )
            c.CAR_CALENDAR_NAME = await self.__process_setting(
                setting_object=self.SETTING_CAR_CALENDAR_NAME, callback=None
            )  # Callback here is none as it will be set from __init_caldav_calendar

            await self.__init_caldav_calendar()

        else:
            c.INTEGRATION_CALENDAR_ENTITY_NAME = await self.__process_setting(
                setting_object=self.SETTING_INTEGRATION_CALENDAR_ENTITY_NAME,
                callback=callback_method,
            )
            if self.calendar_client is not None:
                res = await self.calendar_client.initialise_calendar()
                self.__log(
                    f"__read_and_process_calendar_settings: init HA calendar result: '{res}'."
                )
            else:
                self.__log(
                    f"__read_and_process_calendar_settings. Could not call initialise_calendar on calendar_client"
                    f"as it is None."
                )
        await self.__collect_action_triggers(source="changed calendar settings")

        self.__log("__read_and_process_calendar_settings completed")

    async def __read_and_process_fm_client_settings(
        self, entity=None, attribute=None, old=None, new=None, kwargs=None
    ):
        # Split for future when the python lib fm_client_app is used: that needs to be re-inited
        self.__log("__read_and_process_fm_client_settings called")

        callback_method = self.__read_and_process_fm_client_settings
        c.FM_ACCOUNT_USERNAME = await self.__process_setting(
            setting_object=self.SETTING_FM_ACCOUNT_USERNAME, callback=callback_method
        )
        c.FM_ACCOUNT_PASSWORD = await self.__process_setting(
            setting_object=self.SETTING_FM_ACCOUNT_PASSWORD, callback=callback_method
        )
        use_other_url = await self.__process_setting(
            setting_object=self.SETTING_USE_OTHER_FM_BASE_URL, callback=callback_method
        )
        if use_other_url:
            c.FM_BASE_URL = await self.__process_setting(
                setting_object=self.SETTING_FM_BASE_URL, callback=callback_method
            )
        else:
            c.FM_BASE_URL = self.SETTING_FM_BASE_URL["factory_default"]
            await self.__cancel_setting_listener(self.SETTING_FM_BASE_URL)

        # If the above settings change (through the UI) we need the user to re-test the FM connection
        # Do not set this at startup, tested by self.collect_action_handle is not None
        if self.collect_action_handle is not None:
            await self.__set_fm_connection_status(
                "FlexMeasure settings have changed, please (re-)test."
            )
        else:
            await self.__test_fm_connection()

        self.__log("__read_and_process_fm_client_settings completed")

    async def __read_and_process_optimisation_settings(
        self, entity=None, attribute=None, old=None, new=None, kwargs=None
    ):
        self.__log("__read_and_process_optimisation_settings called")
        callback_method = self.__read_and_process_optimisation_settings

        c.OPTIMISATION_MODE = await self.__process_setting(
            setting_object=self.SETTING_OPTIMISATION_MODE, callback=callback_method
        )

        c.ELECTRICITY_PROVIDER = await self.__process_setting(
            setting_object=self.SETTING_ELECTRICITY_PROVIDER, callback=callback_method
        )

        if c.ELECTRICITY_PROVIDER == "au_amber_electric":
            c.HA_OWN_CONSUMPTION_PRICE_ENTITY_ID = await self.__process_setting(
                setting_object=self.SETTING_OWN_CONSUMPTION_PRICE_ENTITY_ID,
                callback=callback_method,
            )
            c.HA_OWN_PRODUCTION_PRICE_ENTITY_ID = await self.__process_setting(
                setting_object=self.SETTING_OWN_PRODUCTION_PRICE_ENTITY_ID,
                callback=callback_method,
            )
            c.UTILITY_CONTEXT_DISPLAY_NAME = "Amber Electric"
            c.EMISSIONS_UOM = "%"
            c.CURRENCY = "AUD"
            c.PRICE_RESOLUTION_MINUTES = 30

            await self.__cancel_setting_listener(self.SETTING_OCTOPUS_IMPORT_CODE)
            await self.__cancel_setting_listener(self.SETTING_OCTOPUS_EXPORT_CODE)
            await self.__cancel_setting_listener(self.SETTING_GB_DNO_REGION)

        elif c.ELECTRICITY_PROVIDER == "gb_octopus_energy":
            c.OCTOPUS_IMPORT_CODE = await self.__process_setting(
                setting_object=self.SETTING_OCTOPUS_IMPORT_CODE,
                callback=callback_method,
            )
            c.OCTOPUS_EXPORT_CODE = await self.__process_setting(
                setting_object=self.SETTING_OCTOPUS_EXPORT_CODE,
                callback=callback_method,
            )
            c.GB_DNO_REGION = await self.__process_setting(
                setting_object=self.SETTING_GB_DNO_REGION, callback=callback_method
            )
            c.UTILITY_CONTEXT_DISPLAY_NAME = "Octopus Energy"
            c.EMISSIONS_UOM = "kg/MWh"
            c.CURRENCY = "GBP"
            c.PRICE_RESOLUTION_MINUTES = 30

            await self.__cancel_setting_listener(
                self.SETTING_OWN_CONSUMPTION_PRICE_ENTITY_ID
            )
            await self.__cancel_setting_listener(
                self.SETTING_OWN_PRODUCTION_PRICE_ENTITY_ID
            )

        else:
            context = c.DEFAULT_UTILITY_CONTEXTS.get(
                c.ELECTRICITY_PROVIDER,
                c.DEFAULT_UTILITY_CONTEXTS["nl_generic"],
            )
            c.EMISSIONS_UOM = "kg/MWh"
            c.CURRENCY = "EUR"
            c.PRICE_RESOLUTION_MINUTES = 60
            # TODO Notify user if fallback "nl_generic" is used..
            c.FM_PRICE_PRODUCTION_SENSOR_ID = context["production-sensor"]
            c.FM_PRICE_CONSUMPTION_SENSOR_ID = context["consumption-sensor"]
            c.FM_EMISSIONS_SENSOR_ID = context["emissions-sensor"]
            c.UTILITY_CONTEXT_DISPLAY_NAME = context["display-name"]
            self.__log(
                f"__read_and_process_optimisation_settings:\n"
                f"    FM_PRICE_PRODUCTION_SENSOR_ID: {c.FM_PRICE_PRODUCTION_SENSOR_ID}.\n"
                f"    FM_PRICE_CONSUMPTION_SENSOR_ID: {c.FM_PRICE_CONSUMPTION_SENSOR_ID}.\n"
                f"    FM_EMISSIONS_SENSOR_ID: {c.FM_EMISSIONS_SENSOR_ID}.\n"
                f"    UTILITY_CONTEXT_DISPLAY_NAME: {c.UTILITY_CONTEXT_DISPLAY_NAME}."
            )

            await self.__cancel_setting_listener(
                self.SETTING_OWN_CONSUMPTION_PRICE_ENTITY_ID
            )
            await self.__cancel_setting_listener(
                self.SETTING_OWN_PRODUCTION_PRICE_ENTITY_ID
            )
            await self.__cancel_setting_listener(self.SETTING_OCTOPUS_IMPORT_CODE)
            await self.__cancel_setting_listener(self.SETTING_OCTOPUS_EXPORT_CODE)
            await self.__cancel_setting_listener(self.SETTING_GB_DNO_REGION)

        await self.__set_fm_optimisation_context()

        c.USE_VAT_AND_MARKUP = await self.__process_setting(
            setting_object=self.SETTING_USE_VAT_AND_MARKUP, callback=callback_method
        )
        # Only relevant for electricity_providers "generic" and possibly for none EPEX,
        # for others we expect netto prices (including VAT and Markup).
        # If self_provided data (e.g. au_amber_electric, gb_octopus_energy) also includes VAT and markup.
        if c.USE_VAT_AND_MARKUP:
            c.ENERGY_PRICE_VAT = await self.__process_setting(
                setting_object=self.SETTING_ENERGY_PRICE_VAT, callback=callback_method
            )
            c.ENERGY_PRICE_MARKUP_PER_KWH = await self.__process_setting(
                setting_object=self.SETTING_ENERGY_PRICE_MARKUP_PER_KWH,
                callback=callback_method,
            )
        else:
            # Not reset VAT/MARKUP to factory defaults, the calculations in get_fm_data are based on USE_VAT_AND_MARKUP
            await self.__cancel_setting_listener(self.SETTING_ENERGY_PRICE_VAT)
            await self.__cancel_setting_listener(
                self.SETTING_ENERGY_PRICE_MARKUP_PER_KWH
            )

        await self.__collect_action_triggers(source="changed optimisation settings")

    async def __reset_to_factory_default(self, setting_object):
        entity_name = setting_object["entity_name"]
        entity_type = setting_object["entity_type"]
        entity_id = f"{entity_type}.{entity_name}"
        factory_default = setting_object["factory_default"]
        if factory_default is None:
            return_value = None
        elif factory_default == "":
            return_value = ""
        else:
            return_value, has_changed = await self.__check_and_convert_value(
                setting_object, factory_default
            )

        self.__store_setting(entity_id=entity_id, setting_value=return_value)
        await self.__write_setting_to_ha(
            setting=setting_object, setting_value=return_value, source="factory_default"
        )
        return return_value

    async def __set_fm_optimisation_context(self):
        # To be called after c.FM_PRICE_CONSUMPTION_SENSOR_ID, c.FM_PRICE_PRODUCTION_SENSOR_ID and
        # c.FM_EMISSIONS_SENSOR_ID have been set.
        if c.OPTIMISATION_MODE == "price":
            c.FM_OPTIMISATION_CONTEXT = {
                "consumption-price-sensor": c.FM_PRICE_CONSUMPTION_SENSOR_ID,
                "production-price-sensor": c.FM_PRICE_PRODUCTION_SENSOR_ID,
            }
        else:
            # Assumed optimisation = emissions
            c.FM_OPTIMISATION_CONTEXT = {
                "consumption-price-sensor": c.FM_EMISSIONS_SENSOR_ID,
                "production-price-sensor": c.FM_EMISSIONS_SENSOR_ID,
            }
        self.__log(
            f"__set_fm_optimisation_context c.FM_OPTIMISATION_CONTEXT: '{c.FM_OPTIMISATION_CONTEXT}"
        )

    async def __collect_action_triggers(self, source: str):
        # When settings change the application needs partial restart.
        # To prevent a restart for every detailed change always wait a little as
        # the user likely changes several settings at a time_to_round.
        # This also prevents calling V2G Liberty too early when it has not
        # completed initialisation yet.

        if self.collect_action_handle is None:
            # This is the initial, init has not finished yet
            return
        if self.hass.info_timer(self.collect_action_handle):
            await self.hass.cancel_timer(self.collect_action_handle, True)
        self.collect_action_handle = await self.hass.run_in(
            self.__collective_action, delay=15, source=source
        )

    async def __collective_action(self, source: str):
        """Provides partial restart of each module of the whole application

        :param v2g_args: String for debugging only
        :return: Nothing
        """

        self.__log(f"__collective_action, called with source: '{source}'.")

        if self.evse_client_app is not None:
            await self.evse_client_app.initialise_charger(v2g_args=source)
        else:
            self.__log(
                "__collective_action. Could not call initialise_charger on evse_client_app as it is None."
            )

        if self.calendar_client is not None:
            await self.calendar_client.initialise_calendar()
        else:
            self.__log(
                "__collective_action. Could not call initialise_calendar on calendar_client as it is None."
            )

        if self.v2g_main_app is not None:
            await self.v2g_main_app.initialise_v2g_liberty(v2g_args=source)
        else:
            self.__log(
                "__collective_action. Could not call initialise_v2g_liberty on v2g_main_app as it is None."
            )

        if c.ELECTRICITY_PROVIDER == "au_amber_electric":
            self.__log("__collective_action. Amber Electric ")
            if self.amber_price_data_manager is not None:
                await self.amber_price_data_manager.kick_off_amber_price_management()
            else:
                self.__log(
                    "__collective_action. Could not call kick_off_amber_price_management on "
                    "amber_price_data_manager as it is None."
                )
        elif c.ELECTRICITY_PROVIDER == "gb_octopus_energy":
            self.__log("__collective_action. Octopus Energy")
            if self.octopus_price_data_manager is not None:
                await (
                    self.octopus_price_data_manager.kick_off_octopus_price_management()
                )
            else:
                self.__log(
                    "__collective_action. Could not call kick_off_octopus_price_management on "
                    "octopus_price_data_manager as it is None."
                )

        if self.fm_data_retrieve_client is not None:
            await self.fm_data_retrieve_client.finalize_initialisation(v2g_args=source)
        else:
            self.__log(
                "__collective_action. Could not call finalize_initialisation on "
                "fm_data_retrieve_client as it is None."
            )

        self.__log(f"__collective_action, completed.")

    ######################################################################
    #                           UTIL METHODS                             #
    ######################################################################

    async def __check_and_convert_value(self, setting_object, value_to_convert):
        """Check number against min/max from setting object, altering to stay within these limits.
            Convert to required type (in setting object)

        Args:
            setting_object (dict): dict with setting_object with entity_type/name and min/max
            value_to_convert (any): the value to convert

        Returns:
            any: depending on the setting type
        """
        entity_id = f"{setting_object['entity_type']}.{setting_object['entity_name']}"
        value_type = setting_object.get("value_type", "str")
        has_changed = False
        min_value = setting_object.get("min", None)
        max_value = setting_object.get("max", None)
        if value_type == "float":
            return_value = float(value_to_convert)
            rv = return_value
            if min_value:
                min_value = float(min_value)
                rv = max(min_value, return_value)
            if max_value:
                max_value = float(max_value)
                rv = min(max_value, return_value)
            if rv != return_value:
                has_changed = True
                return_value = rv
        elif value_type == "int":
            # Assume int
            return_value = int(float(value_to_convert))
            rv = return_value
            if min_value:
                min_value = int(float(min_value))
                rv = max(min_value, return_value)
            if max_value:
                max_value = int(float(max_value))
                rv = min(max_value, return_value)
            if rv != return_value:
                has_changed = True
                return_value = rv
        elif value_type == "bool":
            if value_to_convert in [True, "true", "True", "on", 1]:
                return_value = True
            else:
                return_value = False
        elif value_type == "str":
            # Convert to string and strip leading and trailing whitespace chars.
            return_value = str(value_to_convert).strip()

        if has_changed:
            msg = f"Adjusted '{entity_id}' to '{return_value}' to stay within limits."
            ntf_id = f"auto_adjusted_setting_{entity_id}"
            await self.create_persistent_notification(
                message=msg,
                title="Automatically adjusted setting",
                notification_id=ntf_id,
            )
            self.__log(f"__check_and_convert_number {msg}")
        return return_value, has_changed

    async def __cancel_setting_listener(self, setting_object: dict):
        listener_id = setting_object["listener_id"]
        if listener_id is not None and listener_id != "":
            # It seems "info_listen_state" does not work async, so just always cancel
            # the listener without first checking if it is still active.
            try:
                await self.hass.cancel_listen_state(listener_id)
            except Exception as e:
                self.__log(f"__cancel_setting_listener, exception: {e}")
        setting_object["listener_id"] = None


######################################################################
#                           UTIL FUNCTIONS                           #
######################################################################


def is_price_epex_based() -> bool:
    # Alle EPEX based electricity providers have a daily update frequency.
    # This also applies to Octopus Energy
    return c.ELECTRICITY_PROVIDER not in ["au_amber_electric", "gb_octopus_energy"]


def time_mod(time_to_mod, delta, epoch=None):
    """From https://stackoverflow.com/a/57877961/13775459"""
    if epoch is None:
        epoch = datetime(1970, 1, 1, tzinfo=time_to_mod.tzinfo)
    return (time_to_mod - epoch) % delta


# TODO: refactor to round_to_resolution where this function knows the resolution already and is not a parameter
def time_round(time_to_round, delta, epoch=None):
    """From https://stackoverflow.com/a/57877961/13775459"""
    mod = time_mod(time_to_round, delta, epoch)
    if mod < (delta / 2):
        return time_to_round - mod
    return time_to_round + (delta - mod)


def time_ceil(time_to_ceil, delta, epoch=None):
    """From https://stackoverflow.com/a/57877961/13775459"""
    mod = time_mod(time_to_ceil, delta, epoch)
    if mod:
        return time_to_ceil + (delta - mod)
    return time_to_ceil


def time_floor(time_to_floor, delta, epoch=None):
    mod = time_mod(time_to_floor, delta, epoch)
    return time_to_floor - mod


def get_local_now():
    return datetime.now(tz=c.TZ)


def get_keepalive():
    now = get_local_now().strftime(c.DATE_TIME_FORMAT)
    return {"keep_alive": now}


_html_escape_table = {
    "&": "&amp;",
    '"': "&quot;",
    "'": "&apos;",
    ">": "&gt;",
    "<": "&lt;",
}


def he(text):
    # 'he' is short for HTML Escape.
    # Produce entities within text.
    return "".join(_html_escape_table.get(c, c) for c in text)


def convert_to_duration_string(duration_in_minutes: int) -> str:
    """
    Args:
        duration_in_minutes (int): duration in minutes to convert

    Returns:
        str: a duration string e.g. PT9H35M
    """
    duration_in_minutes = round(duration_in_minutes, c.FM_EVENT_RESOLUTION_IN_MINUTES)
    MIH = 60  # Minutes In an Hour
    MID = MIH * 24  # Minutes In a Day
    days = math.floor(duration_in_minutes / MID)
    hours = math.floor((duration_in_minutes - (days * MID)) / 60)
    minutes = int(duration_in_minutes - (hours * MIH) - (days * MID))
    if days > 0:
        str_days = str(days) + "D"
    else:
        str_days = ""
    return f"P{str_days}T{str(hours)}H{str(minutes)}M"
