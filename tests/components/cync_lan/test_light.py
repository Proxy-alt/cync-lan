"""Tests for the light platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.light import CyncLanLight, async_setup_entry


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
