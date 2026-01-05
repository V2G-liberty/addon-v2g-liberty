from pymodbus.client import ModbusTcpClient
from pymodbus.datastore import ModbusServerContext
from pymodbus.datastore.remote import RemoteDeviceContext
from pymodbus.server import ModbusTcpServer

from ._log_wrapper import get_class_method_logger


class Tcp2TcpProxyServer:
    """TCP to TCP Proxy Server"""

    client: ModbusTcpClient
    server: ModbusTcpServer

    def __init__(self, host, port, client_host, client_port):
        """Initialize the server"""
        self.client_host = client_host
        self.client_port = client_port
        self.client = ModbusTcpClient(host=self.client_host, port=self.client_port)
        self.host = host
        self.port = port
        remote_context = RemoteDeviceContext(self.client, device_id=1)
        context = ModbusServerContext(devices=remote_context, single=True)
        self.server = ModbusTcpServer(
            context,
            address=(self.host, self.port),
        )
        self.log = get_class_method_logger(print)

    def set_log(self, log):
        self.log = log

    async def run(self):
        """Run the server"""
        self.client.connect()
        assert self.client.connected
        message = f"TCP client on {self.client_host}:{self.client_port}"
        self.log(message)

        message = f"serving on {self.host} port {self.port}"
        self.log(message)
        await self.server.serve_forever()

    async def stop(self):
        """Stop the server"""
        if self.server:
            await self.server.shutdown()
            self.log("TCP server is down")
