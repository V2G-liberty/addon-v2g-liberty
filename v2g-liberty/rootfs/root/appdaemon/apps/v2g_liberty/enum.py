from enum import Enum
from typing import Union, TypeVar

class DataStatus(Enum):
    UNAVAILABLE = "unavailable" # Data that cannot be retrieved at the moment, e.g. SoC when car is disconnected
    # UNKNOWN = "unknown" # Data is unknown, e.g. no connection to a modbus client has been established yet

T = TypeVar('T')
MaybeData = Union[T, DataStatus]