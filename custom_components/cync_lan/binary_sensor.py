"""Binary sensor platform for Cync LAN: standalone motion sensors, built-in
occupancy sensors on some light/switch models, and a diagnostic "app mesh
active" entity."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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

    entities.append(CyncLanReadyToControlSensor(entry.entry_id, runtime_data))
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



class CyncLanReadyToControlSensor(BinarySensorEntity):
    """Whether anything is currently able to carry a command to the mesh.

    Deliberately on the bridge and not on each device, which is where this
    started and where it was wrong. Commands are not sent to a device's own
    connection: broadcast_control_command picks a random sample of the whole
    session pool and each packet names its target, so ANY ready session can
    drive ANY device. Controllability is a property of the pool.

    Per-device it read false for almost every device - in one real log, 43
    devices had identified themselves but only 10 still held their own live
    session, so the rest looked uncontrollable while being perfectly
    controllable through someone else's connection. Whether a given device
    holds its own connection is a different question, and sensor.py's IP
    address / relay source sensors already answer it.
    """

    _attr_has_entity_name = True
    _attr_should_poll = True
    _attr_translation_key = "ready_to_control"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry_id: str, runtime_data: Any) -> None:
        self._runtime_data = runtime_data
        self._attr_unique_id = f"{entry_id}_ready_to_control"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            manufacturer=MANUFACTURER,
            name="Cync LAN Bridge",
        )

    @property
    def is_on(self) -> bool:
        # Polled: sessions come and go in the protocol layer, which has no
        # hook to notify on.
        try:
            pool = self._runtime_data.ncync_server.get_dev_tcp_pool_sync()
        except Exception:  # noqa: BLE001 - a diagnostic must not break setup
            return False
        return any(
            getattr(s, "ready_to_control", False) or getattr(s, "mitm_mode", False)
            for s in pool
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The counts behind the boolean - "0 of 10 ready" and "no sessions at
        all" are very different problems."""
        try:
            pool = list(self._runtime_data.ncync_server.get_dev_tcp_pool_sync())
        except Exception:  # noqa: BLE001
            return {}
        return {
            "sessions": len(pool),
            "ready_sessions": sum(
                1 for s in pool if getattr(s, "ready_to_control", False)
            ),
        }
