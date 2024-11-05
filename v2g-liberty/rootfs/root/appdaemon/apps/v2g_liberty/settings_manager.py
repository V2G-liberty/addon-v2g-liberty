import json
import os


class SettingsManager:

    settings: dict = {}
    settings_file_path = "/data/v2g_liberty_settings.json"

    def __init__(self, log):
        self.log = log

    def retrieve_settings(self):
        """Retrieve all settings from the settings file"""

        self.log(f"retrieve_settings called")

        self.settings = {}
        if not os.path.exists(self.settings_file_path):
            self.log(f"retrieve_settings, no settings file found")
        else:
            try:
                with open(self.settings_file_path, 'r', encoding='utf-8') as read_file:
                    settings = json.load(read_file)
                    if isinstance(settings, dict):
                        self.settings = settings
                    else:
                        self.log(f"retrieve_settings, loading file content error, "
                                 f"no dict: '{settings}'.")
            except (json.JSONDecodeError, FileNotFoundError) as e:
                self.log(f"__retrieve_settings, Error reading settings file: {e}")

    def store_setting(self, entity_id: str, value: any):
        """Store (overwrite or create) a setting in settings file.

        Args:
            entity_id (str): setting name = the full entity_id from HA
            setting_value: the value to set.
        """
        # self.log(f"__store_setting, entity_id: '{entity_id}' to value '{value}'.")
        self.settings[entity_id] = value
        self.__write_to_file()

    def __write_to_file(self):
        # self.log(f"__write_to_file, settings: '{self.settings}'.")
        with open(self.settings_file_path, 'w', encoding='utf-8') as write_file:
            json.dump(self.settings, write_file)

    def reset(self):
        self.settings = {}
        self.__write_to_file()

    def get(self, entity_id):
        return self.settings.get(entity_id, None)