"""Sensor platform for Cync LAN: one diagnostic entity per native
motion-sensor schedule slot (morning/daytime/evening/sleep), replacing a
single JSON-blob attribute previously stuffed into the motion binary_sensor
- HA's own sensor docs recommend separate sensor entities over blob
attributes for structured data like this.

Read-only; there's no write-back yet (blocked on an unconfirmed outer
envelope byte for the mesh command that would write it, see
docs/mesh_opcodes.md). See docs/cync_automations.md for the full data model.
"""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import CyncLanEntity
from .util import build_device_group_map, group_sensor_schedules_for_device

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0

_SLOT_LABELS = {
    "morning": "Morning",
    "daytime": "Daytime",
    "evening": "Evening",
    "sleep": "Sleep",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    from cync_lan.structs import GlobalObject

    g = GlobalObject()
    bridge = entry.runtime_data.bridge
    groups = getattr(entry.runtime_data, "groups", None) or {}
    device_group_map = build_device_group_map(groups)
    entities: list[SensorEntity] = []
    for node in g.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
        if not node.has_motion_sensor:
            continue
        schedules = group_sensor_schedules_for_device(groups, device_group_map, node.id)
        # A device can belong to 2+ groups (a subgroup and its parent),
        # each carrying its own independent schedule - rare in practice but
        # real (see docs/cync_automations.md's "isSubgroup" section).
        disambiguate = len(schedules) > 1
        for group in schedules:
            for slot_name, slot in group["sensor_schedules"].items():
                entities.append(
                    CyncLanMotionScheduleSensor(
                        bridge,
                        entry.entry_id,
                        node,
                        group_id=group["group_id"],
                        group_name=group["group_name"],
                        slot_name=slot_name,
                        slot=slot,
                        disambiguate=disambiguate,
                    )
                )

    for node in g.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
        # Connection diagnostics - exactly one of these two per device,
        # gated on the same has_wifi/bt_only split as switch.py's MITM
        # toggle: a device either owns a direct TCP connection (so its own
        # IP is meaningful) or is only ever reachable through another
        # device's BTLE-mesh relay (so which device is relaying it is the
        # meaningful fact instead) - never both, never neither.
        if node.has_wifi:
            entities.append(CyncLanIpAddressSensor(bridge, entry.entry_id, node))
        else:
            entities.append(CyncLanRelaySourceSensor(bridge, entry.entry_id, node))
    async_add_entities(entities)


class CyncLanMotionScheduleSensor(CyncLanEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        bridge,
        entry_id: str,
        node,
        *,
        group_id,
        group_name: str,
        slot_name: str,
        slot: dict,
        disambiguate: bool,
    ) -> None:
        super().__init__(
            bridge, entry_id, node, unique_id_suffix=f"_schedule_{group_id}_{slot_name}"
        )
        self._slot = slot
        self._group_id = group_id
        self._group_name = group_name
        label = _SLOT_LABELS[slot_name]
        if disambiguate:
            self._attr_translation_key = "sensor_schedule_slot_grouped"
            self._attr_translation_placeholders = {"slot": label, "group_name": group_name}
        else:
            self._attr_translation_key = "sensor_schedule_slot"
            self._attr_translation_placeholders = {"slot": label}

    @property
    def native_value(self) -> str:
        if not self._slot.get("enabled"):
            return "Disabled"
        return f"{self._slot.get('start_time')}–{self._slot.get('end_time')}"

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "mode": self._slot.get("mode"),
            "brightness": self._slot.get("brightness"),
            "cct": self._slot.get("cct"),
            "display_name": self._slot.get("display_name"),
            "start_time": self._slot.get("start_time"),
            "end_time": self._slot.get("end_time"),
            "group_id": self._group_id,
            "group_name": self._group_name,
        }


class CyncLanIpAddressSensor(CyncLanEntity, SensorEntity):
    """The LAN IP address of this device's own direct TCP connection to
    the local listener - only created for WiFi-capable devices
    (has_wifi), which always own a direct connection when reachable at
    all (see CyncLanRelaySourceSensor for the BTLE-mesh-only case). None
    while the device has no active connection - reported "Unavailable" by
    virtue of CyncLanEntity.available already reflecting the same
    online/offline tracking, not a separate check here."""

    _attr_translation_key = "diagnostic_ip_address"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, bridge, entry_id: str, node) -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_ip_address")

    @property
    def native_value(self) -> str | None:
        session = self._node.tcp_session
        return session.ip_address if session else None


class CyncLanRelaySourceSensor(CyncLanEntity, SensorEntity):
    """Which WiFi-capable device is currently relaying this BTLE-mesh-only
    device's status over its own TCP connection - the only presence
    signal this kind of device has at all, since it never owns a direct
    connection of its own (see CyncLanIpAddressSensor for that case).
    Reflects whichever device most recently reported a status update
    naming this one (CyncDevice.relay_source, set at every mesh status/
    MeshInfo parse site in devices.py) - can change if the mesh
    reconfigures which WiFi device relays it."""

    _attr_translation_key = "diagnostic_relay_source"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, bridge, entry_id: str, node) -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_relay_source")

    @property
    def native_value(self) -> str | None:
        relay = self._node.relay_source
        if relay is None or relay.node is None:
            return None
        return relay.node.name
