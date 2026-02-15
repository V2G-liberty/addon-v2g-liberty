"""
Wallbox Quasar Modbus Register Map

This module defines the Modbus register addresses used by the Wallbox Quasar charger.
These registers are used for reading status and controlling the charger via Modbus TCP.

For V2G Liberty development: https://github.com/V2G-liberty/addon-v2g-liberty
"""

# Control Registers
CONTROL_MODE_REGISTER = 81  # 0 = User control, 1 = Remote control

# State Registers
CHARGER_STATE_REGISTER = 0x0219  # 537 - Current charger state (see states.py)
SOC_REGISTER = 0x021A  # 538 - State of Charge (0-100%)
INTERNAL_ERROR_REGISTER = 539  # Unrecoverable errors register high

# Power Registers
ACTUAL_POWER_REGISTER = 0x020E  # 526 - Current actual power (W, signed)
REQUESTED_POWER_REGISTER = 260  # Requested power from controller (W, signed)
MAX_AVAILABLE_POWER_REGISTER = 0x0202  # 514 - Maximum available power (W)

# Modbus Constants
MAX_UNSIGNED_SHORT = 65536  # 2^16 - Used for encoding negative values in Modbus

# Power Limits
MIN_POWER_WATTS = -7400  # Maximum discharge power (V2G)
MAX_POWER_WATTS = 7400   # Maximum charge power
MIN_MAX_POWER = 1        # Minimum value for max available power
MAX_MAX_POWER = 7400     # Maximum value for max available power

# SoC Limits
MIN_SOC_PERCENT = 0
MAX_SOC_PERCENT = 100

def encode_signed_power(power: int) -> int:
    """
    Convert signed power value to unsigned Modbus register value.

    Negative values are represented as (MAX_UNSIGNED_SHORT + power).
    This is how Modbus handles signed integers in unsigned registers.

    Args:
        power: Signed power value in watts

    Returns:
        Unsigned register value (0-65535)
    """
    if power < 0:
        return power + MAX_UNSIGNED_SHORT
    return power

def decode_signed_power(register_value: int) -> int:
    """
    Convert unsigned Modbus register value to signed power value.

    Values > MAX_UNSIGNED_SHORT/2 are treated as negative.

    Args:
        register_value: Unsigned register value (0-65535)

    Returns:
        Signed power value in watts
    """
    if register_value > MAX_UNSIGNED_SHORT / 2:
        return register_value - MAX_UNSIGNED_SHORT
    return register_value
