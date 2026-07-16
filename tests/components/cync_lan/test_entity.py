"""Tests for the shared CyncLanEntity base and build_device_info."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.cync_lan.bridge import CyncLanBridge, signal_entity_update
from custom_components.cync_lan.entity import CyncLanEntity, build_device_info
from custom_components.cync_lan.const import DOMAIN


def _fake_node(**overrides):
    node = MagicMock()
    node.id = 5
    node.name = "Test Light"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.metadata = MagicMock()
    node.metadata.model_string = "Some Model"
    node.version_str = "1.2.3"
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


def test_build_device_info_includes_both_connections():
    info = build_device_info("entry1", _fake_node())
    assert (DOMAIN, "entry1_5") in info["identifiers"]
    assert ("bluetooth", "aa:bb:cc:dd:ee:ff") in info["connections"]
    assert ("mac", "11:22:33:44:55:66") in info["connections"]
    assert info["name"] == "Test Light"
    assert info["model"] == "Some Model"
    assert info["sw_version"] == "1.2.3"


def test_build_device_info_bt_only_skips_wifi_connection():
    node = _fake_node(bt_only=True)
    info = build_device_info("entry1", node)
    assert ("mac", "11:22:33:44:55:66") not in info["connections"]
    assert ("bluetooth", "aa:bb:cc:dd:ee:ff") in info["connections"]


def test_build_device_info_no_metadata_falls_back_to_unknown_model():
    node = _fake_node(metadata=None)
    info = build_device_info("entry1", node)
    assert info["model"] == "Unknown"


def test_unique_id_includes_sub_id_only_when_nonzero():
    node = _fake_node()
    bridge = MagicMock()
    primary = CyncLanEntity(bridge, "entry1", node)
    assert primary.unique_id == "entry1_5"

    sub = CyncLanEntity(bridge, "entry1", node, sub_id=2)
    assert sub.unique_id == "entry1_5_2"


async def test_available_reflects_bridge_online_state(hass):
    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanEntity(bridge, "entry1", node)

    assert entity.available is True
    await bridge.pub_online(5, False)
    assert entity.available is False


async def test_entity_state_reads_through_to_bridge(hass):
    from cync_lan.structs import EntityState

    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanEntity(bridge, "entry1", node)

    assert entity._entity_state() is None
    await bridge.parse_entity_state(EntityState(name="x", dev_id=5, power=1))
    assert entity._entity_state().power == 1


async def test_added_to_hass_subscribes_and_triggers_state_write(hass):
    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanEntity(bridge, "entry1", node)
    entity.hass = hass
    entity.entity_id = "sensor.test"
    entity.async_write_ha_state = MagicMock()

    await entity.async_added_to_hass()

    from homeassistant.helpers.dispatcher import async_dispatcher_send

    async_dispatcher_send(hass, signal_entity_update("entry1_5"))
    await hass.async_block_till_done()

    entity.async_write_ha_state.assert_called()


def test_handle_update_is_marked_hass_callback():
    """Regression test: _handle_update must be decorated with @callback
    (homeassistant.core) or HA's HassJob classifier can't tell it's safe
    to run directly on the event loop, and defaults to dispatching it
    through the executor thread pool instead - i.e. "a thread other than
    the event loop", exactly the error HA's own thread-safety guard
    raises on async_write_ha_state(). A real user's logs showed hundreds
    of these errors, one per dispatched state update, meaning entity
    state was computed correctly internally but never actually reached
    HA's frontend.

    test_added_to_hass_subscribes_and_triggers_state_write above doesn't
    catch this: hass.async_block_till_done() waits for outstanding
    executor jobs too, so it passes whether _handle_update runs on the
    loop or on a worker thread - it only proves async_write_ha_state was
    *eventually* called, not that it ran somewhere HA considers safe.
    """
    assert getattr(CyncLanEntity._handle_update, "_hass_callback", False) is True
