"""Every experimental command must be reachable from the UI once the user
opts in - and completely absent until then.

The split is deliberate: commands with persistent state become entities,
parameterised one-shots become options-flow wizards. This file checks both
halves, and that the gate holds in both directions.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cync_lan.const import (
    CONF_ENABLE_EXPERIMENTAL,
    DOMAIN,
    FADE_OPTIONS,
    REACH_FLAG_OPTIONS,
    SCHEDULE_MODE_OPTIONS,
    SCHEDULE_SLOT_OPTIONS,
)


def _node(dev_id: int, *, rgb: bool = True, motion: bool = False) -> MagicMock:
    node = MagicMock(id=dev_id)
    node.name = f"Device {dev_id}"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.metadata = MagicMock(supported=True)
    node.metadata.model_string = "Model"
    node.supports_rgb = rgb
    node.has_motion_sensor = motion
    node.is_light = True
    node.is_switch = False
    node.is_fan_controller = False
    node.has_wifi = True
    node.has_multi_entities = False
    node.entities = None
    node.set_multicolor_gradient_mode = AsyncMock()
    node.set_multicolor_segment_count = AsyncMock()
    node.set_multicolor_segments = AsyncMock()
    node.set_group_membership = AsyncMock()
    node.set_motion_sensor_schedule = AsyncMock()
    node.add_to_scene = AsyncMock()
    node.remove_from_scene = AsyncMock()
    return node


# ---------------------------------------------------------------------------
# entities: commands with state
# ---------------------------------------------------------------------------


def _platform_entry(
    hass, *, experimental: bool, nodes=None, groups=None, enable_light_groups=False
):
    from custom_components.cync_lan.bridge import CyncLanBridge
    from custom_components.cync_lan.const import CONF_ENABLE_LIGHT_GROUPS

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {
        CONF_ENABLE_EXPERIMENTAL: experimental,
        CONF_ENABLE_LIGHT_GROUPS: enable_light_groups,
    }
    entry.runtime_data = SimpleNamespace(
        bridge=CyncLanBridge(hass, "entry1"),
        ncync_server=SimpleNamespace(node_devices=nodes or {}),
        groups=groups or {},
        scenes={},
        schedules={},
    )
    return entry


def _switch_node(dev_id: int) -> MagicMock:
    """A switch-domain node (is_light=False) - unlike _node() above, which
    is a light-domain fixture and therefore never eligible for
    CyncLanSwitchGroup (see async_add_switch_groups' has_light_member
    check)."""
    node = MagicMock(id=dev_id)
    node.name = f"Switch {dev_id}"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.metadata = MagicMock(supported=True)
    node.metadata.model_string = "Model"
    node.is_light = False
    node.is_switch = True
    node.is_fan_controller = False
    node.is_plug = False
    node.has_wifi = True
    node.has_multi_entities = False
    node.entities = None
    node.set_power = AsyncMock()
    return node


@pytest.mark.parametrize("experimental", [False, True])
async def test_multicolor_entities_follow_the_gate(hass, experimental):
    from custom_components.cync_lan.number import (
        CyncLanMultiColorSegmentCount,
        async_setup_entry as number_setup,
    )
    from custom_components.cync_lan.switch import (
        CyncLanMultiColorGradientSwitch,
        async_setup_entry as switch_setup,
    )

    entry = _platform_entry(hass, experimental=experimental, nodes={5: _node(5)})

    switches: list = []
    numbers: list = []
    await switch_setup(hass, entry, lambda e: switches.extend(e))
    await number_setup(hass, entry, lambda e: numbers.extend(e))

    has_switch = any(isinstance(e, CyncLanMultiColorGradientSwitch) for e in switches)
    has_number = any(isinstance(e, CyncLanMultiColorSegmentCount) for e in numbers)
    assert has_switch is experimental
    assert has_number is experimental


async def test_multicolor_entities_only_for_colour_capable_devices(hass):
    from custom_components.cync_lan.switch import async_setup_entry as switch_setup

    entry = _platform_entry(
        hass, experimental=True, nodes={5: _node(5, rgb=False)}
    )
    added: list = []
    await switch_setup(hass, entry, lambda e: added.extend(e))

    from custom_components.cync_lan.switch import CyncLanMultiColorGradientSwitch

    assert not any(isinstance(e, CyncLanMultiColorGradientSwitch) for e in added)


async def test_switch_group_sends_the_group_command_when_experimental(hass):
    """Replaces the old standalone group-power switch: with the
    experimental option on, CyncLanSwitchGroup addresses the group's own
    MeshAddress directly on turn_on/turn_off instead of fanning out to
    member switches."""
    from custom_components.cync_lan.switch import CyncLanSwitchGroup

    group = CyncLanSwitchGroup(
        "entry1_group_32770",
        "Kitchen",
        ["switch.a"],
        group_id=32770,
        use_group_command=True,
    )
    group.hass = hass

    with patch("cync_lan.devices.set_group_power", new=AsyncMock()) as mock:
        await group.async_turn_on()
    mock.assert_awaited_once_with(32770, 1)

    with patch("cync_lan.devices.set_group_power", new=AsyncMock()) as mock:
        await group.async_turn_off()
    mock.assert_awaited_once_with(32770, 0)


async def test_switch_group_fans_out_when_not_experimental(hass):
    """Without the experimental option, CyncLanSwitchGroup must behave like
    a plain HA switch group - fanning out to members - and never touch
    set_group_power."""
    from custom_components.cync_lan.switch import CyncLanSwitchGroup

    group = CyncLanSwitchGroup(
        "entry1_group_32770",
        "Kitchen",
        ["switch.a"],
        group_id=32770,
        use_group_command=False,
    )
    group.hass = MagicMock()
    group.hass.services.async_call = AsyncMock()

    with patch("cync_lan.devices.set_group_power", new=AsyncMock()) as mock:
        await group.async_turn_on()

    mock.assert_not_called()
    group.hass.services.async_call.assert_awaited_once()


async def test_multicolor_gradient_switch_sends_and_assumes_state(hass):
    from custom_components.cync_lan.bridge import CyncLanBridge
    from custom_components.cync_lan.switch import CyncLanMultiColorGradientSwitch

    node = _node(5)
    switch = CyncLanMultiColorGradientSwitch(CyncLanBridge(hass, "e1"), "e1", node)
    switch.hass = hass
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_on()

    node.set_multicolor_gradient_mode.assert_awaited_once_with(True)
    assert switch.is_on is True


async def test_segment_count_number_sends_and_assumes_state(hass):
    from custom_components.cync_lan.bridge import CyncLanBridge
    from custom_components.cync_lan.number import CyncLanMultiColorSegmentCount

    node = _node(5)
    number = CyncLanMultiColorSegmentCount(CyncLanBridge(hass, "e1"), "e1", node)
    number.hass = hass
    number.async_write_ha_state = MagicMock()

    await number.async_set_native_value(6)

    node.set_multicolor_segment_count.assert_awaited_once_with(6)
    assert number.native_value == 6


# ---------------------------------------------------------------------------
# wizards: parameterised one-shots
# ---------------------------------------------------------------------------


def _options_entry(hass, *, experimental: bool, nodes=None, scenes=None, groups=None):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        options={CONF_ENABLE_EXPERIMENTAL: experimental},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        ncync_server=SimpleNamespace(node_devices=nodes or {}),
        scenes=scenes or {},
        groups=groups or {},
        bridge=MagicMock(),
    )
    return entry


async def test_experimental_menu_hidden_until_opted_in(hass):
    entry = _options_entry(hass, experimental=False)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert "experimental_menu" not in result["menu_options"]


async def test_experimental_menu_appears_when_opted_in(hass):
    entry = _options_entry(hass, experimental=True)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert "experimental_menu" in result["menu_options"]


async def test_every_experimental_command_has_a_menu_entry(hass):
    """The point of this work: nothing is reachable only from Developer
    Tools."""
    entry = _options_entry(hass, experimental=True)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "experimental_menu"}
    )

    assert set(result["menu_options"]) == {
        "exp_push_automation",
        "exp_scene_membership",
        "exp_group_membership",
        "exp_motion_schedule",
        "exp_multicolor_segments",
    }


async def test_scene_membership_wizard_adds_a_device(hass):
    node = _node(5)
    entry = _options_entry(
        hass, experimental=True, nodes={5: node}, scenes={3: {"name": "Movie"}}
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "experimental_menu"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "exp_scene_membership"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"device": "5", "scene": "3", "action": "add", "cct": 50, "fade": "no_fade"},
    )

    assert result["type"] is FlowResultType.ABORT
    node.add_to_scene.assert_awaited_once_with(
        3, cct=50, rgb=None, fade=FADE_OPTIONS["no_fade"]
    )


async def test_scene_membership_wizard_removes_a_device(hass):
    node = _node(5)
    entry = _options_entry(
        hass, experimental=True, nodes={5: node}, scenes={3: {"name": "Movie"}}
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "experimental_menu"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "exp_scene_membership"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device": "5", "scene": "3", "action": "remove"}
    )

    node.remove_from_scene.assert_awaited_once_with(3)


async def test_group_membership_wizard_sends_the_command(hass):
    node = _node(5)
    entry = _options_entry(
        hass,
        experimental=True,
        nodes={5: node},
        groups={32770: {"name": "Kitchen", "device_ids": []}},
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "experimental_menu"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "exp_group_membership"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"device": "5", "group": "32770", "member": True, "reach_flag": "normal"},
    )

    node.set_group_membership.assert_awaited_once_with(
        32770, member=True, reach_flag=REACH_FLAG_OPTIONS["normal"]
    )


async def test_motion_schedule_wizard_sends_the_slot(hass):
    node = _node(5, motion=True)
    entry = _options_entry(hass, experimental=True, nodes={5: node})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "experimental_menu"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "exp_motion_schedule"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "device": "5",
            "slot": "morning",
            "mode": "occupancy",
            "start_hour": 7,
            "start_minute": 30,
            "end_hour": 9,
            "end_minute": 0,
            "brightness": 60,
        },
    )

    node.set_motion_sensor_schedule.assert_awaited_once_with(
        slot_id=SCHEDULE_SLOT_OPTIONS["morning"],
        mode=SCHEDULE_MODE_OPTIONS["occupancy"],
        start_hour=7,
        start_minute=30,
        end_hour=9,
        end_minute=0,
        brightness=60,
        cct=None,
        rgb=None,
    )


async def test_multicolor_wizard_rejects_an_empty_submission(hass):
    """Both segments blank means there is nothing to send."""
    entry = _options_entry(hass, experimental=True, nodes={5: _node(5)})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "experimental_menu"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "exp_multicolor_segments"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device": "5"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_segments"}


async def test_wizard_aborts_cleanly_when_the_command_fails(hass):
    """These commands routinely time out on a notification channel that may
    not exist on the user's hardware - that must read as a message, not a
    traceback."""
    from homeassistant.exceptions import HomeAssistantError

    node = _node(5)
    node.remove_from_scene = AsyncMock(side_effect=HomeAssistantError("no reply"))
    entry = _options_entry(
        hass, experimental=True, nodes={5: node}, scenes={3: {"name": "Movie"}}
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "experimental_menu"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "exp_scene_membership"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device": "5", "scene": "3", "action": "remove"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "experimental_failed"


async def test_scene_wizard_aborts_when_the_account_has_no_scenes(hass):
    entry = _options_entry(hass, experimental=True, nodes={5: _node(5)}, scenes={})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "experimental_menu"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "exp_scene_membership"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_scenes_or_devices"


async def test_gradient_switch_restores_its_assumed_state(hass):
    """It is assumed-state, so the last value HA set must survive a restart -
    which means it has to actually inherit RestoreEntity. It did not at
    first, and async_added_to_hass raised AttributeError on startup."""
    from custom_components.cync_lan.bridge import CyncLanBridge
    from custom_components.cync_lan.switch import CyncLanMultiColorGradientSwitch

    switch = CyncLanMultiColorGradientSwitch(CyncLanBridge(hass, "e1"), "e1", _node(5))
    switch.hass = hass
    switch.async_on_remove = MagicMock()

    with patch.object(
        CyncLanMultiColorGradientSwitch,
        "async_get_last_state",
        new=AsyncMock(return_value=SimpleNamespace(state="on")),
    ):
        await switch.async_added_to_hass()

    assert switch.is_on is True


async def test_segment_count_restores_its_assumed_state(hass):
    from homeassistant.components.number import NumberExtraStoredData

    from custom_components.cync_lan.bridge import CyncLanBridge
    from custom_components.cync_lan.number import CyncLanMultiColorSegmentCount

    number = CyncLanMultiColorSegmentCount(CyncLanBridge(hass, "e1"), "e1", _node(5))
    number.hass = hass
    number.async_on_remove = MagicMock()

    with patch.object(
        CyncLanMultiColorSegmentCount,
        "async_get_last_number_data",
        new=AsyncMock(
            return_value=NumberExtraStoredData(
                native_max_value=255,
                native_min_value=0,
                native_step=1,
                native_unit_of_measurement=None,
                native_value=12,
            )
        ),
    ):
        await number.async_added_to_hass()

    assert number.native_value == 12


# ---------------------------------------------------------------------------
# dimmer level-bar LEDs
# ---------------------------------------------------------------------------


def _dimmer(dev_id=5, *, dimmable=True):
    """A dimmer switch, as one can actually exist.

    This used to set `is_dimmable=True, is_light=False`, which no device
    type in the map can be: a dimmable switch has is_light True by the
    carve-out that keeps it on the light platform, and is_dimmable was gated
    to LIGHT-classified types. The entities below were guarded by
    `is_dimmable and not is_light`, so this fixture was the only "device"
    in existence that satisfied them - the test proved the gate worked using
    a device that cannot be bought.

    Both sides are now the one question that was meant, is_dimmer_switch,
    and test_dimmer_fixture_describes_a_real_device_type keeps this honest.
    """
    node = _node(dev_id)
    node.is_dimmer_switch = dimmable
    node.is_dimmable = dimmable
    node.is_light = True
    node.supports_rgb = False
    node.set_dimmer_led_mode = AsyncMock()
    node.set_dimmer_led_brightness = AsyncMock()
    return node


def test_dimmer_fixture_describes_a_real_device_type():
    """The fixture above must match some type in the map, or the tests using
    it prove nothing about any device a user owns."""
    from cync_lan import classify
    from cync_lan.metadata.model_info import device_type_map

    real = [t for t in device_type_map if classify.is_dimmer_switch(t)]
    assert real, "no device type is a dimmer switch; the fixture is fiction"
    for dev_type in real:
        # exactly the shape _dimmer() fabricates
        assert classify.is_dimmer_switch(dev_type) is True
        assert classify.is_dimmable(dev_type) is True
        assert classify.is_light(dev_type) is True


@pytest.mark.parametrize("experimental", [False, True])
async def test_dimmer_led_entities_follow_the_gate(hass, experimental):
    from custom_components.cync_lan.number import (
        CyncLanDimmerLedBrightness,
        async_setup_entry as number_setup,
    )
    from custom_components.cync_lan.select import (
        CyncLanDimmerLedModeSelect,
        async_setup_entry as select_setup,
    )

    entry = _platform_entry(hass, experimental=experimental, nodes={5: _dimmer()})
    selects: list = []
    numbers: list = []
    await select_setup(hass, entry, lambda e: selects.extend(e))
    await number_setup(hass, entry, lambda e: numbers.extend(e))

    assert any(isinstance(e, CyncLanDimmerLedModeSelect) for e in selects) is experimental
    assert any(isinstance(e, CyncLanDimmerLedBrightness) for e in numbers) is experimental


async def test_dimmer_led_entities_skip_non_dimmers(hass):
    """A binary switch has no level bar."""
    from custom_components.cync_lan.select import (
        CyncLanDimmerLedModeSelect,
        async_setup_entry as select_setup,
    )

    entry = _platform_entry(
        hass, experimental=True, nodes={5: _dimmer(dimmable=False)}
    )
    added: list = []
    await select_setup(hass, entry, lambda e: added.extend(e))

    assert not any(isinstance(e, CyncLanDimmerLedModeSelect) for e in added)


async def test_dimmer_led_mode_sends_the_selected_value(hass):
    from custom_components.cync_lan.bridge import CyncLanBridge
    from custom_components.cync_lan.select import CyncLanDimmerLedModeSelect

    node = _dimmer()
    entity = CyncLanDimmerLedModeSelect(CyncLanBridge(hass, "e1"), "e1", node)
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()

    await entity.async_select_option("briefly_display")

    node.set_dimmer_led_mode.assert_awaited_once_with(1)
    assert entity.current_option == "briefly_display"


async def test_dimmer_led_mode_offers_only_the_two_real_values(hass):
    """DimmingLedsIndicatorMode has no "off" - the bar cannot be disabled."""
    from custom_components.cync_lan.bridge import CyncLanBridge
    from custom_components.cync_lan.select import CyncLanDimmerLedModeSelect

    entity = CyncLanDimmerLedModeSelect(CyncLanBridge(hass, "e1"), "e1", _dimmer())

    assert set(entity.options) == {"briefly_display", "always_on"}


async def test_dimmer_led_brightness_sends_and_assumes_state(hass):
    from custom_components.cync_lan.bridge import CyncLanBridge
    from custom_components.cync_lan.number import CyncLanDimmerLedBrightness

    node = _dimmer()
    entity = CyncLanDimmerLedBrightness(CyncLanBridge(hass, "e1"), "e1", node)
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()

    await entity.async_set_native_value(40)

    node.set_dimmer_led_brightness.assert_awaited_once_with(40)
    assert entity.native_value == 40


async def test_switch_group_names_come_from_the_group_directly(hass):
    """Unlike the old bridge-attached group-power switch (which needed
    has_entity_name + translation_key placeholders to avoid every group
    rendering as "Cync LAN Bridge"), CyncLanSwitchGroup is a standalone HA
    GroupEntity that takes its name straight from the constructor - see
    test_switch.py's full-pipeline group tests for per-group naming through
    async_setup_entry/async_add_switch_groups."""
    from custom_components.cync_lan.switch import CyncLanSwitchGroup

    kitchen = CyncLanSwitchGroup(
        "entry1_group_32770", "Kitchen", ["switch.a"], group_id=32770,
        use_group_command=False,
    )
    hallway = CyncLanSwitchGroup(
        "entry1_group_32771", "Hallway", ["switch.b"], group_id=32771,
        use_group_command=False,
    )
    assert {kitchen.name, hallway.name} == {"Kitchen", "Hallway"}


def test_no_entity_declares_a_translated_name_it_cannot_use():
    """Guards the whole class of bug, not the six that hit it.

    Home Assistant only applies `translation_key` naming when
    `has_entity_name` is set. An entity with a translation_key and neither
    that flag nor an explicit `_attr_name` silently falls back to its
    *device* name - so every instance renders identically. That shipped
    twice: group power switches, then all six bridge button types, where a
    Delete button per scene/schedule/automation/group all read "Cync LAN
    Bridge".

    Written as a source scan rather than per-class assertions so a new
    entity added later is covered without anyone remembering to.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "custom_components" / "cync_lan"
    offenders = []
    for path in sorted(root.glob("*.py")):
        classes = {
            m.group(1): (m.group(2), m.group(3))
            for m in re.finditer(
                r"class (\w+)\(([^)]*)\):(.*?)(?=\nclass |\Z)", path.read_text(), re.S
            )
        }

        def names_itself(cls: str, seen: tuple = ()) -> bool:
            if cls in seen:
                return False
            bases, body = classes.get(cls, ("", ""))
            if "_attr_has_entity_name" in body or "_attr_name" in body:
                return True
            if "CyncLanEntity" in bases or "CyncLanIndicatorLed" in bases:
                return True
            return any(
                names_itself(b.strip(), seen + (cls,))
                for b in bases.split(",")
                if b.strip() in classes
            )

        for name, (bases, body) in classes.items():
            if not re.search(r'_attr_translation_key = "', body):
                continue
            inherits = any(
                names_itself(b.strip()) for b in bases.split(",") if b.strip() in classes
            )
            if not (
                "_attr_has_entity_name" in body
                or "_attr_name" in body
                or "CyncLanEntity" in bases
                or "CyncLanIndicatorLed" in bases
                or inherits
            ):
                offenders.append(f"{path.name}::{name}")

    assert not offenders, (
        "these declare a translated name Home Assistant will never apply, so "
        "each falls back to the device name: " + ", ".join(offenders)
    )
