from datetime import datetime, timedelta
import pytz
import math
from appdaemon.plugins.hass.hassapi import Hass
import constants as c
import log_wrapper
from service_response_app import ServiceResponseApp
from settings_manager import SettingsManager


class V2GLibertyGlobals:
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
    SCHEDULE_SETTINGS_INITIALISED = {
        "entity_name": "schedule_settings_initialised",
        "entity_type": "input_boolean",
        "value_type": "bool",
        "factory_default": False,
    }
    SETTING_FM_ACCOUNT_USERNAME = {
        "entity_name": "fm_account_username",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
    }
    SETTING_FM_ACCOUNT_PASSWORD = {
        "entity_name": "fm_account_password",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
    }
    SETTING_USE_OTHER_FM_BASE_URL = {
        "entity_name": "fm_show_option_to_change_url",
        "entity_type": "input_boolean",
        "value_type": "bool",
        "factory_default": False,
    }
    SETTING_FM_BASE_URL = {
        "entity_name": "fm_host_url",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": "https://seita.energy",
    }
    SETTING_FM_ASSET = {
        "entity_name": "fm_asset",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
    }

    # Settings related to optimisation
    SETTING_OPTIMISATION_MODE = {
        "entity_name": "optimisation_mode",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": "price",
    }
    ELECTRICITY_CONTRACT_SETTINGS_INITIALISED = {
        "entity_name": "electricity_contract_settings_initialised",
        "entity_type": "input_boolean",
        "value_type": "bool",
        "factory_default": False,
    }
    SETTING_ELECTRICITY_PROVIDER = {
        "entity_name": "electricity_provider",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": "nl_generic",
    }

    # The entity_name's to which the third party integration
    # writes the Consumption- and Production Price (Forecasts)
    SETTING_OWN_PRODUCTION_PRICE_ENTITY_ID = {
        "entity_name": "own_production_price_entity_id",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
    }
    SETTING_OWN_CONSUMPTION_PRICE_ENTITY_ID = {
        "entity_name": "own_consumption_price_entity_id",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
    }

    # Settings related to Octopus Agile contracts
    SETTING_OCTOPUS_IMPORT_CODE = {
        "entity_name": "octopus_import_code",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
    }
    SETTING_OCTOPUS_EXPORT_CODE = {
        "entity_name": "octopus_export_code",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
    }
    SETTING_GB_DNO_REGION = {
        "entity_name": "gb_dno_region",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": None,
    }

    # Settings related to charger
    CHARGER_SETTINGS_INITIALISED = {
        "entity_name": "charger_settings_initialised",
        "entity_type": "input_boolean",
        "value_type": "bool",
        "factory_default": False,
    }
    SETTING_CHARGER_HOST_URL = {
        "entity_name": "charger_host_url",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
    }
    SETTING_CHARGER_PORT = {
        "entity_name": "charger_port",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 502,
    }

    # Settings related to car
    SETTING_CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY = {
        "entity_name": "charger_plus_car_roundtrip_efficiency",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 85,
    }
    SETTING_CAR_MAX_CAPACITY_IN_KWH = {
        "entity_name": "car_max_capacity_in_kwh",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 24,
    }
    SETTING_CAR_CONSUMPTION_WH_PER_KM = {
        "entity_name": "car_consumption_wh_per_km",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 175,
    }

    # Settings related to optimisation
    SETTING_CAR_MIN_SOC_IN_PERCENT = {
        "entity_name": "car_min_soc_in_percent",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 20,
    }
    SETTING_CAR_MAX_SOC_IN_PERCENT = {
        "entity_name": "car_max_soc_in_percent",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 80,
    }
    SETTING_ALLOWED_DURATION_ABOVE_MAX_SOC_IN_HRS = {
        "entity_name": "allowed_duration_above_max_soc_in_hrs",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 4,
        "min": 1,
        "max": 12,
    }
    SETTING_USE_REDUCED_MAX_CHARGE_POWER = {
        "entity_name": "use_reduced_max_charge_power",
        "entity_type": "input_boolean",
        "value_type": "bool",
        "factory_default": False,
    }
    SETTING_CHARGER_MAX_CHARGE_POWER = {
        "entity_name": "charger_max_charging_power",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 1380,
        "min": 1380,
        "max": 25000,
    }
    SETTING_CHARGER_MAX_DISCHARGE_POWER = {
        "entity_name": "charger_max_discharging_power",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 1380,
        "min": 1380,
        "max": 25000,
    }

    # Settings related to showing prices
    SETTING_ENERGY_PRICE_VAT = {
        "entity_name": "energy_price_vat",
        "entity_type": "input_number",
        "value_type": "int",
        "factory_default": 0,
    }
    SETTING_ENERGY_PRICE_MARKUP_PER_KWH = {
        "entity_name": "energy_price_markup_per_kwh",
        "entity_type": "input_number",
        "value_type": "float",
        "factory_default": 0,
    }
    SETTING_USE_VAT_AND_MARKUP = {
        "entity_name": "use_vat_and_markup",
        "entity_type": "input_boolean",
        "value_type": "bool",
        "factory_default": False,
    }

    # Settings related to notifications
    ADMIN_SETTINGS_INITIALISED = {
        "entity_name": "admin_settings_initialised",
        "entity_type": "input_boolean",
        "value_type": "bool",
        "factory_default": False,
    }
    SETTING_ADMIN_MOBILE_NAME = {
        "entity_name": "admin_mobile_name",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": "",
    }
    SETTING_ADMIN_MOBILE_PLATFORM = {
        "entity_name": "admin_mobile_platform",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": "ios",
    }

    # Settings related to calendar
    CALENDAR_SETTINGS_INITIALISED = {
        "entity_name": "calendar_settings_initialised",
        "entity_type": "input_boolean",
        "value_type": "bool",
        "factory_default": False,
    }
    SETTING_CAR_CALENDAR_SOURCE = {
        "entity_name": "car_calendar_source",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": "Direct caldav source",
    }
    SETTING_INTEGRATION_CALENDAR_ENTITY_NAME = {
        "entity_name": "integration_calendar_entity_name",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": "",
    }
    SETTING_CALENDAR_ACCOUNT_INIT_URL = {
        "entity_name": "calendar_account_init_url",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
    }
    SETTING_CALENDAR_ACCOUNT_USERNAME = {
        "entity_name": "calendar_account_username",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
    }
    SETTING_CALENDAR_ACCOUNT_PASSWORD = {
        "entity_name": "calendar_account_password",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
    }
    SETTING_CAR_CALENDAR_NAME = {
        "entity_name": "car_calendar_name",
        "entity_type": "input_select",
        "value_type": "str",
        "factory_default": None,
    }

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

        self.hass.listen_event(
            self.__save_administrator_settings, "save_administrator_settings"
        )
        self.hass.listen_event(self.__save_calendar_settings, "save_calendar_settings")
        self.hass.listen_event(self.__save_charger_settings, "save_charger_settings")
        self.hass.listen_event(
            self.__save_electricity_contract_settings,
            "save_electricity_contract_settings",
        )
        self.hass.listen_event(self.__save_schedule_settings, "save_schedule_settings")
        self.hass.listen_event(self.__save_setting, "save_setting")
        # Listen to [TEST] buttons
        self.hass.listen_event(
            self.__test_charger_connection, "test_charger_connection"
        )
        self.hass.listen_event(self.__test_caldav_connection, "test_caldav_connection")
        self.hass.listen_event(
            self.__test_schedule_connection, "test_schedule_connection"
        )

        # TODO: Remove these and their scripts
        # self.hass.listen_event(self.__init_caldav_calendar, "TEST_CALENDAR_CONNECTION")
        # self.hass.listen_event(self.__test_fm_connection, "TEST_FM_CONNECTION")
        self.hass.listen_event(
            self.__reset_to_factory_defaults, "RESET_TO_FACTORY_DEFAULTS"
        )
        self.hass.listen_event(self.restart_v2g_liberty, "RESTART_HA")

        await self.__kick_off_settings()

        self.__log("Completed initializing V2GLibertyGlobals")

    ######################################################################
    #                         PUBLIC METHODS                             #
    ######################################################################

    # TODO: refactory this in a less indirect approach
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
            "sensor.charger_max_available_power", state=max_available_charge_power
        )

        # await self.__initialise_charger_settings()

    ######################################################################
    #                    INITIALISATION METHODS                          #
    ######################################################################

    async def __kick_off_settings(self):
        # To be called from initialise or restart event
        self.__log("__kick_off_settings called")

        self.v2g_settings.retrieve_settings()
        # TODO: Add a listener for changes in registered devices (smartphones with HA installed)?
        await self.__initialise_devices()
        await self.__initialise_notification_settings()

        await self.__initialise_charger_settings()
        await self.__initialise_electricity_contract_settings()
        await self.__initialise_calendar_settings()
        await self.__initialise_general_settings()
        # # FlexMeasures settings are influenced by the optimisation_ and general_settings.
        await self.__initialise_fm_client_settings()

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

        self.__log("Completed Initializing devices configuration")

    ######################################################################
    #                    CALLBACK METHODS FROM UI                        #
    ######################################################################

    async def __save_administrator_settings(self, event, data, kwargs):
        self.__store_setting("input_text.admin_mobile_name", data["mobileName"])
        self.__store_setting(
            "input_select.admin_mobile_platform", data["mobilePlatform"]
        )
        self.__store_setting("input_boolean.admin_settings_initialised", True)

        self.hass.fire_event("save_administrator_settings.result")

        await self.__initialise_notification_settings()

    async def __save_calendar_settings(self, event, data, kwargs):
        if data["source"] == "Direct caldav source":
            self.__store_setting("input_text.calendar_account_init_url", data["url"])
            self.__store_setting(
                "input_text.calendar_account_username", data["username"]
            )
            self.__store_setting(
                "input_text.calendar_account_password", data["password"]
            )
            # TODO: make text
            self.__store_setting("input_select.car_calendar_name", data["calendar"])
        else:
            # TODO: make text
            self.__store_setting(
                "input_select.integration_calendar_entity_name", data["calendar"]
            )
        # TODO: make text/boolean?
        self.__store_setting("input_select.car_calendar_source", data["source"])
        self.__store_setting("input_boolean.calendar_settings_initialised", True)

        self.hass.fire_event("save_calendar_settings.result")

        await self.__initialise_calendar_settings()
        await self.v2g_main_app.initialise_v2g_liberty()

    async def __save_charger_settings(self, event, data, kwargs):
        self.__store_setting("input_text.charger_host_url", data["host"])
        self.__store_setting("input_number.charger_port", data["port"])
        self.__store_setting(
            "input_boolean.use_reduced_max_charge_power",
            data["useReducedMaxChargePower"],
        )
        if data["useReducedMaxChargePower"]:
            self.__store_setting(
                "input_number.charger_max_charging_power", data["maxChargingPower"]
            )
            self.__store_setting(
                "input_number.charger_max_discharging_power",
                data["maxDischargingPower"],
            )
        self.__store_setting("input_boolean.charger_settings_initialised", True)

        self.hass.fire_event("save_charger_settings.result")

        await self.__initialise_charger_settings()
        await self.v2g_main_app.initialise_v2g_liberty()

    async def __save_electricity_contract_settings(self, event, data, kwargs):
        if data["contract"] == "nl_generic":
            self.__store_setting("input_number.energy_price_vat", data["vat"])
            self.__store_setting(
                "input_number.energy_price_markup_per_kwh", data["markup"]
            )
        if data["contract"] == "au_amber_electric":
            self.__store_setting(
                "input_text.own_consumption_price_entity_id",
                data["consumptionPriceEntity"],
            )
            self.__store_setting(
                "input_text.own_production_price_entity_id",
                data["productionPriceEntity"],
            )
        if data["contract"] == "gb_octopus_energy":
            self.__store_setting("input_text.octopus_import_code", data["importCode"])
            self.__store_setting("input_text.octopus_export_code", data["exportCode"])
            self.__store_setting("input_select.gb_dno_region", data["region"])
        # TODO: make this text
        self.__store_setting("input_select.electricity_provider", data["contract"])
        self.__store_setting(
            "input_boolean.electricity_contract_settings_initialised", True
        )
        self.hass.fire_event("save_electricity_contract_settings.result")

        await self.__initialise_electricity_contract_settings()
        await self.v2g_main_app.initialise_v2g_liberty()
        await self.fm_data_retrieve_client.finalize_initialisation(
            v2g_args="initialise_energy_contract"
        )

    async def __save_schedule_settings(self, event, data, kwargs):
        self.__store_setting("input_text.fm_account_username", data["username"])
        self.__store_setting("input_text.fm_account_password", data["password"])
        self.__store_setting(
            "input_boolean.fm_show_option_to_change_url", data["useOtherServer"]
        )
        self.__store_setting("input_text.fm_host_url", data["host"])
        self.__store_setting("input_text.fm_asset", data["asset"])
        self.__store_setting("input_boolean.schedule_settings_initialised", True)

        self.hass.fire_event("save_schedule_settings.result")

        await self.__initialise_fm_client_settings()
        await self.v2g_main_app.initialise_v2g_liberty()

    async def __save_setting(self, event, data, kwargs):
        self.__store_setting(data["entity"], data["value"])

        self.hass.fire_event("save_setting.result")

        if data["entity"] == "input_select.optimisation_mode":
            await self.__set_fm_optimisation_context()
        await self.__initialise_general_settings()
        await self.v2g_main_app.initialise_v2g_liberty()

    async def __test_caldav_connection(self, event=None, data=None, kwargs=None):
        self.__log("__test_caldav_connection called")
        url = data["url"]
        username = data["username"]
        password = data["password"]

        msg = "Successfully connected"
        calendars = self.calendar_client.test_caldav_connection(url, username, password)
        if isinstance(calendars, str):
            msg = calendars
            calendars = None

        self.hass.fire_event(
            "test_caldav_connection.result", msg=msg, calendars=calendars
        )

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
        host = data["host"]
        port = data["port"]
        (
            success,
            max_available_power,
        ) = await self.evse_client_app.test_charger_connection(host, port)
        msg = "Successfully connected" if success else "Failed to connect"
        self.__log(f'__test_charger_connection result: "{msg}", {max_available_power}')
        self.hass.fire_event(
            "test_charger_connection.result",
            msg=msg,
            max_available_power=max_available_power,
        )

    async def __test_schedule_connection(self, event, data, kwargs):
        self.__log("__test_schedule_connection called")
        username = data["username"]
        password = data["password"]
        use_other_server = data["useOtherServer"]
        host = data["host"] if use_other_server else c.FM_BASE_URL

        try:
            assets = await self.fm_client_app.test_fm_connection(
                host, username, password
            )
            msg = "Successfully connected"
        except:
            assets = None
            msg = "Failed to connect"

        self.hass.fire_event("test_schedule_connection.result", msg=msg, assets=assets)

    ######################################################################
    #                            HA METHODS                              #
    ######################################################################

    # FSC, move to a separate utils module?
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
            self.__log(
                f"create_persistent_notification failed. Exception: '{e}'",
                level="WARNING",
            )

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

    # FSC YESS, this can be ditched as in the cards this is generally replaced by a radiobutton group.
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

    # FSC YESS, this can be ditched as in the cards this is generally replaced by a radiobutton group.
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

    async def __process_setting(self, setting_object: dict):
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
                self.__store_setting(entity_id=entity_id, setting_value=return_value)
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

    async def __initialise_charger_settings(self):
        self.__log("__initialise_charger_settings called")

        is_initialised = await self.__process_setting(
            setting_object=self.CHARGER_SETTINGS_INITIALISED
        )
        if not is_initialised:
            return

        c.CHARGER_HOST_URL = await self.__process_setting(
            setting_object=self.SETTING_CHARGER_HOST_URL
        )
        c.CHARGER_PORT = await self.__process_setting(
            setting_object=self.SETTING_CHARGER_PORT
        )

        use_reduced_max_charge_power = await self.__process_setting(
            setting_object=self.SETTING_USE_REDUCED_MAX_CHARGE_POWER
        )
        if use_reduced_max_charge_power:
            # set c.CHARGER_MAX_CHARGE_POWER and c.CHARGER_MAX_DISCHARGE_POWER to max from settings page
            c.CHARGER_MAX_CHARGE_POWER = await self.__process_setting(
                setting_object=self.SETTING_CHARGER_MAX_CHARGE_POWER,
            )
            c.CHARGER_MAX_DISCHARGE_POWER = await self.__process_setting(
                setting_object=self.SETTING_CHARGER_MAX_DISCHARGE_POWER,
            )
        else:
            # set c.CHARGER_MAX_CHARGE_POWER and c.CHARGER_MAX_DISCHARGE_POWER to max from charger
            c.CHARGER_MAX_CHARGE_POWER = self.SETTING_CHARGER_MAX_CHARGE_POWER["max"]
            c.CHARGER_MAX_DISCHARGE_POWER = self.SETTING_CHARGER_MAX_DISCHARGE_POWER[
                "max"
            ]
        self.__log(
            f"__initialise_charger_settings \n"
            f"    c.CHARGER_MAX_CHARGE_POWER: {c.CHARGER_MAX_CHARGE_POWER}.\n"
            f"    c.CHARGER_MAX_DISCHARGE_POWER: {c.CHARGER_MAX_DISCHARGE_POWER}."
        )

        await self.evse_client_app.initialise_charger()

    async def __initialise_notification_settings(self):
        self.__log("__initialise_notification_settings called")

        is_initialised = await self.__process_setting(
            setting_object=self.ADMIN_SETTINGS_INITIALISED
        )
        if not is_initialised:
            return

        c.ADMIN_MOBILE_NAME = await self.__process_setting(
            setting_object=self.SETTING_ADMIN_MOBILE_NAME
        )
        c.ADMIN_MOBILE_PLATFORM = await self.__process_setting(
            setting_object=self.SETTING_ADMIN_MOBILE_PLATFORM
        )

        # Assume iOS as standard
        # FSC: Move to module where notifications are sent.
        c.PRIORITY_NOTIFICATION_CONFIG = {
            "push": {"sound": {"critical": 1, "name": "default", "volume": 0.9}}
        }
        if c.ADMIN_MOBILE_PLATFORM.lower() == "android":
            c.PRIORITY_NOTIFICATION_CONFIG = {"ttl": 0, "priority": "high"}

        self.__log("__initialise_notification_settings completed")

    async def __initialise_general_settings(self):
        self.__log("__initialise_general_settings called")

        c.OPTIMISATION_MODE = await self.__process_setting(
            setting_object=self.SETTING_OPTIMISATION_MODE,
        )

        c.CAR_CONSUMPTION_WH_PER_KM = await self.__process_setting(
            setting_object=self.SETTING_CAR_CONSUMPTION_WH_PER_KM,
        )
        c.USAGE_PER_EVENT_TIME_INTERVAL = (
            c.KM_PER_HOUR_OF_CALENDAR_ITEM * c.CAR_CONSUMPTION_WH_PER_KM / 1000
        ) / (60 / c.FM_EVENT_RESOLUTION_IN_MINUTES)

        c.CAR_MAX_CAPACITY_IN_KWH = await self.__process_setting(
            setting_object=self.SETTING_CAR_MAX_CAPACITY_IN_KWH,
        )

        c.CAR_MIN_SOC_IN_KWH = (
            c.CAR_MAX_CAPACITY_IN_KWH * c.CAR_MIN_SOC_IN_PERCENT / 100
        )
        c.CAR_MIN_SOC_IN_PERCENT = await self.__process_setting(
            setting_object=self.SETTING_CAR_MIN_SOC_IN_PERCENT,
        )
        c.CAR_MAX_SOC_IN_PERCENT = await self.__process_setting(
            setting_object=self.SETTING_CAR_MAX_SOC_IN_PERCENT,
        )
        c.CAR_MAX_SOC_IN_KWH = (
            c.CAR_MAX_CAPACITY_IN_KWH * c.CAR_MAX_SOC_IN_PERCENT / 100
        )

        c.ALLOWED_DURATION_ABOVE_MAX_SOC = await self.__process_setting(
            setting_object=self.SETTING_ALLOWED_DURATION_ABOVE_MAX_SOC_IN_HRS,
        )

        c.CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY = await self.__process_setting(
            setting_object=self.SETTING_CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY
        )
        c.ROUNDTRIP_EFFICIENCY_FACTOR = c.CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY / 100

        self.__log("__initialise_general_settings completed")

    async def __initialise_calendar_settings(self):
        self.__log("__initialise_calendar_settings called")

        is_initialised = await self.__process_setting(
            setting_object=self.CALENDAR_SETTINGS_INITIALISED
        )
        if not is_initialised:
            return

        c.CAR_CALENDAR_SOURCE = await self.__process_setting(
            setting_object=self.SETTING_CAR_CALENDAR_SOURCE,
        )
        if c.CAR_CALENDAR_SOURCE == "Direct caldav source":
            c.CALENDAR_ACCOUNT_INIT_URL = await self.__process_setting(
                setting_object=self.SETTING_CALENDAR_ACCOUNT_INIT_URL,
            )
            c.CALENDAR_ACCOUNT_USERNAME = await self.__process_setting(
                setting_object=self.SETTING_CALENDAR_ACCOUNT_USERNAME,
            )
            c.CALENDAR_ACCOUNT_PASSWORD = await self.__process_setting(
                setting_object=self.SETTING_CALENDAR_ACCOUNT_PASSWORD,
            )
            c.CAR_CALENDAR_NAME = await self.__process_setting(
                setting_object=self.SETTING_CAR_CALENDAR_NAME
            )

            await self.calendar_client.initialise_calendar()
        else:
            c.INTEGRATION_CALENDAR_ENTITY_NAME = await self.__process_setting(
                setting_object=self.SETTING_INTEGRATION_CALENDAR_ENTITY_NAME,
            )

            res = await self.calendar_client.initialise_calendar()
            self.__log(
                f"__initialise_calendar_settings: init HA calendar result: '{res}'."
            )

        self.__log("__initialise_calendar_settings completed")

    async def __initialise_fm_client_settings(self):
        # Split for future when the python lib fm_client_app is used: that needs to be re-inited
        self.__log("__initialise_fm_client_settings called")

        is_initialised = await self.__process_setting(
            setting_object=self.SCHEDULE_SETTINGS_INITIALISED
        )
        if not is_initialised:
            return

        c.FM_ACCOUNT_USERNAME = await self.__process_setting(
            setting_object=self.SETTING_FM_ACCOUNT_USERNAME,
        )
        c.FM_ACCOUNT_PASSWORD = await self.__process_setting(
            setting_object=self.SETTING_FM_ACCOUNT_PASSWORD,
        )
        use_other_url = await self.__process_setting(
            setting_object=self.SETTING_USE_OTHER_FM_BASE_URL,
        )
        if use_other_url:
            c.FM_BASE_URL = await self.__process_setting(
                setting_object=self.SETTING_FM_BASE_URL,
            )
        else:
            c.FM_BASE_URL = self.SETTING_FM_BASE_URL["factory_default"]

        asset_name = await self.__process_setting(setting_object=self.SETTING_FM_ASSET)

        await self.fm_client_app.initialise_and_test_fm_client()
        sensors = await self.fm_client_app.get_fm_sensors_by_asset_name(asset_name)
        await self.__process_fm_sensors(sensors)
        await self.__set_fm_optimisation_context()

        self.__log("__initialise_fm_client_settings completed")

    async def __process_fm_sensors(self, sensors):
        self.__log("__process_fm_sensors called")

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
                # self.__log(f"__process_fm_sensors for au_amber_electric/gb_octopus_energy, sensor: '{sensor}'.")
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
            f"__process_fm_sensors: \n"
            f"    c.FM_ACCOUNT_POWER_SENSOR_ID: {c.FM_ACCOUNT_POWER_SENSOR_ID}. \n"
            f"    c.FM_ACCOUNT_AVAILABILITY_SENSOR_ID: {c.FM_ACCOUNT_AVAILABILITY_SENSOR_ID}. \n"
            f"    c.FM_ACCOUNT_SOC_SENSOR_ID: {c.FM_ACCOUNT_SOC_SENSOR_ID}. \n"
            f"    c.FM_ACCOUNT_COST_SENSOR_ID: {c.FM_ACCOUNT_COST_SENSOR_ID}."
        )

        if c.ELECTRICITY_PROVIDER in ["au_amber_electric", "gb_octopus_energy"]:
            self.__log(
                f"__process_fm_sensors, (own_prices): \n"
                f"    c.FM_PRICE_CONSUMPTION_SENSOR_ID:  {c.FM_PRICE_CONSUMPTION_SENSOR_ID}. \n"
                f"    c.FM_PRICE_PRODUCTION_SENSOR_ID:  {c.FM_PRICE_PRODUCTION_SENSOR_ID}. \n"
                f"    c.FM_EMISSIONS_SENSOR_ID: {c.FM_EMISSIONS_SENSOR_ID}."
            )

        self.__log("__process_fm_sensors completed")

    async def __initialise_electricity_contract_settings(self):
        self.__log("__initialise_electricity_contract_settings called")

        is_initialised = await self.__process_setting(
            setting_object=self.ELECTRICITY_CONTRACT_SETTINGS_INITIALISED
        )
        if not is_initialised:
            return

        c.ELECTRICITY_PROVIDER = await self.__process_setting(
            setting_object=self.SETTING_ELECTRICITY_PROVIDER,
        )

        if c.ELECTRICITY_PROVIDER == "au_amber_electric":
            c.HA_OWN_CONSUMPTION_PRICE_ENTITY_ID = await self.__process_setting(
                setting_object=self.SETTING_OWN_CONSUMPTION_PRICE_ENTITY_ID,
            )
            c.HA_OWN_PRODUCTION_PRICE_ENTITY_ID = await self.__process_setting(
                setting_object=self.SETTING_OWN_PRODUCTION_PRICE_ENTITY_ID,
            )
            c.UTILITY_CONTEXT_DISPLAY_NAME = "Amber Electric"
            c.EMISSIONS_UOM = "%"
            c.CURRENCY = "AUD"
            c.PRICE_RESOLUTION_MINUTES = 30

            await self.amber_price_data_manager.kick_off_amber_price_management()

        elif c.ELECTRICITY_PROVIDER == "gb_octopus_energy":
            c.OCTOPUS_IMPORT_CODE = await self.__process_setting(
                setting_object=self.SETTING_OCTOPUS_IMPORT_CODE,
            )
            c.OCTOPUS_EXPORT_CODE = await self.__process_setting(
                setting_object=self.SETTING_OCTOPUS_EXPORT_CODE,
            )
            c.GB_DNO_REGION = await self.__process_setting(
                setting_object=self.SETTING_GB_DNO_REGION,
            )
            c.UTILITY_CONTEXT_DISPLAY_NAME = "Octopus Energy"
            c.EMISSIONS_UOM = "kg/MWh"
            c.CURRENCY = "GBP"
            c.PRICE_RESOLUTION_MINUTES = 30

            await self.octopus_price_data_manager.kick_off_octopus_price_management()

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
                f"__initialise_electricity_contract_settings:\n"
                f"    FM_PRICE_PRODUCTION_SENSOR_ID: {c.FM_PRICE_PRODUCTION_SENSOR_ID}.\n"
                f"    FM_PRICE_CONSUMPTION_SENSOR_ID: {c.FM_PRICE_CONSUMPTION_SENSOR_ID}.\n"
                f"    FM_EMISSIONS_SENSOR_ID: {c.FM_EMISSIONS_SENSOR_ID}.\n"
                f"    UTILITY_CONTEXT_DISPLAY_NAME: {c.UTILITY_CONTEXT_DISPLAY_NAME}."
            )

        c.USE_VAT_AND_MARKUP = await self.__process_setting(
            setting_object=self.SETTING_USE_VAT_AND_MARKUP,
        )
        # Only relevant for electricity_providers "generic" and possibly for none EPEX,
        # for others we expect netto prices (including VAT and Markup).
        # If self_provided data (e.g. au_amber_electric, gb_octopus_energy) also includes VAT and markup.
        if c.USE_VAT_AND_MARKUP:
            c.ENERGY_PRICE_VAT = await self.__process_setting(
                setting_object=self.SETTING_ENERGY_PRICE_VAT,
            )
            c.ENERGY_PRICE_MARKUP_PER_KWH = await self.__process_setting(
                setting_object=self.SETTING_ENERGY_PRICE_MARKUP_PER_KWH,
            )
        else:
            # Not reset VAT/MARKUP to factory defaults, the calculations in get_fm_data are based on USE_VAT_AND_MARKUP
            pass

        await self.__set_fm_optimisation_context()

    # @Ard: is deze methode obsolete?
    #       Volgens VS code wordt hij niet aangeroepen.
    # @Ronald: Ja! Dit is vervangen door settingsmanager reset

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
        is_electricity_contract_initialised = await self.__process_setting(
            setting_object=self.ELECTRICITY_CONTRACT_SETTINGS_INITIALISED
        )
        are_schedule_settings_initialised = await self.__process_setting(
            setting_object=self.SCHEDULE_SETTINGS_INITIALISED
        )
        if (
            not is_electricity_contract_initialised
            or not are_schedule_settings_initialised
        ):
            return

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


def parse_to_int(number_string, default_value: int):
    """Reliably parse a string, float or int to an int. If un-parsable return the default value.
    :param number_string: str, float, int, bool (not dict or list)
    :param default_value: int that is returned if paring failed.
    :return: parsed int
    """
    try:
        return int(float(number_string))
    except:
        return default_value
