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
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


async def test_setup_entry_only_includes_lights(hass):
    from cync_lan.structs import GlobalObject

    g = GlobalObject()
    light = _fake_node()
    not_light = _fake_node(is_light=False)
    unsupported = _fake_node(metadata=None)
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {1: light, 2: not_light, 3: unsupported}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")

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
    await bridge.parse_entity_state(EntityState(name="x", dev_id=5, power=1, brightness=50))
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
    await bridge.parse_entity_state(EntityState(name="x", dev_id=5, red=1, green=2, blue=3))
    assert entity.rgb_color is None


async def test_rgb_color_reads_through_when_supported(hass):
    from cync_lan.structs import EntityState

    node = _fake_node(supports_rgb=True)
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanLight(bridge, "entry1", node)
    await bridge.parse_entity_state(EntityState(name="x", dev_id=5, red=1, green=2, blue=3))
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


async def test_turn_on_with_effect():
    node = _fake_node(supports_rgb=True)
    bridge = MagicMock()
    entity = CyncLanLight(bridge, "entry1", node)

    await entity.async_turn_on(effect="rainbow")
    node.set_lightshow.assert_awaited_with("rainbow")


def test_light_group_uses_or_based_mode():
    """The group's on/off state must be True if ANY member is on (LightGroup's
    `mode` defaults to `any`, not `all`, when constructed with mode=False)."""
    group = CyncLanLightGroup("entry1_group_1", "Test Group", ["light.a", "light.b"])
    assert group.mode is any


def test_light_group_uses_light_groups_icon():
    group = CyncLanLightGroup("entry1_group_1", "Test Group", ["light.a", "light.b"])
    assert group.icon == "mdi:lightbulb-group"


def _make_group_entry(entry_id: str, **options):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=entry_id,
        unique_id="user@example.com",
        options=options,
    )


async def test_groups_disabled_by_default_creates_no_group_entities(hass):
    from cync_lan.structs import GlobalObject

    g = GlobalObject()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {}

    entry = _make_group_entry("entry1")  # enable_light_groups defaults to False
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(
        bridge=CyncLanBridge(hass, "entry1"),
        groups={1: {"name": "Kitchen", "device_ids": [1], "is_subgroup": False}},
    )

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert not any(isinstance(e, CyncLanLightGroup) for e in added)


async def test_groups_created_when_enabled_with_registered_members(hass):
    from cync_lan.structs import GlobalObject
    from homeassistant.const import Platform
    from homeassistant.helpers import entity_registry as er

    g = GlobalObject()
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
    from cync_lan.structs import GlobalObject

    g = GlobalObject()
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

    from cync_lan.structs import GlobalObject
    from homeassistant.const import Platform
    from homeassistant.helpers.entity_platform import EntityPlatform

    g = GlobalObject()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {
        1: _fake_node(id=1, name="Kitchen Sink Light"),
        2: _fake_node(id=2, name="Kitchen Island Light"),
    }

    entry = _make_group_entry("entry1", enable_light_groups=True)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(
        bridge=CyncLanBridge(hass, "entry1"),
        groups={
            32770: {"name": "Kitchen", "device_ids": [1, 2], "is_subgroup": False}
        },
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
    from cync_lan.structs import GlobalObject

    g = GlobalObject()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {}

    entry = _make_group_entry("entry1", enable_light_groups=True)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(
        bridge=CyncLanBridge(hass, "entry1"),
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
        groups={
            32770: {"name": "Kitchen", "device_ids": [1, 2], "is_subgroup": False}
        },
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
