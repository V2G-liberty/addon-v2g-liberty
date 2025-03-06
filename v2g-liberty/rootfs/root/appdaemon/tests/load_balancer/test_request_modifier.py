import pytest
from unittest.mock import MagicMock

from pymodbus.pdu.register_message import WriteSingleRegisterRequest

from apps.load_balancer.request_modifier import RegisterAddress, RequestModifier,int16_to_uint16


@pytest.fixture
def callback() -> MagicMock:
    return MagicMock()


@pytest.fixture
def proxy() -> MagicMock:
    proxy = MagicMock()
    proxy.client = MagicMock()
    proxy.server = MagicMock()
    return proxy


@pytest.fixture
def request_modifier(proxy) -> RequestModifier:
    return RequestModifier(proxy=proxy)


def test_int_to_uint16():
    value = int16_to_uint16(0)
    assert value == 0
    value = int16_to_uint16(1000)
    assert value == 1000
    value = int16_to_uint16(32767)
    assert value == 32767
    
    value = int16_to_uint16(-1)
    assert value == 65535
    value = int16_to_uint16(-1000)
    assert value == 64536
    value = int16_to_uint16(-32768)
    assert value == 32768


def xtest_init(request_modifier):
    assert request_modifier.limit == 0
    

def xtest_set_limit_above_setpoint(proxy, request_modifier, callback):
    request_modifier.add_listener("limit", callback)
    request_modifier._requested_power_setpoint = 1000

    request_modifier.set_limit(2000)

    callback.assert_called_once_with(2000)
    proxy.client.write_register.assert_not_called()
    

def xtest_set_limit_above_negative_setpoint(proxy, request_modifier, callback):
    request_modifier.add_listener("limit", callback)
    request_modifier._requested_power_setpoint = -1000

    request_modifier.set_limit(2000)

    callback.assert_called_once_with(-2000)
    proxy.client.write_register.assert_not_called()
    

def xtest_set_limit_below_setpoint(proxy, request_modifier, callback):
    request_modifier.add_listener("limit", callback)
    request_modifier._requested_power_setpoint = 2000

    request_modifier.set_limit(1000)

    callback.assert_called_once_with(1000)
    proxy.client.write_register.assert_called_once_with(RegisterAddress.SET_POWER_SETPOINT, 1000)
    

def xtest_set_limit_below_negative_setpoint(proxy, request_modifier, callback):
    request_modifier.add_listener("limit", callback)
    request_modifier._requested_power_setpoint = -2000

    request_modifier.set_limit(1000)

    callback.assert_called_once_with(-1000)
    proxy.client.write_register.assert_called_once_with(RegisterAddress.SET_POWER_SETPOINT, int16_to_uint16(-1000))


def test_set_is_charging(request_modifier):
    request = WriteSingleRegisterRequest(
        address=RegisterAddress.ACTION,
        registers=[1] # start charging
    )
    request_modifier._should_be_charging = False

    request = request_modifier.server_trace_pdu(sending=False, pdu=request)
    
    assert request_modifier._should_be_charging
    
    
def test_set_is_not_charging(request_modifier):
    request = WriteSingleRegisterRequest(
        address=RegisterAddress.ACTION,
        registers=[2] # stop charging
    )
    request_modifier._should_be_charging = True

    request = request_modifier.server_trace_pdu(sending=False, pdu=request)
    
    assert not request_modifier._should_be_charging
    

def test_set_setpoint_below_limit(request_modifier):
    request = WriteSingleRegisterRequest(
        address=RegisterAddress.SET_POWER_SETPOINT,
        registers=[1000]
    )
    request_modifier.limit = 2000
    
    request = request_modifier.server_trace_pdu(sending=False, pdu=request)
    
    assert request.registers == [1000]
    

def test_set_negative_setpoint_below_limit(request_modifier):
    minus_thousand = int16_to_uint16(-1000)
    request = WriteSingleRegisterRequest(
        address=RegisterAddress.SET_POWER_SETPOINT,
        registers=[minus_thousand]
    )
    request_modifier.limit = 2000
    
    request = request_modifier.server_trace_pdu(sending=False, pdu=request)
    
    assert request.registers == [minus_thousand]
    

def test_set_setpoint_above_limit(request_modifier):
    request = WriteSingleRegisterRequest(
        address=RegisterAddress.SET_POWER_SETPOINT,
        registers=[2000]
    )
    request_modifier.limit = 1000
    
    request = request_modifier.server_trace_pdu(sending=False, pdu=request)
    
    assert request.registers == [1000]
    

def test_set_negative_setpoint_above_limit(request_modifier):
    minus_two_thousand = int16_to_uint16(-2000)
    request = WriteSingleRegisterRequest(
        address=RegisterAddress.SET_POWER_SETPOINT,
        registers=[minus_two_thousand]
    )
    request_modifier.limit = 1000
    
    request = request_modifier.server_trace_pdu(sending=False, pdu=request)
    
    minus_thousand = int16_to_uint16(-1000)
    assert request.registers == [minus_thousand]
