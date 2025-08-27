"""A centralised Event Bus module"""

import time
import inspect
import asyncio
from pyee.asyncio import AsyncIOEventEmitter
from appdaemon.plugins.hass.hassapi import Hass
from .log_wrapper import get_class_method_logger


class EventBus(AsyncIOEventEmitter):
    """
    A centralized Event Bus for loosely coupled communication.

    Publishers (emitters) and subscribers (listeners) communicate
    via this event bus without directly knowing each other.

    ### List of Events:

    #### Charger / car related

    - `charger_communication_state_change`:
        - **Description**: Update charger communication status (is functional communication
          possible). Kept up to date with polling frequency.
        - **Emitted by** modbus_evse_client
        - **Arguments**:
            - `can_communicate` (bool): communication possible or not.

    - `update_charger_info`:
        - **Description**: Update general info about the charger such as name, firmware,
        serial number, etc. Mainly for debugging, usually set at startup.
        - **Emitted by** modbus_evse_client
        - **Arguments**:
            - `charger_info` (str): General charger info.

    - `soc_change`:
        - **Description**: Monitors changes in the car's state of charge (SoC).
          When the SoC value changes, this event is emitted with the new and old values.
        - **Emitted by** modbus_evse_client
        - **Arguments**:
            - `new_soc` (int): The new state of charge value (1–100).
            - `old_soc` (int): The previous state of charge value (1–100).
            Note that both of these can be 'unknown'.
        - **Example**:
          ```python
          def _handle_soc_change(new_soc, old_soc):
              print(f"State of Charge changed from {old_soc}% to {new_soc}%")
          event_bus.add_event_listener("soc_change", _handle_soc_change)
          ```

    - `charge_power_change`:
        - **Description**: Monitors changes in the chargers actual (real) charge power.
        - **Emitted by** modbus_evse_client
        - **Arguments**:
            - `new_power` (int): The new power value (-7400 - 7400) in Watt, can be 'unavailable'.

    - `charger_state_change`:
        - **Description**: Monitors changes in the chargers state (charging, idle, error etc.).
        - **Emitted by** modbus_evse_client
        - **Arguments**:
            - `new_charger_state` (int): The new state of the charger, can 'unavailable'.
            - `old_charger_state` (int): The old (previous) state of the charger, can 'unavailable'.
            - `new_charger_state_str` (str): text version to show in directly, can be 'unavailable'.

    - `evse_polled`:
        - **Description**: Monitors every (modbus) polling action to evse, a "heart-beat" that can
          change in frequency. Mainly aimed at showing in the UI.
        - **Emitted by** modbus_evse_client
        - **Arguments**:
            - `stop` (bool): If True stop the poll indicator, set text to "".

    - `is_car_connected`:
        - **Description**: Monitors if a car is connected to the charger.
        - **Emitted by** modbus_evse_client
        - **Arguments**:
            - `is_car_connected` (bool): connected state.

    #### FlexMeasures related

    - `fm_connection_status`:
        - **Description**: Monitors if V2G Liberty can communicate with FlexMeasures.
        - **Emitted by** fm_client
        - **Arguments**:
            - `state` (str): connected state.

    """

    def __init__(self, hass: Hass):
        super().__init__()
        self.hass = hass
        self.__log = get_class_method_logger(hass.log)
        self.__log("EventBus initialized successfully.")

    def emit_event(self, event, *args, **kwargs):
        """
        Emit (aka fire or publish) an event with associated arguments.
        Warns if no listeners are registered for the event.
        """
        try:
            listeners = self.listeners(event)
            if not listeners:
                self.__log(f"Event '{event}' has no listeners.", level="WARNING")

            for listener in listeners:
                # Sync listener wrapper
                def run_sync_listener(listener=listener):
                    start = time.perf_counter()
                    try:
                        listener(*args, **kwargs)
                    except Exception as e:
                        self.__log(
                            f"Error in listener {listener} for '{event}': {e}",
                            level="WARNING",
                        )
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    if elapsed_ms > 200:
                        self.__log(
                            f"Listener {listener} for '{event}' took {elapsed_ms:.2f} ms"
                        )

                if inspect.iscoroutinefunction(listener):
                    # Async listener wrapper
                    async def run_async_listener(listener=listener):
                        start = time.perf_counter()
                        try:
                            await listener(*args, **kwargs)
                        except Exception as e:
                            self.__log(
                                f"Error in async listener {listener} for '{event}': {e}",
                                level="WARNING",
                            )
                        elapsed_ms = (time.perf_counter() - start) * 1000
                        if elapsed_ms > 200:
                            self.__log(
                                f"Async listener {listener} for '{event}' took {elapsed_ms:.2f} ms"
                            )

                    asyncio.create_task(run_async_listener())
                else:
                    run_sync_listener()

        except Exception as e:
            self.__log(f"Error while emitting event '{event}': {e}", level="WARNING")

    def add_event_listener(self, event, f):
        """
        Add a listener for a specific event (aka subscribe).
        Warns if the listener is already registered.
        """
        self.__log(f"Adding listener for event '{event}': {f}")
        try:
            if f in self.listeners(event):
                self.__log(
                    f"Listener '{f}' is already registered for event '{event}'!",
                    level="WARNING",
                )
                return
            self.on(event, f)
        except Exception as e:
            self.__log(
                f"Error while adding listener to event '{event}': {e}", level="WARNING"
            )

    def remove_event_listener(self, event, f):
        """
        Remove a specific listener for an event (aka un-subscribe).
        Warns if the listener is not found.
        """
        self.__log(f"Removing listener for event '{event}': {f}")
        try:
            if f not in self.listeners(event):
                self.__log(
                    f"Listener '{f}' not found for event '{event}'. Skipping removal.",
                    level="WARNING",
                )
                return
            self.remove_listener(event, f)
        except Exception as e:
            self.__log(
                f"Error while removing listener from event '{event}': {e}",
                level="WARNING",
            )
