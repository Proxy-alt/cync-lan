"""Tests for the fan platform."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.fan import CyncLanFan, async_setup_entry


def _fake_node(**overrides):
    node = MagicMock()
    node.id = 5
    node.name = "Test Fan"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.metadata = MagicMock(supported=True)
    node.metadata.model_string = "Some Model"
    node.is_fan_controller = True
    node.set_power = AsyncMock()
    node.set_fan_percentage = AsyncMock()
    node.set_fan_speed = AsyncMock()
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


async def test_setup_entry_only_includes_fan_controllers(hass):
    g = SimpleNamespace()
    fan = _fake_node()
    not_fan = _fake_node(is_fan_controller=False)
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {1: fan, 2: not_fan}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = g.ncync_server

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 1
    assert added[0]._node is fan


async def test_is_on_and_percentage_from_bridge(hass):
    from cync_lan.structs import EntityState

    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanFan(bridge, "entry1", node)

    assert entity.is_on is None
    assert entity.percentage is None
    await bridge.parse_entity_state(EntityState(name="x", dev_id=5, power=1, brightness=75))
    assert entity.is_on is True
    assert entity.percentage == 75


async def test_preset_mode_reflects_matching_percentage(hass):
    """Regression test: preset_mode previously had no property at all
    (always read None), so the preset dropdown never showed a selection
    even when percentage was at exactly one of the 4 preset values."""
    from cync_lan.structs import EntityState

    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanFan(bridge, "entry1", node)

    for pct, preset in ((25, "low"), (50, "medium"), (75, "high"), (100, "max")):
        await bridge.parse_entity_state(EntityState(name="x", dev_id=5, brightness=pct))
        assert entity.preset_mode == preset


async def test_preset_mode_none_when_percentage_does_not_match_a_preset(hass):
    from cync_lan.structs import EntityState

    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanFan(bridge, "entry1", node)

    await bridge.parse_entity_state(EntityState(name="x", dev_id=5, brightness=60))
    assert entity.preset_mode is None


def test_preset_mode_none_when_no_state_yet():
    node = _fake_node()
    bridge = MagicMock()
    entity = CyncLanFan(bridge, "entry1", node)
    assert entity.preset_mode is None


async def test_turn_on_with_percentage_calls_set_fan_percentage():
    node = _fake_node()
    bridge = MagicMock()
    entity = CyncLanFan(bridge, "entry1", node)

    await entity.async_turn_on(percentage=50)
    node.set_fan_percentage.assert_awaited_with(50)


async def test_turn_on_with_preset_mode_converts_to_fan_speed():
    node = _fake_node()
    bridge = MagicMock()
    entity = CyncLanFan(bridge, "entry1", node)

    await entity.async_turn_on(preset_mode="high")
    node.set_fan_speed.assert_awaited_once()
    from cync_lan.structs import FanSpeed

    assert node.set_fan_speed.call_args.args[0] == FanSpeed.HIGH


async def test_turn_on_no_args_calls_set_power():
    node = _fake_node()
    bridge = MagicMock()
    entity = CyncLanFan(bridge, "entry1", node)

    await entity.async_turn_on()
    node.set_power.assert_awaited_with(1)


async def test_turn_off_calls_set_power_zero():
    node = _fake_node()
    bridge = MagicMock()
    entity = CyncLanFan(bridge, "entry1", node)

    await entity.async_turn_off()
    node.set_power.assert_awaited_with(0)


async def test_async_set_percentage_direct():
    node = _fake_node()
    bridge = MagicMock()
    entity = CyncLanFan(bridge, "entry1", node)

    await entity.async_set_percentage(33)
    node.set_fan_percentage.assert_awaited_with(33)
