"""Binary sensor platform for Cync LAN: standalone motion sensors, built-in
occupancy sensors on some light/switch models, and a diagnostic "app mesh
active" entity."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_DISABLED_ENTITIES, DOMAIN, MANUFACTURER
from .entity import CyncLanEntity

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    from cync_lan.structs import GlobalObject

    g = GlobalObject()
    bridge = entry.runtime_data.bridge
    entities: list[BinarySensorEntity] = []
    for node in g.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
        if not node.has_motion_sensor:
            continue
        # standalone-sensor devices (e.g. type 96, 112) have no light/switch
        # entity of their own; motion-capable lights/switches (37/49/56) get
        # this as a secondary entity alongside their light/switch entity.
        is_secondary = node.is_light or node.is_switch
        entities.append(
            CyncLanMotionSensor(bridge, entry.entry_id, node, is_secondary)
        )
    entities.append(CyncLanAppMeshActiveSensor(bridge, entry.entry_id))
    async_add_entities(entities)


class CyncLanMotionSensor(CyncLanEntity, BinarySensorEntity):
    def __init__(self, bridge, entry_id: str, node, is_secondary: bool) -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_motion" if is_secondary else "")
        # entity-translations (gold): resolve the name through strings.json's
        # entity.binary_sensor.motion.name instead of a hardcoded literal, so
        # translators can override it without touching code. Only secondary
        # entities (alongside a light/switch's own primary entity) get a name
        # at all - a standalone sensor's only entity uses the device name.
        self._attr_translation_key = "motion" if is_secondary else None
        device_class = "motion"
        if node.metadata and node.metadata.capabilities:
            device_class = node.metadata.capabilities.sensor_device_class
        self._attr_device_class = device_class

    @property
    def is_on(self) -> bool | None:
        return self._bridge.get_motion(self._node.id)


class CyncLanAppMeshActiveSensor(BinarySensorEntity):
    """entity-category + entity-disabled-by-default (gold): diagnostic-only,
    off unless the user opts in - mirrors the upstream MQTT add-on's
    'Cync App Active' occupancy entity, which was similarly opt-in."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "app_mesh_active"
    _attr_device_class = "occupancy"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = "app_mesh_active" not in DEFAULT_DISABLED_ENTITIES

    def __init__(self, bridge, entry_id: str) -> None:
        self._bridge = bridge
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_app_mesh_active"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            manufacturer=MANUFACTURER,
            name="Cync LAN Bridge",
        )

    @property
    def is_on(self) -> bool:
        from .bridge import BridgeEntityState

        bucket: BridgeEntityState = self._bridge._get(-1)
        return bucket.app_mesh_active

    async def async_added_to_hass(self) -> None:
        from .bridge import signal_entity_update

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_entity_update(f"{self._entry_id}_app_mesh_active"),
                self.async_write_ha_state,
            )
        )
