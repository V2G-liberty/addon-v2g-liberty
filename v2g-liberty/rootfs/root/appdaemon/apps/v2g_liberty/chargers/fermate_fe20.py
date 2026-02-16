"""Client to control a Fermate FE20 Electric Vehicle Supply Equipment (EVSE).

The Fermate FE20 is a bidirectional (V2G) charger that uses the SunSpec Modbus
protocol with some custom extensions.

Key characteristics:
- SunSpec DER models for standard registers
- Custom SoC register at address 41104
- Default Modbus TCP port: 8502
"""

from .modbus_types import ModbusConfigEntity, MBR
from .base_sunspec_evse import BaseSunSpecEVSE


class FermateFE20Client(BaseSunSpecEVSE):
    """Client to control a Fermate FE20 EVSE.

    This charger uses the SunSpec protocol with a custom SoC register.
    """

    ################################################################################
    #  Charger-specific constants                                                  #
    ################################################################################

    # Default Modbus TCP port for Fermate FE20
    _DEFAULT_PORT: int = 8502

    # Human-readable charger name
    _CHARGER_NAME: str = "Fermate FE20"

    ################################################################################
    #  Fermate FE20 Custom Registers                                               #
    ################################################################################

    # State of charge of the DER storage (custom Fermate register)
    _MBR_CAR_SOC = MBR(address=41104, data_type="int16", length=1)

    ################################################################################
    #  Modbus Config Entities (MCE) for Fermate-specific registers                 #
    ################################################################################

    _MCE_CAR_SOC = ModbusConfigEntity(
        modbus_register=_MBR_CAR_SOC,
        minimum_value=1,
        maximum_value=100,
        relaxed_min_value=1,
        relaxed_max_value=100,
        current_value=None,
        change_handler="_handle_soc_change",
    )

    ################################################################################
    #  Abstract method implementations                                             #
    ################################################################################

    def _get_soc_mce(self) -> ModbusConfigEntity:
        """Return the Fermate FE20 SoC ModbusConfigEntity.

        The Fermate FE20 provides car SoC at custom register 41104.

        Returns:
            ModbusConfigEntity: The MCE for reading car SoC.
        """
        return self._MCE_CAR_SOC
