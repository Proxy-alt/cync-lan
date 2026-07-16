"""Switch platform for Cync LAN.

Covers binary toggle switches and plugs/outlets. Fan controllers (deviceType
81 etc.) are switches at the protocol level too but get their own richer
entity on the fan platform instead - see fan.py's is_fan_controller filter,
mirrored by the exclusion here.
"""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import CyncLanEntity, CyncLanIndicatorLedEntity

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    from cync_lan.structs import GlobalObject

    g = GlobalObject()
    bridge = entry.runtime_data.bridge
    entities: list[SwitchEntity] = []
    for node in g.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
        if node.is_switch and not node.is_fan_controller:
            if node.has_multi_entities:
                for sub_id in node.entities:
                    entities.append(CyncLanSwitch(bridge, entry.entry_id, node, sub_id))
            else:
                entities.append(CyncLanSwitch(bridge, entry.entry_id, node))
        # Indicator-LED "blink on WiFi disconnect" - a config entity, not
        # gated on is_switch like the device's own primary switch/light
        # entity above (the indicator LED is a whole-device feature, see
        # select.py/number.py for its sibling mode/color/brightness entities).
        entities.append(CyncLanIndicatorLedWifiBlinkSwitch(bridge, entry.entry_id, node))
    async_add_entities(entities)


class CyncLanSwitch(CyncLanEntity, SwitchEntity):
    def __init__(self, bridge, entry_id: str, node, sub_id: int = 0) -> None:
        super().__init__(bridge, entry_id, node, sub_id=sub_id)
        # entity-device-class (gold): outlet vs generic switch.
        self._attr_device_class = (
            SwitchDeviceClass.OUTLET if node.is_plug else SwitchDeviceClass.SWITCH
        )
        if sub_id and node.entities.get(sub_id) is not None:
            self._attr_name = node.entities[sub_id].name
        else:
            self._attr_name = None

    @property
    def is_on(self) -> bool | None:
        state = self._entity_state()
        return bool(state.power) if state else None

    async def async_turn_on(self, **kwargs) -> None:
        await self._node.set_power(1, sub_id=self._sub_id or None)

    async def async_turn_off(self, **kwargs) -> None:
        await self._node.set_power(0, sub_id=self._sub_id or None)


class CyncLanIndicatorLedWifiBlinkSwitch(CyncLanIndicatorLedEntity, RestoreEntity, SwitchEntity):
    """Blink the indicator LED when the device loses WiFi - byte index 3 of
    the same set_indicator_led() payload as select.py's mode/color and
    number.py's brightness. See those files' module docstrings for the
    shared assumed-state/RestoreEntity rationale."""

    _attr_translation_key = "indicator_led_wifi_disconnect_blink"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True

    def __init__(self, bridge, entry_id: str, node) -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_indicator_led_wifi_blink")

    @property
    def is_on(self) -> bool:
        return self._bridge.get_indicator_led(self._node.id).wifi_disconnect_blink

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        def _parse(state: str) -> bool | None:
            if state == "on":
                return True
            if state == "off":
                return False
            return None

        await self._restore_led_field("wifi_disconnect_blink", _parse)

    async def async_turn_on(self, **kwargs) -> None:
        await self._bridge.set_indicator_led_field(self._node, wifi_disconnect_blink=True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._bridge.set_indicator_led_field(self._node, wifi_disconnect_blink=False)
