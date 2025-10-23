"""Main module for setting up the app."""

from appdaemon.plugins.hass.hassapi import Hass
from .event_bus import EventBus
from .ha_ui_manager import HAUIManager
from .notifier_util import Notifier
from .v2g_globals import V2GLibertyGlobals
from .modbus_evse_client import ModbusEVSEclient
from .fm_client import FMClient
from .reservations_client import ReservationsClient
from .main_app import V2Gliberty
from .data_monitor import DataMonitor
from .get_fm_data import FlexMeasuresDataImporter
from .amber_price_data_manager import ManageAmberPriceData
from .octopus_price_data_manager import ManageOctopusPriceData
from .nissan_leaf_monitor import NissanLeafMonitor
from datetime import datetime

class V2GLibertyApp(Hass):
    async def initialize(self):
        start_app = datetime.now()
        print(f"[{start_app.isoformat(sep=' ')}] Starting V2GLibertyApp initialization...")

        start_module = datetime.now()
        event_bus = EventBus(self)
        self._log_init_time("EventBus", start_module)

        start_module = datetime.now()
        ha_ui_manager = HAUIManager(self, event_bus=event_bus)
        self._log_init_time("HAUIManager", start_module)

        start_module = datetime.now()
        notifier = Notifier(self, event_bus=event_bus)
        self._log_init_time("Notifier", start_module)

        start_module = datetime.now()
        v2g_globals = V2GLibertyGlobals(self, notifier=notifier)
        self._log_init_time("V2GLibertyGlobals", start_module)

        start_module = datetime.now()
        modbus_evse_client = ModbusEVSEclient(self, event_bus=event_bus, notifier=notifier)
        self._log_init_time("ModbusEVSEclient", start_module)

        start_module = datetime.now()
        fm_client = FMClient(self, event_bus=event_bus)
        self._log_init_time("FMClient", start_module)

        start_module = datetime.now()
        reservations_client = ReservationsClient(self, event_bus=event_bus)
        self._log_init_time("ReservationsClient", start_module)

        start_module = datetime.now()
        main_app = V2Gliberty(self, event_bus=event_bus, notifier=notifier)
        self._log_init_time("V2Gliberty", start_module)

        start_module = datetime.now()
        data_monitor = DataMonitor(self, event_bus=event_bus)
        self._log_init_time("DataMonitor", start_module)

        start_module = datetime.now()
        nissan_leaf_monitor = NissanLeafMonitor(self, event_bus=event_bus, notifier=notifier)
        self._log_init_time("NissanLeafMonitor", start_module)

        start_module = datetime.now()
        get_fm_data = FlexMeasuresDataImporter(self, notifier=notifier)
        self._log_init_time("FlexMeasuresDataImporter", start_module)

        start_module = datetime.now()
        amber_price_data_manager = ManageAmberPriceData(self)
        self._log_init_time("ManageAmberPriceData", start_module)

        start_module = datetime.now()
        octopus_price_data_manager = ManageOctopusPriceData(self)
        self._log_init_time("ManageOctopusPriceData", start_module)

        v2g_globals.v2g_main_app = main_app
        v2g_globals.evse_client_app = modbus_evse_client
        v2g_globals.fm_client_app = fm_client
        v2g_globals.calendar_client = reservations_client
        v2g_globals.amber_price_data_manager = amber_price_data_manager
        v2g_globals.octopus_price_data_manager = octopus_price_data_manager
        v2g_globals.fm_data_retrieve_client = get_fm_data
        modbus_evse_client.v2g_main_app = main_app
        modbus_evse_client.v2g_globals = v2g_globals
        main_app.evse_client_app = modbus_evse_client
        main_app.fm_client_app = fm_client
        main_app.reservations_client = reservations_client
        data_monitor.evse_client_app = modbus_evse_client
        data_monitor.fm_client_app = fm_client
        get_fm_data.v2g_main_app = main_app
        get_fm_data.fm_client_app = fm_client
        amber_price_data_manager.fm_client_app = fm_client
        amber_price_data_manager.v2g_main_app = main_app
        amber_price_data_manager.get_fm_data_module = get_fm_data
        octopus_price_data_manager.fm_client_app = fm_client
        octopus_price_data_manager.get_fm_data_module = get_fm_data

        start_module = datetime.now()
        await v2g_globals.initialize()
        self._log_init_time("v2g_globals.initialize()", start_module)

        start_module = datetime.now()
        await main_app.initialize()
        self._log_init_time("main_app.initialize()", start_module)

        start_module = datetime.now()
        await amber_price_data_manager.initialize()
        self._log_init_time("amber_price_data_manager.initialize()", start_module)

        start_module = datetime.now()
        await v2g_globals.kick_off_settings()
        self._log_init_time("v2g_globals.kick_off_settings()", start_module)

        start_module = datetime.now()
        await main_app.kick_off_v2g_liberty(v2g_args="initialise")
        self._log_init_time("main_app.kick_off_v2g_liberty()", start_module)

        start_module = datetime.now()
        await get_fm_data.initialize()
        self._log_init_time("get_fm_data.initialize()", start_module)

        self._log_init_time("V2GLibertyApp (total)", start_app, True)

    def _log_init_time(self, name: str, start: datetime, forced: bool = False):
        now = datetime.now()
        delta = now - start
        if forced or delta.total_seconds() >= 1.0:
            minutes, seconds = divmod(delta.seconds, 60)
            milliseconds = delta.microseconds // 1000
            time_diff = f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
            if forced:
                msg = "initialised in:"
            else:
                msg = "took longer than expected:"
            print(f"[{now.isoformat(sep=' ')}] {name} {msg} {time_diff}.")

