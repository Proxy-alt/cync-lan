"""Tests for the light platform."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.const import DOMAIN
from custom_components.cync_lan.light import (
    CyncLanLight,
    CyncLanLightGroup,
    async_add_light_groups,
    async_setup_entry,
)


def _fake_node(**overrides):
    node = MagicMock()
    node.id = 5
    node.name = "Test Light"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.metadata = MagicMock(supported=True, characteristics=None)
    node.metadata.model_string = "Some Model"
    node.is_light = True
    node.supports_temperature = False
    node.supports_rgb = False
    node.set_power = AsyncMock()
    node.set_brightness = AsyncMock()
    node.set_temperature = AsyncMock()
    node.set_rgb = AsyncMock()
    node.set_lightshow = AsyncMock()
    node.set_light_effect = AsyncMock()
    node.set_fine_brightness = AsyncMock()
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


async def test_setup_entry_only_includes_lights(hass):
    g = SimpleNamespace()
    light = _fake_node()
    not_light = _fake_node(is_light=False)
    unsupported = _fake_node(metadata=None)
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {1: light, 2: not_light, 3: unsupported}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = g.ncync_server

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 1
    assert added[0]._node is light


def test_brightness_only_mode_when_no_temp_or_rgb():
    node = _fake_node()
    bridge = MagicMock()
    entity = CyncLanLight(bridge, "entry1", node)
    assert entity.supported_color_modes == {"brightness"}


def test_color_temp_mode_when_supported():
    node = _fake_node(supports_temperature=True)
    bridge = MagicMock()
    entity = CyncLanLight(bridge, "entry1", node)
    assert "color_temp" in entity.supported_color_modes


def test_rgb_mode_exposes_effect_list():
    node = _fake_node(supports_rgb=True)
    bridge = MagicMock()
    entity = CyncLanLight(bridge, "entry1", node)
    assert "rgb" in entity.supported_color_modes
    assert entity.effect_list


def test_effect_list_includes_new_modes():
    """The effect list must cover all 5 light-run-modes (Static/LightShow/
    MusicShow/Reveal/MultiColor), not just the original LightShow-only
    presets - see LIGHT_RUN_MODE_EFFECTS."""
    node = _fake_node(supports_rgb=True)
    bridge = MagicMock()
    entity = CyncLanLight(bridge, "entry1", node)
    assert "rainbow" in entity.effect_list  # existing LightShow preset
    assert "static" in entity.effect_list
    assert "music_midnight" in entity.effect_list
    assert "reveal" in entity.effect_list
    assert "multicolor" in entity.effect_list


def test_kelvin_range_pulled_from_characteristics():
    node = _fake_node(supports_temperature=True)
    node.metadata.characteristics = MagicMock(min_kelvin=2000, max_kelvin=6500)
    bridge = MagicMock()
    entity = CyncLanLight(bridge, "entry1", node)
    assert entity.min_color_temp_kelvin == 2000
    assert entity.max_color_temp_kelvin == 6500


def test_no_characteristics_leaves_kelvin_range_at_ha_default():
    node = _fake_node(supports_temperature=True)
    node.metadata.characteristics = None
    bridge = MagicMock()
    entity = CyncLanLight(bridge, "entry1", node)
    # Falls back to LightEntity's own class-level defaults - just confirm
    # constructing it didn't blow up trying to read a None characteristics.
    assert entity.min_color_temp_kelvin is not None


def test_brightness_is_none_without_state(hass):
    from custom_components.cync_lan.bridge import CyncLanBridge

    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanLight(bridge, "entry1", node)
    assert entity.brightness is None


async def test_is_on_and_brightness_scale_from_bridge(hass):
    from cync_lan.structs import EntityState

    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanLight(bridge, "entry1", node)

    assert entity.is_on is None
    await bridge.parse_entity_state(
        EntityState(name="x", dev_id=5, power=1, brightness=50)
    )
    assert entity.is_on is True
    # cync brightness is 0-100, HA brightness is 0-255
    assert entity.brightness == round(50 * 255 / 100)


async def test_turn_on_with_brightness_converts_scale():
    node = _fake_node()
    bridge = MagicMock()
    entity = CyncLanLight(bridge, "entry1", node)

    await entity.async_turn_on(brightness=128)
    node.set_brightness.assert_awaited_once()
    args = node.set_brightness.call_args.args
    assert args[0] == round(128 * 100 / 255)


async def test_turn_on_no_kwargs_just_powers_on():
    node = _fake_node()
    bridge = MagicMock()
    entity = CyncLanLight(bridge, "entry1", node)

    await entity.async_turn_on()
    node.set_power.assert_awaited_with(1)


async def test_turn_on_with_transition_and_brightness():
    """EXPERIMENTAL: transition= (fade time) combined with brightness=
    routes through set_fine_brightness, not the regular set_brightness."""
    node = _fake_node()
    bridge = MagicMock()
    entity = CyncLanLight(bridge, "entry1", node)

    await entity.async_turn_on(brightness=128, transition=2.5)
    node.set_fine_brightness.assert_awaited_once_with(round(128 * 100 / 255), 2500)
    node.set_brightness.assert_not_awaited()


async def test_turn_on_with_transition_only_falls_back_to_current_brightness(hass):
    """transition= with no explicit brightness= falls back to the entity's
    current brightness (or 100 if there isn't one yet)."""
    from cync_lan.structs import EntityState

    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanLight(bridge, "entry1", node)

    # No state yet -> falls back to 100.
    await entity.async_turn_on(transition=1.0)
    node.set_fine_brightness.assert_awaited_with(100, 1000)

    # With a known current brightness -> falls back to that instead.
    await bridge.parse_entity_state(
        EntityState(name="x", dev_id=5, power=1, brightness=40)
    )
    await entity.async_turn_on(transition=1.0)
    node.set_fine_brightness.assert_awaited_with(40, 1000)


async def test_turn_off_calls_set_power_zero():
    node = _fake_node()
    bridge = MagicMock()
    entity = CyncLanLight(bridge, "entry1", node)

    await entity.async_turn_off()
    node.set_power.assert_awaited_with(0)


async def test_color_temp_kelvin_none_when_unsupported(hass):
    from cync_lan.structs import EntityState

    node = _fake_node(supports_temperature=False)
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanLight(bridge, "entry1", node)
    await bridge.parse_entity_state(EntityState(name="x", dev_id=5, temperature=3000))
    assert entity.color_temp_kelvin is None


async def test_color_temp_kelvin_reads_through_when_supported(hass):
    from cync_lan.structs import EntityState

    node = _fake_node(supports_temperature=True)
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanLight(bridge, "entry1", node)
    assert entity.color_temp_kelvin is None  # no state yet
    await bridge.parse_entity_state(EntityState(name="x", dev_id=5, temperature=3000))
    assert entity.color_temp_kelvin == 3000


async def test_rgb_color_none_when_unsupported(hass):
    from cync_lan.structs import EntityState

    node = _fake_node(supports_rgb=False)
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanLight(bridge, "entry1", node)
    await bridge.parse_entity_state(
        EntityState(name="x", dev_id=5, red=1, green=2, blue=3)
    )
    assert entity.rgb_color is None


async def test_rgb_color_reads_through_when_supported(hass):
    from cync_lan.structs import EntityState

    node = _fake_node(supports_rgb=True)
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanLight(bridge, "entry1", node)
    await bridge.parse_entity_state(
        EntityState(name="x", dev_id=5, red=1, green=2, blue=3)
    )
    assert entity.rgb_color == (1, 2, 3)


async def test_turn_on_with_rgb_color():
    node = _fake_node(supports_rgb=True)
    bridge = MagicMock()
    entity = CyncLanLight(bridge, "entry1", node)

    await entity.async_turn_on(rgb_color=(10, 20, 30))
    node.set_rgb.assert_awaited_with(10, 20, 30)


async def test_turn_on_with_color_temp_kelvin():
    node = _fake_node(supports_temperature=True)
    bridge = MagicMock()
    entity = CyncLanLight(bridge, "entry1", node)

    await entity.async_turn_on(color_temp_kelvin=4000)
    node.set_temperature.assert_awaited_with(4000)


async def test_color_mode_static_when_only_one_mode_supported(hass):
    """Single-mode devices (brightness-only, or only one of RGB/CCT) have
    no live disambiguation to do - color_mode just returns the static
    value computed at construction, regardless of state.temperature."""
    from cync_lan.structs import EntityState

    node = _fake_node(supports_temperature=True, supports_rgb=False)
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanLight(bridge, "entry1", node)
    await bridge.parse_entity_state(EntityState(name="x", dev_id=5, temperature=254))
    assert entity.color_mode == "color_temp"


async def test_color_mode_dual_capable_follows_live_temperature_sentinel(hass):
    """Regression test: previously color_mode was set once at construction
    via next(iter(modes)) and never updated - for a dual RGB+COLOR_TEMP
    device this could permanently lock the wrong control widget in the UI,
    unrelated to what the bulb is actually doing. Mirrors
    src/cync_lan/mqtt_client.py's own established convention: temperature
    == 254 means the device is currently in RGB mode, 0-100 means CCT."""
    from cync_lan.structs import EntityState

    node = _fake_node(supports_temperature=True, supports_rgb=True)
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanLight(bridge, "entry1", node)

    await bridge.parse_entity_state(EntityState(name="x", dev_id=5, temperature=254))
    assert entity.color_mode == "rgb"

    await bridge.parse_entity_state(EntityState(name="x", dev_id=5, temperature=30))
    assert entity.color_mode == "color_temp"


async def test_color_mode_dual_capable_falls_back_to_static_before_any_state(hass):
    node = _fake_node(supports_temperature=True, supports_rgb=True)
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanLight(bridge, "entry1", node)
    assert entity.color_mode == entity._attr_color_mode


async def test_turn_on_with_effect():
    node = _fake_node(supports_rgb=True)
    bridge = MagicMock()
    entity = CyncLanLight(bridge, "entry1", node)

    await entity.async_turn_on(effect="rainbow")
    node.set_light_effect.assert_awaited_with("rainbow")


def test_light_group_uses_or_based_mode():
    """The group's on/off state must be True if ANY member is on (LightGroup's
    `mode` defaults to `any`, not `all`, when constructed with mode=False)."""
    group = CyncLanLightGroup("entry1_group_1", "Test Group", ["light.a", "light.b"])
    assert group.mode is any


def test_light_group_uses_icon_translation_not_static_icon():
    """icon-translations (gold): the group's icon (default + state-based
    "off"/"unavailable" overrides) comes from icons.json via
    translation_key, not a static _attr_icon - see
    test_icons_json_light_group_entry in this file for the actual icon
    string assertions, since Entity.icon only ever returns _attr_icon/
    entity_description.icon, never a translation-resolved value (that
    resolution happens in the frontend)."""
    group = CyncLanLightGroup("entry1_group_1", "Test Group", ["light.a", "light.b"])
    assert group.translation_key == "cync_light_group"
    assert group.icon is None
    assert group.name == "Test Group"  # _attr_name still wins, unaffected


def test_icons_json_light_group_entry():
    import json
    from pathlib import Path

    icons = json.loads(
        (
            Path(__file__).parents[3] / "custom_components/cync_lan/icons.json"
        ).read_text()
    )
    entry = icons["entity"]["light"]["cync_light_group"]
    assert entry["default"] == "mdi:lightbulb-group"
    assert entry["state"]["off"] == "mdi:lightbulb-group-off"
    assert entry["state"]["unavailable"] == "mdi:lightbulb-group-off"


def _make_group_entry(entry_id: str, **options):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=entry_id,
        unique_id="user@example.com",
        options=options,
    )


async def test_groups_disabled_by_default_creates_no_group_entities(hass):
    g = SimpleNamespace()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {}

    entry = _make_group_entry("entry1")  # enable_light_groups defaults to False
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(
        bridge=CyncLanBridge(hass, "entry1"),
        ncync_server=g.ncync_server,
        groups={1: {"name": "Kitchen", "device_ids": [1], "is_subgroup": False}},
    )

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert not any(isinstance(e, CyncLanLightGroup) for e in added)


async def test_groups_created_when_enabled_with_registered_members(hass):
    from homeassistant.const import Platform
    from homeassistant.helpers import entity_registry as er

    g = SimpleNamespace()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {}

    entry = _make_group_entry("entry1", enable_light_groups=True)
    entry.add_to_hass(hass)

    # Simulate the individual CyncLanLight entities for dev_ids 1 and 2
    # already being registered (as they would be from this same platform's
    # own earlier async_add_entities call in a real HA setup).
    registry = er.async_get(hass)
    entry1 = registry.async_get_or_create(
        Platform.LIGHT, DOMAIN, "entry1_1", config_entry=entry
    )
    entry2 = registry.async_get_or_create(
        Platform.LIGHT, DOMAIN, "entry1_2", config_entry=entry
    )

    entry.runtime_data = MagicMock(
        bridge=CyncLanBridge(hass, "entry1"),
        ncync_server=g.ncync_server,
        groups={
            32770: {
                "name": "Kitchen",
                "device_ids": [1, 2],
                "is_subgroup": False,
            }
        },
    )

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    groups = [e for e in added if isinstance(e, CyncLanLightGroup)]
    assert len(groups) == 1
    assert groups[0].name == "Kitchen"
    assert groups[0].unique_id == "entry1_group_32770"
    assert set(groups[0]._entity_ids) == {entry1.entity_id, entry2.entity_id}


async def test_groups_enabled_but_none_exist_creates_nothing(hass):
    g = SimpleNamespace()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {}

    entry = _make_group_entry("entry1", enable_light_groups=True)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(bridge=CyncLanBridge(hass, "entry1"), groups={})

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert added == []


async def test_groups_created_with_real_entity_platform_add_entities(hass):
    """Regression test for the real timing bug: async_add_entities is a
    fire-and-forget callback (its type signature returns None, not a
    coroutine) - it only *schedules* entity registration as a background
    task (EntityPlatform._async_schedule_add_entities_for_entry), it does
    not complete it before returning. Every other group test in this file
    uses a fake, synchronous "collect into a list" stand-in for
    async_add_entities, which happens to make registry lookups work
    immediately regardless of whether the real timing gap is handled -
    none of them would have caught this bug, which is exactly how it
    shipped despite full test coverage on the surface. This test drives
    async_setup_entry with a real EntityPlatform's real scheduling callback
    to exercise the actual registration path a production HA install has.

    async_setup_entry originally waited out this gap with
    hass.async_block_till_done(), which waits for every hass-tracked
    background task process-wide, not just this platform's own scheduled
    work - fine in this lightweight test environment, but on a real HA
    install with many integrations still settling during startup it took
    over 60 seconds and tripped HA's own "platform setup is taking too
    long" warning. Replaced with _wait_for_light_entities(), a bounded
    poll for just these specific entities. This test is still valuable as
    end-to-end coverage of the real entity-platform/registry-resolution
    path (which found and fixed the entity_id-vs-unique_id assumption bug
    below), independent of which waiting strategy is used above it.
    """
    from datetime import timedelta

    from homeassistant.const import Platform
    from homeassistant.helpers.entity_platform import EntityPlatform

    g = SimpleNamespace()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {
        1: _fake_node(id=1, name="Kitchen Sink Light"),
        2: _fake_node(id=2, name="Kitchen Island Light"),
    }

    entry = _make_group_entry("entry1", enable_light_groups=True)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(
        bridge=CyncLanBridge(hass, "entry1"),
        ncync_server=g.ncync_server,
        groups={32770: {"name": "Kitchen", "device_ids": [1, 2], "is_subgroup": False}},
    )

    platform = EntityPlatform(
        hass=hass,
        logger=MagicMock(),
        domain=Platform.LIGHT,
        platform_name=DOMAIN,
        platform=None,
        scan_interval=timedelta(seconds=30),
        entity_namespace=None,
    )
    platform.config_entry = entry

    # The real callback a config-entry-based platform's async_setup_entry
    # actually receives in production (see EntityPlatform.async_setup_entry)
    # - not platform.async_add_entities directly, which is the real
    # coroutine that callback schedules as a background task, not something
    # ever handed to platform code itself.
    await async_setup_entry(
        hass, entry, platform._async_schedule_add_entities_for_entry
    )

    from homeassistant.const import Platform as _Platform
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    expected_entity_ids = {
        registry.async_get_entity_id(_Platform.LIGHT, DOMAIN, "entry1_1"),
        registry.async_get_entity_id(_Platform.LIGHT, DOMAIN, "entry1_2"),
    }

    groups = [e for e in platform.entities.values() if isinstance(e, CyncLanLightGroup)]
    assert len(groups) == 1
    assert set(groups[0]._entity_ids) == expected_entity_ids


async def test_group_skipped_when_no_members_resolve(hass):
    """A group whose device_ids don't map to any registered light entity
    (e.g. all its members are switches/plugs, or were never added) must be
    silently skipped, not create an empty/broken group entity."""
    g = SimpleNamespace()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {}

    entry = _make_group_entry("entry1", enable_light_groups=True)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(
        bridge=CyncLanBridge(hass, "entry1"),
        ncync_server=g.ncync_server,
        groups={99: {"name": "Ghost Group", "device_ids": [404], "is_subgroup": False}},
    )

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert not any(isinstance(e, CyncLanLightGroup) for e in added)


async def test_add_light_groups_noop_when_platform_not_set_up(hass):
    """async_add_light_groups() must be a safe no-op if this platform's own
    async_setup_entry hasn't stashed an async_add_entities callback yet -
    e.g. called from the options flow before the entry has ever finished
    its own initial setup."""
    entry = _make_group_entry("entry1", enable_light_groups=True)
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        light_add_entities=None,
        groups={1: {"name": "Kitchen", "device_ids": [1], "is_subgroup": False}},
        created_light_group_ids=set(),
    )

    await async_add_light_groups(hass, entry)  # must not raise


async def test_add_light_groups_noop_when_no_groups(hass):
    entry = _make_group_entry("entry1", enable_light_groups=True)
    entry.add_to_hass(hass)
    added = []
    entry.runtime_data = SimpleNamespace(
        light_add_entities=lambda entities: added.extend(entities),
        groups={},
        created_light_group_ids=set(),
    )

    await async_add_light_groups(hass, entry)

    assert added == []


async def test_add_light_groups_creates_group_and_tracks_it(hass):
    """Direct call path (as used by the options flow to apply groups
    without a full entry reload), independent of async_setup_entry -
    individual lights are already registered, as they would be from an
    earlier, already-completed platform setup."""
    from homeassistant.const import Platform
    from homeassistant.helpers import entity_registry as er

    entry = _make_group_entry("entry1", enable_light_groups=True)
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    registry.async_get_or_create(Platform.LIGHT, DOMAIN, "entry1_1", config_entry=entry)
    registry.async_get_or_create(Platform.LIGHT, DOMAIN, "entry1_2", config_entry=entry)

    added = []
    entry.runtime_data = SimpleNamespace(
        light_add_entities=lambda entities: added.extend(entities),
        groups={32770: {"name": "Kitchen", "device_ids": [1, 2], "is_subgroup": False}},
        created_light_group_ids=set(),
    )

    await async_add_light_groups(hass, entry)

    assert len(added) == 1
    assert isinstance(added[0], CyncLanLightGroup)
    assert entry.runtime_data.created_light_group_ids == {32770}


async def test_add_light_groups_skips_groups_already_created(hass):
    """A second call (e.g. resaving the options form twice) must not
    re-add a group entity that's already been created - async_add_entities
    isn't safe to call twice with different objects sharing a unique_id."""
    from homeassistant.const import Platform
    from homeassistant.helpers import entity_registry as er

    entry = _make_group_entry("entry1", enable_light_groups=True)
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    registry.async_get_or_create(Platform.LIGHT, DOMAIN, "entry1_1", config_entry=entry)

    added = []
    entry.runtime_data = SimpleNamespace(
        light_add_entities=lambda entities: added.extend(entities),
        groups={1: {"name": "Kitchen", "device_ids": [1], "is_subgroup": False}},
        created_light_group_ids={1},
    )

    await async_add_light_groups(hass, entry)

    assert added == []


async def test_add_light_groups_hides_members_when_enabled(hass):
    """hide_members=True must hide each group member's entity (via
    hidden_by=INTEGRATION) so they disappear from default dashboards
    while the group entity represents them instead."""
    from homeassistant.const import Platform
    from homeassistant.helpers import entity_registry as er

    entry = _make_group_entry("entry1", enable_light_groups=True)
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    registry.async_get_or_create(Platform.LIGHT, DOMAIN, "entry1_1", config_entry=entry)

    entry.runtime_data = SimpleNamespace(
        light_add_entities=lambda entities: None,
        groups={1: {"name": "Kitchen", "device_ids": [1], "is_subgroup": False}},
        created_light_group_ids=set(),
    )

    await async_add_light_groups(hass, entry, hide_members=True)

    entry_id = "entry1"
    reg_entry = registry.async_get(
        registry.async_get_entity_id(Platform.LIGHT, DOMAIN, f"{entry_id}_1")
    )
    assert reg_entry.hidden_by is er.RegistryEntryHider.INTEGRATION


async def test_add_light_groups_reveals_members_when_disabled(hass):
    """hide_members=False must reveal a member entity this integration
    previously hid - toggling the option back off restores visibility."""
    from homeassistant.const import Platform
    from homeassistant.helpers import entity_registry as er

    entry = _make_group_entry("entry1", enable_light_groups=True)
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    entity_entry = registry.async_get_or_create(
        Platform.LIGHT, DOMAIN, "entry1_1", config_entry=entry
    )
    registry.async_update_entity(
        entity_entry.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
    )

    entry.runtime_data = SimpleNamespace(
        light_add_entities=lambda entities: None,
        groups={1: {"name": "Kitchen", "device_ids": [1], "is_subgroup": False}},
        created_light_group_ids=set(),
    )

    await async_add_light_groups(hass, entry, hide_members=False)

    reg_entry = registry.async_get(entity_entry.entity_id)
    assert reg_entry.hidden_by is None


async def test_add_light_groups_never_touches_user_hidden_members(hass):
    """A member the user hid themselves (hidden_by=USER) must be left
    alone regardless of the hide_members option, in either direction."""
    from homeassistant.const import Platform
    from homeassistant.helpers import entity_registry as er

    entry = _make_group_entry("entry1", enable_light_groups=True)
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    entity_entry = registry.async_get_or_create(
        Platform.LIGHT, DOMAIN, "entry1_1", config_entry=entry
    )
    registry.async_update_entity(
        entity_entry.entity_id, hidden_by=er.RegistryEntryHider.USER
    )

    entry.runtime_data = SimpleNamespace(
        light_add_entities=lambda entities: None,
        groups={1: {"name": "Kitchen", "device_ids": [1], "is_subgroup": False}},
        created_light_group_ids=set(),
    )

    await async_add_light_groups(hass, entry, hide_members=True)
    assert (
        registry.async_get(entity_entry.entity_id).hidden_by
        is er.RegistryEntryHider.USER
    )

    await async_add_light_groups(hass, entry, hide_members=False)
    assert (
        registry.async_get(entity_entry.entity_id).hidden_by
        is er.RegistryEntryHider.USER
    )


async def test_add_light_groups_hide_members_defaults_from_entry_options(hass):
    """When hide_members isn't passed explicitly (the async_setup_entry
    call path, where entry.options is always current), it must fall back
    to reading CONF_HIDE_GROUP_MEMBERS from entry.options."""
    from homeassistant.const import Platform
    from homeassistant.helpers import entity_registry as er

    entry = _make_group_entry(
        "entry1", enable_light_groups=True, hide_group_members=True
    )
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    registry.async_get_or_create(Platform.LIGHT, DOMAIN, "entry1_1", config_entry=entry)

    entry.runtime_data = SimpleNamespace(
        light_add_entities=lambda entities: None,
        groups={1: {"name": "Kitchen", "device_ids": [1], "is_subgroup": False}},
        created_light_group_ids=set(),
    )

    await async_add_light_groups(hass, entry)  # hide_members omitted

    reg_entry = registry.async_get(
        registry.async_get_entity_id(Platform.LIGHT, DOMAIN, "entry1_1")
    )
    assert reg_entry.hidden_by is er.RegistryEntryHider.INTEGRATION


async def test_dimmer_minimum_brightness_floor_5_percent():
    """Dimmer switches/plugs (is_light=False, is_dimmable=True) must expose
    min_brightness_pct=5 in extra_state_attributes and clamp turn_on brightness
    to at least 5%."""
    dimmer_node = _fake_node(is_light=False, is_dimmable=True)
    bridge = MagicMock()
    entity = CyncLanLight(bridge, "entry1", dimmer_node)

    assert entity.extra_state_attributes == {"min_brightness_pct": 5}

    # Setting brightness to 1% (2 in 0-255 scale) clamps to 5% floor
    await entity.async_turn_on(brightness=2)
    dimmer_node.set_brightness.assert_called_once_with(5)


# ---------------------------------------------------------------------------
# Indicator ring as a light. Exclusive with the select/number/switch trio.
# ---------------------------------------------------------------------------


def test_nearest_led_color_snaps_to_the_four_the_hardware_has():
    """The device takes an enum, not an RGB triple, so a colour wheel has to
    be mapped onto one of four points before anything can be sent."""
    from custom_components.cync_lan.light import nearest_led_color

    assert nearest_led_color((240, 10, 10)) == "red"
    assert nearest_led_color((10, 200, 40)) == "green"
    assert nearest_led_color((30, 30, 220)) == "blue"
    assert nearest_led_color((250, 250, 240)) == "white"


def test_nearest_led_color_is_exact_on_the_reference_values():
    from custom_components.cync_lan.light import (
        _LED_REFERENCE_RGB,
        nearest_led_color,
    )

    for name, rgb in _LED_REFERENCE_RGB.items():
        assert nearest_led_color(rgb) == name


def test_nearest_led_color_resolves_a_midpoint_deterministically():
    """(255, 255, 0) is equidistant from red and green. Any answer is
    defensible; an unstable one is not."""
    from custom_components.cync_lan.light import nearest_led_color

    assert nearest_led_color((255, 255, 0)) == nearest_led_color((255, 255, 0))
    assert nearest_led_color((255, 255, 0)) in {"red", "green", "white"}


def _ring(mode="normal", color="white", brightness=100):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.cync_lan.light import CyncLanIndicatorLedLight

    bridge = MagicMock()
    bridge.get_indicator_led.return_value = SimpleNamespace(
        mode=mode, color=color, brightness=brightness, wifi_disconnect_blink=False
    )
    bridge.set_indicator_led_field = AsyncMock()
    node = MagicMock()
    node.id = 7
    return CyncLanIndicatorLedLight(bridge, "entry", node), bridge


def test_ring_reports_the_reference_rgb_not_what_was_asked_for():
    """Reporting the requested colour back would claim a precision the device
    does not have - it only stores which of four it was set to."""
    light, _ = _ring(color="red")
    assert light.rgb_color == (255, 0, 0)


def test_ring_scales_brightness_between_the_two_ranges():
    light, _ = _ring(brightness=100)
    assert light.brightness == 255
    light, _ = _ring(brightness=50)
    assert 126 <= light.brightness <= 129


async def test_turning_on_snaps_the_colour_and_scales_brightness():
    from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_RGB_COLOR

    light, bridge = _ring()
    await light.async_turn_on(**{ATTR_RGB_COLOR: (240, 10, 10), ATTR_BRIGHTNESS: 255})

    _, kwargs = bridge.set_indicator_led_field.call_args
    assert kwargs["color"] == "red"
    assert kwargs["brightness"] == 100


async def test_turning_on_a_ring_in_normal_mode_does_not_clobber_it():
    """`normal` is already on, and is a deliberate choice from the mode
    select. Forcing always_on for every colour change would silently undo it."""
    light, bridge = _ring(mode="normal")
    await light.async_turn_on()

    _, kwargs = bridge.set_indicator_led_field.call_args
    assert "mode" not in kwargs


async def test_turning_on_a_ring_that_is_off_does_set_a_mode():
    light, bridge = _ring(mode="always_off")
    await light.async_turn_on()

    _, kwargs = bridge.set_indicator_led_field.call_args
    assert kwargs["mode"] == "always_on"


async def test_off_maps_to_always_off():
    light, bridge = _ring(mode="always_on")
    assert light.is_on is True
    await light.async_turn_off()
    _, kwargs = bridge.set_indicator_led_field.call_args
    assert kwargs["mode"] == "always_off"

    light, _ = _ring(mode="always_off")
    assert light.is_on is False


async def test_the_two_presentations_are_exclusive(hass):
    """One form or the other, never both - they all write the same single
    atomic mesh command, so shipping both puts two UIs in a race over one
    piece of hardware."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from custom_components.cync_lan import light as light_mod
    from custom_components.cync_lan import select as select_mod
    from custom_components.cync_lan.const import CONF_INDICATOR_LED_AS_LIGHT

    node = MagicMock()
    node.id = 7
    node.metadata = SimpleNamespace(
        supported=True, model_string="Switch", model_id=None, sw_version=None
    )
    node.is_light = False  # not a lamp; it still has a status ring

    def _entry(as_light: bool):
        entry = MagicMock()
        entry.entry_id = "e1"
        entry.options = {CONF_INDICATOR_LED_AS_LIGHT: as_light}
        entry.runtime_data = SimpleNamespace(
            bridge=MagicMock(),
            groups={},
            ncync_server=SimpleNamespace(node_devices={7: node}),
        )
        return entry

    names = lambda added: {type(e).__name__ for e in added}

    on: list = []
    await light_mod.async_setup_entry(hass, _entry(True), lambda e, *a: on.extend(e))
    assert "CyncLanIndicatorLedLight" in names(on)

    on_sel: list = []
    await select_mod.async_setup_entry(
        hass, _entry(True), lambda e, *a: on_sel.extend(e)
    )
    assert not any("IndicatorLed" in n for n in names(on_sel)), (
        "the trio must stand down when the light is chosen"
    )

    off: list = []
    await light_mod.async_setup_entry(hass, _entry(False), lambda e, *a: off.extend(e))
    assert "CyncLanIndicatorLedLight" not in names(off)

    off_sel: list = []
    await select_mod.async_setup_entry(
        hass, _entry(False), lambda e, *a: off_sel.extend(e)
    )
    assert any("IndicatorLed" in n for n in names(off_sel))
