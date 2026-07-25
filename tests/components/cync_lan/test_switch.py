"""Tests for the switch platform."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.switch import (
    CyncLanIndicatorLedWifiBlinkSwitch,
    CyncLanMitmModeSwitch,
    CyncLanScheduleSwitch,
    CyncLanSwitch,
    async_setup_entry,
)


def _fake_node(**overrides):
    node = MagicMock()
    node.id = 5
    node.name = "Test Switch"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.has_wifi = True
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
    """CyncLanSwitch is gated on is_switch/not-fan-controller, but every
    supported node (regardless of is_switch) also gets an indicator-LED
    wifi-blink config switch - see test_setup_entry_creates_wifi_blink_switch_for_every_supported_node."""
    g = SimpleNamespace()
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
    entry.options = {}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = g.ncync_server

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    primary_switches = [e for e in added if isinstance(e, CyncLanSwitch)]
    assert len(primary_switches) == 1
    assert primary_switches[0]._node is plain


async def test_setup_entry_creates_wifi_blink_switch_for_every_supported_node(hass):
    g = SimpleNamespace()
    unsupported = _fake_node(metadata=None)
    fan = _fake_node(is_fan_controller=True)
    not_switch = _fake_node(is_switch=False)
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {1: unsupported, 2: fan, 3: not_switch}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = g.ncync_server

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    blink_switches = [e for e in added if isinstance(e, CyncLanIndicatorLedWifiBlinkSwitch)]
    # unsupported (metadata=None) is skipped entirely; fan and not_switch
    # both still get the config switch despite failing CyncLanSwitch's gate.
    assert len(blink_switches) == 2


async def test_setup_entry_creates_mitm_switch_only_for_wifi_devices(hass):
    g = SimpleNamespace()
    unsupported = _fake_node(metadata=None)
    bt_only = _fake_node(has_wifi=False, bt_only=True)
    wifi_capable = _fake_node()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {1: unsupported, 2: bt_only, 3: wifi_capable}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = g.ncync_server

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    mitm_switches = [e for e in added if isinstance(e, CyncLanMitmModeSwitch)]
    assert len(mitm_switches) == 1
    assert mitm_switches[0]._node is wifi_capable


def test_mitm_switch_is_diagnostic_and_disabled_by_default():
    bridge = MagicMock()
    node = _fake_node()
    entity = CyncLanMitmModeSwitch(bridge, "entry1", node)
    assert entity.entity_category == "diagnostic"
    assert entity.entity_registry_enabled_default is False
    assert entity.translation_key == "mitm_mode"


def test_mitm_switch_is_on_reflects_tcp_session_mitm_mode():
    bridge = MagicMock()
    node = _fake_node()
    node.tcp_session = MagicMock(mitm_mode=True)
    entity = CyncLanMitmModeSwitch(bridge, "entry1", node)
    assert entity.is_on is True


def test_mitm_switch_is_on_false_when_no_active_session():
    bridge = MagicMock()
    node = _fake_node()
    node.tcp_session = None
    entity = CyncLanMitmModeSwitch(bridge, "entry1", node)
    assert entity.is_on is False


def test_mitm_switch_unavailable_when_no_active_session():
    bridge = MagicMock()
    node = _fake_node()
    node.tcp_session = None
    entity = CyncLanMitmModeSwitch(bridge, "entry1", node)
    assert entity.available is False


async def test_mitm_switch_turn_on_calls_start_mitm():
    bridge = MagicMock()
    node = _fake_node()
    node.tcp_session = MagicMock()
    node.tcp_session.start_mitm = AsyncMock()
    entity = CyncLanMitmModeSwitch(bridge, "entry1", node)
    entity.async_write_ha_state = MagicMock()

    await entity.async_turn_on()

    node.tcp_session.start_mitm.assert_awaited_once()


async def test_mitm_switch_turn_off_calls_stop_mitm():
    bridge = MagicMock()
    node = _fake_node()
    node.tcp_session = MagicMock()
    node.tcp_session.stop_mitm = AsyncMock()
    entity = CyncLanMitmModeSwitch(bridge, "entry1", node)
    entity.async_write_ha_state = MagicMock()

    await entity.async_turn_off()

    node.tcp_session.stop_mitm.assert_awaited_once()


async def test_mitm_switch_turn_on_raises_when_no_active_session():
    bridge = MagicMock()
    node = _fake_node()
    node.tcp_session = None
    entity = CyncLanMitmModeSwitch(bridge, "entry1", node)

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()


async def test_mitm_switch_turn_off_raises_when_no_active_session():
    bridge = MagicMock()
    node = _fake_node()
    node.tcp_session = None
    entity = CyncLanMitmModeSwitch(bridge, "entry1", node)

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()


async def test_setup_entry_creates_one_entity_per_sub_id(hass):
    from cync_lan.structs import EntityState

    g = SimpleNamespace()
    multi = _fake_node(
        has_multi_entities=True,
        entities={1: EntityState(name="Left", dev_id=4, sub_id=1), 2: EntityState(name="Right", dev_id=4, sub_id=2)},
    )
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {4: multi}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = g.ncync_server

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    primary_switches = {e.unique_id for e in added if isinstance(e, CyncLanSwitch)}
    assert primary_switches == {"entry1_5_1", "entry1_5_2"}
    blink_switches = [e for e in added if isinstance(e, CyncLanIndicatorLedWifiBlinkSwitch)]
    assert len(blink_switches) == 1
    assert blink_switches[0].unique_id == "entry1_5_indicator_led_wifi_blink"


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


async def test_indicator_led_wifi_blink_reflects_bridge_cache(hass):
    node = _fake_node()
    node.set_indicator_led = AsyncMock()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanIndicatorLedWifiBlinkSwitch(bridge, "entry1", node)

    assert entity.is_on is False  # cache default

    await entity.async_turn_on()
    assert entity.is_on is True
    node.set_indicator_led.assert_awaited_once_with(
        mode=2, color=0, brightness=100, wifi_disconnect_blink=True
    )

    await entity.async_turn_off()
    assert entity.is_on is False


async def test_indicator_led_wifi_blink_restores_without_commanding_hardware(hass):
    node = _fake_node()
    node.set_indicator_led = AsyncMock()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanIndicatorLedWifiBlinkSwitch(bridge, "entry1", node)

    last_state = MagicMock(state="on")
    with patch.object(entity, "async_get_last_state", AsyncMock(return_value=last_state)):
        await entity._restore_led_field(
            "wifi_disconnect_blink",
            lambda s: True if s == "on" else False if s == "off" else None,
        )

    assert entity.is_on is True
    node.set_indicator_led.assert_not_awaited()


async def test_indicator_led_wifi_blink_full_restore_lifecycle(hass):
    """Exercises the real async_added_to_hass path (including its inline
    on/off/unrecognized state parser), not just _restore_led_field called
    directly with a test-supplied lambda."""
    node = _fake_node()
    node.set_indicator_led = AsyncMock()
    bridge = CyncLanBridge(hass, "entry1")
    entity = CyncLanIndicatorLedWifiBlinkSwitch(bridge, "entry1", node)
    entity.hass = hass
    entity.entity_id = "switch.test"

    last_state = MagicMock(state="on")
    with patch.object(entity, "async_get_last_state", AsyncMock(return_value=last_state)):
        await entity.async_added_to_hass()
    assert entity.is_on is True

    last_state = MagicMock(state="off")
    with patch.object(entity, "async_get_last_state", AsyncMock(return_value=last_state)):
        await entity.async_added_to_hass()
    assert entity.is_on is False

    last_state = MagicMock(state="unavailable")
    with patch.object(entity, "async_get_last_state", AsyncMock(return_value=last_state)):
        await entity.async_added_to_hass()
    assert entity.is_on is False  # unrecognized - cache unchanged from prior "off"
    node.set_indicator_led.assert_not_awaited()


async def test_setup_entry_creates_one_schedule_switch_per_schedule(hass):
    g = SimpleNamespace()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = g.ncync_server
    entry.runtime_data.schedules = {
        7: {"name": "Weekday Morning", "scene_id": 3, "enabled": True},
        9: {"name": "Weekend", "scene_id": 4, "enabled": False},
    }

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    schedule_switches = [e for e in added if isinstance(e, CyncLanScheduleSwitch)]
    assert len(schedule_switches) == 2
    assert {e.unique_id for e in schedule_switches} == {
        "entry1_schedule_7",
        "entry1_schedule_9",
    }
    assert {e.name for e in schedule_switches} == {"Weekday Morning", "Weekend"}


async def test_setup_entry_no_schedules_creates_no_schedule_switches(hass):
    g = SimpleNamespace()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = g.ncync_server
    entry.runtime_data.schedules = {}

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert [e for e in added if isinstance(e, CyncLanScheduleSwitch)] == []


async def test_schedule_switch_is_assumed_state_seeded_from_export():
    entity = CyncLanScheduleSwitch("entry1", 7, 3, "Weekday Morning", enabled=True)
    assert entity.is_on is True
    assert entity.assumed_state is True
    assert entity.device_info["identifiers"] == {("cync_lan", "entry1")}


async def test_schedule_switch_turn_on_calls_toggle_automation():
    entity = CyncLanScheduleSwitch("entry1", 7, 3, "Weekday Morning", enabled=False)
    entity.async_write_ha_state = MagicMock()

    with patch(
        "cync_lan.devices.toggle_automation", new=AsyncMock()
    ) as mock_toggle:
        await entity.async_turn_on()

    mock_toggle.assert_awaited_once_with(7, 3, True)
    assert entity.is_on is True


async def test_schedule_switch_turn_off_calls_toggle_automation():
    entity = CyncLanScheduleSwitch("entry1", 7, 3, "Weekday Morning", enabled=True)
    entity.async_write_ha_state = MagicMock()

    with patch(
        "cync_lan.devices.toggle_automation", new=AsyncMock()
    ) as mock_toggle:
        await entity.async_turn_off()

    mock_toggle.assert_awaited_once_with(7, 3, False)
    assert entity.is_on is False


async def test_schedule_switch_restores_last_state(hass):
    entity = CyncLanScheduleSwitch("entry1", 7, 3, "Weekday Morning", enabled=True)
    entity.hass = hass
    entity.entity_id = "switch.test_schedule"

    last_state = MagicMock(state="off")
    with patch.object(entity, "async_get_last_state", AsyncMock(return_value=last_state)):
        await entity.async_added_to_hass()

    assert entity.is_on is False
