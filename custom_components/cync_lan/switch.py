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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import CyncLanEntity

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
        if not node.is_switch or node.is_fan_controller:
            continue
        if node.has_multi_entities:
            for sub_id in node.entities:
                entities.append(CyncLanSwitch(bridge, entry.entry_id, node, sub_id))
        else:
            entities.append(CyncLanSwitch(bridge, entry.entry_id, node))
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
