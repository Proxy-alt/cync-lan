"""Custom services for Cync LAN. Every service ID is prefixed "experimental_"
as the primary user-facing risk signal - visible in Developer Tools -> Actions
and the automation/script action picker. Two different kinds of "experimental"
here: most commands' outer envelope byte (cmd_) is PREDICTED (via the length
formula in docs/mesh_opcodes.md's "TCP relay envelope research"), not
confirmed against a real packet capture - experimental_set_group_power is
different, its op_code/cmd_code are fully confirmed (it reuses set_power
exactly) and what's unconfirmed is whether device firmware honors a
group-range target address at all (see docs/mesh_opcodes.md's "Groups
control" section).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Optional

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)

from .bridge import LED_COLOR_TO_INT, LED_MODE_TO_INT
from .const import (
    CONF_ENABLE_EXPERIMENTAL,
    DEFAULT_ENABLE_EXPERIMENTAL,
    DOMAIN,
    FADE_OPTIONS,
    MOTION_SENSOR_SENSITIVITY,
    MOTION_SENSOR_TYPE,
    REACH_FLAG_OPTIONS,
    SCHEDULE_MODE_OPTIONS,
    SCHEDULE_SLOT_OPTIONS,
)

if TYPE_CHECKING:
    from homeassistant.components import automation as automation_component

    from cync_lan.devices import CyncDevice

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_INDICATOR_LED = "experimental_set_indicator_led"
SERVICE_SET_MOTION_SENSOR_SETTINGS = "experimental_set_motion_sensor_settings"
SERVICE_EXECUTE_SCENE = "experimental_execute_scene"
SERVICE_SET_GROUP_POWER = "experimental_set_group_power"
SERVICE_SET_MOTION_SENSOR_SCHEDULE = "experimental_set_motion_sensor_schedule"
SERVICE_DELETE_SCENE = "experimental_delete_scene"
SERVICE_DELETE_SCHEDULE = "experimental_delete_schedule"
SERVICE_TOGGLE_AUTOMATION = "experimental_toggle_automation"
SERVICE_SET_GROUP_MEMBERSHIP = "experimental_set_group_membership"
SERVICE_PUSH_AUTOMATION_TO_HARDWARE = "experimental_push_automation_to_hardware"
SERVICE_ADD_DEVICE_TO_SCENE = "experimental_add_device_to_scene"
SERVICE_REMOVE_DEVICE_FROM_SCENE = "experimental_remove_device_from_scene"
SERVICE_SET_MULTICOLOR_GRADIENT_MODE = "experimental_set_multicolor_gradient_mode"
SERVICE_SET_MULTICOLOR_SEGMENT_COUNT = "experimental_set_multicolor_segment_count"
SERVICE_SET_MULTICOLOR_SEGMENTS = "experimental_set_multicolor_segments"
SERVICE_QUERY_MESH_CREDENTIALS = "experimental_query_mesh_credentials"

ATTR_DEVICE_ID = "device_id"
ATTR_MODE = "mode"
ATTR_COLOR = "color"
ATTR_BRIGHTNESS = "brightness"
ATTR_WIFI_DISCONNECT_BLINK = "wifi_disconnect_blink"
ATTR_SENSOR_TYPE = "sensor_type"
ATTR_ENABLED = "enabled"
ATTR_SENSITIVITY = "sensitivity"
ATTR_DELAY_SECONDS = "delay_seconds"
ATTR_DEACTIVATION_SECONDS = "deactivation_seconds"
ATTR_SCENE_ID = "scene_id"
ATTR_SCHEDULE_ID = "schedule_id"
ATTR_GROUP_ID = "group_id"
ATTR_STATE = "state"
ATTR_SLOT = "slot"
ATTR_START_HOUR = "start_hour"
ATTR_START_MINUTE = "start_minute"
ATTR_END_HOUR = "end_hour"
ATTR_END_MINUTE = "end_minute"
ATTR_CCT = "cct"
ATTR_RGB = "rgb"
ATTR_MEMBER = "member"
ATTR_REACH_FLAG = "reach_flag"
ATTR_AUTOMATION_ENTITY_ID = "automation_entity_id"
ATTR_FADE = "fade"
ATTR_COUNT = "count"
ATTR_SEGMENT_1_POSITION = "segment_1_position"
ATTR_SEGMENT_1_RGB = "segment_1_rgb"
ATTR_SEGMENT_2_POSITION = "segment_2_position"
ATTR_SEGMENT_2_RGB = "segment_2_rgb"

# Day-of-week bitmask - AddAutomationHubCommand.java's WriteBuffer field
# (see cync_lan.devices.add_automation's docstring): Sunday=bit0 through
# Saturday=bit6. Keyed by the exact abbreviation strings HA's own "weekday"
# condition config validates against (config_validation.py's `weekdays`,
# built from const.WEEKDAYS), so an automation's raw_config value can be
# looked up here with no translation table of our own to keep in sync.
_WEEKDAY_BIT = {
    "sun": 0x01,
    "mon": 0x02,
    "tue": 0x04,
    "wed": 0x08,
    "thu": 0x10,
    "fri": 0x20,
    "sat": 0x40,
}
_ALL_DAYS_MASK = 0x7F

# Motion-sensor enums live in const.py (MOTION_SENSOR_TYPE/
# MOTION_SENSOR_SENSITIVITY) - shared with config_flow.py's options-flow
# wizard, which sends the same command. Indicator LED's mode/color enums
# live in bridge.py (LED_MODE_TO_INT/LED_COLOR_TO_INT) - shared with
# select.py so a service call and an entity write converge on the same
# values/cache.


def _resolve_device(
    hass: HomeAssistant, device_id: str
) -> tuple[ConfigEntry, "CyncDevice"]:
    """Resolve a HA device-registry device_id to its (ConfigEntry,
    CyncDevice) pair. Raises ServiceValidationError if it doesn't resolve
    to a real, currently-loaded Cync LAN device - a device from another
    integration, an unknown id, or the bridge hub device itself (which has
    no CyncDevice, only individual devices identified by
    f"{entry_id}_{dev_id}" do - see entity.py's build_device_info)."""
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"Unknown device_id: {device_id}")
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        runtime_data = getattr(entry, "runtime_data", None)
        if runtime_data is None:
            continue
        prefix = f"{entry_id}_"
        for identifier_domain, identifier_value in device.identifiers:
            if identifier_domain != DOMAIN or not identifier_value.startswith(prefix):
                continue
            try:
                dev_id = int(identifier_value[len(prefix):])
            except ValueError:
                continue
            node = runtime_data.ncync_server.node_devices.get(dev_id)
            if node is not None:
                return entry, node
    raise ServiceValidationError(
        f"Device {device_id} is not a Cync LAN device (or its entry isn't loaded). "
        "Note: experimental_execute_scene and experimental_set_group_power target "
        "the 'Cync LAN Bridge' device, not an individual device."
    )


def _resolve_bridge_entry(hass: HomeAssistant, device_id: str) -> ConfigEntry:
    """Resolve a HA device-registry device_id to its config entry, requiring
    it to be the "Cync LAN Bridge" hub device (identifiers=(DOMAIN, entry_id),
    no dev_id suffix - see binary_sensor.py's diagnostic sensors) rather than
    an individual device - used by services that have no single CyncDevice
    to target (Scenes are home-wide; group commands target a group's own
    MeshAddress, not a device's)."""
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"Unknown device_id: {device_id}")
    for identifier_domain, identifier_value in device.identifiers:
        if identifier_domain == DOMAIN:
            entry = hass.config_entries.async_get_entry(identifier_value)
            if entry is not None and entry.domain == DOMAIN:
                return entry
    raise ServiceValidationError(
        f"Device {device_id} is not the Cync LAN Bridge device - this service "
        "must target the bridge device, not an individual light/switch/sensor."
    )


async def _handle_set_indicator_led(hass: HomeAssistant, call: ServiceCall) -> None:
    entry, node = _resolve_device(hass, call.data[ATTR_DEVICE_ID])
    # Routed through the same shared cache select.py/number.py/switch.py's
    # 4 indicator-LED entities use (bridge.py's set_indicator_led_field) -
    # not node.set_indicator_led() directly - so a service call and an
    # entity write always converge on identical cached state instead of
    # silently diverging (see bridge.py's IndicatorLedState docstring).
    await entry.runtime_data.bridge.set_indicator_led_field(
        node,
        mode=call.data[ATTR_MODE],
        color=call.data[ATTR_COLOR],
        brightness=call.data[ATTR_BRIGHTNESS],
        wifi_disconnect_blink=call.data.get(ATTR_WIFI_DISCONNECT_BLINK, False),
    )


async def _handle_set_motion_sensor_settings(hass: HomeAssistant, call: ServiceCall) -> None:
    _, node = _resolve_device(hass, call.data[ATTR_DEVICE_ID])
    sensitivity = call.data.get(ATTR_SENSITIVITY)
    await node.set_motion_sensor_settings(
        setting_type=MOTION_SENSOR_TYPE[call.data[ATTR_SENSOR_TYPE]],
        enabled=call.data.get(ATTR_ENABLED),
        sensitivity=MOTION_SENSOR_SENSITIVITY[sensitivity] if sensitivity else None,
        delay_seconds=call.data.get(ATTR_DELAY_SECONDS, 0),
        deactivation_seconds=call.data.get(ATTR_DEACTIVATION_SECONDS, 0),
    )


async def _handle_execute_scene(hass: HomeAssistant, call: ServiceCall) -> None:
    from cync_lan.devices import execute_scene

    _resolve_bridge_entry(hass, call.data[ATTR_DEVICE_ID])
    await execute_scene(call.data[ATTR_SCENE_ID])


async def _handle_set_group_power(hass: HomeAssistant, call: ServiceCall) -> None:
    from cync_lan.devices import set_group_power

    _resolve_bridge_entry(hass, call.data[ATTR_DEVICE_ID])
    await set_group_power(
        call.data[ATTR_GROUP_ID], 1 if call.data[ATTR_STATE] else 0
    )


async def _handle_set_motion_sensor_schedule(hass: HomeAssistant, call: ServiceCall) -> None:
    _, node = _resolve_device(hass, call.data[ATTR_DEVICE_ID])
    rgb = call.data.get(ATTR_RGB)
    await node.set_motion_sensor_schedule(
        slot_id=SCHEDULE_SLOT_OPTIONS[call.data[ATTR_SLOT]],
        mode=SCHEDULE_MODE_OPTIONS[call.data[ATTR_MODE]],
        start_hour=call.data[ATTR_START_HOUR],
        start_minute=call.data[ATTR_START_MINUTE],
        end_hour=call.data[ATTR_END_HOUR],
        end_minute=call.data[ATTR_END_MINUTE],
        brightness=call.data[ATTR_BRIGHTNESS],
        cct=call.data.get(ATTR_CCT),
        rgb=tuple(rgb) if rgb else None,
    )


async def _handle_delete_scene(hass: HomeAssistant, call: ServiceCall) -> None:
    from cync_lan.devices import delete_scene

    _resolve_bridge_entry(hass, call.data[ATTR_DEVICE_ID])
    await delete_scene(call.data[ATTR_SCENE_ID])


async def _handle_delete_schedule(hass: HomeAssistant, call: ServiceCall) -> None:
    from cync_lan.devices import delete_schedule

    _resolve_bridge_entry(hass, call.data[ATTR_DEVICE_ID])
    await delete_schedule(call.data[ATTR_SCHEDULE_ID])


async def _handle_toggle_automation(hass: HomeAssistant, call: ServiceCall) -> None:
    from cync_lan.devices import toggle_automation

    _resolve_bridge_entry(hass, call.data[ATTR_DEVICE_ID])
    await toggle_automation(
        call.data[ATTR_SCHEDULE_ID],
        call.data[ATTR_SCENE_ID],
        call.data[ATTR_ENABLED],
    )


async def _handle_set_group_membership(hass: HomeAssistant, call: ServiceCall) -> None:
    """Unlike experimental_set_group_power, this targets an individual
    device (the one joining/leaving a group), not the bridge - the group
    is data in the payload, not the addressing target. See
    CyncDevice.set_group_membership()'s docstring."""
    _, node = _resolve_device(hass, call.data[ATTR_DEVICE_ID])
    reach_flag = call.data.get(ATTR_REACH_FLAG, "normal")
    await node.set_group_membership(
        call.data[ATTR_GROUP_ID],
        member=call.data[ATTR_MEMBER],
        reach_flag=REACH_FLAG_OPTIONS[reach_flag],
    )


async def _handle_add_device_to_scene(hass: HomeAssistant, call: ServiceCall) -> None:
    """Adds/updates this device's captured color state within an existing
    scene - reachable standalone (unlike push_automation_to_hardware's
    internal use of the same underlying method), so it can be tested
    against a scene you already have (Cync-app-created or otherwise)
    without going through the whole automation-push flow."""
    _, node = _resolve_device(hass, call.data[ATTR_DEVICE_ID])
    rgb = call.data.get(ATTR_RGB)
    await node.add_to_scene(
        call.data[ATTR_SCENE_ID],
        cct=call.data.get(ATTR_CCT),
        rgb=tuple(rgb) if rgb else None,
        fade=FADE_OPTIONS[call.data.get(ATTR_FADE, "no_fade")],
    )


async def _handle_remove_device_from_scene(hass: HomeAssistant, call: ServiceCall) -> None:
    _, node = _resolve_device(hass, call.data[ATTR_DEVICE_ID])
    await node.remove_from_scene(call.data[ATTR_SCENE_ID])


async def _handle_set_multicolor_gradient_mode(hass: HomeAssistant, call: ServiceCall) -> None:
    _, node = _resolve_device(hass, call.data[ATTR_DEVICE_ID])
    await node.set_multicolor_gradient_mode(call.data[ATTR_ENABLED])


async def _handle_set_multicolor_segment_count(hass: HomeAssistant, call: ServiceCall) -> None:
    _, node = _resolve_device(hass, call.data[ATTR_DEVICE_ID])
    await node.set_multicolor_segment_count(call.data[ATTR_COUNT])


async def _handle_set_multicolor_segments(hass: HomeAssistant, call: ServiceCall) -> None:
    """Builds the (position, rgb) tuple list CyncDevice.set_multicolor_segments()
    expects from up to 2 named, independently-optional slots - position and
    color are independently nullable on the wire (see that method's
    docstring), so each slot only needs BOTH omitted to be skipped
    entirely; either alone given is valid (an explicit position with no
    color, or a color with no position)."""
    _, node = _resolve_device(hass, call.data[ATTR_DEVICE_ID])
    segments = []
    for pos_key, rgb_key in (
        (ATTR_SEGMENT_1_POSITION, ATTR_SEGMENT_1_RGB),
        (ATTR_SEGMENT_2_POSITION, ATTR_SEGMENT_2_RGB),
    ):
        position = call.data.get(pos_key)
        rgb = call.data.get(rgb_key)
        if position is None and rgb is None:
            continue
        segments.append((position, tuple(rgb) if rgb else None))
    if not segments:
        raise ServiceValidationError(
            "At least one segment (segment_1_position/segment_1_rgb or "
            "segment_2_position/segment_2_rgb) must be given."
        )
    await node.set_multicolor_segments(segments)


async def _handle_query_mesh_credentials(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    """Ask a connected hub for the BTLE mesh name and password (op_code
    0x8A, confirmed via QueryHubMeshNameAndPasswordCommand.java).

    These are the two values cync_lan.ble_provision's key_encrypt()/
    generate_sk() derive their AES key from, so they are what a user needs
    to provision a new device onto their EXISTING mesh with the
    cync-lan-ble-provision CLI - which cannot obtain them itself, having no
    server of its own.

    Returned as a service response rather than logged: the password is the
    mesh's shared secret, and HA's own log is a much wider audience than
    the person who deliberately invoked this action.
    """
    from cync_lan.devices import query_hub_mesh_credentials

    _resolve_bridge_entry(hass, call.data[ATTR_DEVICE_ID])
    result: Optional[tuple[str, str]] = await query_hub_mesh_credentials()
    if result is None:
        raise HomeAssistantError(
            "No connected Cync device answered the mesh-credentials query within "
            "the timeout. This command's response channel is experimental (see "
            "docs/mesh_opcodes.md); it may not ride the TCP relay this "
            "integration intercepts on your hardware."
        )
    mesh_name, mesh_password = result
    return {"mesh_name": mesh_name, "mesh_password": mesh_password}


def _resolve_cync_light_entity(
    hass: HomeAssistant, entity_id: str
) -> tuple[ConfigEntry, "CyncDevice"]:
    """Resolve a light entity_id to its (ConfigEntry, CyncDevice) pair, for
    validating that a pushed automation's actions target real Cync devices
    only. Deliberately rejects light GROUPS (see light.py's
    CyncLanLightGroup, unique_id f"{entry_id}_group_{group_id}") - a Scene
    entry is captured per individual device's state
    (CyncDevice.add_to_scene()), so a group has no single state of its own
    to hand to create_scene()/add_to_scene(); the caller should target each
    member device directly instead."""
    if not entity_id.startswith("light."):
        raise ServiceValidationError(
            f"{entity_id} is not a light entity - every action in a pushed automation "
            "must target a Cync LAN light."
        )
    registry = er.async_get(hass)
    reg_entry = registry.async_get(entity_id)
    if reg_entry is None or reg_entry.platform != DOMAIN:
        raise ServiceValidationError(
            f"{entity_id} is not a Cync LAN entity - every action in a pushed automation "
            "must target only Cync LAN devices."
        )
    unique_id = reg_entry.unique_id
    for entry in hass.config_entries.async_entries(DOMAIN):
        prefix = f"{entry.entry_id}_"
        if not unique_id.startswith(prefix):
            continue
        suffix = unique_id[len(prefix):]
        if suffix.startswith("group_"):
            raise ServiceValidationError(
                f"{entity_id} is a Cync light group, not an individual device - Scenes "
                "capture each device's own color state, so target each group member "
                "directly instead of the group."
            )
        runtime_data = getattr(entry, "runtime_data", None)
        if runtime_data is None:
            continue
        try:
            dev_id = int(suffix)
        except ValueError:
            continue
        node = runtime_data.ncync_server.node_devices.get(dev_id)
        if node is not None:
            return entry, node
    raise ServiceValidationError(
        f"{entity_id} is a Cync LAN entity but its device/config entry isn't currently loaded."
    )


def _get_automation_entity(
    hass: HomeAssistant, entity_id: str
) -> "automation_component.BaseAutomationEntity":
    from homeassistant.components import automation as automation_component

    component = hass.data.get(automation_component.DATA_COMPONENT)
    entity = component.get_entity(entity_id) if component else None
    if entity is None:
        raise ServiceValidationError(f"{entity_id} is not a currently loaded automation entity.")
    return entity


def _extract_time_trigger(raw_config: dict[str, Any]) -> tuple[int, int, int]:
    """Validate that `raw_config` has exactly one plain time-of-day
    trigger, returning (hour, minute, second). Reads both the pre- and
    post-2024.10 key names (automation/config.py's `_backward_compat_schema`
    renames "trigger"->"triggers" etc, but `raw_config` is captured
    BEFORE that rename runs - see AutomationConfig.raw_config - so an
    automation stored under either key name must be handled)."""
    triggers = raw_config.get("triggers", raw_config.get("trigger"))
    if not isinstance(triggers, list):
        triggers = [triggers] if triggers else []
    if len(triggers) != 1:
        raise ServiceValidationError(
            "This automation must have exactly one trigger to be pushed - found "
            f"{len(triggers)}. A Cync Schedule supports a single time-of-day trigger only."
        )
    trigger = triggers[0]
    platform = trigger.get("trigger", trigger.get("platform"))
    if platform != "time":
        raise ServiceValidationError(
            f"This automation's trigger is '{platform}', not 'time' - only a plain "
            "time-of-day trigger can be pushed to a Cync Schedule."
        )
    extra_keys = set(trigger) - {"trigger", "platform", "at", "id", "alias", "enabled"}
    if extra_keys:
        raise ServiceValidationError(
            f"This automation's time trigger has unsupported option(s) {sorted(extra_keys)} - "
            "only a plain 'at:' time is supported."
        )
    at_value = trigger.get("at")
    if isinstance(at_value, list):
        if len(at_value) != 1:
            raise ServiceValidationError(
                "This automation's time trigger fires at multiple times - a Cync Schedule "
                "supports only a single time-of-day. Split this into separate automations."
            )
        at_value = at_value[0]
    if not isinstance(at_value, str):
        raise ServiceValidationError(
            "This automation's time trigger's 'at' value must be a plain 'HH:MM:SS' time."
        )
    if "{{" in at_value or "{%" in at_value:
        raise ServiceValidationError(
            "This automation's time trigger uses a template for its 'at' value - a Cync "
            "Schedule has no way to track a dynamic time source; use a fixed HH:MM:SS time "
            "instead."
        )
    if "." in at_value:
        # HA's time trigger also accepts an input_datetime/sensor entity_id here
        # (always containing a literal "." - a bare HH:MM[:SS] time never does).
        raise ServiceValidationError(
            f"This automation's time trigger's 'at' value ('{at_value}') references an entity, "
            "not a fixed time - a Cync Schedule has no way to track a dynamic time source; use "
            "a fixed HH:MM:SS time instead."
        )
    try:
        parsed = cv.time(at_value)
    except vol.Invalid as err:
        raise ServiceValidationError(f"Could not parse trigger time '{at_value}': {err}") from err
    return parsed.hour, parsed.minute, parsed.second


def _extract_day_mask(raw_config: dict[str, Any]) -> int:
    """Validate that `raw_config` has zero or one day-of-week condition,
    returning the AddAutomationHubCommand bitmask (all 7 days if no
    condition is present at all)."""
    conditions = raw_config.get("conditions", raw_config.get("condition")) or []
    if not isinstance(conditions, list):
        conditions = [conditions]
    if not conditions:
        return _ALL_DAYS_MASK
    if len(conditions) != 1:
        raise ServiceValidationError(
            "This automation must have zero or one condition to be pushed - found "
            f"{len(conditions)}. A Cync Schedule supports a single day-of-week filter only."
        )
    condition = conditions[0]
    if condition.get("condition") != "time":
        raise ServiceValidationError(
            f"This automation's condition is '{condition.get('condition')}', not 'time' - "
            "only a day-of-week filter (a Time condition with only 'Days' set) can be pushed."
        )
    extra_keys = set(condition) - {"condition", "weekday", "alias", "enabled"}
    if extra_keys:
        raise ServiceValidationError(
            f"This automation's time condition has unsupported option(s) {sorted(extra_keys)} - "
            "only a day-of-week ('weekday') filter is supported, not a before/after time range "
            "(the trigger's own time already covers that)."
        )
    weekday = condition.get("weekday")
    if not weekday:
        raise ServiceValidationError(
            "This automation's time condition has no days selected - remove the condition "
            "entirely to run every day, or select specific days."
        )
    if isinstance(weekday, str):
        weekday = [weekday]
    mask = 0
    for day in weekday:
        bit = _WEEKDAY_BIT.get(day)
        if bit is None:
            raise ServiceValidationError(f"Unrecognized weekday '{day}'.")
        mask |= bit
    return mask


def _extract_scene_actions(
    hass: HomeAssistant, raw_config: dict[str, Any]
) -> list[tuple["CyncDevice", Optional[int], Optional[tuple[int, int, int]]]]:
    """Validate that `raw_config`'s actions are all `light.turn_on` calls
    targeting Cync LAN lights with exactly one color (rgb_color or
    color_temp_kelvin - the only two forms CyncDevice.add_to_scene()'s
    wire format can capture, see its docstring re: no brightness field
    existing at all). Returns a flat list of (CyncDevice, cct, rgb) tuples,
    one per resolved target entity across all actions."""
    actions = raw_config.get("actions", raw_config.get("action")) or []
    if not isinstance(actions, list):
        actions = [actions]
    if not actions:
        raise ServiceValidationError("This automation has no actions to push.")

    results = []
    for action in actions:
        service = action.get("action", action.get("service"))
        if service != "light.turn_on":
            raise ServiceValidationError(
                f"This automation has an action calling '{service}', not 'light.turn_on' - "
                "every action must turn a Cync light on with a color, since that's all a "
                "Cync Scene can capture."
            )
        target = action.get("target") or {}
        entity_ids = target.get("entity_id", action.get("entity_id"))
        if not entity_ids:
            raise ServiceValidationError("A light.turn_on action has no target entity_id.")
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]

        data = {**(action.get("data") or {}), **(action.get("data_template") or {})}
        unsupported = set(data) - {"rgb_color", "color_temp_kelvin"}
        if unsupported:
            raise ServiceValidationError(
                f"A light.turn_on action sets {sorted(unsupported)} - a Cync Scene entry can "
                "only capture a color (rgb_color or color_temp_kelvin), not brightness, "
                "effects, or transitions. Remove these - they would silently not be reflected "
                "on real hardware."
            )
        rgb_color = data.get("rgb_color")
        cct = data.get("color_temp_kelvin")
        if rgb_color is not None and cct is not None:
            raise ServiceValidationError(
                "A light.turn_on action sets both rgb_color and color_temp_kelvin - a Cync "
                "Scene entry can only be one or the other."
            )
        if rgb_color is None and cct is None:
            raise ServiceValidationError(
                "A light.turn_on action has no rgb_color or color_temp_kelvin - a Cync Scene "
                "has no way to capture a bare on/off or brightness-only state."
            )
        rgb = tuple(rgb_color) if rgb_color is not None else None

        for entity_id in entity_ids:
            _, node = _resolve_cync_light_entity(hass, entity_id)
            results.append((node, cct, rgb))
    return results


async def _handle_push_automation_to_hardware(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    await push_automation_to_hardware(hass, call.data[ATTR_AUTOMATION_ENTITY_ID])


async def push_automation_to_hardware(hass: HomeAssistant, entity_id: str) -> None:
    """Orchestrates create_scene() -> add_to_scene() (once per resolved
    action target) -> create_schedule() -> add_automation() against an
    existing HA automation's own config, so the automation keeps working
    as a normal HA automation while ALSO running natively on the Cync hub
    (e.g. if the HA instance/network is offline). See this service's
    strings.json description for the full experimental-transport caveat
    shared with create_scene/create_schedule/add_automation themselves."""
    from cync_lan.devices import add_automation, create_schedule, create_scene

    entity = _get_automation_entity(hass, entity_id)
    raw_config = entity.raw_config
    if not raw_config:
        raise ServiceValidationError(
            f"{entity_id}'s automation config isn't available to read (raw_config is empty) - "
            "this can happen for automations not defined with plain 'trigger'/'action' keys."
        )

    hour, minute, second = _extract_time_trigger(raw_config)
    day_mask = _extract_day_mask(raw_config)
    scene_actions = _extract_scene_actions(hass, raw_config)

    scene_name = str(raw_config.get("alias") or entity.name or entity_id)[:30]

    scene_id = await create_scene(scene_name)
    if scene_id is None:
        raise HomeAssistantError(
            "The Cync hub did not respond to the scene-creation request within the timeout - "
            "nothing was pushed. This command's transport is experimental (see "
            "docs/cync_automations.md); try again, and report persistent failures with your "
            "device model."
        )

    for node, cct, rgb in scene_actions:
        await node.add_to_scene(scene_id, cct=cct, rgb=rgb)

    schedule_id = await create_schedule(scene_id)
    if schedule_id is None:
        raise HomeAssistantError(
            f"Scene {scene_id} was created and populated on the hub, but it did not respond "
            "to the schedule-creation request within the timeout. The scene now exists even "
            "though no schedule/trigger was created - use experimental_delete_scene "
            f"(scene_id={scene_id}) to clean it up before retrying."
        )

    await add_automation(schedule_id, scene_id, day_mask, hour, minute, second)
    _LOGGER.info(
        "Pushed automation %s to Cync hardware as scene_id=%s schedule_id=%s",
        entity_id,
        scene_id,
        schedule_id,
    )


_SERVICE_SCHEMAS = {
    SERVICE_SET_INDICATOR_LED: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_MODE): vol.In(LED_MODE_TO_INT),
            vol.Required(ATTR_COLOR): vol.In(LED_COLOR_TO_INT),
            vol.Required(ATTR_BRIGHTNESS): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
            vol.Optional(ATTR_WIFI_DISCONNECT_BLINK, default=False): cv.boolean,
        }
    ),
    SERVICE_SET_MOTION_SENSOR_SETTINGS: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_SENSOR_TYPE): vol.In(MOTION_SENSOR_TYPE),
            vol.Optional(ATTR_ENABLED): cv.boolean,
            vol.Optional(ATTR_SENSITIVITY): vol.In(MOTION_SENSOR_SENSITIVITY),
            vol.Optional(ATTR_DELAY_SECONDS, default=0): vol.All(
                vol.Coerce(int), vol.Range(min=0)
            ),
            vol.Optional(ATTR_DEACTIVATION_SECONDS, default=0): vol.All(
                vol.Coerce(int), vol.Range(min=0)
            ),
        }
    ),
    SERVICE_EXECUTE_SCENE: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_SCENE_ID): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        }
    ),
    SERVICE_SET_GROUP_POWER: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_GROUP_ID): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
            vol.Required(ATTR_STATE): cv.boolean,
        }
    ),
    SERVICE_SET_MOTION_SENSOR_SCHEDULE: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_SLOT): vol.In(SCHEDULE_SLOT_OPTIONS),
            vol.Required(ATTR_MODE): vol.In(SCHEDULE_MODE_OPTIONS),
            vol.Required(ATTR_START_HOUR): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
            vol.Required(ATTR_START_MINUTE): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
            vol.Required(ATTR_END_HOUR): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
            vol.Required(ATTR_END_MINUTE): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
            vol.Required(ATTR_BRIGHTNESS): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            vol.Optional(ATTR_CCT): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            vol.Optional(ATTR_RGB): vol.All(
                cv.ensure_list,
                [vol.All(vol.Coerce(int), vol.Range(min=0, max=255))],
                vol.Length(min=3, max=3),
            ),
        }
    ),
    SERVICE_DELETE_SCENE: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_SCENE_ID): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
        }
    ),
    SERVICE_DELETE_SCHEDULE: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_SCHEDULE_ID): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
        }
    ),
    SERVICE_TOGGLE_AUTOMATION: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_SCHEDULE_ID): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
            vol.Required(ATTR_SCENE_ID): vol.All(vol.Coerce(int), vol.Range(min=0, max=0xFFFFFFFF)),
            vol.Required(ATTR_ENABLED): cv.boolean,
        }
    ),
    SERVICE_SET_GROUP_MEMBERSHIP: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_GROUP_ID): vol.All(vol.Coerce(int), vol.Range(min=32768, max=65535)),
            vol.Required(ATTR_MEMBER): cv.boolean,
            vol.Optional(ATTR_REACH_FLAG, default="normal"): vol.In(REACH_FLAG_OPTIONS),
        }
    ),
    SERVICE_PUSH_AUTOMATION_TO_HARDWARE: vol.Schema(
        {
            vol.Required(ATTR_AUTOMATION_ENTITY_ID): cv.entity_domain("automation"),
        }
    ),
    SERVICE_ADD_DEVICE_TO_SCENE: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_SCENE_ID): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.Optional(ATTR_CCT): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            vol.Optional(ATTR_RGB): vol.All(
                cv.ensure_list,
                [vol.All(vol.Coerce(int), vol.Range(min=0, max=255))],
                vol.Length(min=3, max=3),
            ),
            vol.Optional(ATTR_FADE, default="no_fade"): vol.In(FADE_OPTIONS),
        }
    ),
    SERVICE_REMOVE_DEVICE_FROM_SCENE: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_SCENE_ID): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        }
    ),
    SERVICE_SET_MULTICOLOR_GRADIENT_MODE: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_ENABLED): cv.boolean,
        }
    ),
    SERVICE_SET_MULTICOLOR_SEGMENT_COUNT: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_COUNT): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        }
    ),
    SERVICE_QUERY_MESH_CREDENTIALS: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
        }
    ),
    SERVICE_SET_MULTICOLOR_SEGMENTS: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Optional(ATTR_SEGMENT_1_POSITION): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=120)
            ),
            vol.Optional(ATTR_SEGMENT_1_RGB): vol.All(
                cv.ensure_list,
                [vol.All(vol.Coerce(int), vol.Range(min=0, max=255))],
                vol.Length(min=3, max=3),
            ),
            vol.Optional(ATTR_SEGMENT_2_POSITION): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=120)
            ),
            vol.Optional(ATTR_SEGMENT_2_RGB): vol.All(
                cv.ensure_list,
                [vol.All(vol.Coerce(int), vol.Range(min=0, max=255))],
                vol.Length(min=3, max=3),
            ),
        }
    ),
}

# Handlers either act (returning None) or return response data - see
# _RESPONSE_SERVICES.
_HANDLERS: dict[
    str, Callable[[HomeAssistant, ServiceCall], Coroutine[Any, Any, ServiceResponse]]
] = {
    SERVICE_SET_INDICATOR_LED: _handle_set_indicator_led,
    SERVICE_SET_MOTION_SENSOR_SETTINGS: _handle_set_motion_sensor_settings,
    SERVICE_EXECUTE_SCENE: _handle_execute_scene,
    SERVICE_SET_GROUP_POWER: _handle_set_group_power,
    SERVICE_SET_MOTION_SENSOR_SCHEDULE: _handle_set_motion_sensor_schedule,
    SERVICE_DELETE_SCENE: _handle_delete_scene,
    SERVICE_DELETE_SCHEDULE: _handle_delete_schedule,
    SERVICE_TOGGLE_AUTOMATION: _handle_toggle_automation,
    SERVICE_SET_GROUP_MEMBERSHIP: _handle_set_group_membership,
    SERVICE_PUSH_AUTOMATION_TO_HARDWARE: _handle_push_automation_to_hardware,
    SERVICE_ADD_DEVICE_TO_SCENE: _handle_add_device_to_scene,
    SERVICE_REMOVE_DEVICE_FROM_SCENE: _handle_remove_device_from_scene,
    SERVICE_SET_MULTICOLOR_GRADIENT_MODE: _handle_set_multicolor_gradient_mode,
    SERVICE_SET_MULTICOLOR_SEGMENT_COUNT: _handle_set_multicolor_segment_count,
    SERVICE_SET_MULTICOLOR_SEGMENTS: _handle_set_multicolor_segments,
    SERVICE_QUERY_MESH_CREDENTIALS: _handle_query_mesh_credentials,
}

# Services that return data to the caller instead of only acting.
_RESPONSE_SERVICES = {
    SERVICE_QUERY_MESH_CREDENTIALS: SupportsResponse.ONLY,
}


def experimental_enabled(hass: HomeAssistant) -> bool:
    """Whether any loaded Cync LAN entry has opted in to experimental
    commands, via the hub's Configure screen."""
    return any(
        entry.options.get(CONF_ENABLE_EXPERIMENTAL, DEFAULT_ENABLE_EXPERIMENTAL)
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
    )


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the experimental services, but only if the user opted in.

    Every one of these sends a mesh command whose cmd_code is PREDICTED
    from the length formula in docs/mesh_opcodes.md rather than confirmed
    against a packet capture, and most have never been exercised against
    real hardware. Registering them unconditionally put 15 entries in
    Developer Tools -> Actions that look as ordinary as any other, so the
    "experimental_" prefix was the only thing standing between a user and
    an unproven mesh write. The prefix stays; the opt-in gate means the
    services do not exist at all until it is switched on.

    Idempotent in both directions - safe to call whenever the option might
    have changed (entry setup, and the options flow), and it removes the
    services again if the user turns the option back off.
    """
    if not experimental_enabled(hass):
        _async_remove_services(hass)
        return

    for service, schema in _SERVICE_SCHEMAS.items():
        if hass.services.has_service(DOMAIN, service):
            continue
        handler = _HANDLERS[service]

        async def _call(
            call: ServiceCall,
            _handler: Callable[
                [HomeAssistant, ServiceCall], Coroutine[Any, Any, ServiceResponse]
            ] = handler,
        ) -> ServiceResponse:
            # Must RETURN, not just await: a SupportsResponse service whose
            # wrapper drops the handler's dict fails with "expected a
            # dictionary, but got NoneType".
            return await _handler(hass, call)

        hass.services.async_register(
            DOMAIN,
            service,
            _call,
            schema=schema,
            supports_response=_RESPONSE_SERVICES.get(
                service, SupportsResponse.NONE
            ),
        )


def _async_remove_services(hass: HomeAssistant) -> None:
    for service in _SERVICE_SCHEMAS:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove all 15 services, but only once no Cync LAN config entry
    remains loaded - checked as "<=1" rather than "==0" because this runs
    from async_unload_entry *before* HA has finished marking the entry
    currently being unloaded as unloaded, so that entry itself would
    otherwise always still show up in async_loaded_entries()."""
    if len(hass.config_entries.async_loaded_entries(DOMAIN)) > 1:
        return
    _async_remove_services(hass)
