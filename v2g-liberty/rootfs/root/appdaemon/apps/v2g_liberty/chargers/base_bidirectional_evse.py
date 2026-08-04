"""Abstract base for a bidirectional (V2G) EVSE / charger driver.

Adds discharge capability to the uni-directional base. Thin/structural (see
base_unidirectional_evse) — it does not prescribe method names; the concrete
driver (WallboxQuasar1Client) exposes dev's public charger API.
"""

from abc import ABC

from .base_unidirectional_evse import UnidirectionalEVSE


class BidirectionalEVSE(UnidirectionalEVSE, ABC):
    """Base class for a bidirectional (V2G) EVSE / charger driver."""
