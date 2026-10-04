"""Event platform for Cync LAN: a discrete trigger for wire-free switches
(button pressed) and motion sensors (motion detected).

Complements the binary_sensor, which keeps the level ("motion is currently
detected"): an event is the right shape for automations that should fire once
per press or detection, with no state to compare against.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bridge import CyncLanBridge, signal_trigger_event
from .const import WIRE_FREE_SWITCH_TYPES
from .entity import CyncLanEntity

if TYPE_CHECKING:
    from cync_lan.devices import CyncDevice

PARALLEL_UPDATES = 0

EVENT_PRESSED = "pressed"
EVENT_MOTION_DETECTED = "motion_detected"

# The same status report reaches us through every bridge that relays it, within
# about a second of each other (seen on a live install: six relays of one change
# inside 750 ms), so one press must not become several events.
DEBOUNCE_SECONDS = 2.0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data = entry.runtime_data
    bridge = runtime_data.bridge
    entities = [
        CyncLanTriggerEvent(
            bridge,
            entry.entry_id,
            node,
            is_secondary=bool(node.is_light or node.is_switch),
        )
        for node in runtime_data.ncync_server.node_devices.values()
        if node.metadata is not None
        and node.metadata.supported
        and node.has_motion_sensor
    ]
    async_add_entities(entities)


class CyncLanTriggerEvent(CyncLanEntity, EventEntity):
    """One discrete event per press (wire-free switch) or per new detection
    (motion sensor).

    For a wire-free switch every report that sets the flag is a press. For a
    motion sensor only a *new* detection counts: while motion continues the
    flag keeps being reported, and each of those is not a new event."""

    def __init__(
        self,
        bridge: CyncLanBridge,
        entry_id: str,
        node: "CyncDevice",
        is_secondary: bool,
    ) -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_event")
        self._is_button = node.type in WIRE_FREE_SWITCH_TYPES
        if self._is_button:
            self._attr_event_types = [EVENT_PRESSED]
            self._attr_device_class = EventDeviceClass.BUTTON
            self._attr_translation_key = "button"
        else:
            self._attr_event_types = [EVENT_MOTION_DETECTED]
            self._attr_device_class = EventDeviceClass.MOTION
            # A standalone sensor's only entities carry the device name.
            self._attr_translation_key = "motion" if is_secondary else None
        self._last_fired: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_trigger_event(self._entry_id, self._node.id),
                self._handle_trigger,
            )
        )

    @callback
    def _handle_trigger(self, previous: bool | None) -> None:
        if not self._is_button and previous:
            return  # motion was already being reported: not a new detection
        now = time.monotonic()
        if self._last_fired is not None and now - self._last_fired < DEBOUNCE_SECONDS:
            return
        self._last_fired = now
        self._trigger_event(
            EVENT_PRESSED if self._is_button else EVENT_MOTION_DETECTED
        )
        self.async_write_ha_state()
