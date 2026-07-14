"""Tests for the binary_sensor platform."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.cync_lan.binary_sensor import (
    CyncLanAppMeshActiveSensor,
    CyncLanMotionSensor,
    async_setup_entry,
)
from custom_components.cync_lan.bridge import CyncLanBridge


def _fake_node(**overrides):
    node = MagicMock()
    node.id = 5
    node.name = "Test Sensor"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.metadata = MagicMock(supported=True)
    node.metadata.model_string = "Some Model"
    node.metadata.capabilities.sensor_device_class = "motion"
    node.has_motion_sensor = True
    node.is_light = False
    node.is_switch = False
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


async def test_setup_entry_skips_devices_without_motion_sensor(hass):
    from cync_lan.structs import GlobalObject

    g = GlobalObject()
    no_motion = _fake_node(has_motion_sensor=False)
    standalone = _fake_node()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {1: no_motion, 2: standalone}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    # standalone motion sensor + the always-present app-mesh-active diagnostic
    assert len(added) == 2
    motion_entities = [e for e in added if isinstance(e, CyncLanMotionSensor)]
    assert len(motion_entities) == 1
    assert motion_entities[0]._node is standalone


async def test_standalone_sensor_has_no_secondary_suffix(hass):
    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanMotionSensor(bridge, "entry1", node, is_secondary=False)
    assert entity.unique_id == "entry1_5"
    assert entity.name is None


async def test_secondary_sensor_has_motion_suffix_and_name(hass):
    node = _fake_node(is_light=True)
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanMotionSensor(bridge, "entry1", node, is_secondary=True)
    assert entity.unique_id == "entry1_5_motion"
    assert entity.name == "Motion"


async def test_device_class_comes_from_capabilities(hass):
    node = _fake_node()
    node.metadata.capabilities.sensor_device_class = "occupancy"
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanMotionSensor(bridge, "entry1", node, is_secondary=False)
    assert entity.device_class == "occupancy"


async def test_is_on_reads_bridge_motion_state(hass):
    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanMotionSensor(bridge, "entry1", node, is_secondary=False)

    assert entity.is_on is None
    await bridge.publish_motion_state(node, True)
    assert entity.is_on is True


async def test_app_mesh_active_sensor_diagnostic_and_disabled_by_default(hass):
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanAppMeshActiveSensor(bridge, "entry1")
    assert entity.entity_category == "diagnostic"
    assert entity.entity_registry_enabled_default is False
    assert entity.is_on is False

    await bridge.mark_app_mesh_active()
    assert entity.is_on is True
