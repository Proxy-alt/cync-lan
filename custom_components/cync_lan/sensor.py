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

import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .bridge import CyncLanBridge
from .const import (
    CONF_ENABLE_EXPERIMENTAL,
    DEFAULT_ENABLE_EXPERIMENTAL,
    DOMAIN,
    MANUFACTURER,
)
from .entity import CyncLanEntity
from .util import build_device_group_map, group_sensor_schedules_for_device

if TYPE_CHECKING:
    from cync_lan.devices import CyncDevice

PARALLEL_UPDATES = 0

# How often the hub query sensors ask the hub for a fresh value.
#
# These do not use HA's polling. A platform-level SCAN_INTERVAL would slow
# every sensor on the platform, and the rest are cheap local reads, so the
# hub queries drive themselves off a timer instead (see
# _CyncLanHubQuerySensor).
#
# This class of sensor used to be `should_poll = True` with no interval set
# anywhere, which meant HA's 30-second default - despite the docstring
# claiming the interval was "deliberately long". Every one of those polls
# puts a real command on the mesh and blocks for up to 10s waiting for a
# reply that, on this command family, may never come (the transport is
# unconfirmed - see docs/mesh_opcodes.md). On real hardware that produced a
# timeout warning every 30 seconds, around 5,700 log lines a day, plus HA's
# own "taking over 10 seconds" entity warning each time.
#
# Neither firmware version nor hub clock drifts meaningfully in 15 minutes.
HUB_QUERY_SCAN_INTERVAL = timedelta(minutes=15)

_SLOT_LABELS = {
    "morning": "Morning",
    "daytime": "Daytime",
    "evening": "Evening",
    "sleep": "Sleep",
}


def _firmware_capture_dir() -> Optional[str]:
    """Where captured firmware goes, or None if capture is off.

    Read from the environment at call time rather than from
    cync_lan.const, whose module-level constants are fixed at import - which
    for this integration is before configure_environment() has run. Reading
    it here is what lets the option take effect on a config-entry reload
    instead of requiring a full Home Assistant restart.
    """
    return os.environ.get("CYNC_FIRMWARE_CAPTURE_DIR") or None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data = entry.runtime_data
    bridge = runtime_data.bridge
    groups = runtime_data.groups or {}
    device_group_map = build_device_group_map(groups)
    entities: list[SensorEntity] = []
    for node in runtime_data.ncync_server.node_devices.values():
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

    for node in runtime_data.ncync_server.node_devices.values():
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
        entities.append(CyncLanLastSeenSensor(bridge, entry.entry_id, node))
        entities.append(CyncLanDeviceIdSensor(bridge, entry.entry_id, node))

    entities.append(CyncLanConnectedDevicesSensor(entry.entry_id, runtime_data))

    # Only exists when firmware capture is switched on. A release may not
    # appear for months, so an entity that reads "unknown" indefinitely would
    # be clutter for the overwhelming majority who never enable capture.
    if _firmware_capture_dir():
        entities.append(CyncLanLastFirmwareSensor(entry.entry_id))

    # Hub queries. Read-only, but they put a command on the mesh to get an
    # answer, and their reply channel is unconfirmed - so they stay behind
    # the same experimental gate as everything else that does.
    if entry.options.get(CONF_ENABLE_EXPERIMENTAL, DEFAULT_ENABLE_EXPERIMENTAL):
        entities.append(CyncLanHubFirmwareSensor(entry.entry_id))
        entities.append(CyncLanHubClockSensor(entry.entry_id))

    async_add_entities(entities)


class CyncLanMotionScheduleSensor(CyncLanEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        bridge: CyncLanBridge,
        entry_id: str,
        node: "CyncDevice",
        *,
        group_id: int,
        group_name: str,
        slot_name: str,
        slot: dict[str, Any],
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
    def extra_state_attributes(self) -> dict[str, Any]:
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

    def __init__(self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice") -> None:
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

    def __init__(self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice") -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_relay_source")

    @property
    def native_value(self) -> str | None:
        relay = self._node.relay_source
        if relay is None or relay.node is None:
            return None
        return relay.node.name


class CyncLanLastSeenSensor(CyncLanEntity, SensorEntity):
    """When this device was last heard from.

    "It went unavailable" is nearly always followed by "when?", and nothing
    else in the integration records that. A timestamp also distinguishes a
    device that dropped a minute ago from one that has been silent since the
    last restart, which are very different problems.

    Deliberately NOT marked unavailable along with the device: the whole
    point is to still read something once the device stops responding, so it
    overrides `available` to stay on as long as there is a value at all.
    """

    _attr_translation_key = "last_seen"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice") -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_last_seen")

    @property
    def available(self) -> bool:
        return self.native_value is not None

    @property
    def native_value(self) -> Optional[datetime]:
        return self._bridge.get_last_seen(self._node.id)


class CyncLanDeviceIdSensor(CyncLanEntity, SensorEntity):
    """This device's numeric Cync mesh ID.

    Static, so it earns its place only as a support aid - it is the ID that
    appears in debug logs and in every raw experimental_* action, and there
    is otherwise no way to see it from the UI. Available even when the device
    is offline, since it is a property of the config rather than the device.
    """

    _attr_translation_key = "device_id"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice") -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_device_id")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> int:
        return self._node.id


class CyncLanConnectedDevicesSensor(SensorEntity):
    """How many Cync devices currently hold a TCP connection to the listener.

    On the bridge device, because it describes the listener rather than any
    one device. Zero here is the signature of the DNS redirection not being
    in place - the single most common setup failure - which is otherwise only
    surfaced by a repair issue that waits ten minutes before firing.
    """

    _attr_has_entity_name = True
    _attr_should_poll = True
    _attr_translation_key = "connected_devices"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry_id: str, runtime_data: Any) -> None:
        self._runtime_data = runtime_data
        self._attr_unique_id = f"{entry_id}_connected_devices"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            manufacturer=MANUFACTURER,
            name="Cync LAN Bridge",
        )

    @property
    def native_value(self) -> Optional[int]:
        # Polled rather than pushed: connections are opened and closed by the
        # protocol layer, which has no hook to notify on.
        try:
            return len(self._runtime_data.ncync_server.tcp_connections)
        except Exception:  # noqa: BLE001 - a diagnostic must not break setup
            return None


class CyncLanLastFirmwareSensor(SensorEntity):
    """The most recent firmware release the cloud has offered for this account.

    Exists because the wait is the hard part. GE publishes rarely, so this can
    sit at "None" for months - and then one lands, and it is the most valuable
    single artefact this project can get hold of: a real image to inspect for
    whether it is signed or encrypted, and whether an ESPHome/LibreTiny path is
    conceivable at all. Nobody is going to notice a file appearing in a
    directory. An entity that changes state can drive an automation.

    Reports the target version, so the state changes exactly once per release.
    Everything else - where the image was written, its size, whether it matched
    the MD5 the cloud advertised - is in the attributes.

    **Nothing here installs anything.** The sensor reads what the capture
    watcher recorded; the capture path has no route to a device (see
    `cync_lan.cloud_api.capture_firmware`).
    """

    _attr_has_entity_name = True
    _attr_should_poll = True
    _attr_translation_key = "last_firmware_released"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:package-down"

    def __init__(self, entry_id: str) -> None:
        self._attr_unique_id = f"{entry_id}_last_firmware_released"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            manufacturer=MANUFACTURER,
            name="Cync LAN Bridge",
        )

    @property
    def _capture(self) -> Optional[dict]:
        try:
            from cync_lan.cloud_api import CyncCloudAPI

            return CyncCloudAPI().last_firmware_capture
        except Exception:  # noqa: BLE001 - a diagnostic must not break setup
            return None

    @property
    def native_value(self) -> Optional[str]:
        capture = self._capture
        if not capture:
            return None
        return str(capture.get("target_version") or "")[:255] or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        capture = self._capture
        if not capture:
            return {"captured": False}
        return {
            "captured": True,
            "captured_at": capture.get("captured_at"),
            "path": capture.get("path"),
            "product_id": capture.get("product_id"),
            "from_version": capture.get("from_version"),
            "bytes": capture.get("bytes_written"),
            "md5": capture.get("md5"),
            # False here is worth looking at rather than worth hiding: an image
            # that does not match what the cloud advertised is itself a finding.
            "md5_matches": capture.get("md5_matches"),
            "size_matches": capture.get("size_matches"),
            "source_url": capture.get("url"),
        }


class _CyncLanHubQuerySensor(SensorEntity):
    """A bridge sensor whose value comes from asking the hub.

    Polled, not pushed: these are request/response commands with no
    unsolicited updates. The interval is deliberately long - each poll puts a
    real command on the mesh, and none of this data changes quickly.

    A query that times out leaves the previous value in place rather than
    blanking the entity: the reply channel is unconfirmed, so an occasional
    miss is expected and should not look like the hub vanished.

    Self-timed on HUB_QUERY_SCAN_INTERVAL rather than polled by HA. Polling
    is per-platform, and the other sensors here are cheap local reads that
    should stay responsive; only these put a command on the mesh and wait on
    a reply, so only these need the long interval.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry_id: str, unique_id_suffix: str) -> None:
        self._attr_unique_id = f"{entry_id}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            manufacturer=MANUFACTURER,
            name="Cync LAN Bridge",
        )

    async def async_added_to_hass(self) -> None:
        """Start the query timer, and take one reading now.

        The first reading is deliberately not awaited here: these queries
        block for up to their timeout, and setup should not wait on a reply
        channel that may never answer.
        """
        await super().async_added_to_hass()

        @callback
        def _schedule(_now: Any = None) -> None:
            self.hass.async_create_task(self._async_refresh())

        self.async_on_remove(
            async_track_time_interval(
                self.hass, _schedule, HUB_QUERY_SCAN_INTERVAL
            )
        )
        _schedule()

    async def async_update(self) -> None:
        """Issue the query and store the result. Implemented per subclass."""
        raise NotImplementedError

    async def _async_refresh(self) -> None:
        await self.async_update()
        self.async_write_ha_state()


class CyncLanHubFirmwareSensor(_CyncLanHubQuerySensor):
    """The hub's own firmware version, read over the mesh (op_code 0x4B).

    Distinct from a device's `sw_version` in the device registry, which comes
    from the cloud export - this is what the hardware reports right now, so a
    mismatch between the two means the export is stale.
    """

    _attr_translation_key = "hub_firmware"

    def __init__(self, entry_id: str) -> None:
        super().__init__(entry_id, "hub_firmware")
        self._attr_extra_state_attributes: dict[str, Any] = {}

    async def async_update(self) -> None:
        from cync_lan.devices import query_hub_info

        info = await query_hub_info()
        if info is None:
            return
        self._attr_native_value = info.get("firmware_version")
        # The setup code is the pairing code printed on the hardware; the MAC
        # is already on the device page, so only surface it as an attribute.
        self._attr_extra_state_attributes = {
            "mac": info.get("mac"),
            "setup_code": info.get("setup_code"),
        }


class CyncLanHubClockSensor(_CyncLanHubQuerySensor):
    """The date and time the hub believes it is (op_code 0x46).

    Native Cync Schedules fire off this clock rather than Home Assistant's,
    so a hub whose time has drifted runs its automations at the wrong moment.
    Nothing else in the integration exposes that.

    Reported naive-as-local: the reply on this path carries no timezone (the
    app's other, BLE-direct parser does, but that layout does not apply here),
    so it is interpreted in Home Assistant's own timezone rather than
    pretending to know better.
    """

    _attr_translation_key = "hub_clock"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry_id: str) -> None:
        super().__init__(entry_id, "hub_clock")

    async def async_update(self) -> None:
        from cync_lan.devices import query_device_time

        reported = await query_device_time()
        if reported is None:
            return
        self._attr_native_value = reported.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
