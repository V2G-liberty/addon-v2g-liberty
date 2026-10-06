"""Abstract base for a bidirectional (V2G) EVSE / charger driver.

Adds discharge capability to the uni-directional base. Thin/structural (see
base_unidirectional_evse) — it does not prescribe method names; the concrete
driver (WallboxQuasar1Client) exposes dev's public charger API.
"""

from abc import ABC

from .base_unidirectional_evse import UnidirectionalEVSE


class BidirectionalEVSE(UnidirectionalEVSE, ABC):
    """Base class for a bidirectional (V2G) EVSE / charger driver."""

    # Provided by the concrete driver: DISCHARGING_STATE, _am_i_active.

    async def is_discharging(self) -> bool:
        """Indicates if currently the connected car is discharging (not charging)"""
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        return await self._get_charger_state() == self.DISCHARGING_STATE

    ######################################################################
    #                  INITIALISATION RELATED FUNCTIONS                  #
    ######################################################################
