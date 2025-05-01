from service_response_app import ServiceResponseApp

from event_bus import EventBus
from notifier_util import Notifier

from v2g_globals import V2GLibertyGlobals
from modbus_evse_client import ModbusEVSEclient
from fm_client import FMClient
from reservations_client import ReservationsClient
from v2g_liberty import V2Gliberty
from data_monitor import DataMonitor
from get_fm_data import FlexMeasuresDataImporter
from amber_price_data_manager import ManageAmberPriceData
from octopus_price_data_manager import ManageOctopusPriceData
from nissan_leaf_monitor import NissanLeafMonitor


class V2GLibertyApp(ServiceResponseApp):
    async def initialize(self):
        event_bus = EventBus(self)
        notifier = Notifier(self)
        v2g_globals = V2GLibertyGlobals(self, notifier=notifier)
        modbus_evse_client = ModbusEVSEclient(
            self, event_bus=event_bus, notifier=notifier
        )
        fm_client = FMClient(self)
        reservations_client = ReservationsClient(self)
        v2g_liberty = V2Gliberty(self, notifier=notifier)
        data_monitor = DataMonitor(self)
        nissan_leaf_monitor = NissanLeafMonitor(
            self, event_bus=event_bus, notifier=notifier
        )
        get_fm_data = FlexMeasuresDataImporter(self, notifier=notifier)

        amber_price_data_manager = ManageAmberPriceData(self)
        octopus_price_data_manager = ManageOctopusPriceData(self)

        v2g_globals.v2g_main_app = v2g_liberty
        v2g_globals.evse_client_app = modbus_evse_client
        v2g_globals.fm_client_app = fm_client
        v2g_globals.calendar_client = reservations_client
        v2g_globals.amber_price_data_manager = amber_price_data_manager
        v2g_globals.octopus_price_data_manager = octopus_price_data_manager
        v2g_globals.fm_data_retrieve_client = get_fm_data

        modbus_evse_client.v2g_main_app = v2g_liberty
        modbus_evse_client.v2g_globals = v2g_globals

        v2g_liberty.evse_client_app = modbus_evse_client
        v2g_liberty.fm_client_app = fm_client
        v2g_liberty.reservations_client = reservations_client

        data_monitor.evse_client_app = modbus_evse_client
        data_monitor.fm_client_app = fm_client

        get_fm_data.v2g_main_app = v2g_liberty
        get_fm_data.fm_client_app = fm_client

        amber_price_data_manager.fm_client_app = fm_client
        amber_price_data_manager.v2g_main_app = v2g_liberty
        amber_price_data_manager.get_fm_data_module = get_fm_data

        octopus_price_data_manager.fm_client_app = fm_client
        octopus_price_data_manager.get_fm_data_module = get_fm_data

        await v2g_globals.initialize()
        await v2g_liberty.initialize()

        await data_monitor.initialize()
        await get_fm_data.initialize()

        await amber_price_data_manager.initialize()
        await octopus_price_data_manager.initialize()

        await v2g_globals.kick_off_settings()
        await v2g_liberty.kick_off_v2g_liberty(v2g_args="initialise")
