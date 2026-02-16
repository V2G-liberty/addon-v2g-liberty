import sys
from unittest.mock import MagicMock

def pytest_configure(config):
    # Mock the missing module before any tests are collected
    # sys.modules["v2g_liberty.chargers"] = MagicMock()
    sys.modules["v2g_liberty.chargers.wallbox_quasar_1"] = MagicMock()
    sys.modules["v2g_liberty.chargers.wallbox_quasar_1"].WallboxQuasar1Client = MagicMock()
    # sys.modules["v2g_liberty.evs"] = MagicMock()
    sys.modules["v2g_liberty.evs.electric_vehicle"] = MagicMock()
    sys.modules["v2g_liberty.evs.electric_vehicle"].ElectricVehicle = MagicMock()