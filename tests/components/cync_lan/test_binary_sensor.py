"""Tests for the binary_sensor platform."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.cync_lan.binary_sensor import (
    CyncLanAppMeshActiveSensor,
    CyncLanAppWifiActiveSensor,
    CyncLanMotionSensor,
    async_setup_entry,
)
from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.const import DOMAIN


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
    g = SimpleNamespace()
    no_motion = _fake_node(has_motion_sensor=False)
    standalone = _fake_node()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {1: no_motion, 2: standalone}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = g.ncync_server
    entry.runtime_data.groups = {}

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    # Count only motion entities - the platform also adds the two app-active
    # diagnostics and a ready-to-control sensor per WiFi device.
    motion_entities = [e for e in added if isinstance(e, CyncLanMotionSensor)]
    assert len(motion_entities) == 1
    assert motion_entities[0]._node is standalone


async def test_standalone_sensor_has_no_secondary_suffix(hass):
    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanMotionSensor(bridge, "entry1", node, is_secondary=False)
    assert entity.unique_id == "entry1_5"
    assert entity.name is None


async def test_secondary_sensor_has_motion_suffix_and_translation_key(hass):
    node = _fake_node(is_light=True)
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanMotionSensor(bridge, "entry1", node, is_secondary=True)
    assert entity.unique_id == "entry1_5_motion"
    # name resolution through translation_key requires real platform setup
    # (self.platform_data) - see test_entity_translations.py for that,
    # end-to-end, against the actual strings.json content.
    assert entity.translation_key == "motion"


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


async def test_app_wifi_active_sensor_diagnostic_and_disabled_by_default(hass):
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanAppWifiActiveSensor(bridge, "entry1")
    assert entity.entity_category == "diagnostic"
    assert entity.entity_registry_enabled_default is False
    assert entity.is_on is False

    await bridge.mark_app_wifi_active()
    assert entity.is_on is True


async def test_app_wifi_active_is_independent_of_app_mesh_active(hass):
    """These track genuinely different signals (TCP login vs. BTLE
    proximity) - one firing must not flip the other."""
    bridge = CyncLanBridge(hass, "entry1")
    mesh_entity = CyncLanAppMeshActiveSensor(bridge, "entry1")
    wifi_entity = CyncLanAppWifiActiveSensor(bridge, "entry1")

    await bridge.mark_app_wifi_active()
    assert wifi_entity.is_on is True
    assert mesh_entity.is_on is False



def _pool_entry(hass, sessions):
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = MagicMock()
    entry.runtime_data.ncync_server.node_devices = {}
    entry.runtime_data.ncync_server.get_dev_tcp_pool_sync = MagicMock(return_value=sessions)
    return entry


def _session(ready=True, mitm=False):
    return SimpleNamespace(ready_to_control=ready, mitm_mode=mitm)


async def test_ready_to_control_is_true_when_any_session_can_carry_a_command(hass):
    """Commands go to a random sample of the whole pool with the target named
    in the packet, so one ready session is enough for every device."""
    from custom_components.cync_lan.binary_sensor import CyncLanReadyToControlSensor

    entry = _pool_entry(hass, [_session(ready=False), _session(ready=True)])
    sensor = CyncLanReadyToControlSensor("entry1", entry.runtime_data)

    assert sensor.is_on is True
    assert sensor.extra_state_attributes == {"sessions": 2, "ready_sessions": 1}


async def test_ready_to_control_is_false_when_no_session_is_ready(hass):
    from custom_components.cync_lan.binary_sensor import CyncLanReadyToControlSensor

    entry = _pool_entry(hass, [_session(ready=False), _session(ready=False)])
    sensor = CyncLanReadyToControlSensor("entry1", entry.runtime_data)

    assert sensor.is_on is False
    # "0 of 2 ready" and "no sessions at all" are different problems.
    assert sensor.extra_state_attributes == {"sessions": 2, "ready_sessions": 0}


async def test_ready_to_control_is_false_with_an_empty_pool(hass):
    from custom_components.cync_lan.binary_sensor import CyncLanReadyToControlSensor

    entry = _pool_entry(hass, [])
    sensor = CyncLanReadyToControlSensor("entry1", entry.runtime_data)

    assert sensor.is_on is False
    assert sensor.extra_state_attributes == {"sessions": 0, "ready_sessions": 0}


async def test_mitm_session_also_counts_as_able_to_carry_a_command(hass):
    """broadcast_control_command accepts ready_to_control OR mitm_mode."""
    from custom_components.cync_lan.binary_sensor import CyncLanReadyToControlSensor

    entry = _pool_entry(hass, [_session(ready=False, mitm=True)])
    sensor = CyncLanReadyToControlSensor("entry1", entry.runtime_data)

    assert sensor.is_on is True


async def test_ready_to_control_lives_on_the_bridge_not_each_device(hass):
    """The regression this replaces: as a per-device entity it read false for
    every device that did not hold its own connection, while those devices
    were perfectly controllable through another session."""
    from custom_components.cync_lan.binary_sensor import (
        CyncLanReadyToControlSensor,
        async_setup_entry as bs_setup,
    )

    node = _fake_node(has_motion_sensor=False)
    entry = _pool_entry(hass, [_session()])
    entry.runtime_data.ncync_server.node_devices = {5: node}

    added = []
    await bs_setup(hass, entry, lambda e: added.extend(e))

    sensors = [e for e in added if isinstance(e, CyncLanReadyToControlSensor)]
    assert len(sensors) == 1  # one, not one per device
    assert sensors[0].device_info["identifiers"] == {(DOMAIN, "entry1")}
