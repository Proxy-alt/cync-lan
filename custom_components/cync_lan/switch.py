"""Switch platform for Cync LAN.

Covers binary toggle switches and plugs/outlets. Fan controllers (deviceType
81 etc.) are switches at the protocol level too but get their own richer
entity on the fan platform instead - see fan.py's is_fan_controller filter,
mirrored by the exclusion here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.group.switch import SwitchGroup
from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .bridge import CyncLanBridge
from .const import (
    CONF_ENABLE_EXPERIMENTAL,
    CONF_ENABLE_LIGHT_GROUPS,
    CONF_HIDE_GROUP_MEMBERS,
    DEFAULT_DISABLED_ENTITIES,
    DEFAULT_ENABLE_EXPERIMENTAL,
    DEFAULT_ENABLE_LIGHT_GROUPS,
    DEFAULT_HIDE_GROUP_MEMBERS,
    DOMAIN,
    MANUFACTURER,
)
from .entity import CyncLanEntity, CyncLanIndicatorLedEntity
from .groups import apply_group_member_visibility, wait_for_member_entities

if TYPE_CHECKING:
    from cync_lan.devices import CyncDevice

PARALLEL_UPDATES = 0


# Indicator-LED presentation: one form or the other, never both. All of these
# entities write the same single atomic mesh command, so offering both a light
# and the select/number/switch trio would put two UIs in a race over one piece
# of hardware and let them disagree about its state.
def _indicator_led_is_a_light(entry: ConfigEntry) -> bool:
    from .const import CONF_INDICATOR_LED_AS_LIGHT, DEFAULT_INDICATOR_LED_AS_LIGHT

    return bool(
        entry.options.get(CONF_INDICATOR_LED_AS_LIGHT, DEFAULT_INDICATOR_LED_AS_LIGHT)
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data = entry.runtime_data
    bridge = runtime_data.bridge
    entities: list[SwitchEntity] = []
    switch_dev_ids: list[int] = []
    for node in runtime_data.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
        if node.is_switch and not node.is_fan_controller:
            if node.has_multi_entities and node.entities is not None:
                for sub_id in node.entities:
                    entities.append(CyncLanSwitch(bridge, entry.entry_id, node, sub_id))
            else:
                entities.append(CyncLanSwitch(bridge, entry.entry_id, node))
            switch_dev_ids.append(node.id)
        # Indicator-LED "blink on WiFi disconnect" - a config entity, not
        # gated on is_switch like the device's own primary switch/light
        # entity above (the indicator LED is a whole-device feature, see
        # select.py/number.py for its sibling mode/color/brightness entities).
        if not _indicator_led_is_a_light(entry):
            entities.append(
                CyncLanIndicatorLedWifiBlinkSwitch(bridge, entry.entry_id, node)
            )
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

    async_add_entities(entities)

    # Stashed so switch groups can be (re)applied later - e.g. from the
    # options flow when the user enables/refreshes them - without a full
    # entry reload. See async_add_switch_groups() below.
    entry.runtime_data.switch_add_entities = async_add_entities
    entry.runtime_data.created_switch_group_ids = set()

    if not entry.options.get(CONF_ENABLE_LIGHT_GROUPS, DEFAULT_ENABLE_LIGHT_GROUPS):
        return
    if not entry.runtime_data.groups:
        return

    # See wait_for_member_entities' docstring for why this polls instead of
    # hass.async_block_till_done().
    registry = er.async_get(hass)
    await wait_for_member_entities(
        hass, registry, Platform.SWITCH, entry.entry_id, switch_dev_ids
    )

    await async_add_switch_groups(hass, entry)


async def async_add_switch_groups(
    hass: HomeAssistant,
    entry: ConfigEntry,
    hide_members: bool | None = None,
    use_group_command: bool | None = None,
) -> None:
    """Create and add any switch-group entities that don't exist yet, and
    apply each eligible group's member-visibility state.

    A group only gets its aggregate entity here when *none* of its members
    are light-domain devices - light.py's async_add_light_groups already
    owns pure-light and mixed groups (see CyncLanLightGroup's docstring for
    why a mixed group isn't split across both domains). Classification
    reads node.is_light straight from runtime_data rather than the light
    platform's own registered entities, so this doesn't depend on load
    order between the two platforms.

    Callable both from this platform's own async_setup_entry and directly
    from the options flow, same reload-avoidance and staleness reasoning as
    light.async_add_light_groups - see that function's docstring, including
    for hide_members and use_group_command's None-falls-back-to-entry.options
    default.
    """
    runtime_data = entry.runtime_data
    add_entities = runtime_data.switch_add_entities
    if add_entities is None:
        return
    groups = runtime_data.groups or {}
    if not groups:
        return

    node_devices = runtime_data.ncync_server.node_devices

    def _has_light_member(group: dict[str, Any]) -> bool:
        return any(
            (node := node_devices.get(dev_id)) is not None
            and node.metadata is not None
            and node.metadata.supported
            and node.is_light
            for dev_id in group.get("device_ids", [])
        )

    switch_only_groups = {
        group_id: group
        for group_id, group in groups.items()
        if not _has_light_member(group)
    }
    if not switch_only_groups:
        return

    registry = er.async_get(hass)
    already_created = runtime_data.created_switch_group_ids
    if use_group_command is None:
        use_group_command = entry.options.get(
            CONF_ENABLE_EXPERIMENTAL, DEFAULT_ENABLE_EXPERIMENTAL
        )
    group_entities = []
    for group_id, group in switch_only_groups.items():
        if group_id in already_created:
            continue
        member_entity_ids = []
        for dev_id in group.get("device_ids", []):
            unique_id = f"{entry.entry_id}_{dev_id}"
            entity_id = registry.async_get_entity_id(Platform.SWITCH, DOMAIN, unique_id)
            if entity_id is not None:
                member_entity_ids.append(entity_id)
        if not member_entity_ids:
            # Group has no members that ended up as switch entities here
            # (e.g. devices this account no longer has) - nothing to
            # aggregate.
            continue
        group_entities.append(
            CyncLanSwitchGroup(
                unique_id=f"{entry.entry_id}_group_{group_id}",
                name=group.get("name") or f"Group {group_id}",
                entity_ids=member_entity_ids,
                group_id=group_id,
                use_group_command=use_group_command,
            )
        )
        already_created.add(group_id)
    if group_entities:
        add_entities(group_entities)

    if hide_members is None:
        hide_members = entry.options.get(
            CONF_HIDE_GROUP_MEMBERS, DEFAULT_HIDE_GROUP_MEMBERS
        )
    apply_group_member_visibility(
        registry, Platform.SWITCH, entry.entry_id, switch_only_groups, hide_members
    )


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

    def __init__(
        self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice"
    ) -> None:
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


class CyncLanSwitchGroup(SwitchGroup):
    """A Cync device group ("Garage", etc.) exposed as an aggregate switch
    entity, for groups whose members are all switch-domain devices (wired
    switches/plugs, no light-domain member - see async_add_switch_groups).
    Built entirely on Home Assistant's own built-in group-switch
    implementation, mirroring light.py's CyncLanLightGroup exactly:

    - async_turn_on/async_turn_off forward to every member via the
      standard switch.turn_on/switch.turn_off services - unless the
      experimental option is on, see below.
    - is_on is OR-based across members (SwitchGroup's `mode` parameter,
      left at its default/falsy - `any`, not `all`).

    No _attr_device_info: like CyncLanLightGroup, this is a virtual
    aggregate, not tied to a physical device.

    **Experimental direct-group-command override.** By default, turning
    this on/off issues one command per member device via the fanout above -
    the confirmed-working path. When the experimental option is on, a
    single command addresses the group's own MeshAddress directly instead
    (cync_lan.devices.set_group_power) - unconfirmed against real firmware,
    which is exactly why it's opt-in. This replaces the old standalone
    group-power switch entity, which did the same thing but as a separate,
    bridge-attached control disconnected from the room's actual switch
    entity; folding it in here means there's one group control per room,
    not two disagreeing ones.

    icon-translations (gold): translation_key drives icons.json's
    entity.switch.cync_switch_group block, giving "off" a proper
    mdi:toggle-switch-variant-off instead of the "on" icon at all times -
    same reasoning as CyncLanLightGroup's own icon-translations note. Safe
    to set translation_key here despite _attr_name also being set below
    (via SwitchGroup.__init__, from the `name` constructor argument) -
    Entity._name_internal checks _attr_name first and never falls through
    to translation_key for naming, so this only affects icon resolution,
    not the group's display name (e.g. "Garage").
    """

    _attr_translation_key = "cync_switch_group"

    def __init__(
        self,
        unique_id: str,
        name: str,
        entity_ids: list[str],
        group_id: int,
        use_group_command: bool,
    ) -> None:
        super().__init__(unique_id, name, entity_ids, mode=False)
        self._group_id = group_id
        self._use_group_command = use_group_command

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self._use_group_command:
            from cync_lan.devices import set_group_power

            await set_group_power(self._group_id, 1)
            return
        await super().async_turn_on(**kwargs)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._use_group_command:
            from cync_lan.devices import set_group_power

            await set_group_power(self._group_id, 0)
            return
        await super().async_turn_off(**kwargs)


class CyncLanSwitch(CyncLanEntity, SwitchEntity):
    def __init__(
        self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice", sub_id: int = 0
    ) -> None:
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

    def __init__(
        self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice"
    ) -> None:
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

    def __init__(
        self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice"
    ) -> None:
        super().__init__(
            bridge, entry_id, node, unique_id_suffix="_indicator_led_wifi_blink"
        )

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
        await self._bridge.set_indicator_led_field(
            self._node, wifi_disconnect_blink=True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._bridge.set_indicator_led_field(
            self._node, wifi_disconnect_blink=False
        )


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
