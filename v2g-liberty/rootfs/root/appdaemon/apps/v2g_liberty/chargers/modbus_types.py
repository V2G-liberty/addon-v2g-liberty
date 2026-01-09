"""Type definitions for Modbus-related configuration"""

from dataclasses import dataclass
from typing import Any
import struct


@dataclass
class MBR:
    """
    A dataclass representing a ModBus Register (MBR) to read/write.

    Attributes:
        address (int): The starting Modbus register address (0-based or 1-based,
                       depending on your device's convention).

        data_type (str): The data type to interpret the registers as. Defaults to "uint16".
                        Valid options:
                        - "uint16": Unsigned 16-bit integer (default).
                        - "int16": Signed 16-bit integer.
                        - "uint32": Unsigned 32-bit integer (requires length >= 2).
                        - "int32": Signed 32-bit integer (requires length >= 2).
                        - "int64": Signed 64-bit integer (requires length >= 4).
                        - "float32": 32-bit floating-point (requires length >= 2).
                        - "string": UTF-8 encoded string (any length).

        length (int): Number of consecutive registers to read/write.
        device_id (int): Modbus device ID, defaults to 1.
    """

    address: int
    data_type: str = "uint16"
    length: int = 1
    device_id: int = 1

    def decode(self, registers: list[int]) -> Any:
        """
        Decode a list of raw Modbus register values into a Python value.

        Args:
            registers (list[int]): Raw 16-bit register values.

        Returns:
            Any: Decoded Python value (int, float, str, or None).
        """
        try:
            # Strings use all registers
            if self.data_type == "string":
                bytes_data = b"".join(reg.to_bytes(2, "big") for reg in registers)
                return bytes_data.decode("utf-8").rstrip("\x00")

            # 32-bit types
            if self.data_type in ("uint32", "int32", "float32"):
                if self.length < 2:
                    return None
                bytes_data = b"".join(reg.to_bytes(2, "big") for reg in registers[:2])
                if self.data_type == "uint32":
                    return struct.unpack(">I", bytes_data)[0]
                if self.data_type == "int32":
                    return struct.unpack(">i", bytes_data)[0]
                if self.data_type == "float32":
                    return struct.unpack(">f", bytes_data)[0]

            # 64-bit signed integer
            if self.data_type == "int64":
                if self.length < 4:
                    return None
                bytes_data = b"".join(reg.to_bytes(2, "big") for reg in registers[:4])
                return struct.unpack(">q", bytes_data)[0]

            # 16-bit types
            if self.data_type == "int16":
                return int.from_bytes(
                    registers[0].to_bytes(2, "big"),
                    "big",
                    signed=True,
                )

            if self.data_type == "uint16":
                return registers[0]

            print(f"[WARNING] Unknown data_type: {self.data_type}")
            return None

        except Exception as e:
            print(f"[WARNING] Error decoding MBR {self}: {e}")
            return None

    def encode(self, value: Any) -> list[int]:
        """
        Encode a Python value into a list of 16-bit Modbus registers.

        Args:
            value (Any): The value to encode (int, float, or str).

        Returns:
            list[int]: A list of 16-bit register values ready for writing.

        Notes:
            - Strings are UTF-8 encoded and padded/truncated to match length.
            - Multi-register numeric types are big-endian.
        """
        try:
            # Strings: encode to bytes, pad/truncate to length*2 bytes
            if self.data_type == "string":
                raw = str(value).encode("utf-8")
                raw = raw[: self.length * 2]  # truncate
                raw = raw.ljust(self.length * 2, b"\x00")  # pad
                return [
                    int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)
                ]

            # 32-bit types
            if self.data_type in ("uint32", "int32", "float32"):
                if self.length < 2:
                    raise ValueError(f"{self.data_type} requires length >= 2")

                if self.data_type == "uint32":
                    bytes_data = struct.pack(">I", int(value))
                elif self.data_type == "int32":
                    bytes_data = struct.pack(">i", int(value))
                else:  # float32
                    bytes_data = struct.pack(">f", float(value))

                return [
                    int.from_bytes(bytes_data[0:2], "big"),
                    int.from_bytes(bytes_data[2:4], "big"),
                ]

            # 64-bit signed integer
            if self.data_type == "int64":
                if self.length < 4:
                    raise ValueError("int64 requires length >= 4")
                bytes_data = struct.pack(">q", int(value))
                return [
                    int.from_bytes(bytes_data[i : i + 2], "big") for i in range(0, 8, 2)
                ]

            # 16-bit types
            if self.data_type == "int16":
                return [int(value) & 0xFFFF]

            if self.data_type == "uint16":
                return [int(value) & 0xFFFF]

            raise ValueError(f"Unsupported data_type: {self.data_type}")

        except Exception as e:
            print(f"[WARNING] Error encoding value for MBR {self}: {e}")
            return []


@dataclass
class ModbusConfigEntity:
    """
    Represents a generic entity configuration with a Modbus Register (MBR)
    and optional value constraints.

    This configuration defines how to read and interpret Modbus registers,
    including numeric ranges, relaxed limits, current state, and optional
    change‑handling behavior.

    Attributes:
        minimum_value (int | float | None): The minimum valid value for
            entities with numeric values.
        maximum_value (int | float | None): The maximum valid value for
            entities with numeric values.
        relaxed_min_value (int | float | None): Optional relaxed minimum
            value used in certain operating modes.
        relaxed_max_value (int | float | None): Optional relaxed maximum
            value used in certain operating modes.
        current_value (Any | None): The current value of this entity, or
            None if not yet read or unavailable.
        change_handler (str | None): Optional name of a function to call
            when the value changes.
    """

    modbus_register: MBR
    minimum_value: int | float | None = None
    maximum_value: int | float | None = None
    relaxed_min_value: int | float | None = None
    relaxed_max_value: int | float | None = None
    current_value: Any | None = None
    pre_processor: str | None = None
    change_handler: str | None = None

    def set_value(self, new_value: Any, owner: Any = None) -> bool:
        """
        Update the entity's current_value after optional pre-processing
        and min/max validation.

        Args:
            new_value (Any): The raw value to set.
            owner (Any): The object that owns the pre_processor method.
                        Required only if pre_processor is a method name.

        Returns:
            bool: True if the value changed, False otherwise.
        """

        if new_value is None:
            # When read_registers returns None there was an error, not an actual value
            return False

        try:
            dt = self.modbus_register.data_type

            if dt in ("uint16", "int16", "uint32", "int32", "int64"):
                new_value = int(float(new_value))
            elif dt == "float32":
                new_value = float(new_value)
            elif dt == "string":
                new_value = str(new_value)
            else:
                print(
                    f"[WARNING] Unknown data_type '{dt}' in MBR; value left unchanged"
                )

        except Exception as e:
            print(
                f"[WARNING] Failed to convert value '{new_value}' for type '{dt}': {e}"
            )
            return False

        if self.pre_processor is not None:
            try:
                if owner is None:
                    raise ValueError("owner must be provided when using pre_processor")

                bound_method = getattr(owner, self.pre_processor)
                new_value = bound_method(new_value)

            except Exception as e:
                print(f"[WARNING] Pre_processor '{self.pre_processor}' failed: {e}")
                return False

        if (
            self.minimum_value is not None
            and self.maximum_value is not None
            and not (self.minimum_value <= new_value <= self.maximum_value)
        ):
            # Value is outside min/max

            if self.current_value is not None:
                # Ignore and keep current value unless that is None
                return False

            # Current value is None: This is rare, current_value will only be None at startup.
            # Not setting a value will cause the application to hang.
            # TODO: Check if this is still the case, seems strange as it still sets value to None...
            # Lets use the relaxed  min/max if the entity supports that.
            if self.relaxed_min_value is None and self.relaxed_max_value is None:
                new_value = None
            elif self.relaxed_min_value <= new_value <= self.relaxed_max_value:
                # New value in relaxed range and current value is none, accept the new_value
                pass
            else:
                # Current_value is None and new_value is outside releaxed range.
                new_value = None

        has_changed = new_value != self.current_value
        self.current_value = new_value

        return has_changed
