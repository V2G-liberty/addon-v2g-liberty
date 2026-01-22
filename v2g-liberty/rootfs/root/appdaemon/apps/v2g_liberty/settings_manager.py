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
                        self.__write_to_file()
                    else:
                        self.__log(
                            f"loading file content error, no dict: '{settings}'.",
                            level="WARNING",
                        )
            except (json.JSONDecodeError, FileNotFoundError) as e:
                self.__log(f"Error reading settings file: {e}", level="WARNING")

    def __upgrade(self, settings: dict):
        settings = self.__upgrade_charger_type(settings)
        settings = self.__upgrade_car_ev_id(settings)
        return settings

    def __upgrade_obsolete_settings(self, settings: dict):
        self.__log("Called")
        # Keep for now eventhough it is un used now, in later versions there might be a need for this
        return settings

    def __upgrade_charger_type(self, settings: dict):
        # It is safe to assume charger will be of type "wallbox-quasar-1" if currenttly the
        # URL, port and reduce boolean are present.
        if (
            "input_text.charger_host_url" in settings
            and "input_number.charger_port" in settings
            and "input_boolean.use_reduced_max_charge_power" in settings
            and "input_text.charger_type" not in settings
        ):
            # settings["input_boolean.charger_settings_initialised"] = True
            settings["input_text.charger_type"] = "wallbox-quasar-1"
            self.__log("Assuming charger_type to be 'wallbox-quasar-1'.")

        return settings

    def __upgrade_car_ev_id(self, settings: dict):
        """Generate fake ev_id for existing Wallbox users who don't have one."""
        # Check if car settings exist but ev_id doesn't
        car_name = settings.get("input_text.car_name", "")
        ev_id = settings.get("car_ev_id", "")
        charger_type = settings.get("input_text.charger_type", "")

        if charger_type == "wallbox-quasar-1" and car_name and not ev_id:
            # Generate fake ev_id for Wallbox
            fake_ev_id = f"wallbox_{car_name.replace(' ', '_')}"
            settings["car_ev_id"] = fake_ev_id
            self.__log(f"Migrated Wallbox car with fake ev_id: {fake_ev_id}")
        elif not ev_id and car_name:
            # Default migration for unknown cases
            fake_ev_id = f"unknown_{car_name.replace(' ', '_')}"
            settings["car_ev_id"] = fake_ev_id
            self.__log(f"Migrated car with default fake ev_id: {fake_ev_id}")

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
