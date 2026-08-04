"""Generic Modbus-TCP transport for V2G Liberty chargers.

This module provides ONLY the raw Modbus-TCP transport:
- connection management (initialise / terminate / connect);
- reading a list of :class:`MBR` registers into decoded values;
- writing a single :class:`MBR` value;
- raw register read/write pass-throughs used by legacy chargers that keep
  their own exception/grace-timer state machine (e.g. the Wallbox Quasar 1).

Deliberately, the exception/grace-timer STATE MACHINE is NOT hosted here. A
charger that needs it (the Wallbox Quasar 1) keeps that logic on the charger and
routes only its raw reads/writes through this transport. The transport holds the
underlying pymodbus client as a replaceable attribute (``_mbc``) so tests can
inject a fake low-level client.
"""

from typing import List, Union

from appdaemon.plugins.hass.hassapi import Hass
from pymodbus.client import AsyncModbusTcpClient as amtc
from pymodbus.exceptions import ModbusException
from pyee.asyncio import AsyncIOEventEmitter

from ..log_wrapper import get_class_method_logger
from .modbus_types import MBR


class V2GmodbusClient(AsyncIOEventEmitter):
    """A generic V2G Liberty module for raw Modbus-TCP transport."""

    def __init__(self, hass: Hass):
        """Initialise the transport.

        Configuration and connecting the modbus client is done separately in
        :meth:`initialise`.
        """
        super().__init__()
        self.hass = hass
        self._log = get_class_method_logger(module_name="v2g_modbus_client")
        # The underlying pymodbus client; replaceable so tests can inject a fake.
        self._mbc = None
        self._log("V2GmodbusClient init completed")

    ################################################################################################
    #                          CONNECTION MANAGEMENT                                               #
    ################################################################################################

    async def adhoc_read_register(
        self, modbus_address: int, host: str, port: int = 502
    ) -> tuple[bool, int | None]:
        """Adhoc reading of a value from a modbus register with a given host without the need for
        prior initialisation.
        It's used for testing user entered host/port in the charger settings dialog.

        Args:
            modbus_address (int): address of the register must be 0 < address < 65536
            host (str): IP address or hostname of the EVSE charger.
            port (int): Modbus TCP port of the EVSE charger, defaults to 502

        Returns:
            tuple[bool, int | None]:
            - Element 1: boolean indicating connection success.
            - Element 2: int value that was read from the register. Is None if element 1 == False.
        """
        temporary_mb_client = await self.__create_client(host=host, port=port)

        if temporary_mb_client is None:
            return False, None

        try:
            result = await temporary_mb_client.read_holding_registers(
                modbus_address, count=1, device_id=1
            )
            result = result.registers[0]
            return True, result
        except ModbusException as me:
            self._log(f"Error Adhoc reading of register: {me}", level="WARNING")
            return False, None
        finally:
            temporary_mb_client.close()

    async def __create_client(self, host: str, port: int) -> amtc:
        if host is None or port is None:
            self._log(
                "Could not create Modbus client: host or port are None.",
                level="WARNING",
            )
            return None

        try:
            client = amtc(
                host=host,
                port=port,
            )
            await client.connect()
        except ModbusException as me:
            self._log(
                f"Could not initialise modbus client, ModbusException: {me}.",
                level="WARNING",
            )
            return None

        # client.connect() never throws a ConnectionException, but the connected
        # property is a reliable way of checking if host and port can be reached.
        # Matches dev's ModbusEVSEclient.__init_client: no probe read, no extra
        # Modbus transaction on connect (strict behaviour-preservation).
        if not client.connected:
            return None

        return client

    async def initialise(self, host: str, port: int = 502) -> bool:
        """
        Initialises the Modbus TCP client. Tries to setup a connection.

        Args:
            host (str): The hostname or IP address of the Modbus device.
            port (int, optional): The TCP port of the Modbus device. Defaults to 502.

        Returns:
            bool: True if the connection was successful, False otherwise.
        """
        if self._mbc is not None:
            self.terminate()

        self._mbc = await self.__create_client(host=host, port=port)
        if self._mbc is None:
            self._log("Modbus client not created.", level="WARNING")
            return False
        self._log(f"Succesful connection to {host}:{port}.")
        return True

    def terminate(self) -> None:
        """
        Terminates (closes) the Modbus TCP client connection and cleans up resources.
        """
        if self._mbc is not None:
            try:
                self._mbc.close()
            except ModbusException as me:
                self._log(
                    f"Error while closing Modbus connection: {me}", level="WARNING"
                )
            finally:
                self._mbc = None

    @property
    def is_initialised(self) -> bool:
        """Whether an underlying modbus client has been created (connection set up)."""
        return self._mbc is not None

    @property
    def connected(self) -> bool:
        """Whether the underlying modbus client currently reports a live connection."""
        return self._mbc is not None and self._mbc.connected

    async def connect(self):
        """(Re)connect the underlying modbus client."""
        if self._mbc is not None:
            return await self._mbc.connect()
        return None

    ################################################################################################
    #                          RAW REGISTER PASS-THROUGH                                           #
    #  For chargers that keep their own exception/grace-timer state machine (Wallbox Quasar 1).    #
    #  These deliberately do NOT catch ModbusException — the caller handles it.                    #
    ################################################################################################

    async def read_holding_registers(
        self, address: int, count: int = 1, device_id: int = 1
    ):
        """Raw read pass-through. Propagates ModbusException to the caller."""
        return await self._mbc.read_holding_registers(
            address=address, count=count, device_id=device_id
        )

    async def write_register(self, address: int, value: int, device_id: int = 1):
        """Raw write pass-through. Propagates ModbusException to the caller."""
        return await self._mbc.write_register(
            address=address, value=value, device_id=device_id
        )

    ################################################################################################
    #                          MBR-BASED READ / WRITE (decoded)                                    #
    #  Transport-only: no exception state machine — ModbusException propagates to the caller.      #
    ################################################################################################

    async def read_registers(
        self, modbus_registers: List[MBR]
    ) -> List[Union[int, float, str, None]]:
        """Read a list of MBRs and return their decoded values (one per MBR).

        Reads the min-to-max span per device_id in a single request and decodes
        each MBR from its slice. Returns None for a register whose device read
        failed (isError). Propagates ModbusException to the caller.
        """
        if not modbus_registers:
            return []

        if self._mbc is None:
            self._log("Modbus client not initialised", level="WARNING")
            return [None] * len(modbus_registers)

        if not self._mbc.connected:
            self._log("Connecting Modbus client")
            await self._mbc.connect()

        # Use id(mbr) because MBR is not hashable
        index_map = {id(mbr): i for i, mbr in enumerate(modbus_registers)}

        # Group by device_id
        mbr_by_device: dict[int, list[MBR]] = {}
        for mbr in modbus_registers:
            mbr_by_device.setdefault(mbr.device_id, []).append(mbr)

        results = [None] * len(modbus_registers)

        for device_id, device_ranges in mbr_by_device.items():
            # Sort by address
            sorted_ranges = sorted(device_ranges, key=lambda r: r.address)

            start_address = sorted_ranges[0].address
            end_address = sorted_ranges[-1].address + sorted_ranges[-1].length - 1

            response = await self._mbc.read_holding_registers(
                address=start_address,
                count=end_address - start_address + 1,
                device_id=device_id,
            )

            if response.isError():
                self._log(
                    f"Modbus error for device {device_id}: {response}", level="WARNING"
                )
                continue

            registers = response.registers

            # Decode each MBR in sorted order
            for mbr in sorted_ranges:
                idx = index_map[id(mbr)]

                slice_start = mbr.address - start_address
                slice_end = slice_start + mbr.length
                reg_slice = registers[slice_start:slice_end]

                results[idx] = mbr.decode(reg_slice)

        return results

    async def write_modbus_register(
        self, modbus_register: MBR, value: int | float | str
    ) -> bool:
        """
        Writes a value to a Modbus register range defined by an MBR dataclass.

        Transport-only: propagates ModbusException to the caller.

        Args:
            modbus_register (MBR): The Modbus register definition.
            value (int | float | str): The value to write.

        Returns:
            bool: True if successful, False otherwise.
        """
        if self._mbc is None:
            self._log("Modbus client not initialised", level="WARNING")
            return False

        if not self._mbc.connected:
            self._log("Connecting Modbus client")
            await self._mbc.connect()

        registers = modbus_register.encode(value)
        if not registers:
            self._log(f"Encoding failed for {modbus_register}", level="WARNING")
            return False

        # Use write_register for single register, write_registers for multiple
        if len(registers) == 1:
            response = await self._mbc.write_register(
                address=modbus_register.address,
                value=registers[0],
                device_id=modbus_register.device_id,
            )
        else:
            response = await self._mbc.write_registers(
                address=modbus_register.address,
                values=registers,
                device_id=modbus_register.device_id,
            )

        if response.isError():
            self._log(
                f"Write error for address {modbus_register.address}: {response}.",
                level="WARNING",
            )
            return False

        return True
