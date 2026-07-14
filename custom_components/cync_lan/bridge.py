"""Adapter that lets the unmodified cync_lan protocol/server code push state
directly into Home Assistant entities instead of publishing MQTT discovery
JSON.

devices.py (in the upstream `cync_lan` package this integration depends on)
calls a fixed set of methods on a module-level `g.mqtt_client` singleton to
report state changes and command acknowledgements - see MQTTClient in
cync_lan/mqtt_client.py for the original MQTT-based implementation. This
class implements the exact same call surface (verified against every
`g.mqtt_client.<method>` call site in devices.py) but instead of touching
MQTT, it records the latest EntityState per (dev_id, sub_id) and notifies
entities via Home Assistant's dispatcher, which they listen to in
async_added_to_hass.

One addition needed a small, matching change in devices.py itself:
report_unknown_device_id() (see below) is called from
_process_73_mesh_info's existing "Received MeshInfo for unknown device ID"
branch, with a no-op mirror added to MQTTClient for the MQTT-based add-on
so that branch doesn't need an isinstance check to stay safe for both.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN

if TYPE_CHECKING:
    from cync_lan.devices import CyncDevice
    from cync_lan.structs import EntityState

_LOGGER = logging.getLogger(__name__)


def signal_entity_update(unique_id: str) -> str:
    """Dispatcher signal name for a single entity's state changing."""
    return f"{DOMAIN}_update_{unique_id}"


def signal_device_online(dev_id: int) -> str:
    """Dispatcher signal name for a device's availability changing."""
    return f"{DOMAIN}_online_{dev_id}"


@dataclass
class BridgeEntityState:
    """Latest known state for one (dev_id, sub_id) entity, plus availability."""

    entity_state: Optional["EntityState"] = None
    online: bool = True
    motion: Optional[bool] = None
    app_mesh_active: bool = False


class CyncLanBridge:
    """Drop-in replacement for cync_lan.mqtt_client.MqttClient.

    Assigned to the protocol layer's `g.mqtt_client` global so devices.py's
    existing state-reporting/command-ack calls land here instead of MQTT.
    """

    # dynamic-devices (gold): require the same unknown dev_id to show up
    # this many times before treating it as a real new device rather than
    # transient packet noise (a single corrupted/misaligned MeshInfo entry).
    UNKNOWN_DEVICE_SEEN_THRESHOLD = 3
    # ...and never trigger more than once per cooldown window, regardless of
    # how many different unknown dev_ids show up - this calls the real Cync
    # cloud API, and the whole point of this integration is not hammering
    # that cloud dependency at runtime.
    UNKNOWN_DEVICE_TRIGGER_COOLDOWN_SECONDS = 900.0  # 15 minutes

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        on_unknown_device: Optional[Callable[[], None]] = None,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        # topic is only read by devices.py to build a lookup key for
        # get_startup_topic_state_sync (MITM-mode restore-on-reconnect) - the
        # MQTT-retained-message concept it's built for doesn't apply here, so
        # this is just a stable stand-in string, not a real MQTT topic.
        self.topic = f"{DOMAIN}/{entry_id}"
        # key: (dev_id, sub_id)
        self._states: dict[tuple[int, int], BridgeEntityState] = {}
        # raw topic->payload sink for the handful of informational
        # server.py/exporter.py status pings (e.g. "tcp_server/running") that
        # call publish() directly rather than a typed method below - surfaced
        # via diagnostics.py rather than turned into entities, since they're
        # bridge-internal debug status, not device state.
        self.raw_topics: dict[str, bytes] = {}
        # main.py assigns its own asyncio.Task handle here after calling
        # start() - a plain attribute, not something this class manages.
        self.start_task = None
        # Called (fire-and-forget, via hass.async_create_task) once a truly
        # new device is confirmed - set by __init__.py to trigger an
        # immediate cloud re-export instead of waiting for the periodic
        # refresh timer (which may be hours away, or disabled).
        self._on_unknown_device = on_unknown_device
        self._unknown_device_sightings: dict[int, int] = {}
        self._last_unknown_device_trigger: float = 0.0

    def _get(self, dev_id: int, sub_id: int = 0) -> BridgeEntityState:
        key = (dev_id, sub_id)
        if key not in self._states:
            self._states[key] = BridgeEntityState()
        return self._states[key]

    def get_state(self, dev_id: int, sub_id: int = 0) -> Optional["EntityState"]:
        return self._get(dev_id, sub_id).entity_state

    def get_motion(self, dev_id: int) -> Optional[bool]:
        return self._get(dev_id).motion

    def is_online(self, dev_id: int) -> bool:
        return self._get(dev_id).online

    def _set_online(self, dev_id: int, value: bool) -> None:
        """log-when-unavailable (silver): log the transition, not every call -
        every one of this method's callers fires on every status packet, so
        logging unconditionally would be noise, not a useful diagnostic
        trail."""
        bucket = self._get(dev_id)
        if bucket.online != value:
            _LOGGER.info(
                "Cync device %s is now %s", dev_id, "online" if value else "offline"
            )
        bucket.online = value

    # --- primary state-reporting surface, called from devices.py ---

    async def parse_entity_state(
        self, entity_state: "EntityState", from_pkt: Optional[str] = None
    ) -> bool:
        bucket = self._get(entity_state.dev_id, entity_state.sub_id)
        bucket.entity_state = entity_state
        self._set_online(entity_state.dev_id, True)
        unique_id = f"{self.entry_id}_{entity_state.dev_id}"
        if entity_state.sub_id:
            unique_id = f"{unique_id}_{entity_state.sub_id}"
        async_dispatcher_send(self.hass, signal_entity_update(unique_id))
        return True

    async def publish_motion_state(
        self, node: "CyncDevice", motion: bool, from_pkt: Optional[str] = None
    ) -> bool:
        bucket = self._get(node.id)
        bucket.motion = motion
        self._set_online(node.id, True)
        unique_id = f"{self.entry_id}_{node.id}"
        async_dispatcher_send(self.hass, signal_entity_update(unique_id))
        return True

    async def pub_online(self, dev_id: int, value: bool) -> None:
        """Must stay async: devices.py wraps this call directly in
        asyncio.create_task(), which requires a coroutine - a plain sync def
        here would raise "a coroutine was expected, got None" at runtime.
        Confirmed against the real MQTTClient.pub_online, which is async for
        the same reason."""
        self._set_online(dev_id, value)
        async_dispatcher_send(self.hass, signal_device_online(dev_id))

    def report_unknown_device_id(self, dev_id: int) -> None:
        """dynamic-devices (gold): called from devices.py's
        _process_73_mesh_info when a MeshInfo entry names a dev_id this
        integration has no CyncDevice for - i.e. real mesh hardware Cync's
        cloud hasn't told us about yet. Unlike the noisier per-status-packet
        "unknown device" path (which fires constantly for group/room
        broadcast pseudo-addresses), MeshInfo entries are individually
        addressed real devices, so this is a much more trustworthy "there's
        a new device" signal.

        Debounced twice over: the same dev_id must be seen
        UNKNOWN_DEVICE_SEEN_THRESHOLD times before being trusted (filters a
        single corrupted/misaligned packet), and even a confirmed new device
        won't trigger more than once per
        UNKNOWN_DEVICE_TRIGGER_COOLDOWN_SECONDS - this ends up calling the
        real Cync cloud API, and hammering it on every packet from a device
        that, for whatever reason, never resolves would defeat this
        integration's whole "don't depend on the cloud at runtime" premise.
        """
        if self._on_unknown_device is None:
            return
        count = self._unknown_device_sightings.get(dev_id, 0) + 1
        self._unknown_device_sightings[dev_id] = count
        if count < self.UNKNOWN_DEVICE_SEEN_THRESHOLD:
            return
        now = time.monotonic()
        if (
            now - self._last_unknown_device_trigger
            < self.UNKNOWN_DEVICE_TRIGGER_COOLDOWN_SECONDS
        ):
            return
        self._last_unknown_device_trigger = now
        _LOGGER.info(
            "Confirmed unknown Cync device ID %s seen %d times, triggering "
            "an immediate cloud re-export",
            dev_id,
            count,
        )
        self.hass.async_create_task(self._call_on_unknown_device())

    async def _call_on_unknown_device(self) -> None:
        try:
            result = self._on_unknown_device()
            if result is not None:
                await result
        except Exception:  # noqa: BLE001 - must not crash the packet-parse loop
            _LOGGER.exception("Error handling confirmed unknown device")

    async def mark_app_mesh_active(self, timeout: float = 60.0) -> None:
        # "Cync App Active" diagnostic entity - disabled by default (see
        # DEFAULT_DISABLED_ENTITIES in const.py), no per-device state to key
        # on, so it's tracked under a synthetic dev_id.
        bucket = self._get(-1)
        bucket.app_mesh_active = True
        async_dispatcher_send(self.hass, signal_entity_update(f"{self.entry_id}_app_mesh_active"))

    # --- command-ack callbacks (bound via functools.partial in devices.py) ---

    async def update_entity_power(self, node: "CyncDevice", state: int, sub_id: int) -> None:
        bucket = self._get(node.id, sub_id)
        if bucket.entity_state is not None:
            bucket.entity_state.power = state
        unique_id = f"{self.entry_id}_{node.id}" + (f"_{sub_id}" if sub_id else "")
        async_dispatcher_send(self.hass, signal_entity_update(unique_id))

    async def update_brightness(self, node: "CyncDevice", bri: int) -> None:
        bucket = self._get(node.id)
        if bucket.entity_state is not None:
            bucket.entity_state.brightness = bri
        async_dispatcher_send(self.hass, signal_entity_update(f"{self.entry_id}_{node.id}"))

    async def update_temperature(self, node: "CyncDevice", temp: int) -> None:
        bucket = self._get(node.id)
        if bucket.entity_state is not None:
            bucket.entity_state.temperature = temp
        async_dispatcher_send(self.hass, signal_entity_update(f"{self.entry_id}_{node.id}"))

    async def update_rgb(self, node: "CyncDevice", rgb: tuple[int, int, int]) -> None:
        bucket = self._get(node.id)
        if bucket.entity_state is not None:
            bucket.entity_state.red, bucket.entity_state.green, bucket.entity_state.blue = rgb
        async_dispatcher_send(self.hass, signal_entity_update(f"{self.entry_id}_{node.id}"))

    async def update_fan_percent(self, node: "CyncDevice", perc: int) -> None:
        bucket = self._get(node.id)
        if bucket.entity_state is not None:
            bucket.entity_state.brightness = perc
        async_dispatcher_send(self.hass, signal_entity_update(f"{self.entry_id}_{node.id}"))

    async def update_fan_speed(self, node: "CyncDevice", speed) -> None:
        await self.update_fan_percent(node, speed.to_perc())

    # --- MITM debug mode - not yet ported, safe no-ops ---
    # Home Assistant has no MQTT-retained-message equivalent for restoring
    # MITM mode across restarts, and the per-device MITM toggle isn't exposed
    # as an entity in this integration yet (CYNC_MITM_ENTITIES was opt-in and
    # off by default upstream too). These are still called unconditionally by
    # devices.py, so they must exist, but they're deliberately inert here.

    async def add_mitm_button(self, node: "CyncDevice") -> None:
        return None

    async def remove_mitm_button(self, node: "CyncDevice") -> None:
        return None

    def get_startup_topic_state_sync(self, topic_str: str, timeout_seconds: float = 3.0):
        return None

    # --- generic surface used by server.py/exporter.py/utils.py ---
    # These callers publish bridge-internal status pings (server running,
    # export progress) directly rather than through a typed method, and
    # main.py drives this class through the same start()/stop() shape it
    # uses for the real MQTT client. There's no broker connection for this
    # bridge to establish, so start()/stop() are no-ops; publish() just
    # records the latest value per topic for diagnostics.py to surface.

    async def publish(self, topic: str, msg_data: bytes, retain: bool = None, qos: int = None) -> bool:
        self.raw_topics[topic] = msg_data
        return True

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None
