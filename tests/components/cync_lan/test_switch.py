"""Tests for the switch platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.switch import CyncLanSwitch, async_setup_entry


def _fake_node(**overrides):
    node = MagicMock()
    node.id = 5
    node.name = "Test Switch"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.metadata = MagicMock(supported=True)
    node.metadata.model_string = "Some Model"
    node.is_switch = True
    node.is_light = False
    node.is_fan_controller = False
    node.is_plug = False
    node.has_multi_entities = False
    node.entities = {}
    node.set_power = AsyncMock()
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


async def test_setup_entry_skips_unsupported_and_fan_controllers(hass):
    from cync_lan.structs import GlobalObject

    g = GlobalObject()
    unsupported = _fake_node(metadata=None)
    fan = _fake_node(is_fan_controller=True)
    not_switch = _fake_node(is_switch=False)
    plain = _fake_node()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {
        1: unsupported,
        2: fan,
        3: not_switch,
        4: plain,
    }

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 1
    assert added[0]._node is plain


async def test_setup_entry_creates_one_entity_per_sub_id(hass):
    from cync_lan.structs import GlobalObject, EntityState

    g = GlobalObject()
    multi = _fake_node(
        has_multi_entities=True,
        entities={1: EntityState(name="Left", dev_id=4, sub_id=1), 2: EntityState(name="Right", dev_id=4, sub_id=2)},
    )
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {4: multi}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert {e.unique_id for e in added} == {"entry1_5_1", "entry1_5_2"}


async def test_device_class_outlet_for_plug():
    node = _fake_node(is_plug=True)
    bridge = MagicMock()
    entity = CyncLanSwitch(bridge, "entry1", node)
    assert entity.device_class == "outlet"


async def test_device_class_switch_for_non_plug():
    node = _fake_node(is_plug=False)
    bridge = MagicMock()
    entity = CyncLanSwitch(bridge, "entry1", node)
    assert entity.device_class == "switch"


async def test_is_on_reflects_bridge_state(hass):
    from cync_lan.structs import EntityState

    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanSwitch(bridge, "entry1", node)

    assert entity.is_on is None
    await bridge.parse_entity_state(EntityState(name="x", dev_id=5, power=1))
    assert entity.is_on is True


async def test_turn_on_off_call_node_set_power():
    node = _fake_node()
    bridge = MagicMock()
    entity = CyncLanSwitch(bridge, "entry1", node)

    await entity.async_turn_on()
    node.set_power.assert_awaited_with(1, sub_id=None)

    await entity.async_turn_off()
    node.set_power.assert_awaited_with(0, sub_id=None)


async def test_turn_on_passes_sub_id_for_multi_entity():
    node = _fake_node(has_multi_entities=True)
    bridge = MagicMock()
    entity = CyncLanSwitch(bridge, "entry1", node, sub_id=2)

    await entity.async_turn_on()
    node.set_power.assert_awaited_with(1, sub_id=2)
