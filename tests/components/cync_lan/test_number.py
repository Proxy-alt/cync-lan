"""Tests for the number platform: indicator-LED brightness."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.number import (
    CyncLanIndicatorLedBrightness,
    async_setup_entry,
)


def _fake_node(**overrides):
    node = MagicMock()
    node.id = 5
    node.name = "Test Device"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.metadata = MagicMock(supported=True)
    node.metadata.model_string = "Some Model"
    node.set_indicator_led = AsyncMock()
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


async def test_setup_entry_creates_one_per_supported_node(hass):
    g = SimpleNamespace()
    unsupported = _fake_node(metadata=None)
    node = _fake_node()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {1: unsupported, 2: node}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = g.ncync_server

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 1
    assert isinstance(added[0], CyncLanIndicatorLedBrightness)


async def test_default_value_bounds_and_category(hass):
    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanIndicatorLedBrightness(bridge, "entry1", node)

    assert entity.native_value == 100  # cache default
    assert entity.native_min_value == 1
    assert entity.native_max_value == 100
    assert entity.entity_category == "config"
    assert entity.assumed_state is True


async def test_set_native_value_writes_full_merged_state(hass):
    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanIndicatorLedBrightness(bridge, "entry1", node)

    await entity.async_set_native_value(42)

    assert entity.native_value == 42
    node.set_indicator_led.assert_awaited_once_with(
        mode=2, color=0, brightness=42, wifi_disconnect_blink=False
    )


async def test_restore_seeds_cache_without_commanding_hardware(hass):
    """RestoreNumber path: a restored value must seed the shared cache
    directly (seed_indicator_led_field), never calling node.set_indicator_led
    - HA restarts must not re-issue a live mesh command just from restoring
    the UI's last-known brightness."""
    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanIndicatorLedBrightness(bridge, "entry1", node)

    entity.hass = hass
    entity.entity_id = "number.test"

    last_data = MagicMock(native_value=77.0)
    with patch.object(
        entity, "async_get_last_number_data", AsyncMock(return_value=last_data)
    ):
        await entity.async_added_to_hass()

    assert bridge.get_indicator_led(5).brightness == 77
    node.set_indicator_led.assert_not_awaited()


async def test_restore_noop_when_no_prior_data(hass):
    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanIndicatorLedBrightness(bridge, "entry1", node)
    entity.hass = hass
    entity.entity_id = "number.test"

    with patch.object(
        entity, "async_get_last_number_data", AsyncMock(return_value=None)
    ):
        await entity.async_added_to_hass()

    assert bridge.get_indicator_led(5).brightness == 100  # unchanged default
