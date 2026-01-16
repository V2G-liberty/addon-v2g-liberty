"""A generic V2G Liberty module for Modbus communication"""

import asyncio
from typing import Callable, Optional, Union, List
from collections import defaultdict
from appdaemon import Hass
from apps.v2g_liberty.log_wrapper import get_class_method_logger
from pymodbus.client import AsyncModbusTcpClient as amtc
from pymodbus.exceptions import ModbusException, ConnectionException
from pyee.asyncio import AsyncIOEventEmitter
from apps.v2g_liberty.conversion_util import parse_to_int
from apps.v2g_liberty.utils.hass_util import cancel_timer_silently
from .modbus_types import MBR


class V2GmodbusClient(AsyncIOEventEmitter):
    """A generic V2G Liberty module for Modbus communication"""

    FORCED_READ_TIMEOUT_IN_SECONDS: int = 60
    WAIT_AFTER_MODBUS_READ_IN_MS: int = 50

    # Max value of an “unsigned short integer” 2^16, used for negative values in modbus.
    MAX_USI = 65536
    HALF_MAX_USI = MAX_USI / 2

    MAX_MODBUS_EXCEPTION_STATE_DURATION_IN_SECONDS: int = 60

    def __init__(
        self, hass: Hass, cb_modbus_state: Optional[Callable[[bool], None]] = None
    ):
        """Initialise ModbusClient
        Configuration and connecting the modbus client is done separately in initialise_charger.

        Args:
            cb_modbus_state: Optional callable that is called when the persistent_modbus_exception
            state changes. If this state is True it usually means that the modbus server module
            has crashed and needs to be restarted.
        """
        super().__init__()
        self.hass = hass
        self._log = get_class_method_logger(hass.log)
        self._cb_modbus_state = cb_modbus_state
        # ModBusClient
        self._mbc = None

        # For tracking modbus failure in charger communication
        # At first successful connection this counter is set to 0
        # Until then do not trigger this counter, as most likely the user is still busy configuring
        self._modbus_exception_counter: int = None
        self._timer_id_check_modus_exception_state: str = None
        self._timer_id_check_error_state: str = None

        self._log("V2GmodbusClient init completed")

    async def adhoc_read_register(
        self, modbus_address: int, host: str, port: int = 502
    ) -> tuple[bool, int | None]:
        """Adhoc reading of a value from a modbus register with a given host without the need for
        prior initialisation.
        It's usedfor testing user entered host/port in the charger settings dialog.

        Args:
            modbus_address (int): address of the register must be 0 < address < 65536
            host (str): IP address or hostname of the EVSE charger.
            port (int): Modbus TCP port of the EVSE charger, defaults to 502

        Returns:
            tuple[bool, int | None]:
            - Element 1: boolean indicating connection success.
            - Element 2: int value that was read from the register. Is None if element 1 == False.
        """

        # This method does not activate the exception tracking, as it is only used for testing
        # user entered configuration.
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

            # A dummy read that forces the TCP connection to establish, use defaults where possible.
            await client.read_holding_registers(address=0)
        except ConnectionException:
            self._log(
                f"Could not establish TCP connection to '{host}:{port}', aborting.",
                level="WARNING",
            )
            return None
        except ModbusException as me:
            # Other Modbus errors (e.g. illegal address due to use of default?) still mean the
            # device is reachable
            self._log(f"Could not read from modbus client, ModbusException: {me}.")

        return client

    async def initialise(self, host: str, port: int = 502) -> bool:
        """
        Initializes the Modbus TCP client. Tries to setup a connection.

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

    ################################################################################################
    #                OLD METHODS FIRST GENERATION MODBUS CHARGERS (EG WALBOX QUASAR 1)             #
    ################################################################################################

    # async def modbus_write(self, address: int, value: int, source: str) -> bool:
    #     """Generic modbus write function.
    #        Writing to the modbus server should exclusively be done through this function

    #     Args:
    #         address (int): the register / address to write to
    #         value (int): the value to write
    #         source (str): only for debugging

    #     Returns:
    #         bool: True if write was successful
    #     """

    #     if self._mbc is None:
    #         return False

    #     if value < 0:
    #         # Modbus cannot handle negative values directly.
    #         value = self.MAX_USI + value

    #     result = None
    #     try:
    #         result = await self._mbc.write_register(
    #             address=address,
    #             value=value,
    #             device_id=1,
    #         )
    #     except ModbusException as me:
    #         self._log(f"ModbusException {me}", level="WARNING")
    #         await self._handle_modbus_exception(source="modbus_write")
    #         return False
    #     else:
    #         await self._reset_modbus_exception()

    #     if result is None:
    #         self._log("Failed to write to modbus server.", level="WARNING")
    #         return False

    #     return True

    # Deprecated, but keep for now for debugging
    async def modbus_read(self, address: int, length: int = 1, source: str = "unknown"):
        """Generic modbus read function.
           Reading from the modbus server is preferably done through this function

        Args:
            address (int): The starting register/address from which to read the values
            length (int, optional): Number of successive addresses to read. Defaults to 1.
            source (str, optional): only for debugging.

        Raises:
            exc: ModbusException

        Returns:
            _type_: List of int values
        """

        if self._mbc is None:
            self._log("Modbus client not initialised", level="WARNING")
            return None

        if not self._mbc.connected:
            self._log("Connecting Modbus client")
            await self._mbc.connect()

        result = None
        try:
            result = await self._mbc.read_holding_registers(
                address=address,
                count=length,
                device_id=1,
            )
        except ModbusException as me:
            self._log(f"ModbusException {me}", level="WARNING")
            is_unrecoverable = await self._handle_modbus_exception(source="modbus_read")
            if is_unrecoverable:
                return None
        else:
            await self._reset_modbus_exception()

        if result.isError():
            self._log(f"Modbus error for address {address}: {result}", level="WARNING")
        elif result is None:
            self._log(
                f"Result is None for address '{address}' and length '{length}'.",
                level="WARNING",
            )
            return None

        result = list(map(self._get_2comp, result.registers))
        return result

    async def force_get_register(
        self,
        register: int,
        min_value_at_forced_get: int,
        max_value_at_forced_get: int,
        min_value_after_forced_get: int = None,
        max_value_after_forced_get: int = None,
    ) -> int | None:
        """
        LEGACY METHOD for Wallbox Quasar 1 compatibility.

        Repeatedly reads a modbus register until a value within the acceptable range is retrieved.
        This is needed for Wallbox Quasar 1 which:
        - Returns 0 for SoC when not charging (hardware limitation)
        - Has unreliable readings during state transitions

        Modern chargers (EVtec BiDiPro) should NOT use this method.
        Instead, trust the polled values from ModbusConfigEntity which are updated every 5 seconds.

        WARNING: This method can take 10-20 seconds to complete.
        Check if polling already has a valid value before calling this method
        to avoid unnecessary delays.

        The method loops every 0.25 seconds until a value is within the expected range,
        or times out after 60 seconds.

        :param register: The address to read from
        :param min_value_at_forced_get: min acceptable value (e.g., 2% for SoC)
        :param max_value_at_forced_get: max acceptable value (e.g., 97% for SoC)
        :param min_value_after_forced_get: relaxed min value after timeout (e.g., 1% for SoC)
        :param max_value_after_forced_get: relaxed max value after timeout (e.g., 100% for SoC)
        :return: the read value, or None if timeout with invalid value
        """

        if self._mbc is None:
            return None

        if not self._mbc.connected:
            await self._mbc.connect()

        # Times in seconds
        total_time = 0
        delay_between_reads = 0.25

        # If the acceptable value is not read yet, keep trying until timeout
        # self.MAX_MODBUS_EXCEPTION_STATE_DURATION_IN_SECONDS
        while True:
            result = None
            try:
                # Only 1 register is read so count = 1, the quasar charger expects device_id = 1.
                result = await self._mbc.read_holding_registers(
                    register, count=1, device_id=1
                )
            except ModbusException as me:
                self._log(f"ModbusException {me}", level="WARNING")
                is_unrecoverable = await self._handle_modbus_exception(
                    source="force_get_register"
                )
                if is_unrecoverable:
                    return
            else:
                await self._reset_modbus_exception()

            if result is not None:
                try:
                    result = self._get_2comp(result.registers[0])
                    if min_value_at_forced_get <= result <= max_value_at_forced_get:
                        # Acceptable result retrieved
                        self._log(
                            f"After {total_time} sec. value {result} was retrieved."
                        )
                        break
                except TypeError:
                    pass
            total_time += delay_between_reads

            # We need to stop at some point
            if total_time >= self.FORCED_READ_TIMEOUT_IN_SECONDS:
                # No check for value_to_translate_to_none as this method is only called when:
                # - for SoC when car is connected and 0 represents an error
                # - for max_charger_power there is no value_to_translate_to_none

                # After the timeout a more lenient range is applicable for some entities
                if (
                    min_value_after_forced_get is not None
                    and max_value_after_forced_get is not None
                    and result is not None
                ):
                    if (
                        min_value_after_forced_get
                        <= result
                        <= max_value_after_forced_get
                    ):
                        self._log(f"after timed out relevant value was {result}.")
                        break

                self._log(
                    "timed out, no relevant value was retrieved.", level="WARNING"
                )
                # This does not always trigger a connection exception, but we can assume the
                # connection is down. This normally would result in ModbusExceptions earlier
                # and these would normally trigger __handle_un_recoverable_error already.
                await self._handle_un_recoverable_error()
                return None

            await asyncio.sleep(delay_between_reads)
            continue
        # End of while loop

        # await self.__update_charger_communication_state(can_communicate=True)
        await asyncio.sleep(self.WAIT_AFTER_MODBUS_READ_IN_MS / 1000)
        return result

    ################################################################################################
    #                NEW METHODS USING MODBUS REGISTERS (MBR)                                      #
    ################################################################################################

    async def read_registers(
        self, modbus_registers: List[MBR]
    ) -> List[Union[int, float, str, None]]:
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
        mbr_by_device: dict[int, list[MBR]] = defaultdict(list)
        for mbr in modbus_registers:
            mbr_by_device[mbr.device_id].append(mbr)

        results = [None] * len(modbus_registers)

        for device_id, device_ranges in mbr_by_device.items():
            # Sort by address
            sorted_ranges = sorted(device_ranges, key=lambda r: r.address)

            start_address = sorted_ranges[0].address
            end_address = sorted_ranges[-1].address + sorted_ranges[-1].length - 1

            try:
                response = await self._mbc.read_holding_registers(
                    address=start_address,
                    count=end_address - start_address + 1,
                    device_id=device_id,
                )
            except ModbusException as me:
                self._log(
                    f"ModbusException reading device {device_id}: {me}", level="WARNIG"
                )
                is_unrecoverable = await self._handle_modbus_exception(
                    source="read_registers"
                )
                # Set None for all registers in this device
                for mbr in device_ranges:
                    results[index_map[id(mbr)]] = None
                if is_unrecoverable:
                    # Return immediately - no point continuing if connection is dead
                    return results
                # Otherwise continue to next device (recoverable error)
                continue
            else:
                # Only reset exception counter on successful reads
                await self._reset_modbus_exception()

            if response.isError():
                self._log(
                    f"Modbus error for device {device_id}: {response}", level="WARNING"
                )
                for mbr in device_ranges:
                    results[index_map[id(mbr)]] = None
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

        Args:
            modbus_register (MBR): The Modbus register definition.
            value (int | float | str): The value to write.

        Returns:
            bool: True if successful, False otherwise.
        """

        # Ensure client exists
        if self._mbc is None:
            self._log("Modbus client not initialized", level="WARNING")
            return False

        # Ensure connection
        if not self._mbc.connected:
            self._log("Connecting Modbus client")
            await self._mbc.connect()

        try:
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
                await self._handle_modbus_exception(source="write_modbus_register")
                return False

            await self._reset_modbus_exception()
            return True

        except ModbusException as me:
            self._log(
                f"ModbusException writing register {modbus_register}: {me}",
                level="WARNING",
            )
            await self._handle_modbus_exception(source="write_modbus_register")
            return False
        except Exception as e:
            self._log(
                f"Error writing Modbus register {modbus_register}: {e}", level="WARNING"
            )
            return False

    ################################################################################################
    #                       PRIVATE METHODS FOR ERROR HANDLING (OLD)                               #
    ################################################################################################

    async def _handle_modbus_exception(self, source):
        """Modbus (connection) exception occurs regularly with the Wallbox Quasar 1 (e.g. bi-weekly)
        and is usually not self resolving.
        This method checks the severity of the connection problem and notifies the user if needed.

        This method is to be called from modbus_read and modbus_write methods.
        Connection exceptions occurs on client.read() and client.write() instead of, as you would
        expect, on client.connect().

        :param source: Only for logging
        :return: Is the exception persistent for longer than the set timeout.
        """
        is_unrecoverable = False

        # The counter is initiated at None.
        # At first successful modbus call this counter is set to 0 by __reset_modbus_exception.
        # Until then do not treat the exception as a problem and do not increment the counter.
        # Most likely the app is still initialising or user is still busy configuring.
        if self._modbus_exception_counter is None:
            self._log(f"{source}: modbus exception. Configuration not (yet) valid?")
            is_unrecoverable = False

        # So, there is an exception after initialisation, this still could self recover.
        # We'll wait self.MAX_MODBUS_EXCEPTION_STATE_DURATION_IN_SECONDS, until then consider it
        # recoverable.
        if self._modbus_exception_counter == 0:
            # First modbus exception.
            self._timer_id_check_modus_exception_state = self.hass.run_in(
                self._handle_un_recoverable_error,
                delay=self.MAX_MODBUS_EXCEPTION_STATE_DURATION_IN_SECONDS,
            )
            self._modbus_exception_counter = 1
            is_unrecoverable = False
        else:
            # This is a repeated exception, so a timer has been set to handle this
            # as unrecoverable, just above here.
            # If there is no timer any more the time has run out to see this as
            # recoverable.
            if self._timer_id_check_modus_exception_state in [None, ""]:
                is_unrecoverable = True
            else:
                is_unrecoverable = False

        return is_unrecoverable

    async def _handle_un_recoverable_error(self, *_args):
        self._log("There are persistent modbus exceptions.", level="WARNING")
        # This method could be called from two timers. Make sure both are canceled so no double
        # notifications get sent.
        cancel_timer_silently(self.hass, self._timer_id_check_modus_exception_state)
        cancel_timer_silently(self.hass, self._timer_id_check_error_state)
        await self._cb_modbus_state(persistent_problem=True)

    async def _reset_modbus_exception(self):
        """Reset modbus_exception_counter and cancel timer_id_check_modus_exception_state
        and set the connection status in the UI to is_alive=True
        Works in conjunction with _handle_modbus_exception.
        To be called every time there has been a successful modbus read/write.
        """
        if self._modbus_exception_counter == 1:
            self._log("There was an modbus exception, now solved.")
            await self._cb_modbus_state(persistent_problem=False)
        self._modbus_exception_counter = 0
        cancel_timer_silently(self.hass, self._timer_id_check_modus_exception_state)
        self._timer_id_check_modus_exception_state = None

    ################################################################################################
    #                                   UTIL METHODS                                               #
    ################################################################################################

    def _get_2comp(self, number):
        """Util function to covert a modbus read value to in with two's complement values
           into negative int numbers.

        Args:
            number: value to convert, normally int, but can be other type
                    should be: 0 < number < self.MAX_USI

        Returns:
            int: With negative values if applicable
        """
        return_value = parse_to_int(number, None)
        if return_value is None:
            return number
        if return_value > self.HALF_MAX_USI:
            # This represents a negative value.
            return_value = return_value - self.MAX_USI
        return return_value
