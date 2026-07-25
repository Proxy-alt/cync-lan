"""Binary sensor platform for Cync LAN: standalone motion sensors, built-in
occupancy sensors on some light/switch models, and a diagnostic "app mesh
active" entity."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bridge import CyncLanBridge, signal_entity_update
from .const import DEFAULT_DISABLED_ENTITIES, DOMAIN, MANUFACTURER
from .entity import CyncLanEntity

if TYPE_CHECKING:
    from cync_lan.devices import CyncDevice

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data = entry.runtime_data
    bridge = runtime_data.bridge
    entities: list[BinarySensorEntity] = []
    for node in runtime_data.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
        if not node.has_motion_sensor:
            continue
        # standalone-sensor devices (e.g. type 96, 112) have no light/switch
        # entity of their own; motion-capable lights/switches (37/49/56) get
        # this as a secondary entity alongside their light/switch entity.
        is_secondary = node.is_light or node.is_switch
        entities.append(CyncLanMotionSensor(bridge, entry.entry_id, node, is_secondary))

    for node in runtime_data.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
        # Only WiFi devices own a session to be ready or not - a BTLE-mesh
        # device is reached through whichever WiFi device relays it, which
        # sensor.py's relay-source sensor already reports.
        if node.has_wifi:
            entities.append(CyncLanReadyToControlSensor(bridge, entry.entry_id, node))
    entities.append(CyncLanAppMeshActiveSensor(bridge, entry.entry_id))
    entities.append(CyncLanAppWifiActiveSensor(bridge, entry.entry_id))
    async_add_entities(entities)


class CyncLanMotionSensor(CyncLanEntity, BinarySensorEntity):
    def __init__(self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice", is_secondary: bool) -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_motion" if is_secondary else "")
        # entity-translations (gold): resolve the name through strings.json's
        # entity.binary_sensor.motion.name instead of a hardcoded literal, so
        # translators can override it without touching code. Only secondary
        # entities (alongside a light/switch's own primary entity) get a name
        # at all - a standalone sensor's only entity uses the device name.
        self._attr_translation_key = "motion" if is_secondary else None
        device_class = BinarySensorDeviceClass.MOTION
        if node.metadata and node.metadata.capabilities:
            device_class = BinarySensorDeviceClass(
                node.metadata.capabilities.sensor_device_class
            )
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
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = "app_mesh_active" not in DEFAULT_DISABLED_ENTITIES

    def __init__(self, bridge: CyncLanBridge, entry_id: str) -> None:
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
        return self._bridge.get_app_mesh_active()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_entity_update(f"{self._entry_id}_app_mesh_active"),
                self.async_write_ha_state,
            )
        )


class CyncLanAppWifiActiveSensor(BinarySensorEntity):
    """entity-category + entity-disabled-by-default (gold): diagnostic-only,
    off unless the user opts in. Distinct from CyncLanAppMeshActiveSensor:
    this fires whenever the Cync app's TCP login handshake reaches this
    server (i.e. the app is running and on the same WiFi/LAN), regardless of
    whether the phone is physically near a BTLE-mesh device."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "app_wifi_active"
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = "app_wifi_active" not in DEFAULT_DISABLED_ENTITIES

    def __init__(self, bridge: CyncLanBridge, entry_id: str) -> None:
        self._bridge = bridge
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_app_wifi_active"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            manufacturer=MANUFACTURER,
            name="Cync LAN Bridge",
        )

    @property
    def is_on(self) -> bool:
        return self._bridge.get_app_wifi_active()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_entity_update(f"{self._entry_id}_app_wifi_active"),
                self.async_write_ha_state,
            )
        )


class CyncLanReadyToControlSensor(CyncLanEntity, BinarySensorEntity):
    """Whether this device's own TCP session will actually accept commands.

    A device can be connected and still refuse to act: `ready_to_control` is
    only set once the session completes its handshake, and commands sent
    before that are silently dropped. That is a genuinely distinct state from
    "offline", and until now nothing surfaced it - a user seeing an
    unresponsive-but-available device had no way to tell the two apart.

    Overrides `available` for the same reason the MITM switch does: this
    describes the device's own session, so it is meaningful precisely when
    the device looks reachable but is not behaving.
    """

    _attr_translation_key = "ready_to_control"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice") -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_ready_to_control")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        session = self._node.tcp_session
        if session is None:
            return False
        return bool(getattr(session, "ready_to_control", False))
