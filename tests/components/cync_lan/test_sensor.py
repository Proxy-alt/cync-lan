"""Tests for the sensor platform: motion-sensor native schedule slots."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.sensor import (
    CyncLanMotionScheduleSensor,
    async_setup_entry,
)


def _fake_node(**overrides):
    node = MagicMock()
    node.id = 5
    node.name = "Test Sensor"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.metadata = MagicMock(supported=True)
    node.metadata.model_string = "Some Model"
    node.has_motion_sensor = True
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


_DAYTIME_SLOT = {
    "slot_id": 1,
    "enabled": True,
    "mode": "simple",
    "start_time": "06:00",
    "end_time": "08:59",
    "brightness": 100,
    "cct": 50,
    "display_name": "Daytime",
}
_DISABLED_SLOT = {
    "slot_id": 3,
    "enabled": False,
    "mode": "disabled",
    "start_time": "22:00",
    "end_time": "05:59",
    "brightness": 0,
    "cct": 0,
    "display_name": "Sleep",
}


async def test_setup_entry_skips_devices_without_motion_sensor_or_schedules(hass):
    from cync_lan.structs import GlobalObject

    g = GlobalObject()
    unsupported = _fake_node(metadata=None)
    no_motion = _fake_node(has_motion_sensor=False)
    no_schedule = _fake_node()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {0: unsupported, 1: no_motion, 2: no_schedule}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.groups = {}

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert added == []


async def test_setup_entry_creates_one_sensor_per_slot(hass):
    from cync_lan.structs import GlobalObject

    g = GlobalObject()
    node = _fake_node()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {5: node}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.groups = {
        32770: {
            "name": "Utility Room",
            "device_ids": [5],
            "sensor_schedules": {"daytime": _DAYTIME_SLOT, "sleep": _DISABLED_SLOT},
        }
    }

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 2
    unique_ids = {e.unique_id for e in added}
    assert unique_ids == {
        "entry1_5_schedule_32770_daytime",
        "entry1_5_schedule_32770_sleep",
    }


def test_single_group_uses_ungrouped_translation_key():
    bridge = MagicMock()
    node = _fake_node()
    entity = CyncLanMotionScheduleSensor(
        bridge, "entry1", node,
        group_id=1, group_name="Utility Room", slot_name="daytime",
        slot=_DAYTIME_SLOT, disambiguate=False,
    )
    assert entity.translation_key == "sensor_schedule_slot"
    assert entity._attr_translation_placeholders == {"slot": "Daytime"}


def test_multi_group_uses_grouped_translation_key_with_group_name():
    bridge = MagicMock()
    node = _fake_node()
    entity = CyncLanMotionScheduleSensor(
        bridge, "entry1", node,
        group_id=1, group_name="Utility Room", slot_name="daytime",
        slot=_DAYTIME_SLOT, disambiguate=True,
    )
    assert entity.translation_key == "sensor_schedule_slot_grouped"
    assert entity._attr_translation_placeholders == {
        "slot": "Daytime", "group_name": "Utility Room",
    }


def test_native_value_formats_enabled_slot_as_time_range():
    bridge = MagicMock()
    node = _fake_node()
    entity = CyncLanMotionScheduleSensor(
        bridge, "entry1", node,
        group_id=1, group_name="Utility Room", slot_name="daytime",
        slot=_DAYTIME_SLOT, disambiguate=False,
    )
    assert entity.native_value == "06:00–08:59"


def test_native_value_disabled_slot():
    bridge = MagicMock()
    node = _fake_node()
    entity = CyncLanMotionScheduleSensor(
        bridge, "entry1", node,
        group_id=1, group_name="Utility Room", slot_name="sleep",
        slot=_DISABLED_SLOT, disambiguate=False,
    )
    assert entity.native_value == "Disabled"


def test_extra_state_attributes_exposes_slot_detail():
    bridge = MagicMock()
    node = _fake_node()
    entity = CyncLanMotionScheduleSensor(
        bridge, "entry1", node,
        group_id=32770, group_name="Utility Room", slot_name="daytime",
        slot=_DAYTIME_SLOT, disambiguate=False,
    )
    assert entity.extra_state_attributes == {
        "mode": "simple",
        "brightness": 100,
        "cct": 50,
        "display_name": "Daytime",
        "start_time": "06:00",
        "end_time": "08:59",
        "group_id": 32770,
        "group_name": "Utility Room",
    }


def test_entity_category_is_diagnostic():
    bridge = MagicMock()
    node = _fake_node()
    entity = CyncLanMotionScheduleSensor(
        bridge, "entry1", node,
        group_id=1, group_name="Utility Room", slot_name="daytime",
        slot=_DAYTIME_SLOT, disambiguate=False,
    )
    assert entity.entity_category == "diagnostic"
