"""Abstract base for a (uni-directional) EVSE / charger driver.

Fase 2a of the 359 migration introduces the ``chargers/`` package structure. The
base classes provide the shared type hierarchy and make a charger an event
emitter (``AsyncIOEventEmitter``), but deliberately do NOT prescribe 359's method
names or its generic 0-10 state model: dev's charger keeps its own public API and
the raw Wallbox Quasar state numbering. A richer abstract contract can be added
once a second charger (EVtec, Fase 3) shares it.
"""

from abc import ABC

from pyee.asyncio import AsyncIOEventEmitter


class UnidirectionalEVSE(AsyncIOEventEmitter, ABC):
    """Base class for a uni-directional EVSE / charger driver.

    Concrete drivers implement V2G Liberty's charger API (test_charger_connection,
    initialise_charger, complete_init, set_active/set_inactive,
    start_charge_with_power, stop_charging, is_car_connected/is_charging, the SoC
    getters, ...) and route their events through the shared EventBus (not the
    pyee emitter).
    """

    def __init__(self):
        super().__init__()
