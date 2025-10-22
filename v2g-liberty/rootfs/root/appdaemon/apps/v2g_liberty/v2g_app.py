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
        start = datetime.now()
        event_bus = EventBus(self)
        ha_ui_manager = HAUIManager(self, event_bus=event_bus)
        notifier = Notifier(self, event_bus=event_bus)
        v2g_globals = V2GLibertyGlobals(self, notifier=notifier)
        modbus_evse_client = ModbusEVSEclient(
            self, event_bus=event_bus, notifier=notifier
        )
        fm_client = FMClient(self, event_bus=event_bus)
        reservations_client = ReservationsClient(self, event_bus=event_bus)
        main_app = V2Gliberty(self, event_bus=event_bus, notifier=notifier)
        data_monitor = DataMonitor(self, event_bus=event_bus)
        nissan_leaf_monitor = NissanLeafMonitor(
            self, event_bus=event_bus, notifier=notifier
        )

        get_fm_data = FlexMeasuresDataImporter(self, notifier=notifier)
        amber_price_data_manager = ManageAmberPriceData(self)
        octopus_price_data_manager = ManageOctopusPriceData(self)

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

        await v2g_globals.initialize()
        await main_app.initialize()

        await amber_price_data_manager.initialize()

        await v2g_globals.kick_off_settings()
        await main_app.kick_off_v2g_liberty(v2g_args="initialise")

        await get_fm_data.initialize()

        now = datetime.now()
        delta = now - start
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        time_diff = f"{delta.days * 24 + hours}:{minutes:02d}:{seconds:02d}"
        print(f"[{now.isoformat(sep=' ')}] V2GLibertyApp initialised in {time_diff}.")
