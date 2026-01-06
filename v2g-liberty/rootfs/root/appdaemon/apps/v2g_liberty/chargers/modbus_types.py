"""Type definitions for Modbus-related configuration"""

from typing import TypedDict


class MBR(TypedDict):
    """
    A typed dictionary representing a ModBus Register (MBR) to read.

    Attributes:
        address (int): The starting Modbus register address (0-based or 1-based,
                       depending on your device's convention).
        data_type (str): The data type to interpret the registers as, defaults to "uint16"
                        Valid options:
                        - "uint16": Unsigned 16-bit integer (default).
                        - "int16": Signed 16-bit integer.
                        - "uint32": Unsigned 32-bit integer (requires length >= 2).
                        - "int32": Signed 32-bit integer (requires length >= 2).
                        - "int64": Signed 64-bit integer (requires length >= 4).
                        - "float32": Signed 32-bit floating-point (requires length >= 2).
                        - "string": UTF-8 encoded string (any length).
        length (int): The number of consecutive registers to read.
        device_id (int): Modbus device ID, defaults to 1.
    """

    address: int
    data_type: str = "uint16"
    length: int = 1
    device_id: int = 1


class ModbusConfigEntity(TypedDict):
    """
    Represents a generic entity configuration with Modbus range and value constraints.

    This configuration is used to define how to read and interpret Modbus registers,
    including value ranges, current state, and change handling.

    Attributes:
        modbus_register (MBR): Configuration for Modbus register access including
            device ID, address, length, and data type.
        minimum_value (int): The minimum valid value for this entity.
        maximum_value (int): The maximum valid value for this entity.
        relaxed_min_value (int | None): Optional relaxed minimum value that might be used
            in certain operating modes. Defaults to None.
        relaxed_max_value (int | None): Optional relaxed maximum value that might be used
            in certain operating modes. Defaults to None.
        current_value (int | None): The current value of this entity. Defaults to None
            when not yet read or unavailable.
        change_handler (str | None): Optional name of a function to call when this value
            changes. Defaults to None.
    """

    modbus_register: MBR
    minimum_value: int
    maximum_value: int
    relaxed_min_value: int | None = None
    relaxed_max_value: int | None = None
    current_value: int | None = None
    change_handler: str | None = None
