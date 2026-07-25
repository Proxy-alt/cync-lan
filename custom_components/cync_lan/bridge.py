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
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import DOMAIN

if TYPE_CHECKING:
    from cync_lan.devices import CyncDevice
    from cync_lan.structs import EntityState, FanSpeed

_LOGGER = logging.getLogger(__name__)


def signal_entity_update(unique_id: str) -> str:
    """Dispatcher signal name for a single entity's state changing."""
    return f"{DOMAIN}_update_{unique_id}"


def signal_device_online(dev_id: int) -> str:
    """Dispatcher signal name for a device's availability changing."""
    return f"{DOMAIN}_online_{dev_id}"


def signal_indicator_led_update(entry_id: str, dev_id: int) -> str:
    """Dispatcher signal shared by all 4 indicator-LED entities for one
    device - they all read the same merged IndicatorLedState, so all 4 must
    re-render whenever any one of them changes, not just the one written to."""
    return f"{DOMAIN}_indicator_led_{entry_id}_{dev_id}"


# CyncDevice.set_indicator_led()'s int enums, confirmed working on real
# hardware this session - see src/cync_lan/devices.py and
# docs/mesh_opcodes.md's "Indicator LED ring" section.
LED_MODE_TO_INT = {"always_on": 0, "always_off": 1, "normal": 2}
LED_COLOR_TO_INT = {"white": 0, "red": 1, "green": 2, "blue": 3}

# Synthetic dev_id for home-wide state that belongs to no real device (the
# two "Cync app active" diagnostic flags). Negative so it can never collide
# with a real Cync device ID.
_APP_ACTIVITY_DEV_ID = -1


@dataclass
class IndicatorLedState:
    """Assumed state for a device's indicator LED - devices never report
    this back over the mesh, so these defaults are reasonable-looking
    placeholders, not confirmed factory defaults. Real values only exist
    once a user sets one of the 4 entities (or a restart restores the last
    HA-known value via RestoreEntity/RestoreNumber)."""

    mode: str = "normal"
    color: str = "white"
    brightness: int = 100
    wifi_disconnect_blink: bool = False


@dataclass
class BridgeEntityState:
    """Latest known state for one (dev_id, sub_id) entity, plus availability."""

    entity_state: Optional["EntityState"] = None
    online: bool = True
    # When this device last gave any evidence of being alive - a status
    # packet, a motion report, or an explicit online push. "Offline since
    # when" is the first thing worth knowing about a device that stopped
    # responding, and nothing else records it.
    last_seen: Optional[datetime] = None
    motion: Optional[bool] = None
    app_mesh_active: bool = False
    app_wifi_active: bool = False
    indicator_led: IndicatorLedState = field(default_factory=IndicatorLedState)


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
        on_unknown_device: Optional[
            Callable[[], Optional[Coroutine[Any, Any, None]]]
        ] = None,
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
        # None, not 0.0, means "never triggered". time.monotonic() is
        # seconds since boot on Linux, so with 0.0 the cooldown check reads
        # `monotonic() - 0.0 < 900` and suppresses the FIRST trigger on any
        # host that booted less than the cooldown ago - a rebooted Pi, an HA
        # OS restart, a fresh container. It only looked fine on a
        # long-running dev machine, where monotonic() is already huge.
        self._last_unknown_device_trigger: Optional[float] = None
        # BridgeEntityState field name -> its pending auto-clear timer.
        self._app_active_expiry_unsubs: dict[str, Callable[[], None]] = {}

    def _get(self, dev_id: int, sub_id: int = 0) -> BridgeEntityState:
        key = (dev_id, sub_id)
        if key not in self._states:
            self._states[key] = BridgeEntityState()
        return self._states[key]

    def _entity_unique_id(self, dev_id: int, sub_id: int) -> str:
        """The unique_id CyncLanEntity built for this (dev_id, sub_id) - a
        falsy sub_id means the device's own primary entity and carries no
        suffix (see entity.py's CyncLanEntity.__init__)."""
        return f"{self.entry_id}_{dev_id}" + (f"_{sub_id}" if sub_id else "")

    def get_state(self, dev_id: int, sub_id: int = 0) -> Optional["EntityState"]:
        return self._get(dev_id, sub_id).entity_state

    def get_motion(self, dev_id: int) -> Optional[bool]:
        return self._get(dev_id).motion

    def get_app_mesh_active(self) -> bool:
        """Whether the Cync app was recently seen in BTLE-mesh proximity.
        Home-wide, so tracked under the synthetic dev_id below rather than
        any real device - see mark_app_mesh_active."""
        return self._get(_APP_ACTIVITY_DEV_ID).app_mesh_active

    def get_app_wifi_active(self) -> bool:
        """Whether the Cync app's TCP login handshake recently reached this
        server - see mark_app_wifi_active."""
        return self._get(_APP_ACTIVITY_DEV_ID).app_wifi_active

    def get_indicator_led(self, dev_id: int) -> IndicatorLedState:
        return self._get(dev_id).indicator_led

    async def set_indicator_led_field(self, node: "CyncDevice", **fields: Any) -> None:
        """Merge the given field(s) into the cached IndicatorLedState and
        send the FULL merged state live - CyncDevice.set_indicator_led()
        sends mode/color/brightness/wifi_disconnect_blink as one atomic
        mesh command, so changing just one HA entity must still resend the
        other 3's last-known values, not just the field that changed."""
        bucket = self._get(node.id)
        for key, value in fields.items():
            setattr(bucket.indicator_led, key, value)
        state = bucket.indicator_led
        await node.set_indicator_led(
            mode=LED_MODE_TO_INT[state.mode],
            color=LED_COLOR_TO_INT[state.color],
            brightness=state.brightness,
            wifi_disconnect_blink=state.wifi_disconnect_blink,
        )
        async_dispatcher_send(self.hass, signal_indicator_led_update(self.entry_id, node.id))

    def seed_indicator_led_field(self, node: "CyncDevice", **fields: Any) -> None:
        """Restore-on-startup path: update the cache and notify sibling
        entities WITHOUT sending a live command. Deliberately a separate,
        non-async method rather than a send_command=True/False flag on
        set_indicator_led_field - makes it structurally impossible for a
        restore call site to accidentally re-issue a live mesh command to
        every device on every HA restart just by getting a default wrong."""
        bucket = self._get(node.id)
        for key, value in fields.items():
            setattr(bucket.indicator_led, key, value)
        async_dispatcher_send(self.hass, signal_indicator_led_update(self.entry_id, node.id))

    def is_online(self, dev_id: int) -> bool:
        return self._get(dev_id).online

    def get_last_seen(self, dev_id: int) -> Optional[datetime]:
        """When this device was last heard from, or None if never."""
        return self._get(dev_id).last_seen

    def _set_online(self, dev_id: int, value: bool) -> None:
        """log-when-unavailable (silver): log the transition, not every call -
        every one of this method's callers fires on every status packet, so
        logging unconditionally would be noise, not a useful diagnostic
        trail."""
        bucket = self._get(dev_id)
        if value:
            # Every caller reaching here with True has just had real inbound
            # evidence from the device, which is exactly what "last seen"
            # should mean - not the last time we sent it something.
            bucket.last_seen = dt_util.utcnow()
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
        async_dispatcher_send(
            self.hass,
            signal_entity_update(
                self._entity_unique_id(entity_state.dev_id, entity_state.sub_id)
            ),
        )
        return True

    async def publish_motion_state(
        self, node: "CyncDevice", motion: bool, from_pkt: Optional[str] = None
    ) -> bool:
        bucket = self._get(node.id)
        bucket.motion = motion
        self._set_online(node.id, True)
        async_dispatcher_send(
            self.hass, signal_entity_update(self._entity_unique_id(node.id, 0))
        )
        return True

    async def pub_online(self, device_id: int, status: bool) -> bool:
        """Must stay async: devices.py wraps this call directly in
        asyncio.create_task(), which requires a coroutine - a plain sync def
        here would raise "a coroutine was expected, got None" at runtime.
        Confirmed against the real MQTTClient.pub_online, which is async for
        the same reason."""
        self._set_online(device_id, status)
        async_dispatcher_send(self.hass, signal_device_online(device_id))
        return True

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
            self._last_unknown_device_trigger is not None
            and now - self._last_unknown_device_trigger
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
        if self._on_unknown_device is None:
            return
        try:
            result = self._on_unknown_device()
            if result is not None:
                await result
        except Exception:  # noqa: BLE001 - must not crash the packet-parse loop
            _LOGGER.exception("Error handling confirmed unknown device")

    def _mark_app_active(self, field: str, timeout: float) -> None:
        """Set one of the two app-activity flags and (re)arm its
        auto-clear timer. Both flags are home-wide with no per-device state
        to key on, so they live on a synthetic dev_id bucket; the timer
        restarts on every burst, so the flag only clears after `timeout`
        seconds of genuine silence.
        """
        bucket = self._get(_APP_ACTIVITY_DEV_ID)
        setattr(bucket, field, True)
        signal = signal_entity_update(f"{self.entry_id}_{field}")
        async_dispatcher_send(self.hass, signal)

        unsub = self._app_active_expiry_unsubs.pop(field, None)
        if unsub is not None:
            unsub()

        @callback
        def _expire(_now: datetime) -> None:
            setattr(bucket, field, False)
            async_dispatcher_send(self.hass, signal)
            self._app_active_expiry_unsubs.pop(field, None)

        self._app_active_expiry_unsubs[field] = async_call_later(
            self.hass, timeout, _expire
        )

    async def mark_app_mesh_active(self, timeout: float = 60.0) -> None:
        # "Cync App Active" diagnostic entity - disabled by default (see
        # DEFAULT_DISABLED_ENTITIES in const.py). Fires on BTLE-mesh-
        # proximity bursts, mirroring
        # cync_lan.mqtt_client.MQTTClient.mark_app_mesh_active.
        self._mark_app_active("app_mesh_active", timeout)

    async def mark_app_wifi_active(self, timeout: float = 60.0) -> None:
        # Distinct from mark_app_mesh_active: this fires whenever the app's
        # TCP login handshake reaches this server at all (packet header
        # 0x10/0x13 - see PacketBuilder.APP_REQUEST_HEADERS), regardless of
        # BTLE proximity to any specific device. The app being on WiFi at
        # all is a broader, more frequent "app is active" signal than
        # actually being near a mesh device.
        self._mark_app_active("app_wifi_active", timeout)

    # --- command-ack callbacks (bound via functools.partial in devices.py) ---

    def _ensure_entity_state(self, node: "CyncDevice", sub_id: int = 0) -> "EntityState":
        """Get (creating if needed) the EntityState these command-ack
        callbacks mutate. Without this, a device whose state was never
        seeded by an unsolicited mesh/MeshInfo broadcast would have its
        command acks silently no-op forever (the old code only mutated an
        *existing* EntityState, never created the first one) - the entity
        would still re-render on every command since the dispatcher signal
        fires regardless, but is_on/brightness/etc. would stay stuck at
        None (HA shows this as separate "Turn On"/"Turn Off" actions
        instead of a toggle reflecting real state). EntityState only
        strictly requires dev_id - every other field defaults - so
        constructing one fresh here is always valid.
        """
        from cync_lan.structs import EntityState

        bucket = self._get(node.id, sub_id)
        if bucket.entity_state is None:
            # node.name is Optional[str] on CyncDevice (None until identity
            # resolves) but EntityState.name requires a str - coerce rather
            # than let a command ack that races ahead of identification
            # crash with a pydantic ValidationError.
            bucket.entity_state = EntityState(
                dev_id=node.id, sub_id=sub_id, name=node.name or ""
            )
        return bucket.entity_state

    async def update_entity_power(
        self, node: "CyncDevice", state: int, sub_id: Optional[int] = None
    ) -> bool:
        _sub_id = sub_id or 0
        self._ensure_entity_state(node, _sub_id).power = state
        async_dispatcher_send(
            self.hass, signal_entity_update(self._entity_unique_id(node.id, _sub_id))
        )
        return True

    async def update_brightness(
        self, node: "CyncDevice", bri: int, sub_id: Optional[int] = None
    ) -> bool:
        _sub_id = sub_id or 0
        self._ensure_entity_state(node, _sub_id).brightness = bri
        async_dispatcher_send(
            self.hass, signal_entity_update(self._entity_unique_id(node.id, _sub_id))
        )
        return True

    async def update_temperature(
        self, node: "CyncDevice", temp: int, sub_id: Optional[int] = None
    ) -> bool:
        _sub_id = sub_id or 0
        self._ensure_entity_state(node, _sub_id).temperature = temp
        async_dispatcher_send(
            self.hass, signal_entity_update(self._entity_unique_id(node.id, _sub_id))
        )
        return True

    async def update_rgb(
        self,
        node: "CyncDevice",
        rgb: tuple[int, int, int],
        sub_id: Optional[int] = None,
    ) -> bool:
        _sub_id = sub_id or 0
        state = self._ensure_entity_state(node, _sub_id)
        state.red, state.green, state.blue = rgb
        async_dispatcher_send(
            self.hass, signal_entity_update(self._entity_unique_id(node.id, _sub_id))
        )
        return True

    async def update_fan_percent(self, node: "CyncDevice", perc: int) -> bool:
        self._ensure_entity_state(node).brightness = perc
        async_dispatcher_send(
            self.hass, signal_entity_update(self._entity_unique_id(node.id, 0))
        )
        return True

    async def update_fan_speed(self, node: "CyncDevice", speed: "FanSpeed") -> bool:
        return await self.update_fan_percent(node, speed.to_perc())

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

    def get_startup_topic_state_sync(
        self, topic_str: str, timeout_seconds: float = 3.0
    ) -> Optional[str]:
        return None

    # --- generic surface used by server.py/exporter.py/utils.py ---
    # These callers publish bridge-internal status pings (server running,
    # export progress) directly rather than through a typed method, and
    # main.py drives this class through the same start()/stop() shape it
    # uses for the real MQTT client. There's no broker connection for this
    # bridge to establish, so start()/stop() are no-ops; publish() just
    # records the latest value per topic for diagnostics.py to surface.

    async def publish(
        self,
        topic: str,
        msg_data: bytes,
        retain: Optional[bool] = None,
        qos: Optional[int] = None,
    ) -> bool:
        self.raw_topics[topic] = msg_data
        return True

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None
