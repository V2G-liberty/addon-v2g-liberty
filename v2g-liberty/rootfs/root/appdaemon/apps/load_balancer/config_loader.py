import json
import os


class ConfigLoader:
    config_file_path = "/homeassistant/quasar_load_balancer.json"

    def load(self):
        config = {"enabled": False}

        if not os.path.exists(self.config_file_path):
            return config

        try:
            with open(self.config_file_path, "r", encoding="utf-8") as read_file:
                content = json.load(read_file)
                if isinstance(content, dict) and "enabled" in content:
                    config = content

        except (json.JSONDecodeError, FileNotFoundError):
            pass

        return config
