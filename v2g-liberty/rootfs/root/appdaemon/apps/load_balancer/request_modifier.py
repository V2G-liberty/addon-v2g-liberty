import enum
from pyee.base import EventEmitter
from pymodbus.pdu import ModbusPDU
from pymodbus.pdu.register_message import WriteSingleRegisterRequest

from ._log_wrapper import get_class_method_logger
from .server import Tcp2TcpProxyServer


class RegisterAddress(int, enum.Enum):
    ACTION = 0x0101
    SET_POWER_SETPOINT = 0x0104


class RequestModifier(EventEmitter):
    """Request Modifier"""

    proxy: Tcp2TcpProxyServer

    def __init__(self, proxy):
        super().__init__()
        """Initialize the server"""
        self.proxy = proxy
        proxy.server.trace_pdu = self.server_trace_pdu
        self._write_handlers = {
            RegisterAddress.ACTION: self._handle_set_action,
            RegisterAddress.SET_POWER_SETPOINT: self._handle_set_power_setpoint,
        }
        self._should_be_charging = False
        self._requested_power_setpoint = 0
        self.limit = 0
        self.log = get_class_method_logger(print)

    def set_log(self, log):
        self.log = log

    def set_limit(self, new_limit_in_watt):
        self.limit = new_limit_in_watt
        self.log(f"client.connected {self.proxy.client.connected}")
        self.log(f"set limit to {new_limit_in_watt} ({self._requested_power_setpoint})")
        sign = -1 if self._requested_power_setpoint < 0 else 1
        self.emit("limit", sign * self.limit)
        if self.limit < abs(self._requested_power_setpoint):
            self.log(f"writing new limit {self.limit * sign} to client")
            # Push new limit onto the server
            result = self.proxy.client.write_register(
                RegisterAddress.SET_POWER_SETPOINT, int16_to_uint16(self.limit * sign)
            )
            self.log(f"{result}")

    def server_trace_pdu(self, sending: bool, pdu: ModbusPDU) -> ModbusPDU:
        # print(f"---> TRACE_PDU: {pdu}")
        if isinstance(pdu, WriteSingleRegisterRequest):
            self._update_write_single_register_request(pdu)
        return pdu

    def _update_write_single_register_request(
        self, request: WriteSingleRegisterRequest
    ):
        address = request.address
        if address in self._write_handlers:
            self._write_handlers[address](request)

    def _handle_set_action(self, request: WriteSingleRegisterRequest):
        value = request.registers[0]
        if value == 1:
            if not self._should_be_charging:
                self.log("start (dis)charging")
            self._should_be_charging = True
        elif value == 2:
            if self._should_be_charging:
                self.log("stop (dis)charging")
            self._should_be_charging = False

    def _handle_set_power_setpoint(self, request: WriteSingleRegisterRequest):
        # self.log(f"{request.address}: {uint16_to_int16(request.registers[0])}")
        requested_value = uint16_to_int16(request.registers[0])
        self._requested_power_setpoint = requested_value
        limited_value = self._limit_value(requested_value, self.limit)
        if limited_value != requested_value:
            self.log(f"--> limited {requested_value} to {limited_value}")
        request.registers[0] = int16_to_uint16(limited_value)
        # self.log(f"=> {request.address}: {uint16_to_int16(request.registers[0])}")

    def _limit_value(self, value, limit):
        if value < 0:
            value = max(value, -limit)
        elif value > 0:
            value = min(value, limit)
        return value


MAX_USI = 65536
HALF_MAX_USI = MAX_USI / 2


def int16_to_uint16(value):
    if value < 0:
        value = value + MAX_USI
    return value


def uint16_to_int16(value):
    if value > HALF_MAX_USI:
        value = value - MAX_USI
    return value
