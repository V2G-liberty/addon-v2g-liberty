"""A generic V2G Liberty module for Modbus communication"""

import asyncio
from typing import Callable, Optional
from appdaemon import Hass
import pymodbus.client as modbusClient
from pymodbus.exceptions import ModbusException
from pyee.asyncio import AsyncIOEventEmitter
from apps.v2g_liberty.util import parse_to_int


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
        self._cb_modbus_state = cb_modbus_state
        # ModBusClient
        self._mbc = None

        # For tracking modbus failure in charger communication
        # At first successful connection this counter is set to 0
        # Until then do not trigger this counter, as most likely the user is still busy configuring
        self._modbus_exception_counter: int = None
        self._timer_id_check_modus_exception_state: str = None
        self._timer_id_check_error_state: str = None

        print("V2GmodbusClient init completed")

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
        temporary_mb_client = modbusClient.AsyncModbusTcpClient(
            host=host,
            port=port,
        )
        try:
            await temporary_mb_client.connect()
            if temporary_mb_client.connected:
                result = await temporary_mb_client.read_holding_registers(
                    modbus_address, count=1, slave=1
                )
                result = result.registers[0]
                return True, result
            else:
                return False, None
        except Exception as e:
            print(f"[WARNING] Error Adhoc reading of register: {e}")
            return False, None
        finally:
            temporary_mb_client.close()

    async def initialise(self, host: str, port: int = 502):
        if self._mbc is not None:
            self._mbc.close()

        self._mbc = modbusClient.AsyncModbusTcpClient(
            host=host,
            port=port,
        )
        await self._mbc.connect()
        result = self._mbc.connected
        print(f"initialise at {host}:{port}, success: {result}")
        return result

    async def modbus_write(self, address: int, value: int, source: str) -> bool:
        """Generic modbus write function.
           Writing to the modbus server should exclusively be done through this function

        Args:
            address (int): the register / address to write to
            value (int): the value to write
            source (str): only for debugging

        Returns:
            bool: True if write was successful
        """

        if self._mbc is None:
            return False

        if not self._mbc.connected:
            await self._mbc.connect()

        if value < 0:
            # Modbus cannot handle negative values directly.
            value = self.MAX_USI + value

        result = None
        try:
            # I hate using the word 'slave', this should be 'server' but
            # pyModbus has not changed this yet...
            result = await self._mbc.write_register(
                address=address,
                value=value,
                slave=1,
            )
        except ModbusException as me:
            print(f"[WARNING] ModbusException {me}")
            await self._handle_modbus_exception(source="modbus_write")
            return False
        else:
            await self._reset_modbus_exception()

        if result is None:
            print("[WARNING] Failed to write to modbus server.")
            return False

        return True

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
            print("[WARNING] Modbus client not initialised")
            return None

        if not self._mbc.connected:
            print("Connecting Modbus client")
            await self._mbc.connect()

        result = None
        try:
            # I hate using the word 'slave', this should be 'server' but pyModbus
            # has not changed this yet...
            result = await self._mbc.read_holding_registers(
                address=address,
                count=length,
                slave=1,
            )
        except ModbusException as me:
            print(f"[WARNING] ModbusException {me}")
            is_unrecoverable = await self._handle_modbus_exception(source="modbus_read")
            if is_unrecoverable:
                return None
        else:
            await self._reset_modbus_exception()

        if result is None:
            print(
                f"[WARNING] Result is None for address '{address}' and length '{length}'."
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
        When a 'realtime' reading from the modbus server is needed, as opposed to
        stored value from polling.
        It is expected to be between min_value_at_forced_get/max_value_at_forced_get.
        This is aimed at the SoC, this is expected to be between 2 and 97%, but at
        timeout 1% to 100% is acceptable.

        If the value is not in the wider acceptable range at timeout we assume
        the modbus server has crashed, and we call __handle_un_recoverable_error.

        :param register: The address to read from
        :param min_value_at_forced_get: min acceptable value
        :param max_value_at_forced_get: max acceptable value
        :param min_value_after_forced_get: min acceptable value after the timeout
        :param max_value_after_forced_get: max acceptable value after the timeout
        :return: the read value
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
                # Only one register is read so count = 1, the charger expects slave to be 1.
                result = await self._mbc.read_holding_registers(
                    register, count=1, slave=1
                )
            except ModbusException as me:
                print(f"[WARNING] ModbusException {me}")
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
                        print(f"After {total_time} sec. value {result} was retrieved.")
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
                        print(f"after timed out relevant value was {result}.")
                        break

                print("timed out, no relevant value was retrieved.")
                # This does not always trigger a connection exception, but we can assume the
                # connection is down. This normally would result in ModbusExceptions earlier
                # and these would normally trigger __handle_un_recoverable_error already.
                await self._handle_un_recoverable_error(
                    reason="timeout", source="orce_get_register"
                )
                return None

            await asyncio.sleep(delay_between_reads)
            continue
        # End of while loop

        # await self.__update_charger_communication_state(can_communicate=True)
        await asyncio.sleep(self.WAIT_AFTER_MODBUS_READ_IN_MS / 1000)
        return result

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
            print(f"{source}: modbus exception. Configuration not (yet) valid?")
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
        print("[WARNING] There are persistent modbus exceptions.")
        # This method could be called from two timers. Make sure both are canceled so no double
        # notifications get sent.
        self._cancel_timer(self._timer_id_check_modus_exception_state)
        self._cancel_timer(self._timer_id_check_error_state)
        await self._cb_modbus_state(persistent_problem=True)

    async def _reset_modbus_exception(self):
        """Reset modbus_exception_counter and cancel timer_id_check_modus_exception_state
        and set the connection status in the UI to is_alive=True
        Works in conjunction with _handle_modbus_exception.
        To be called every time there has been a successful modbus read/write.
        """
        if self._modbus_exception_counter == 1:
            print("There was an modbus exception, now solved.")
            await self._cb_modbus_state(persistent_problem=False)
        self._modbus_exception_counter = 0
        self._cancel_timer(self._timer_id_check_modus_exception_state)
        self._timer_id_check_modus_exception_state = None

    ################# UTILS #################

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

    # TODO: Consolidate this function to a util, it is copied in multiple classes
    def _cancel_timer(self, timer_id: str):
        """Utility function to silently cancel a timer.
        Born because the "silent" flag in cancel_timer does not work and the
        logs get flooded with useless warnings.

        Args:
            timer_id: timer_handle to cancel
        """
        if timer_id in [None, ""]:
            return
        if self.hass.timer_running(timer_id):
            silent = True  # Does not really work
            self.hass.cancel_timer(timer_id, silent)
