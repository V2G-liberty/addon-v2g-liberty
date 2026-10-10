"""Charger driver factory.

Maps the persisted charger type (``input_text.charger_type``) onto the driver
class. ``v2g_app`` builds the driver at start-up from the stored type;
``v2g_globals`` uses the factory to test a connection for a type the user is
selecting and to swap the running driver when the type changes.
"""

from .evtec_bidipro import EVtecBiDiProClient
from .wallbox_quasar_1 import WallboxQuasar1Client

# Existing installations predate the setting, and the Quasar was the only
# supported charger, so it is the default when nothing is stored.
DEFAULT_CHARGER_TYPE = WallboxQuasar1Client.CHARGER_TYPE

CHARGER_TYPES = {
    WallboxQuasar1Client.CHARGER_TYPE: WallboxQuasar1Client,
    EVtecBiDiProClient.CHARGER_TYPE: EVtecBiDiProClient,
}


def create_evse_client(charger_type: str, hass, event_bus, notifier):
    """Return a new, not yet initialised, driver for ``charger_type``.

    Raises ValueError for an unknown type.
    """
    try:
        driver_class = CHARGER_TYPES[charger_type]
    except KeyError:
        raise ValueError(
            f"Unknown charger_type '{charger_type}', known: {sorted(CHARGER_TYPES)}."
        ) from None
    return driver_class(hass, event_bus=event_bus, notifier=notifier)
