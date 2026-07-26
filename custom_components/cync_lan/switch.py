"""Switch platform for Cync LAN.

Covers binary toggle switches and plugs/outlets. Fan controllers (deviceType
81 etc.) are switches at the protocol level too but get their own richer
entity on the fan platform instead - see fan.py's is_fan_controller filter,
mirrored by the exclusion here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .bridge import CyncLanBridge
from .const import (
    CONF_ENABLE_EXPERIMENTAL,
    DEFAULT_DISABLED_ENTITIES,
    DEFAULT_ENABLE_EXPERIMENTAL,
    DOMAIN,
    MANUFACTURER,
)
from .entity import CyncLanEntity, CyncLanIndicatorLedEntity

if TYPE_CHECKING:
    from cync_lan.devices import CyncDevice

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data = entry.runtime_data
    bridge = runtime_data.bridge
    entities: list[SwitchEntity] = []
    for node in runtime_data.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
        if node.is_switch and not node.is_fan_controller:
            if node.has_multi_entities and node.entities is not None:
                for sub_id in node.entities:
                    entities.append(CyncLanSwitch(bridge, entry.entry_id, node, sub_id))
            else:
                entities.append(CyncLanSwitch(bridge, entry.entry_id, node))
        # Indicator-LED "blink on WiFi disconnect" - a config entity, not
        # gated on is_switch like the device's own primary switch/light
        # entity above (the indicator LED is a whole-device feature, see
        # select.py/number.py for its sibling mode/color/brightness entities).
        entities.append(CyncLanIndicatorLedWifiBlinkSwitch(bridge, entry.entry_id, node))
        # MITM debug mode - only devices capable of their own direct TCP
        # connection can be put into MITM mode at all (see
        # CyncLanMitmModeSwitch's docstring); BTLE-mesh-only devices never
        # have a tcp_session to toggle.
        if node.has_wifi:
            entities.append(CyncLanMitmModeSwitch(bridge, entry.entry_id, node))

    # Schedule enable/disable - home-wide, not tied to any device, attached
    # to the bridge device like scene.py's scene entities. See
    # CyncLanScheduleSwitch's docstring.
    schedules = runtime_data.schedules or {}
    for schedule_id, schedule in schedules.items():
        entities.append(
            CyncLanScheduleSwitch(
                entry.entry_id,
                schedule_id,
                schedule["scene_id"],
                schedule["name"],
                schedule.get("enabled", True),
            )
        )
    # Experimental-only switches. Gated like the experimental_* services -
    # both send commands whose cmd_code is predicted rather than confirmed.
    if entry.options.get(CONF_ENABLE_EXPERIMENTAL, DEFAULT_ENABLE_EXPERIMENTAL):
        for node in runtime_data.ncync_server.node_devices.values():
            if node.metadata is None or not node.metadata.supported:
                continue
            if node.supports_rgb:
                entities.append(
                    CyncLanMultiColorGradientSwitch(bridge, entry.entry_id, node)
                )
        for group_id, group in (runtime_data.groups or {}).items():
            entities.append(
                CyncLanGroupPowerSwitch(
                    entry.entry_id, group_id, group.get("name") or f"Group {group_id}"
                )
            )

    async_add_entities(entities)


class CyncLanMultiColorGradientSwitch(CyncLanEntity, RestoreEntity, SwitchEntity):
    """Gradient mode for a custom MultiColor scheme, replacing
    experimental_set_multicolor_gradient_mode.

    Assumed state: the device never reports this back, so like the
    indicator-LED entities this reflects the last value HA set, restored
    across restarts rather than read from hardware.

    Only one of the three primitives a full custom scheme needs (see
    CyncDevice.set_multicolor_gradient_mode's docstring) - this integration
    does not orchestrate the whole multi-send sequence, so setting this
    alone may not produce a visible change on its own.
    """

    _attr_translation_key = "multicolor_gradient_mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True

    def __init__(self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice") -> None:
        super().__init__(
            bridge, entry_id, node, unique_id_suffix="_multicolor_gradient_mode"
        )
        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in ("on", "off"):
            self._attr_is_on = last_state.state == "on"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, enabled: bool) -> None:
        await self._node.set_multicolor_gradient_mode(enabled)
        self._attr_is_on = enabled
        self.async_write_ha_state()


class CyncLanGroupPowerSwitch(RestoreEntity, SwitchEntity):
    """Turn a whole Cync device group on or off in one mesh command,
    replacing experimental_set_group_power and its numeric group_id.

    Distinct from light.py's CyncLanLightGroup, which is Home Assistant's
    own group helper fanning out one service call per member. This sends a
    single command addressed to the group's own MeshAddress, which is the
    thing that has never been confirmed to work against real firmware - see
    docs/mesh_opcodes.md's "Groups control". Kept as a separate,
    experimental-only entity for exactly that reason: if it silently does
    nothing, the working per-member path is still there.

    Home-wide, so it lives on the bridge device. Assumed state - a group
    has no state of its own to read back.
    """

    _attr_should_poll = False
    _attr_assumed_state = True
    # Required for translation_key naming to apply at all. Without it the
    # "{group_name} power" string is never used, Entity.name stays None, and
    # every one of these falls back to the device name - so a home with six
    # groups showed six switches all called "Cync LAN Bridge". Every other
    # bridge-attached entity either sets this or sets _attr_name directly;
    # this class did neither.
    _attr_has_entity_name = True
    _attr_translation_key = "group_power"

    def __init__(self, entry_id: str, group_id: int, name: str) -> None:
        self._group_id = group_id
        self._attr_unique_id = f"{entry_id}_group_power_{group_id}"
        self._attr_translation_placeholders = {"group_name": name}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            manufacturer=MANUFACTURER,
            name="Cync LAN Bridge",
        )
        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in ("on", "off"):
            self._attr_is_on = last_state.state == "on"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, on: bool) -> None:
        from cync_lan.devices import set_group_power

        await set_group_power(self._group_id, 1 if on else 0)
        self._attr_is_on = on
        self.async_write_ha_state()


class CyncLanSwitch(CyncLanEntity, SwitchEntity):
    def __init__(self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice", sub_id: int = 0) -> None:
        super().__init__(bridge, entry_id, node, sub_id=sub_id)
        # entity-device-class (gold): outlet vs generic switch.
        self._attr_device_class = (
            SwitchDeviceClass.OUTLET if node.is_plug else SwitchDeviceClass.SWITCH
        )
        entity_state = node.entities.get(sub_id) if sub_id and node.entities else None
        self._attr_name = entity_state.name if entity_state is not None else None

    @property
    def is_on(self) -> bool | None:
        state = self._entity_state()
        return bool(state.power) if state else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._node.set_power(1, sub_id=self._sub_id or None)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._node.set_power(0, sub_id=self._sub_id or None)


class CyncLanMitmModeSwitch(CyncLanEntity, SwitchEntity):
    """Debug/reverse-engineering aid: when enabled, this device's TCP
    session is disconnected and forced to reconnect through a real
    connection to the Cync cloud instead of this integration - every byte
    exchanged is proxied and logged (see the underlying package's
    start_mitm()/stop_mitm()), useful for capturing traffic to support a
    new device/feature, but the device can't be controlled locally while
    active. Diagnostic category and disabled-by-default
    (entity-disabled-by-default, gold) - most users never need this, and
    it's easy to forget was left on, at which point the device silently
    stops responding to local commands.

    Only created for WiFi-capable devices (has_wifi) - a BTLE-mesh-only
    device never has a tcp_session of its own to put into MITM mode.
    Reads/writes CyncTCPSession.mitm_mode directly rather than going
    through the underlying package's MQTT-discovery-oriented
    add_mitm_button/remove_mitm_button (a dynamic-entity mechanism this
    integration's static, HA-native entity model has no use for - see
    bridge.py's docstring on those two no-op stubs).
    """

    _attr_translation_key = "mitm_mode"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = "mitm_mode" not in DEFAULT_DISABLED_ENTITIES
    _attr_assumed_state = True

    def __init__(self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice") -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_mitm_mode")

    @property
    def is_on(self) -> bool:
        session = self._node.tcp_session
        return bool(session.mitm_mode) if session else False

    @property
    def available(self) -> bool:
        # Overrides CyncLanEntity.available (bridge.is_online) - MITM mode
        # is a property of this device's OWN tcp_session specifically, not
        # its general mesh online/offline status. A device that's only
        # reachable via BTLE-mesh relay right now (its own direct
        # connection dropped, even if some other device relays its normal
        # status broadcasts) genuinely can't have MITM toggled - reflect
        # that instead of showing a control that would silently no-op.
        return self._node.tcp_session is not None

    async def async_turn_on(self, **kwargs: Any) -> None:
        session = self._node.tcp_session
        if session is None:
            raise HomeAssistantError(
                f"{self._node.name} has no active connection to toggle MITM mode on right now."
            )
        await session.start_mitm()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        session = self._node.tcp_session
        if session is None:
            raise HomeAssistantError(
                f"{self._node.name} has no active connection to toggle MITM mode on right now."
            )
        await session.stop_mitm()
        self.async_write_ha_state()


class CyncLanIndicatorLedWifiBlinkSwitch(CyncLanIndicatorLedEntity, SwitchEntity):
    """Blink the indicator LED when the device loses WiFi - byte index 3 of
    the same set_indicator_led() payload as select.py's mode/color and
    number.py's brightness. See those files' module docstrings for the
    shared assumed-state/RestoreEntity rationale."""

    _attr_translation_key = "indicator_led_wifi_disconnect_blink"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True

    def __init__(self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice") -> None:
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

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._bridge.set_indicator_led_field(self._node, wifi_disconnect_blink=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._bridge.set_indicator_led_field(self._node, wifi_disconnect_blink=False)


class CyncLanScheduleSwitch(RestoreEntity, SwitchEntity):
    """Enable/disable a saved Cync Schedule, replacing the raw
    experimental_toggle_automation service (which requires knowing both a
    numeric schedule_id AND the scene_id it triggers) with a plain on/off
    entity. Home-wide, not tied to any device - attached to the "Cync LAN
    Bridge" device like scene.py's scene entities, same has_entity_name
    reasoning (a schedule's name is its own identity, not a facet of the
    bridge).

    No live readback exists for a schedule's enabled state (same situation
    as the indicator LED entities before real-hardware confirmation) -
    seeded once from the cloud export's parsed "enabled" field at startup,
    then RestoreEntity-backed and updated optimistically on toggle.
    _attr_assumed_state=True signals this to the UI, same convention as
    CyncLanIndicatorLedWifiBlinkSwitch above.

    UNVALIDATED against a real populated export - see cloud_api.py's
    parse_schedules() docstring. toggle_automation() itself is also
    EXPERIMENTAL (predicted cmd_code, unresolved transport question) - see
    docs/cync_automations.md.
    """

    _attr_should_poll = False
    _attr_assumed_state = True

    def __init__(
        self, entry_id: str, schedule_id: int, scene_id: int, name: str, enabled: bool
    ) -> None:
        self._schedule_id = schedule_id
        self._scene_id = scene_id
        self._attr_name = name
        self._attr_unique_id = f"{entry_id}_schedule_{schedule_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            manufacturer=MANUFACTURER,
            name="Cync LAN Bridge",
        )
        self._attr_is_on = enabled

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_is_on = last_state.state == "on"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_enabled(False)

    async def _set_enabled(self, enabled: bool) -> None:
        from cync_lan.devices import toggle_automation

        await toggle_automation(self._schedule_id, self._scene_id, enabled)
        self._attr_is_on = enabled
        self.async_write_ha_state()
