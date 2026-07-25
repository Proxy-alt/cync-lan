"""Tests for the select platform: indicator-LED mode and color."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.select import (
    CyncLanIndicatorLedColorSelect,
    CyncLanIndicatorLedModeSelect,
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


async def test_setup_entry_creates_both_selects_for_every_supported_node(hass):
    g = SimpleNamespace()
    unsupported = _fake_node(metadata=None)
    node = _fake_node()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {1: unsupported, 2: node}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = g.ncync_server

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 2
    assert {type(e) for e in added} == {
        CyncLanIndicatorLedModeSelect,
        CyncLanIndicatorLedColorSelect,
    }


async def test_mode_select_default_and_write(hass):
    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanIndicatorLedModeSelect(bridge, "entry1", node)

    assert entity.current_option == "normal"  # cache default
    assert entity.options == ["always_on", "always_off", "normal"]
    assert entity.entity_category == "config"
    assert entity.assumed_state is True

    await entity.async_select_option("always_on")
    assert entity.current_option == "always_on"
    node.set_indicator_led.assert_awaited_once_with(
        mode=0, color=0, brightness=100, wifi_disconnect_blink=False
    )


async def test_color_select_default_and_write(hass):
    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanIndicatorLedColorSelect(bridge, "entry1", node)

    assert entity.current_option == "white"
    assert entity.options == ["white", "red", "green", "blue"]

    await entity.async_select_option("red")
    assert entity.current_option == "red"
    node.set_indicator_led.assert_awaited_once_with(
        mode=2, color=1, brightness=100, wifi_disconnect_blink=False
    )


async def test_mode_select_restores_cache_without_commanding_hardware(hass):
    """RestoreEntity path: a restored value must seed the shared cache via
    seed_indicator_led_field (not set_indicator_led_field), so HA restarts
    never re-issue a live mesh command just from restoring UI state."""
    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanIndicatorLedModeSelect(bridge, "entry1", node)

    last_state = MagicMock(state="always_off")
    with patch.object(entity, "async_get_last_state", AsyncMock(return_value=last_state)):
        await entity._restore_led_field(
            "mode", lambda s: s if s in ("always_on", "always_off", "normal") else None
        )

    assert bridge.get_indicator_led(5).mode == "always_off"
    node.set_indicator_led.assert_not_awaited()


async def test_restore_field_ignores_unrecognized_stale_value(hass):
    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanIndicatorLedModeSelect(bridge, "entry1", node)

    last_state = MagicMock(state="some_removed_option")
    with patch.object(entity, "async_get_last_state", AsyncMock(return_value=last_state)):
        await entity._restore_led_field(
            "mode", lambda s: s if s in ("always_on", "always_off", "normal") else None
        )

    assert bridge.get_indicator_led(5).mode == "normal"  # unchanged default


async def test_restore_field_noop_when_no_prior_state(hass):
    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanIndicatorLedModeSelect(bridge, "entry1", node)

    with patch.object(entity, "async_get_last_state", AsyncMock(return_value=None)):
        await entity._restore_led_field("mode", lambda s: s)

    assert bridge.get_indicator_led(5).mode == "normal"


async def test_mode_and_color_writes_do_not_reset_each_other(hass):
    """The 4 LED entities share one atomic mesh command - setting mode
    must not silently reset color (or any other field) back to its
    default, and vice versa."""
    node = _fake_node()
    bridge = CyncLanBridge(hass, "entry1")
    mode_entity = CyncLanIndicatorLedModeSelect(bridge, "entry1", node)
    color_entity = CyncLanIndicatorLedColorSelect(bridge, "entry1", node)

    await color_entity.async_select_option("blue")
    await mode_entity.async_select_option("always_off")

    assert color_entity.current_option == "blue"
    assert mode_entity.current_option == "always_off"
    node.set_indicator_led.assert_awaited_with(
        mode=1, color=3, brightness=100, wifi_disconnect_blink=False
    )
