from event_bus import EventBus
import log_wrapper
import datetime
from appdaemon.plugins.hass.hassapi import Hass


class HAUIManager:
    """
    Decouples code from UI, specifically the need to know about specific entity_id's.
    Writes
    """

    hass: Hass = None
    event_bus: EventBus = None

    # For indication to the user if/how fast polling is in progress
    poll_update_text: str = ""

    def __init__(self, hass: Hass, event_bus: EventBus):
        self.hass = hass
        self.event_bus = event_bus

        self.__log = log_wrapper.get_class_method_logger(hass.log)

        self._initialize()

        self.__log("Completed __init__")

    def _initialize(self):
        self.event_bus.add_event_listener(
            "update_charger_connection_state", self._update_charger_connection_state
        )

        self.event_bus.add_event_listener(
            "update_charger_info", self._update_charger_info
        )
        self.event_bus.add_event_listener("soc_change", self._handle_soc_change)
        self.event_bus.add_event_listener(
            "remaining_range_change", self._handle_remaining_range_change
        )
        self.event_bus.add_event_listener(
            "evse_polled", self._update_poll_indicator_in_ui
        )
        self.event_bus.add_event_listener(
            "charger_state_change", self._handle_charger_state_change
        )
        self.event_bus.add_event_listener(
            "charge_power_change", self._handle_charge_power_change
        )

        self.__log("Completed initialize")

    async def _update_charger_connection_state(self, is_alive: bool):
        keep_alive = {"set_at": datetime.datetime.now()}
        # self.__log(f"Dictionary keep_alive: {keep_alive}.")
        msg = "Successfully connected" if is_alive else "Connection error"
        await self.__update_ha_entity(
            entity_id="sensor.charger_connection_status",
            new_value=msg,
            attributes=keep_alive,
        )

    async def _update_charger_info(self, charger_info: str):
        await self.__update_ha_entity(
            entity_id="sensor.charger_info", new_value=charger_info
        )

    async def _handle_charge_power_change(self, new_power: int):
        """Handle changes in the actual charge power."""
        await self.__update_ha_entity(
            entity_id="sensor.charger_real_charging_power", new_value=new_power
        )

    async def _handle_soc_change(self, new_soc: int, old_soc: int):
        """Handle changes in the car's state of charge (SoC)."""
        await self.__update_ha_entity(
            entity_id="sensor.car_state_of_charge", new_value=new_soc
        )

    async def _handle_remaining_range_change(self, remaining_range: int):
        """Handle changes in the car's remaining range."""
        self.__log(f"handling remaining range: {remaining_range}.")
        await self.__update_ha_entity(
            entity_id="sensor.car_remaining_range", new_value=remaining_range
        )

    async def _update_poll_indicator_in_ui(self, stop: bool = False):
        # Toggles the char in the UI to indicate polling activity,
        # as the "last_changed" attribute also changes, an "age" can be shown based on this as well.
        self.poll_update_text = "↺" if self.poll_update_text != "↺" else "↻"
        if stop:
            self.poll_update_text = ""
        await self.__update_ha_entity(
            entity_id="sensor.poll_refresh_indicator", new_value=self.poll_update_text
        )

    async def _handle_charger_state_change(
        self, new_charger_state: int, new_charger_state_str: str
    ):
        """Handle changes in the charger state."""
        await self.__update_ha_entity(
            entity_id="sensor.charger_state_int", new_value=new_charger_state
        )
        await self.__update_ha_entity(
            entity_id="sensor.charger_state_text", new_value=new_charger_state_str
        )

    async def __update_ha_entity(
        self,
        entity_id: str,
        new_value=None,
        attributes: dict = None,
    ):
        """
        Generic function for updating the state of an entity in Home Assistant
        If it does not exist, create it.
        If it has attributes, keep them (if not overwrite with empty)

        Args:
            entity_id (str):
              Full entity_id including type, e.g. sensor.charger_state
            new_value (any, optional):
              The value the entity should be written with. Defaults to None, can be "unavailable" or
              "unknown" (treated as unavailable).
            attributes (dict, optional):
              The dict the attributes should be written with. Defaults to None.
        """
        if attributes is None:
            attributes = {}

        new_attributes = {}
        if self.hass.entity_exists(entity_id):
            entity_state = await self.hass.get_state(entity_id, attribute="all")
            if entity_state is not None:
                # Even though it exists it's state can still be None
                new_attributes = entity_state.get("attributes", {})
            new_attributes.update(attributes)
        else:
            new_attributes = attributes

        if entity_id.startswith("binary_sensor."):
            if new_value in [None, "unavailable", "unknown", ""]:
                availability = "off"
            else:
                availability = "on"
                if new_value in [True, "true", "on", 1]:
                    new_value = "on"
                else:
                    new_value = "off"

            # A work-around, sighhh... but not used currently
            # This should be done by parameter availability=False in the set_state call (not as part
            # of the attributes) but that does not work..
            # So, there is an extra sensor with the same name as the original + _availability that
            # is used in the availability template of the original.
            availability_entity_id = f"{entity_id}_availability"
            await self.hass.set_state(
                availability_entity_id,
                state=availability,
            )
            if availability == "on":
                await self.hass.set_state(
                    entity_id,
                    state=new_value,
                    attributes=new_attributes,
                )
        else:
            if new_value is None:
                # A sensor cannot be set to None, results in HA error.
                new_value = "unavailable"
            await self.hass.set_state(
                entity_id,
                state=new_value,
                attributes=new_attributes,
            )
