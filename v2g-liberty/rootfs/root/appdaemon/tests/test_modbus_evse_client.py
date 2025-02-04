import pytest
from unittest.mock import ANY, Mock, mock_open, patch
from apps.v2g_liberty.modbus_evse_client import ModbusEVSEclient


@pytest.fixture
def log_mock():
    return Mock()


@pytest.fixture
def modbus_evse_client(log_mock):
    return ModbusEVSEclient(log=log_mock)


class TestStateChange:
    # TODO
    ### change state
    pass


class TestFatalErrors:
    # TODO
    ### Fatal error screnario's:
    # 1. Set error: this sets the charger_state to error (7)
    # 2. Set internal_error: this sets error entity value to not 0
    # 3. Stop the mock docker => throws a ModbusException
    # 4. Do 1..3 with car disconnected => should result in none-critical notification
    # 5. Set soc to 0, this is considered an invalid value
    pass
