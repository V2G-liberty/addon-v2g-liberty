import json
import os
import time


class SettingsManager:
    settings: dict = {}

    # What the last read made of the settings file. The two failures need the
    # user to do different things, so v2g_globals turns this into one of two
    # notifications -- see retrieve_settings.
    FILE_OK = None
    FILE_SET_ASIDE = "set_aside"  # content was broken; started fresh
    FILE_UNREADABLE = "unreadable"  # could not read it; touching nothing
    file_problem: str | None = FILE_OK
    set_aside_path: str = ""

    _SETTINGS_FILE_PATH = "/data/v2g_liberty_settings.json"
    # The removed Modbus-proxy load balancer kept its own settings here. The
    # file is read once, to restore the charger's address, and then left alone.
    _LOAD_BALANCER_JSON_PATH = "/homeassistant/quasar_load_balancer.json"
    _LOAD_BALANCER_IMPORT_KEY = "load_balancer_json_import"
    # Where the proxy listened, unless its own file said otherwise.
    _PROXY_HOSTS = ("127.0.0.1", "localhost", "::1")
    _PROXY_PORT = 5020
    _FM_USER_ID_KEY = "fm_user_id"
    _FM_POWER_SOURCE_ID_KEY = "fm_power_source_id"

    # The car(s) live in one list of objects, one element per car. In this
    # release the list holds exactly one car; the list form is there so more
    # cars can follow without another migration.
    _CARS_KEY = "cars"
    _LEGACY_CAR_KEYS = {
        "input_number.car_max_capacity_in_kwh": "capacity_kwh",
        "input_number.charger_plus_car_roundtrip_efficiency": "roundtrip_efficiency",
        "input_number.car_consumption_wh_per_km": "consumption_wh_per_km",
        "input_number.car_min_soc_in_percent": "min_soc_percent",
        "input_number.car_max_soc_in_percent": "max_soc_percent",
        "input_number.allowed_duration_above_max_soc_in_hrs": "allowed_duration_above_max_soc_hrs",
    }
    # Factory defaults, only used to tell "the user chose this" from "the app
    # wrote the default on the very first boot". Kept in sync with the setting
    # dicts in v2g_globals by a test.
    _CAR_FACTORY_DEFAULTS = {
        "capacity_kwh": 24,
        "roundtrip_efficiency": 85,
        "consumption_wh_per_km": 175,
        "min_soc_percent": 20,
        "max_soc_percent": 80,
        "allowed_duration_above_max_soc_hrs": 4,
    }

    def __init__(self, log):
        self.__log = log

    def retrieve_settings(self):
        """Retrieve all settings from the settings file"""

        self.__log("called")

        self.settings = {}
        # Deliberately not cleared here: only a successful read clears it. The
        # file is read more than once during start-up, and after setting one
        # aside the next read finds nothing -- which would otherwise wipe the
        # very problem we are about to report.
        if not os.path.exists(self._SETTINGS_FILE_PATH):
            self.__log("no settings file found", level="WARNING")
            return

        # Only the reading is guarded. Writing used to sit inside this try as
        # well, so a failure to write came back as "cannot read" -- and with the
        # guard in __write_to_file that would wedge every later save.
        try:
            with open(self._SETTINGS_FILE_PATH, "r", encoding="utf-8") as read_file:
                settings = json.load(read_file)
        except json.JSONDecodeError as e:
            # The file is there and is demonstrably not JSON.
            self.__set_file_aside(f"it is not valid JSON ({e})")
            return
        except OSError as e:
            # Permissions, I/O, a volume not mounted yet -- and FileNotFoundError,
            # which here means the file vanished between the check above and this
            # open. The content may be perfectly fine and merely out of reach, so
            # do not touch it: moving it aside could destroy a good file over a
            # passing fault. A restart is the way out.
            self.file_problem = self.FILE_UNREADABLE
            self.__log(
                f"Could not read the settings file: {e}. Leaving it untouched; "
                "nothing will be saved until the add-on is restarted.",
                level="ERROR",
            )
            return

        if not isinstance(settings, dict):
            self.__set_file_aside(f"it holds {type(settings).__name__}, not an object")
            return

        self.file_problem = self.FILE_OK
        self.set_aside_path = ""
        self.settings = self.__upgrade(settings)
        self.__write_to_file()

    def __set_file_aside(self, reason: str):
        """The content is broken beyond use. Leaving it in place strands the
        app -- running on defaults, saving nothing -- and a user cannot reach
        /data to repair it, so there would be no way out. Move it aside: the
        app starts clean and saving works again, and the original is kept for
        support. The timestamp means one of these can never overwrite another.
        """
        path = f"{self._SETTINGS_FILE_PATH}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            os.replace(self._SETTINGS_FILE_PATH, path)
        except OSError as e:
            # If it cannot even be moved, fall back to touching nothing.
            self.file_problem = self.FILE_UNREADABLE
            self.__log(
                f"The settings file is unusable ({reason}) and could not be set "
                f"aside either: {e}. Leaving it untouched; nothing will be saved "
                "until the add-on is restarted.",
                level="ERROR",
            )
            return
        self.file_problem = self.FILE_SET_ASIDE
        self.set_aside_path = path
        self.__log(
            f"The settings file was unusable ({reason}) and has been set aside "
            f"as '{path}'. Starting with factory defaults.",
            level="ERROR",
        )

    def __upgrade(self, settings: dict):
        settings = self.__upgrade_obsolete_settings(settings)
        settings = self.__upgrade_charger_type(settings)
        settings = self.__upgrade_administrator_settings_initialised(settings)
        settings = self.__upgrade_calendar_settings_initialised(settings)
        settings = self.__upgrade_charger_settings_initialised(settings)
        settings = self.__upgrade_electricity_contract_settings_initialised(settings)
        settings = self.__upgrade_schedule_settings_initialised(settings)
        settings = self.__upgrade_car_settings(settings)
        settings = self.__upgrade_load_balancer_json(settings)
        return settings

    def __upgrade_obsolete_settings(self, settings: dict):
        self.__log("Called")

        # TODO: Review if this code can be removed again if all users have upgraded to
        # version 0.5.0 (Feb 2025) or above."""
        for obsolete, new in {
            "input_select.admin_mobile_name": "input_text.admin_mobile_name",
            "input_select.fm_asset": "input_text.fm_asset",
            "input_select.integration_calendar_entity_name": "input_text.integration_calendar_entity_name",
            "input_select.car_calendar_source": "input_text.car_calendar_source",
            "input_select.car_calendar_name": "input_text.car_calendar_name",
        }.items():
            if obsolete in settings:
                self.__log(f"Changed obsolete setting key: {obsolete} to {new}.")
                value = settings.get(obsolete)
                settings.update({new: value})
                settings.pop(obsolete)

        # TODO: Review if this code can be removed again if all users have upgraded to
        # version 0.5.0 (Feb 2025) or above."""
        setting_key = "input_text.car_calendar_source"
        for obsolete, new in {
            "Home Assistant integration": "localIntegration",
            "Direct caldav source": "remoteCaldav",
        }.items():
            value = settings.get(setting_key)
            if value == obsolete:
                self.__log(f"Changed setting ({setting_key}): {obsolete} to {new}.")
                settings.update({setting_key: new})

        # TODO: Review if this code can be removed again if all users have upgraded to
        # version 0.5.3 (March 2025) or above."""
        setting_key = "input_text.fm_host_url"
        value = settings.get(setting_key)
        if value == "https://seita.energy":
            self.__log(
                f"Changed ({setting_key}): 'https://seita.energy' to 'https://ems.seita.energy'."
            )
            settings.update({setting_key: "https://ems.seita.energy"})

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

    def __upgrade_charger_type(self, settings: dict):
        """Installations from before the charger-type setting (phase 3 of the
        359 migration) can only have a Wallbox Quasar 1: that was the only
        supported charger. Assume it when the charger is configured but the type
        is missing, so an upgrade keeps working without a visit to the settings.
        """
        if (
            "input_text.charger_host_url" in settings
            and "input_number.charger_port" in settings
            and "input_boolean.use_reduced_max_charge_power" in settings
            and "input_text.charger_type" not in settings
        ):
            settings["input_text.charger_type"] = "wallbox-quasar-1"
            self.__log("Assuming charger_type to be 'wallbox-quasar-1'.")
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
        # Earlier versions before 0.5.0 did not store the URL if the default was used.
        # So, the input_text.fm_host_url cannot be used for testing initialisation.
        if (
            "input_text.fm_account_username" in settings
            and "input_text.fm_account_password" in settings
            and "input_boolean.fm_show_option_to_change_url" in settings
            and "input_text.fm_asset" in settings
        ):
            settings["input_boolean.schedule_settings_initialised"] = True
        return settings

    def __upgrade_car_settings(self, settings: dict):
        """The car values used to live in six separate entity-keyed settings.
        They now live in one object in the ``cars`` list, so the values of a car
        can later be stored per car. No name is invented: the user picks one at
        the first edit.

        ``configured`` is only set when this installation really had a
        configured car. The six factory defaults are written to the settings
        file on the very first boot, so their mere presence proves nothing:
        the installation must have been in use (a configured charger) or the
        user must have changed at least one value.
        """
        if self._CARS_KEY in settings:
            return settings
        car = {
            new: settings[old]
            for old, new in self._LEGACY_CAR_KEYS.items()
            if old in settings
        }
        if not car:
            return settings
        car.setdefault("name", "")
        car.setdefault("ev_id", "")
        was_in_use = bool(settings.get("input_boolean.charger_settings_initialised"))
        user_changed_a_value = any(
            car.get(key) not in (None, default)
            for key, default in self._CAR_FACTORY_DEFAULTS.items()
        )
        car["configured"] = was_in_use or user_changed_a_value
        settings[self._CARS_KEY] = [car]
        # TODO: Review if the legacy flat keys can be removed once all users have
        # upgraded past version 0.9.x. Keeping them one release makes a roll-back
        # to the previous add-on version land on the real values instead of on
        # the factory defaults. The new version no longer reads them.
        self.__log(
            f"Migrated {len(car) - 3} car settings into the 'cars' list "
            f"(configured={car['configured']})."
        )
        return settings

    def __upgrade_load_balancer_json(self, settings: dict):
        """The Modbus-proxy load balancer is gone; the fuse guard does its work
        from the grid connection settings. Users of the proxy had pointed the
        charger settings at the proxy (127.0.0.1:5020, unless the proxy's JSON
        said otherwise) and kept the charger's real address in that file. Put
        that address back, whether the proxy was enabled or not: a loopback
        host no longer answers.

        The file is read once (the marker key records the outcome). When the
        import succeeded, it is renamed to ``quasar_load_balancer.json.migrated``
        so the user can see it is no longer needed; when it could not be read,
        or the charger still points at the proxy without a real address in
        it, it stays as it is for the user to look at. Nothing else in it maps
        onto the fuse guard, which takes
        its per-phase sensors and capacity from the grid connection dialog;
        the log names the phase sensor and limit the proxy used, as a lead.
        """
        path = self._LOAD_BALANCER_JSON_PATH
        if self._LOAD_BALANCER_IMPORT_KEY in settings:
            # Imported by a version that left the file in place: rename it now
            # if that import succeeded.
            done = settings[self._LOAD_BALANCER_IMPORT_KEY]
            if (
                os.path.isfile(path)
                and isinstance(done, dict)
                and "error" not in done
                and not done.get("charger_host_missing")
            ):
                self.__set_load_balancer_json_aside(path)
            return settings
        if not os.path.isfile(path):
            return settings

        outcome = {"enabled": False, "charger_host_restored": False}
        try:
            with open(path, "r", encoding="utf-8") as read_file:
                content = json.load(read_file)
        except (json.JSONDecodeError, OSError) as e:
            outcome["error"] = str(e)
            content = {}
        if not isinstance(content, dict):
            outcome["error"] = f"holds {type(content).__name__}, not an object"
            content = {}
        outcome["enabled"] = bool(content.get("enabled"))

        proxy_hosts = set(self._PROXY_HOSTS)
        proxy_ports = {self._PROXY_PORT}
        own_host = self.__as_text(content.get("host"))
        if own_host:
            proxy_hosts.add(own_host.lower())
        own_port = self.__as_port(content.get("port"))
        if own_port:
            proxy_ports.add(own_port)

        host = str(settings.get("input_text.charger_host_url", "")).strip().lower()
        port = self.__as_port(settings.get("input_number.charger_port"))
        charger_was_the_proxy = host in proxy_hosts and port in proxy_ports

        if charger_was_the_proxy:
            real_host = self.__as_text(content.get("quasar_host"))
            if real_host:
                real_port = self.__as_port(content.get("quasar_port")) or 502
                settings["input_text.charger_host_url"] = real_host
                settings["input_number.charger_port"] = str(real_port)
                outcome["charger_host_restored"] = True
                self.__log(
                    "The Modbus-proxy load balancer has been removed. The charger "
                    f"settings pointed at the proxy; restored the charger's address "
                    f"from quasar_load_balancer.json: {real_host}:{real_port}. "
                    "Please check it in the charger settings.",
                    level="WARNING",
                )
            else:
                outcome["charger_host_missing"] = True
                self.__log(
                    "The Modbus-proxy load balancer has been removed, but the "
                    f"charger settings still point at the proxy ({host}:{port}) "
                    "and quasar_load_balancer.json holds no charger address. Set "
                    "the charger's real address in the charger settings.",
                    level="ERROR",
                )
        else:
            self.__log(
                "The Modbus-proxy load balancer has been removed; "
                "quasar_load_balancer.json is no longer read. The charger "
                "settings did not point at the proxy, so nothing was changed.",
                level="WARNING",
            )

        lead = ""
        sensor = self.__as_text(content.get("total_power_entity_id"))
        if sensor:
            lead = f" The proxy watched {sensor}"
            limit_w = content.get("total_power_limit")
            if (
                isinstance(limit_w, (int, float))
                and not isinstance(limit_w, bool)
                and limit_w > 0
            ):
                lead += f" with a limit of {limit_w:.0f} W ({limit_w / 230:.0f} A)"
            lead += (
                "; that is a lead for the phase it was on, the sensor's sign "
                "convention may differ."
            )
        self.__log(
            "Power limiting is now done by the fuse guard, which needs the grid "
            "connection settings: a power sensor per phase and the capacity per "
            f"phase. Without those it does nothing.{lead}",
            level="WARNING",
        )
        if "error" not in outcome and not outcome.get("charger_host_missing"):
            self.__set_load_balancer_json_aside(path)
        settings[self._LOAD_BALANCER_IMPORT_KEY] = outcome
        return settings

    def __set_load_balancer_json_aside(self, path: str):
        """Rename the imported file so the user sees it is no longer needed.
        A failed rename only costs that hint: the marker key keeps the file
        from being read again."""
        migrated = f"{path}.migrated"
        try:
            os.replace(path, migrated)
        except OSError as e:
            self.__log(
                f"Could not rename {path} to {migrated}: {e}; it is no longer "
                "read and can be removed.",
                level="WARNING",
            )
            return
        self.__log(
            f"Renamed {path} to {migrated}: its settings are taken over and the "
            "file is no longer needed."
        )

    @staticmethod
    def __as_text(value) -> str:
        """A non-empty string from a JSON value, stripped; "" for anything else."""
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def __as_port(value) -> int | None:
        """A TCP port from a JSON or settings value; None for anything else."""
        if isinstance(value, bool):
            return None
        try:
            port = int(float(value))
        except (TypeError, ValueError):
            return None
        return port if 0 < port <= 65535 else None

    def store_setting(self, entity_id: str, value: any):
        """Store (overwrite or create) a setting in settings file.

        Args:
            entity_id (str): setting name = the full entity_id from HA
            setting_value: the value to set.
        """
        self.__log(
            f"__store_setting, entity_id: '{entity_id}' to value '{value}'.",
            level="DEBUG",
        )
        self.settings[entity_id] = value
        self.__write_to_file()

    def __write_to_file(self):
        if self.file_problem == self.FILE_UNREADABLE:
            # The file is still the user's configuration; we just cannot read
            # it. Writing would replace it with the little we hold in memory --
            # which after a failed load is nothing at all. One stray byte used
            # to be enough to lose every setting.
            self.__log(
                "Refusing to write: the settings file could not be read.",
                level="ERROR",
            )
            return
        self.__log(f"__write_to_file, settings: '{self.settings}'.", level="DEBUG")
        # Write to a temporary file first, then atomically replace.
        # This prevents an empty settings file if the process is killed
        # during write (e.g. system reboot, HA restart, power loss).
        tmp_path = self._SETTINGS_FILE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as write_file:
            json.dump(self.settings, write_file, indent=2)
            # Closing only hands the bytes to the kernel. Without this they can
            # still be in the page cache when the power goes, and the rename
            # below then publishes an empty or half-written file over the good
            # one -- the very outcome the rename is here to prevent.
            write_file.flush()
            os.fsync(write_file.fileno())
        os.replace(tmp_path, self._SETTINGS_FILE_PATH)

    def reset(self):
        # A deliberate wipe is also how you recover from an unreadable file,
        # so it clears the guard rather than tripping over it.
        self.settings = {}
        self.file_problem = self.FILE_OK
        self.__write_to_file()

    def get(self, entity_id):
        return self.settings.get(entity_id, None)

    def get_object(
        self, key: str, default: dict | list | None = None
    ) -> dict | list | None:
        """Retrieve a structured object (dict or list) from settings by key.

        Returns the stored value if it is a dict or list. Falls back to
        ``default`` when the key is missing or the stored value is of any
        other type.
        """
        value = self.settings.get(key, None)
        if isinstance(value, (dict, list)):
            return value
        return default

    def store_object(self, key: str, data: dict | list):
        """Store a structured object (dict or list) in settings.

        Overwrites any existing value for the given key.
        """
        self.settings[key] = data
        self.__write_to_file()

    # ── Internal (non-HA) values ──────────────────────────────────────────────
    # These are technical values with no corresponding HA input entity.
    # Keys use a plain string (no "input_*." prefix) to distinguish them.

    def store_fm_power_source_id(self, source_id: int | None) -> None:
        self.store_setting(self._FM_POWER_SOURCE_ID_KEY, source_id)

    def get_fm_power_source_id(self) -> int | None:
        value = self.settings.get(self._FM_POWER_SOURCE_ID_KEY)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def store_fm_user_id(self, user_id: int | None) -> None:
        self.store_setting(self._FM_USER_ID_KEY, user_id)

    def get_fm_user_id(self) -> int | None:
        value = self.settings.get(self._FM_USER_ID_KEY)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
