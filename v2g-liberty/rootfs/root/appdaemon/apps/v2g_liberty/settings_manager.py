import json
import os


class SettingsManager:
    settings: dict = {}
    settings_file_path = "/data/v2g_liberty_settings.json"

    def __init__(self, log):
        self.__log = log

    def retrieve_settings(self):
        """Retrieve all settings from the settings file"""

        self.__log("called")

        self.settings = {}
        if not os.path.exists(self.settings_file_path):
            self.__log("no settings file found", level="WARNING")
        else:
            try:
                with open(self.settings_file_path, "r", encoding="utf-8") as read_file:
                    settings = json.load(read_file)
                    if isinstance(settings, dict):
                        self.settings = self.__upgrade(settings)
                    else:
                        self.__log(
                            f"loading file content error, no dict: '{settings}'.",
                            level="WARNING",
                        )
            except (json.JSONDecodeError, FileNotFoundError) as e:
                self.__log(f"Error reading settings file: {e}", level="WARNING")

    def __upgrade(self, settings: dict):
        settings = self.__upgrade_obsolete_settings(settings)
        settings = self.__upgrade_administrator_settings_initialised(settings)
        settings = self.__upgrade_calendar_settings_initialised(settings)
        settings = self.__upgrade_charger_settings_initialised(settings)
        settings = self.__upgrade_electricity_contract_settings_initialised(settings)
        settings = self.__upgrade_schedule_settings_initialised(settings)
        return settings

    def __upgrade_obsolete_settings(self, settings: dict):
        for obsolete, new in {
            "input_select.admin_mobile_name": "input_text.admin_mobile_name",
            "input_select.fm_asset": "input_text.fm_asset",
            "input_select.integration_calendar_entity_name": "input_text.integration_calendar_entity_name",
            "input_select.car_calendar_source": "input_text.car_calendar_source",
        }.items():
            if obsolete in settings:
                value = settings.get(obsolete)
                settings.update({new: value})
                settings.pop(obsolete)

        setting_key = "input_text.car_calendar_source"
        for obsolete, new in {
            "Home Assistant integration": "localIntegration",
            "Direct caldav source": "remoteCaldav",
        }.items():
            value = settings.get(setting_key)
            if value == obsolete:
                settings.update({setting_key: new})

        return settings

    def __upgrade_administrator_settings_initialised(self, settings: dict):
        if (
            "input_text.admin_mobile_name" in settings
            and "input_select.admin_mobile_platform" in settings
        ):
            settings["input_boolean.admin_settings_initialised"] = True
        return settings

    def __upgrade_calendar_settings_initialised(self, settings: dict):
        source = settings.get("input_text.car_calendar_source", None)
        if (
            source == "remoteCaldav"
            and "input_text.calendar_account_init_url" in settings
            and "input_text.calendar_account_username" in settings
            and "input_text.calendar_account_password" in settings
            and "input_text.car_calendar_name" in settings
        ) or (
            source == "localIntegration"
            and "input_text.integration_calendar_entity_name" in settings
        ):
            settings["input_boolean.calendar_settings_initialised"] = True
        return settings

    def __upgrade_charger_settings_initialised(self, settings: dict):
        if (
            "input_text.charger_host_url" in settings
            and "input_number.charger_port" in settings
            and "input_boolean.use_reduced_max_charge_power" in settings
        ):
            settings["input_boolean.charger_settings_initialised"] = True
        return settings

    def __upgrade_electricity_contract_settings_initialised(self, settings: dict):
        if "input_select.electricity_provider" in settings:
            contract = settings["input_select.electricity_provider"]
            if (
                (
                    contract
                    in [
                        "nl_anwb_energie",
                        "nl_greenchoice",
                        "nl_next_energy",
                        "nl_tibber",
                    ]
                )
                or (
                    contract == "nl_generic"
                    and "input_number.energy_price_vat" in settings
                    and "input_number.energy_price_markup_per_kwh" in settings
                )
                or (
                    contract == "au_amber_electric"
                    and "input_text.own_consumption_price_entity_id" in settings
                    and "input_text.own_production_price_entity_id" in settings
                )
                or (
                    contract == "gb_octopus_energy"
                    and "input_text.octopus_import_code" in settings
                    and "input_text.octopus_export_code" in settings
                    and "input_select.gb_dno_region" in settings
                )
            ):
                settings["input_boolean.electricity_contract_settings_initialised"] = (
                    True
                )
        return settings

    def __upgrade_schedule_settings_initialised(self, settings: dict):
        if (
            "input_text.fm_account_username" in settings
            and "input_text.fm_account_password" in settings
            and "input_boolean.fm_show_option_to_change_url" in settings
            and "input_text.fm_host_url" in settings
            and "input_text.fm_asset" in settings
        ):
            settings["input_boolean.schedule_settings_initialised"] = True
        return settings

    def store_setting(self, entity_id: str, value: any):
        """Store (overwrite or create) a setting in settings file.

        Args:
            entity_id (str): setting name = the full entity_id from HA
            setting_value: the value to set.
        """
        # self.__log(f"__store_setting, entity_id: '{entity_id}' to value '{value}'.")
        self.settings[entity_id] = value
        self.__write_to_file()

    def __write_to_file(self):
        # self.__log(f"__write_to_file, settings: '{self.settings}'.")
        with open(self.settings_file_path, "w", encoding="utf-8") as write_file:
            json.dump(self.settings, write_file, indent=2)

    def reset(self):
        self.settings = {}
        self.__write_to_file()

    def get(self, entity_id):
        return self.settings.get(entity_id, None)
