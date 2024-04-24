from datetime import datetime, timedelta
import pytz
import asyncio
import json
import appdaemon.plugins.hass.hassapi as hass
import constants as c


class V2GLibertyGlobals(hass.Hass):
    v2g_main_app: object
    evse_client: object

    # Entity to store entity (id's) that have been initialised,
    # so we know they should not be overwritten with the default setting_value.
    # This entity should never show up in the UI.
    ha_initiated_entities_id: str = "input_text.initiated_ha_entities"
    ha_initiated_entities: list = []

    SETTING_FM_ACCOUNT_USERNAME = {
        "entity_name": "fm_account_username",
        "entity_type": "input_text",
        "listen": True
    }
    SETTING_FM_ACCOUNT_PASSWORD = {
        "entity_name": "fm_account_password",
        "entity_type": "input_text",
        "listen": True
    }
    SETTING_FM_BASE_URL = {
        "entity_name": "fm_host_url",
        "entity_type": "input_text",
        "listen": True
    }

    # Sensors for sending data to FM
    SETTING_FM_ACCOUNT_POWER_SENSOR_ID = {
        "entity_name": "fm_account_power_sensor_id",
        "entity_type": "input_number",
        "listen": True
    }
    SETTING_FM_ACCOUNT_AVAILABILITY_SENSOR_ID = {
        "entity_name": "fm_account_availability_sensor_id",
        "entity_type": "input_number",
        "listen": True
    }
    SETTING_FM_ACCOUNT_SOC_SENSOR_ID = {
        "entity_name": "fm_account_soc_sensor_id",
        "entity_type": "input_number",
        "listen": True
    }
    SETTING_FM_ACCOUNT_COST_SENSOR_ID = {
        "entity_name": "fm_account_cost_sensor_id",
        "entity_type": "input_number",
        "listen": True
    }

    # Sensors for optimisation context in case of self_provided
    SETTING_FM_PRICE_PRODUCTION_SENSOR_ID = {
        "entity_name": "fm_own_price_production_sensor_id",
        "entity_type": "input_number",
        "listen": True
    }
    SETTING_FM_PRICE_CONSUMPTION_SENSOR_ID = {
        "entity_name": "fm_own_price_consumption_sensor_id",
        "entity_type": "input_number",
        "listen": True
    }
    SETTING_FM_EMISSIONS_SENSOR_ID = {
        "entity_name": "fm_own_emissions_sensor_id",
        "entity_type": "input_number",
        "listen": True
    }
    SETTING_UTILITY_CONTEXT_DISPLAY_NAME = {
        "entity_name": "fm_own_context_display_name",
        "entity_type": "input_text",
        "listen": True
    }

    SETTING_OPTIMISATION_MODE = {
        "entity_name": "optimisation_mode",
        "entity_type": "input_select",
        "listen": True
    }
    SETTING_ELECTRICITY_PROVIDER = {
        "entity_name": "electricity_provider",
        "entity_type": "input_select",
        "listen": True
    }

    # Settings related to charger
    SETTING_CHARGER_HOST_URL = {
        "entity_name": "charger_host_url",
        "entity_type": "input_text",
        "listen": True
    }
    SETTING_CHARGER_PORT = {
        "entity_name": "charger_port",
        "entity_type": "input_number",
        "listen": True
    }
    SETTING_CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY = {
        "entity_name": "charger_plus_car_roundtrip_efficiency",
        "entity_type": "input_number",
        "listen": True
    }
    SETTING_CAR_MAX_CAPACITY_IN_KWH = {
        "entity_name": "car_max_capacity_in_kwh",
        "entity_type": "input_number",
        "listen": True
    }
    SETTING_CAR_CONSUMPTION_WH_PER_KM = {
        "entity_name": "car_consumption_wh_per_km",
        "entity_type": "input_number",
        "listen": True
    }
    
    SETTING_CAR_MIN_SOC_IN_PERCENT = {
        "entity_name": "car_min_soc_in_percent",
        "entity_type": "input_number",
        "listen": True
    }
    SETTING_CAR_MAX_SOC_IN_PERCENT = {
        "entity_name": "car_max_soc_in_percent",
        "entity_type": "input_number",
        "listen": True
    }
    SETTING_ALLOWED_DURATION_ABOVE_MAX_SOC_IN_HRS = {
        "entity_name": "allowed_duration_above_max_soc_in_hrs",
        "entity_type": "input_number",
        "listen": True
    }

    SETTING_CHARGER_MAX_CHARGE_POWER = {
        "entity_name": "charger_max_charging_power",
        "entity_type": "input_number",
        "min": 1380,
        "max": 25000,
        "listen": True
    }
    SETTING_CHARGER_MAX_DISCHARGE_POWER = {
        "entity_name": "charger_max_discharging_power",
        "entity_type": "input_number",
        "min": 1380,
        "max": 25000,
        "listen": True
    }

    # Settings related to showing prices
    SETTING_ENERGY_PRICE_VAT = {
        "entity_name": "energy_price_vat",
        "entity_type": "input_number",
        "listen": True
    }
    SETTING_ENERGY_PRICE_MARKUP_PER_KWH = {
        "entity_name": "energy_price_markup_per_kwh",
        "entity_type": "input_number",
        "listen": True
    }
    HELPER_ADMIN_MOBILE_NAME = {
        "entity_name": "admin_mobile_name",
        "entity_type": "input_select",
        "listen": True
    }
    SETTING_ADMIN_MOBILE_NAME = {
        "entity_name": "admin_mobile_name",
        "entity_type": "input_text",
        "listen": True
    }
    SETTING_ADMIN_MOBILE_PLATFORM = {
        "entity_name": "admin_mobile_platform",
        "entity_type": "input_select",
        "listen": True
    }


    # Used by method __collect_action_triggers
    action_handle = None

    async def initialize(self):
        self.log("Initializing V2GLibertyGlobals")
        c.TZ = pytz.timezone(self.get_timezone())

        self.v2g_main_app = await self.get_app("v2g_liberty")
        self.evse_client = await self.get_app("modbus_evse_client")

        await self.__get_initiated_entities()
        await self.__initialise_devices()
        await self.__read_and_process_charger_settings()
        await self.__read_and_process_fm_client_settings()
        await self.__read_and_process_general_settings()
        await self.__keep_admin_select_in_sync()

        self.listen_event(self.__test_charger_connection, "TEST_CHARGER_CONNECTION")

        # Was None, which blocks processing during initialisation
        self.action_handle = ""
        self.log("Completed initializing V2GLibertyGlobals")


    async def __initialise_devices(self):
        # List of all the recipients to notify
        # Check if Admin is configured correctly
        # Warn user about bad config with persistent notification in UI.
        self.log("Initializing devices configuration")

        c.NOTIFICATION_RECIPIENTS.clear()
        # Service "mobile_app_" seems more reliable than using get_trackers,
        # as these names do not always match with the service.
        for service in self.list_services():
            if service["service"].startswith("mobile_app_"):
                c.NOTIFICATION_RECIPIENTS.append(service["service"].replace("mobile_app_", ""))
                # TODO?? Get current selected option and set it again after populating

        if len(c.NOTIFICATION_RECIPIENTS) == 0:
            message = f"No mobile devices (e.g. phone, tablet, etc.) have been registered in Home Assistant " \
                      f"for notifications.<br/>" \
                      f"It is highly recommended to do so. Please install the HA companion app on your mobile device " \
                      f"and connect it to Home Assistant.<br/>"
            self.log(f"Configuration error: {message}.")
            # TODO: Research if showing this only to admin users is possible.
            await self.call_service('persistent_notification/create', title="Configuration error", message=message,
                              notification_id="notification_config_error")
        else:
            self.log(f"__initialise_devices - recipients for notifications: {c.NOTIFICATION_RECIPIENTS}.")

            self.call_service("input_select/set_options", entity_id="input_select.admin_mobile_name", options=c.NOTIFICATION_RECIPIENTS)
            stored_admin_name = await self.get_state("input_text.admin_mobile_name", attribute='state')
            if stored_admin_name in ["", "unknown", "undefined"]:
                stored_admin_name = c.NOTIFICATION_RECIPIENTS[0]
            # Select the right option
            self.log(f"__initialise_devices - admin_mobile_name: {stored_admin_name}.")
            await self.__write_setting(self.HELPER_ADMIN_MOBILE_NAME, stored_admin_name)

        self.log("Completed Initializing devices configuration")


    async def __test_charger_connection(self, event, data, kwargs):
        """ Tests the connection with the charger and processes the maximum charge power read from the charger
            Called from the settings page."""
        self.log("__test_charger_connection called")
        # The url and port settings have been changed via the listener
        await self.set_state("input_text.charger_connection_status", state="Trying to connect...")
        if not await self.evse_client.initialise_charger():
            await self.set_state("input_text.charger_connection_status", state="Failed to connect")
            return
        # min/max power is set self.evse_client.initialise_charger()
        await self.set_state("input_text.charger_connection_status", state="Successfully connected to charger!")


    async def __get_initiated_entities(self):
        self.log(f"__get_initiated_entities called")
        str_list = await self.get_state(self.ha_initiated_entities_id, attribute="storage")
        self.log(f"__get_initiated_entities, str_list: {str_list}")
        if str_list is None:
            self.ha_initiated_entities = []
        else:
            self.ha_initiated_entities = list(json.loads(str_list))
        self.log(f"__get_initiated_entities, self.ha_initiated_entities: {self.ha_initiated_entities}")

    async def __add_initiated_entity(self, entity_id: str):
        # self.log(f"__add_initiated_entity, entity_id: {entity_id}")
        self.ha_initiated_entities.append(entity_id)
        # Remove any duplicates
        self.ha_initiated_entities = list(set(self.ha_initiated_entities))
        # self.log(f"__add_initiated_entity, self.ha_initiated_entities: {self.ha_initiated_entities}")
        str_list = json.dumps(self.ha_initiated_entities)
        # self.log(f"__add_initiated_entity, to write str_list: {str_list}")
        await self.set_state(self.ha_initiated_entities_id, storage=str_list)

    async def __write_setting(self, setting: dict, setting_value, min_allowed_value = None, max_allowed_value = None):
        """
           This method writes the value to the HA entity and registers the entity_id as initialised
           by calling __add_initiated_entity().
           The min_allowed_value and max_allowed_value are only used in combination with an input_number and input text
           These set the min/max attributes of the entity so that input validation can word accordingly.
        """
        entity_name = setting['entity_name']
        entity_type = setting['entity_type']
        entity_id = f"{entity_type}.{entity_name}"
        self.log(f'__write_setting called with value {setting_value} for entity {entity_id}.')

        if setting_value is not None:
            await self.__add_initiated_entity(entity_id)
            # constant_to_set has a relevant setting_value to set to HA
            if entity_type == "input_select":
                await self.select_option(entity_id, setting_value)
            elif entity_type == "input_boolean":
                if setting_value is True or setting_value.lower() in ["true", "on"]:
                    await self.turn_on(entity_id)
                else:
                    await self.turn_off(entity_id)
            else:
                new_attributes = {}
                if min_allowed_value:
                    new_attributes["min"] = min_allowed_value
                if max_allowed_value:
                    new_attributes["max"] = max_allowed_value

                self.log(f"__write_setting() attributes-to-write: {new_attributes}.")

                # Assume input_text or input_number
                if new_attributes:
                    await self.set_state(entity_id, state=setting_value, attributes=new_attributes)
                    # Unfortunately the UI does not pickup these new limits... so need to check locally.
                else:
                    await self.set_state(entity_id, state=setting_value)

    async def __process_setting(self, constant_to_set: object, setting: dict, callback):
        """
           This method checks if the setting-entity is empty, if so:
           - set the setting-entity setting_value to the default that is set in constants
           if not empty:
           - return the setting_value of the setting-entity
        """
        entity_name = setting['entity_name']
        entity_type = setting['entity_type']
        entity_id = f"{entity_type}.{entity_name}"
        setting_entity = await self.get_state(entity_id, attribute="all")
        if setting_entity is None:
            self.log(f" __process_setting, Error: HA entity '{entity_id}' not found.")
            return

        # self.log(f"HA entity: {setting_entity}.")
        setting_entity_value = setting_entity['state']

        ha_entity_should_be_overwritten_with_default = False
        if entity_type == "input_text":
            # This works for input_text, the default setting_value can be "", this is the preferred way
            # of checking if the HA entity should be overwritten.
            if setting_entity_value is None or setting_entity_value in ["", "unknown", "undefined"]:
                ha_entity_should_be_overwritten_with_default = True
        else:
            # For input_number the un-initialised ha-state-setting_value is the min setting_value.
            # For input_select the default setting_value is the first option
            # For input_boolean the default state is True

            # We use self.ha_initiated_entities to store whether
            # the state of the entity has been set through this code.
            # self.log(f"__process_setting check if inited. id: {entity_id} "
            #          f"in {self.ha_initiated_entities} ({type(self.ha_initiated_entities)}).")
            if entity_id not in self.ha_initiated_entities:
                # self.log("__process_setting: NO, so do set default to HA")
                ha_entity_should_be_overwritten_with_default = True
            else:
                # self.log("__process_setting: YES, keep current setting.")
                pass

        return_value = constant_to_set

        if ha_entity_should_be_overwritten_with_default:
            # HA setting-entity was empty, populate with the default from constants if that is not
            if constant_to_set is not None and constant_to_set != "":
                # self.log(f"__process_setting, setting HA entity '{entity_id}' to default setting_value '{constant_to_set}'.")
                await self.__write_setting(setting = setting, setting_value=constant_to_set)
        else:
            if entity_type == "input_number":
                msg = ""
                min_value = setting.get('min', None)
                max_value = setting.get('max', None)
                if type(constant_to_set) == "<class 'float'>":
                    return_value = float(setting_entity_value)
                    rv = return_value
                    if min_value:
                        min_value = float(min_value)
                        rv = max(min_value, return_value)
                    if max_value:
                        max_value = float(max_value)
                        rv = min(max_value, return_value)
                    if rv != return_value:
                        msg = "changed"
                        return_value = rv
                else:
                    # Assume int
                    return_value = int(float(setting_entity_value))
                    rv = return_value
                    if min_value:
                        min_value = int(float(min_value))
                        rv = max(min_value, return_value)
                    if max_value:
                        max_value = int(float(max_value))
                        rv = min(max_value, return_value)
                    if rv != return_value:
                        msg = "changed"
                        return_value = rv
                if msg != "":
                    await self.__write_setting(setting = setting, setting_value=rv, min_allowed_value=min_value, max_allowed_value=max_value)
                    msg = f"Adjusted '{entity_name}' to '{return_value}' to stay within limits."
                    ntf_id = f"auto_adjusted_setting_{entity_id}"
                    self.create_persistent_notification(message=msg, title='Automatically adjusted setting', notification_id=ntf_id)
            else:
                # input_text, input_select, input_boolean
                return_value = setting_entity_value

        # Setting listener
        if setting['listen']:
            setting['listen'] = False
            self.listen_state(callback, entity_id, attribute="all")

        # Not an exact match of the constant name but good enough for logging
        message = f"v2g_globals, __process_setting set c.{entity_name.upper()} to "
        mode = setting_entity['attributes'].get('mode', 'none').lower()
        if mode == "password":
            message = f"{message} '********'"
        else:
            message = f"{message} '{return_value}'"

        uom = setting_entity['attributes'].get('unit_of_measurement')
        if uom:
            message = f"{message} {uom}."
        else:
            message = f"{message}."
        self.log(message)

        return return_value


    async def __read_and_process_charger_settings(self, entity=None, attribute=None, old=None, new=None, kwargs=None):

        callback_method = self.__read_and_process_charger_settings

        c.CHARGER_HOST_URL = await self.__process_setting(
            constant_to_set=c.CHARGER_HOST_URL,
            setting=self.SETTING_CHARGER_HOST_URL,
            callback=callback_method
        )
        c.CHARGER_PORT = await self.__process_setting(
            constant_to_set=c.CHARGER_PORT,
            setting=self.SETTING_CHARGER_PORT,
            callback=callback_method
        )
        c.CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY = await self.__process_setting(
            constant_to_set=c.CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY,
            setting=self.SETTING_CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY,
            callback=callback_method
        )
        c.ROUNDTRIP_EFFICIENCY_FACTOR = c.CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY/100

        c.CHARGER_MAX_CHARGE_POWER = await self.__process_setting(
            constant_to_set=c.CHARGER_MAX_CHARGE_POWER,
            setting=self.SETTING_CHARGER_MAX_CHARGE_POWER,
            callback=callback_method
        )
        c.CHARGER_MAX_DISCHARGE_POWER = await self.__process_setting(
            constant_to_set=c.CHARGER_MAX_DISCHARGE_POWER,
            setting=self.SETTING_CHARGER_MAX_DISCHARGE_POWER,
            callback=callback_method
        )
        if kwargs is not None and kwargs.get('run_once', False):
            # To prevent a loop
            return
        await self.__collect_action_triggers(source="changed charger_settings")

    async def __keep_admin_select_in_sync(self, entity=None, attribute=None, old=None, new=None, kwargs=None):
        # self.log(f"__keep_admin_select_in_sync called")
        admin = ""
        admin = await self.__process_setting(
            constant_to_set=admin,
            setting=self.HELPER_ADMIN_MOBILE_NAME,
            callback=self.__keep_admin_select_in_sync
        )
        # self.log(f"__keep_admin_select_in_sync admin: {admin}")
        await self.__write_setting(self.SETTING_ADMIN_MOBILE_NAME, setting_value=admin)
        

    async def __read_and_process_notification_settings(self, entity=None, attribute=None, old=None, new=None, kwargs=None):
        callback_method = self.__read_and_process_notification_settings
        
        # This only changed by select that holds the options for admin, set by __init. It is not shown in th UI.
        c.ADMIN_MOBILE_NAME = await self.__process_setting(
            constant_to_set=c.ADMIN_MOBILE_NAME,
            setting=self.SETTING_ADMIN_MOBILE_NAME,
            callback=callback_method
        )
        if c.ADMIN_MOBILE_NAME not in c.NOTIFICATION_RECIPIENTS:
            message = f"The admin mobile name ***{c.ADMIN_MOBILE_NAME}*** in configuration is not a registered.<br/>" \
                      f"Please choose one in the settings view."
            self.log(f"Configuration error: {message}.")
            # TODO: Research if showing this only to admin users is possible.
            self.call_service('persistent_notification/create', title="Configuration error", message=message,
                              notification_id="notification_config_error_no_admin")
            c.ADMIN_MOBILE_NAME = c.NOTIFICATION_RECIPIENTS[0]

        c.ADMIN_MOBILE_PLATFORM = await self.__process_setting(
            constant_to_set=c.ADMIN_MOBILE_PLATFORM,
            setting=self.SETTING_ADMIN_MOBILE_PLATFORM,
            callback=callback_method
        )
        # Assume iOS as standard
        c.PRIORITY_NOTIFICATION_CONFIG = {"push": {"sound": {"critical": 1, "name": "default", "volume": 0.9}}}
        if c.ADMIN_MOBILE_PLATFORM == "android":
            c.PRIORITY_NOTIFICATION_CONFIG = {"ttl": 0, "priority": "high"}



    async def __read_and_process_general_settings(self, entity=None, attribute=None, old=None, new=None, kwargs=None):
        callback_method = self.__read_and_process_general_settings

        c.CAR_CONSUMPTION_WH_PER_KM = await self.__process_setting(
            constant_to_set=c.CAR_CONSUMPTION_WH_PER_KM,
            setting=self.SETTING_CAR_CONSUMPTION_WH_PER_KM,
            callback=callback_method
        )
        c.CAR_MAX_CAPACITY_IN_KWH = await self.__process_setting(
            constant_to_set=c.CAR_MAX_CAPACITY_IN_KWH,
            setting=self.SETTING_CAR_MAX_CAPACITY_IN_KWH,
            callback=callback_method
        )
        c.CAR_MIN_SOC_IN_PERCENT = await self.__process_setting(
            constant_to_set=c.CAR_MIN_SOC_IN_PERCENT,
            setting=self.SETTING_CAR_MIN_SOC_IN_PERCENT,
            callback=callback_method
        )
        c.CAR_MAX_SOC_IN_PERCENT = await self.__process_setting(
            constant_to_set=c.CAR_MAX_SOC_IN_PERCENT,
            setting=self.SETTING_CAR_MAX_SOC_IN_PERCENT,
            callback=callback_method
        )
        c.ALLOWED_DURATION_ABOVE_MAX_SOC = await self.__process_setting(
            constant_to_set=c.ALLOWED_DURATION_ABOVE_MAX_SOC,
            setting=self.SETTING_ALLOWED_DURATION_ABOVE_MAX_SOC_IN_HRS,
            callback=callback_method
        )

        c.CAR_MIN_SOC_IN_KWH = c.CAR_MAX_CAPACITY_IN_KWH * c.CAR_MIN_SOC_IN_PERCENT / 100
        c.CAR_MAX_SOC_IN_KWH = c.CAR_MAX_CAPACITY_IN_KWH * c.CAR_MAX_SOC_IN_PERCENT / 100

        await self.__collect_action_triggers(source="changed general_settings")

    async def __read_and_process_fm_client_settings(self, entity=None, attribute=None, old=None, new=None, kwargs=None):
        # Split for future when the python lib fm_client is used: that needs to be re-inited
        self.log("__read_and_process_fm_client_settings called")

        callback_method = self.__read_and_process_fm_client_settings
        c.FM_ACCOUNT_USERNAME = await self.__process_setting(
            constant_to_set=c.FM_ACCOUNT_USERNAME,
            setting=self.SETTING_FM_ACCOUNT_USERNAME,
            callback=callback_method
        )
        c.FM_ACCOUNT_PASSWORD = await self.__process_setting(
            constant_to_set=c.FM_ACCOUNT_PASSWORD,
            setting=self.SETTING_FM_ACCOUNT_PASSWORD,
            callback=callback_method
        )
        c.FM_BASE_URL = await self.__process_setting(
            constant_to_set=c.FM_BASE_URL,
            setting=self.SETTING_FM_BASE_URL,
            callback=callback_method
        )
        c.FM_ACCOUNT_POWER_SENSOR_ID = await self.__process_setting(
            constant_to_set=c.FM_ACCOUNT_POWER_SENSOR_ID,
            setting=self.SETTING_FM_ACCOUNT_POWER_SENSOR_ID,
            callback=callback_method
        )
        c.FM_ACCOUNT_AVAILABILITY_SENSOR_ID = await self.__process_setting(
            constant_to_set=c.FM_ACCOUNT_AVAILABILITY_SENSOR_ID,
            setting=self.SETTING_FM_ACCOUNT_AVAILABILITY_SENSOR_ID,
            callback=callback_method
        )
        c.FM_ACCOUNT_SOC_SENSOR_ID = await self.__process_setting(
            constant_to_set=c.FM_ACCOUNT_SOC_SENSOR_ID,
            setting=self.SETTING_FM_ACCOUNT_SOC_SENSOR_ID,
            callback=callback_method
        )
        c.FM_ACCOUNT_COST_SENSOR_ID = await self.__process_setting(
            constant_to_set=c.FM_ACCOUNT_COST_SENSOR_ID,
            setting=self.SETTING_FM_ACCOUNT_COST_SENSOR_ID,
            callback=callback_method
        )

        c.OPTIMISATION_MODE = await self.__process_setting(
            constant_to_set=c.OPTIMISATION_MODE,
            setting=self.SETTING_OPTIMISATION_MODE,
            callback=callback_method
        )
        c.ELECTRICITY_PROVIDER = await self.__process_setting(
            constant_to_set=c.ELECTRICITY_PROVIDER,
            setting=self.SETTING_ELECTRICITY_PROVIDER,
            callback=callback_method
        )

        # If the price and emissions data is provided to FM by V2G Liberty (noe EPEX markets)
        # this is labeled as "self_provided".
        if c.ELECTRICITY_PROVIDER == "self_provided":
            c.FM_PRICE_PRODUCTION_SENSOR_ID = await self.__process_setting(
                constant_to_set=c.FM_PRICE_PRODUCTION_SENSOR_ID,
                setting=self.SETTING_FM_PRICE_PRODUCTION_SENSOR_ID,
                callback=callback_method
            )
            c.FM_PRICE_CONSUMPTION_SENSOR_ID = await self.__process_setting(
                constant_to_set=c.FM_PRICE_CONSUMPTION_SENSOR_ID,
                setting=self.SETTING_FM_PRICE_CONSUMPTION_SENSOR_ID,
                callback=callback_method
            )
            c.FM_EMISSIONS_SENSOR_ID = await self.__process_setting(
                constant_to_set=c.FM_EMISSIONS_SENSOR_ID,
                setting=self.SETTING_FM_EMISSIONS_SENSOR_ID,
                callback=callback_method
            )
            c.UTILITY_CONTEXT_DISPLAY_NAME = await self.__process_setting(
                constant_to_set=c.UTILITY_CONTEXT_DISPLAY_NAME,
                setting=self.SETTING_UTILITY_CONTEXT_DISPLAY_NAME,
                callback=callback_method
            )
        else:
            context = c.DEFAULT_UTILITY_CONTEXTS.get(
                c.ELECTRICITY_PROVIDER,
                c.DEFAULT_UTILITY_CONTEXTS["nl_generic"],
            )
            self.log(f"De utility_context is: {context}.")
            # ToDo: Notify user if fallback "nl_generic" is used..
            c.FM_PRICE_PRODUCTION_SENSOR_ID = context["production-sensor"]
            c.FM_PRICE_CONSUMPTION_SENSOR_ID = context["consumption-sensor"]
            c.FM_EMISSIONS_SENSOR_ID = context["emissions-sensor"]
            c.UTILITY_CONTEXT_DISPLAY_NAME = context["display-name"]
            self.log(f"v2g_globals FM_PRICE_PRODUCTION_SENSOR_ID: {c.FM_PRICE_PRODUCTION_SENSOR_ID}.")
            self.log(f"v2g_globals FM_PRICE_CONSUMPTION_SENSOR_ID: {c.FM_PRICE_CONSUMPTION_SENSOR_ID}.")
            self.log(f"v2g_globals FM_EMISSIONS_SENSOR_ID: {c.FM_EMISSIONS_SENSOR_ID}.")
            self.log(f"v2g_globals UTILITY_CONTEXT_DISPLAY_NAME: {c.UTILITY_CONTEXT_DISPLAY_NAME}.")

        # Only for these electricity_providers do we take the VAT and markup from the secrets into account.
        # For others, we expect netto prices (including VAT and Markup).
        # If self_provided data also includes VAT and markup the values in secrets can
        # be set to 1 and 0 respectively to achieve the same result as here.
        if c.ELECTRICITY_PROVIDER in ["self_provided", "nl_generic", "no_generic"]:
            c.ENERGY_PRICE_VAT = await self.__process_setting(
                constant_to_set=c.ENERGY_PRICE_VAT,
                setting=self.SETTING_ENERGY_PRICE_VAT,
                callback=callback_method
            )
            c.ENERGY_PRICE_MARKUP_PER_KWH = await self.__process_setting(
                constant_to_set=c.ENERGY_PRICE_MARKUP_PER_KWH,
                setting=self.SETTING_ENERGY_PRICE_MARKUP_PER_KWH,
                callback=callback_method
            )

        await self.__collect_action_triggers(source="changed fm_settings")

    async def __collect_action_triggers(self, source: str):
        # Prevent parallel calls to set_next_cation and always wait a little as
        # the user likely changes several settings at a time.
        # This also prevents calling V2G Liberty too early when it has not
        # completed initialisation yet.

        if self.action_handle is None:
            # This is the initial, init has not finished yet
            return
        if self.info_timer(self.action_handle):
            silent = True # Does not really work..
            await self.cancel_timer(self.action_handle, silent)
        self.action_handle = await self.run_in(self.__collective_action, delay=15)

    async def __collective_action(self, v2g_args = None):
        await self.evse_client.initialise_charger(v2g_args="changed settings")
        await self.v2g_main_app.set_next_action(v2g_args="changed settings")

    async def process_max_power_settings(self, min_charge_power: int, max_charge_power: int):
        """To be called from modbus_evse_client to check if setting in the charger
           is lower than the setting by the user.
        """
        self.log(f'process_max_power_settings called with power {max_charge_power}.')
        self.SETTING_CHARGER_MAX_CHARGE_POWER['max'] = max_charge_power
        self.SETTING_CHARGER_MAX_CHARGE_POWER['min'] = min_charge_power
        self.SETTING_CHARGER_MAX_DISCHARGE_POWER['max'] = max_charge_power
        self.SETTING_CHARGER_MAX_DISCHARGE_POWER['min'] = min_charge_power
        kwargs={'run_once': True}
        await self.__read_and_process_charger_settings(kwargs=kwargs)

    def create_persistent_notification(self, message: str, title: str, notification_id: str):
        self.call_service(
            'persistent_notification/create',
            title=title,
            message=message,
            notification_id=notification_id
        )

def time_mod(time, delta, epoch=None):
    """From https://stackoverflow.com/a/57877961/13775459"""
    if epoch is None:
        epoch = datetime(1970, 1, 1, tzinfo=time.tzinfo)
    return (time - epoch) % delta


def time_round(time, delta, epoch=None):
    """From https://stackoverflow.com/a/57877961/13775459"""
    mod = time_mod(time, delta, epoch)
    if mod < (delta / 2):
        return time - mod
    return time + (delta - mod)


def time_ceil(time, delta, epoch=None):
    """From https://stackoverflow.com/a/57877961/13775459"""
    mod = time_mod(time, delta, epoch)
    if mod:
        return time + (delta - mod)
    return time
