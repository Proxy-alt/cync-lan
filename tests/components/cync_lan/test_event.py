"""Tests for the event platform: wire-free button presses and motion triggers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.cync_lan import event as event_mod
from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.event import (
    EVENT_MOTION_DETECTED,
    EVENT_PRESSED,
    CyncLanTriggerEvent,
    async_setup_entry,
)


def _fake_node(**overrides):
    node = MagicMock()
    node.id = 5
    node.type = 96
    node.name = "Test Sensor"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = None
    node.bt_only = True
    node.metadata = MagicMock(supported=True)
    node.metadata.model_string = "Some Model"
    node.has_motion_sensor = True
    node.is_light = False
    node.is_switch = False
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


async def _live(hass, node, **kw):
    """An entity wired to a real bridge, with state writes and the event
    itself captured instead of needing full platform registration."""
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanTriggerEvent(bridge, "entry1", node, **({"is_secondary": False} | kw))
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()
    fired = []
    entity._trigger_event = lambda event_type, extra=None: fired.append(event_type)
    await entity.async_added_to_hass()
    return bridge, entity, fired


async def test_setup_creates_one_event_per_motion_capable_supported_device(hass):
    plain = _fake_node(has_motion_sensor=False)
    sensor = _fake_node()
    unsupported = _fake_node()
    unsupported.metadata.supported = False
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server.node_devices = {1: plain, 2: sensor, 3: unsupported}

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    assert [e._node for e in added] == [sensor]


async def test_wire_free_switch_is_a_button_event(hass):
    entity = (await _live(hass, _fake_node(type=112)))[1]
    assert entity.event_types == [EVENT_PRESSED]
    assert entity.device_class == "button"
    assert entity.unique_id == "entry1_5_event"


async def test_motion_sensor_is_a_motion_event(hass):
    entity = (await _live(hass, _fake_node()))[1]
    assert entity.event_types == [EVENT_MOTION_DETECTED]
    assert entity.device_class == "motion"


async def test_secondary_motion_event_gets_a_name_standalone_does_not(hass):
    assert (await _live(hass, _fake_node(), is_secondary=True))[1].translation_key == "motion"
    assert (await _live(hass, _fake_node()))[1].translation_key is None


async def test_motion_fires_on_new_detection_only(hass):
    node = _fake_node()
    bridge, _, fired = await _live(hass, node)
    with patch.object(event_mod.time, "monotonic", side_effect=[100.0, 200.0, 300.0]):
        await bridge.publish_motion_state(node, True)  # new detection
        await bridge.publish_motion_state(node, True)  # still detected: not new
        await bridge.publish_motion_state(node, False)  # cleared: no event
        await bridge.publish_motion_state(node, True)  # new again
    await hass.async_block_till_done()
    assert fired == [EVENT_MOTION_DETECTED, EVENT_MOTION_DETECTED]


async def test_button_fires_on_every_report_that_sets_the_flag(hass):
    node = _fake_node(type=112)
    bridge, _, fired = await _live(hass, node)
    with patch.object(event_mod.time, "monotonic", side_effect=[100.0, 200.0]):
        await bridge.publish_motion_state(node, True)
        # A second press inside the ~19 s window: the flag is already set.
        await bridge.publish_motion_state(node, True)
        await bridge.publish_motion_state(node, False)  # decay: not a press
    await hass.async_block_till_done()
    assert fired == [EVENT_PRESSED, EVENT_PRESSED]


async def test_relayed_duplicates_within_the_debounce_window_fire_once(hass):
    node = _fake_node(type=112)
    bridge, _, fired = await _live(hass, node)
    with patch.object(event_mod.time, "monotonic", side_effect=[100.0, 100.3, 100.7]):
        for _ in range(3):  # the same report, relayed by three bridges
            await bridge.publish_motion_state(node, True)
    await hass.async_block_till_done()
    assert fired == [EVENT_PRESSED]


async def test_other_devices_do_not_trigger_this_entity(hass):
    node, other = _fake_node(), _fake_node(id=9)
    bridge, _, fired = await _live(hass, node)
    await bridge.publish_motion_state(other, True)
    await hass.async_block_till_done()
    assert fired == []


async def test_a_press_reaches_a_registered_event_entity_state(hass):
    """Not stubbed: register on a real platform and read the resulting state, so
    a broken signal, name, or event type shows up as a missing state change."""
    from pytest_homeassistant_custom_component.common import MockEntityPlatform

    node = _fake_node(type=112, name="Hall Switch")
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanTriggerEvent(bridge, "entry1", node, is_secondary=False)
    platform = MockEntityPlatform(hass, domain="event", platform_name="cync_lan")
    await platform.async_add_entities([entity])

    before = hass.states.get(entity.entity_id)
    assert before is not None and before.state == "unknown"

    await bridge.publish_motion_state(node, True)
    await hass.async_block_till_done()

    after = hass.states.get(entity.entity_id)
    assert after.state != "unknown"
    assert after.attributes["event_type"] == EVENT_PRESSED
    assert after.attributes["device_class"] == "button"
