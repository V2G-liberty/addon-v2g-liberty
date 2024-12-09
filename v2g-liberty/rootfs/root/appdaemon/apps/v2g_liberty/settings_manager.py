import json
import os


class SettingsManager:
    settings: dict = {}
    settings_file_path = "/data/v2g_liberty_settings.json"

    def __init__(self, log):
        self.__log = log

    def retrieve_settings(self):
        """Retrieve all settings from the settings file"""

        self.__log(f"retrieve_settings called")

        self.settings = {}
        if not os.path.exists(self.settings_file_path):
            self.__log(f"retrieve_settings, no settings file found")
        else:
            try:
                with open(self.settings_file_path, "r", encoding="utf-8") as read_file:
                    settings = json.load(read_file)
                    if isinstance(settings, dict):
                        self.settings = self.__upgrade(settings)
                    else:
                        self.__log(
                            f"retrieve_settings, loading file content error, "
                            f"no dict: '{settings}'."
                        )
            except (json.JSONDecodeError, FileNotFoundError) as e:
                self.__log(f"__retrieve_settings, Error reading settings file: {e}")

    def __upgrade(self, settings: dict):
        for obsolete, new in {
            "input_select.admin_mobile_name": "input_text.admin_mobile_name",
            # "input_select.admin_mobile_platform": "input_text.admin_mobile_platform"
            "input_select.fm_asset": "input_text.fm_asset",
        }.items():
            if obsolete in settings:
                value = settings.get(obsolete)
                settings.update({new: value})
                settings.pop(obsolete)
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
