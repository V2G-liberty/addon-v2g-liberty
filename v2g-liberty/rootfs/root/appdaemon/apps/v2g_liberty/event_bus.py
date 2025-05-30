from pyee.asyncio import AsyncIOEventEmitter
import log_wrapper
from appdaemon.plugins.hass.hassapi import Hass


class EventBus(AsyncIOEventEmitter):
    """
    A robust, centralized Event Bus for loosely coupled communication.

    Publishers (emitters) and subscribers (listeners) communicate
    via this event bus without directly knowing each other.

    ### List of Events:
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

    - `evse_polled`:
        - **Description**: Monitors every (modbus) polling action to evse, a "heart-beat" that can
          change in frequency. Mainly aimed at showing in the UI.
        - **Emitted by** modbus_evse_client
        - **Arguments**:
            - `stop` (bool): If True stop the poll indicator, set text to "".


    """

    def __init__(self, hass: Hass):
        super().__init__()
        self.hass = hass
        self.__log = log_wrapper.get_class_method_logger(hass.log)
        self.__log("EventBus initialized successfully.")

    def emit_event(self, event, *args, **kwargs):
        """
        Emit (aka fire or publish) an event with associated arguments.
        Warns if no listeners are registered for the event.
        """
        self.__log(f"Emitting event: '{event}' with args: {args}, kwargs: {kwargs}")
        try:
            if not self.listeners(event):
                self.__log(f"Event '{event}' has no listeners.", level="WARNING")
            self.emit(event, *args, **kwargs)
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
