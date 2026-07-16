"""Unit tests for CyncLanBridge - the adapter devices.py talks to in place
of the real MQTT client. Pure state-tracking logic, tested directly against
the dataclass/dispatcher behavior rather than a full HA entity platform."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.cync_lan.bridge import CyncLanBridge


@pytest.fixture
def bridge(hass):
    return CyncLanBridge(hass, "test_entry")


async def test_parse_entity_state_updates_and_dispatches(bridge, hass):
    from cync_lan.structs import EntityState

    calls = []
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    async_dispatcher_connect(
        hass, "cync_lan_update_test_entry_5", lambda: calls.append(True)
    )

    state = EntityState(name="Test", dev_id=5, power=1, brightness=80)
    await bridge.parse_entity_state(state)
    await hass.async_block_till_done()

    assert bridge.get_state(5).power == 1
    assert bridge.get_state(5).brightness == 80
    assert bridge.is_online(5) is True
    assert calls == [True]


async def test_publish_motion_state_tracks_per_device(bridge):
    node = MagicMock(id=42)
    await bridge.publish_motion_state(node, True)
    assert bridge.get_motion(42) is True

    await bridge.publish_motion_state(node, False)
    assert bridge.get_motion(42) is False
    # unrelated device unaffected
    assert bridge.get_motion(99) is None


async def test_pub_online_tracks_availability(bridge):
    await bridge.pub_online(7, False)
    assert bridge.is_online(7) is False
    await bridge.pub_online(7, True)
    assert bridge.is_online(7) is True


async def test_pub_online_survives_devices_py_calling_convention(bridge):
    """Regression test: devices.py's CyncDevice.online setter calls this as
    `asyncio.get_running_loop().create_task(g.mqtt_client.pub_online(...))`
    directly - create_task() requires a coroutine, so if pub_online is ever
    accidentally changed back to a plain `def`, this raises
    'a coroutine was expected, got None' at runtime. Every other test in
    this file calls pub_online with a bare `await`, which would silently
    pass even against a non-async version (the call just wouldn't do
    anything) - only exercising the real create_task() wrapping catches it.
    """
    import asyncio

    task = asyncio.get_running_loop().create_task(bridge.pub_online(13, False))
    await task
    assert bridge.is_online(13) is False


async def test_online_transition_is_logged_only_on_change(bridge, caplog):
    """log-when-unavailable (silver): a real transition logs, a repeated
    call with the same value doesn't (would be noise - fires on every
    status packet)."""
    import logging

    caplog.set_level(logging.INFO)

    await bridge.pub_online(7, False)  # default True -> False: logs
    assert "Cync device 7 is now offline" in caplog.text

    caplog.clear()
    await bridge.pub_online(7, False)  # no change: silent
    assert caplog.text == ""

    await bridge.pub_online(7, True)  # False -> True: logs
    assert "Cync device 7 is now online" in caplog.text


async def test_parse_entity_state_logs_online_transition(bridge, caplog):
    import logging

    from cync_lan.structs import EntityState

    caplog.set_level(logging.INFO)
    await bridge.pub_online(9, False)
    caplog.clear()

    await bridge.parse_entity_state(EntityState(name="x", dev_id=9))
    assert "Cync device 9 is now online" in caplog.text


async def test_publish_motion_state_logs_online_transition(bridge, caplog):
    import logging

    caplog.set_level(logging.INFO)
    await bridge.pub_online(11, False)
    caplog.clear()

    node = MagicMock(id=11)
    await bridge.publish_motion_state(node, True)
    assert "Cync device 11 is now online" in caplog.text


async def test_update_callbacks_mutate_existing_state(bridge):
    from cync_lan.structs import EntityState

    node = MagicMock(id=3)
    await bridge.parse_entity_state(EntityState(name="x", dev_id=3, brightness=10))

    await bridge.update_brightness(node, 55)
    assert bridge.get_state(3).brightness == 55

    await bridge.update_rgb(node, (10, 20, 30))
    state = bridge.get_state(3)
    assert (state.red, state.green, state.blue) == (10, 20, 30)


async def test_publish_records_raw_topic(bridge):
    await bridge.publish("bridge/tcp_server/running", b"ON")
    assert bridge.raw_topics["bridge/tcp_server/running"] == b"ON"


async def test_mitm_and_lifecycle_methods_are_safe_noops(bridge):
    node = MagicMock(id=1)
    await bridge.add_mitm_button(node)
    await bridge.remove_mitm_button(node)
    await bridge.start()
    await bridge.stop()
    assert bridge.get_startup_topic_state_sync("anything") is None


async def test_parse_entity_state_unique_id_includes_sub_id(bridge):
    from cync_lan.structs import EntityState

    calls = []
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    async_dispatcher_connect(
        bridge.hass, "cync_lan_update_test_entry_6_2", lambda: calls.append(True)
    )

    await bridge.parse_entity_state(EntityState(name="x", dev_id=6, sub_id=2, power=1))
    await bridge.hass.async_block_till_done()

    assert bridge.get_state(6, sub_id=2).power == 1
    assert calls == [True]


async def test_update_entity_power_updates_existing_state_and_sub_id_topic(bridge):
    from cync_lan.structs import EntityState

    node = MagicMock(id=4)
    node.name = "Test Device"  # MagicMock(name=...) sets the mock's own repr, not this
    await bridge.parse_entity_state(EntityState(name="x", dev_id=4, sub_id=1, power=0))

    await bridge.update_entity_power(node, 1, 1)
    assert bridge.get_state(4, sub_id=1).power == 1

    # sub_id=0: no existing state yet for this bucket - must not just avoid
    # raising, the state must actually become readable afterward. Regression
    # test: the original implementation only mutated an *existing*
    # EntityState (`if bucket.entity_state is not None`), so a device whose
    # state was never seeded by an unsolicited mesh broadcast would have
    # every command ack silently no-op on the real state forever - the
    # entity would still re-render (dispatcher fires regardless) but
    # is_on/brightness/etc. stayed stuck at None, which HA's frontend shows
    # as separate "Turn On"/"Turn Off" actions instead of a toggle
    # reflecting real state (reported by a real user after the first
    # command they ever sent).
    await bridge.update_entity_power(node, 1, 0)
    assert bridge.get_state(4, sub_id=0).power == 1


async def test_update_callbacks_create_state_when_none_exists(bridge):
    """Same regression as test_update_entity_power_...: every command-ack
    callback must be able to seed a fresh EntityState, not just mutate one
    that already exists."""
    from cync_lan.structs import EntityState, FanSpeed

    def _node(dev_id: int) -> MagicMock:
        # MagicMock(name=...) sets the mock's own repr, not a `.name`
        # attribute - must be assigned after construction instead.
        node = MagicMock(id=dev_id)
        node.name = f"Fresh Device {dev_id}"
        return node

    assert bridge.get_state(20) is None

    await bridge.update_brightness(_node(20), 77)
    assert bridge.get_state(20).brightness == 77

    await bridge.update_temperature(_node(21), 3500)
    assert bridge.get_state(21).temperature == 3500

    await bridge.update_rgb(_node(22), (5, 6, 7))
    assert (
        bridge.get_state(22).red,
        bridge.get_state(22).green,
        bridge.get_state(22).blue,
    ) == (5, 6, 7)

    await bridge.update_fan_percent(_node(23), 33)
    assert bridge.get_state(23).brightness == 33

    await bridge.update_fan_speed(_node(24), FanSpeed.LOW)
    assert bridge.get_state(24).brightness == FanSpeed.LOW.to_perc()


async def test_update_callbacks_handle_node_with_no_name_yet(bridge):
    """node.name is Optional[str] on the real CyncDevice (None until
    identity resolves) - a command ack racing ahead of that must not crash
    with a pydantic ValidationError when seeding the first EntityState."""
    node = MagicMock(id=30)
    node.name = None

    await bridge.update_entity_power(node, 1, 0)
    assert bridge.get_state(30).power == 1


async def test_update_temperature_updates_existing_state(bridge):
    from cync_lan.structs import EntityState

    node = MagicMock(id=8)
    await bridge.parse_entity_state(EntityState(name="x", dev_id=8, temperature=2700))

    await bridge.update_temperature(node, 4000)
    assert bridge.get_state(8).temperature == 4000


async def test_update_fan_percent_and_speed_update_brightness(bridge):
    from cync_lan.structs import EntityState, FanSpeed

    node = MagicMock(id=9)
    await bridge.parse_entity_state(EntityState(name="x", dev_id=9, brightness=0))

    await bridge.update_fan_percent(node, 42)
    assert bridge.get_state(9).brightness == 42

    await bridge.update_fan_speed(node, FanSpeed.HIGH)
    assert bridge.get_state(9).brightness == FanSpeed.HIGH.to_perc()


async def test_report_unknown_device_id_noop_without_callback(bridge):
    """No callback registered (e.g. bridge constructed without one) - must
    not raise, must not do anything."""
    bridge.report_unknown_device_id(50)  # no exception


async def test_report_unknown_device_id_requires_repeat_sightings(hass):
    from custom_components.cync_lan.bridge import CyncLanBridge

    triggered = []

    async def _on_unknown():
        triggered.append(True)

    bridge = CyncLanBridge(hass, "entry1", on_unknown_device=_on_unknown)

    for _ in range(bridge.UNKNOWN_DEVICE_SEEN_THRESHOLD - 1):
        bridge.report_unknown_device_id(50)
    await hass.async_block_till_done()
    assert triggered == []  # not enough sightings yet

    bridge.report_unknown_device_id(50)  # this one crosses the threshold
    await hass.async_block_till_done()
    assert triggered == [True]


async def test_report_unknown_device_id_different_dev_ids_tracked_separately(hass):
    from custom_components.cync_lan.bridge import CyncLanBridge

    triggered = []

    async def _on_unknown():
        triggered.append(True)

    bridge = CyncLanBridge(hass, "entry1", on_unknown_device=_on_unknown)

    # one sighting each of two different dev_ids - neither alone crosses
    # the per-dev_id threshold
    bridge.report_unknown_device_id(50)
    bridge.report_unknown_device_id(51)
    await hass.async_block_till_done()
    assert triggered == []


async def test_report_unknown_device_id_cooldown_blocks_second_trigger(hass):
    from unittest.mock import patch

    from custom_components.cync_lan.bridge import CyncLanBridge

    triggered = []

    async def _on_unknown():
        triggered.append(True)

    bridge = CyncLanBridge(hass, "entry1", on_unknown_device=_on_unknown)

    for _ in range(bridge.UNKNOWN_DEVICE_SEEN_THRESHOLD):
        bridge.report_unknown_device_id(50)
    await hass.async_block_till_done()
    assert triggered == [True]

    # a second, different device also crosses its own threshold immediately
    # afterward - still within the cooldown window, so no second trigger
    for _ in range(bridge.UNKNOWN_DEVICE_SEEN_THRESHOLD):
        bridge.report_unknown_device_id(60)
    await hass.async_block_till_done()
    assert triggered == [True]

    # once the cooldown has elapsed, a new confirmed device does trigger
    with patch(
        "custom_components.cync_lan.bridge.CyncLanBridge.UNKNOWN_DEVICE_TRIGGER_COOLDOWN_SECONDS",
        0,
    ):
        for _ in range(bridge.UNKNOWN_DEVICE_SEEN_THRESHOLD):
            bridge.report_unknown_device_id(70)
        await hass.async_block_till_done()
    assert triggered == [True, True]


async def test_report_unknown_device_id_swallows_callback_exceptions(hass, caplog):
    import logging

    from custom_components.cync_lan.bridge import CyncLanBridge

    async def _on_unknown():
        raise RuntimeError("cloud export failed")

    caplog.set_level(logging.ERROR)
    bridge = CyncLanBridge(hass, "entry1", on_unknown_device=_on_unknown)

    for _ in range(bridge.UNKNOWN_DEVICE_SEEN_THRESHOLD):
        bridge.report_unknown_device_id(50)
    await hass.async_block_till_done()  # must not raise / crash the test

    assert "Error handling confirmed unknown device" in caplog.text


async def test_mark_app_mesh_active_auto_expires(bridge, hass):
    """Must actually clear after `timeout`, not just latch True forever -
    that's what makes this an occupancy signal rather than a one-shot flag.
    Mirrors cync_lan.mqtt_client.MQTTClient.mark_app_mesh_active's behavior."""
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    await bridge.mark_app_mesh_active(timeout=10)
    assert bridge._get(-1).app_mesh_active is True

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    assert bridge._get(-1).app_mesh_active is False


async def test_mark_app_mesh_active_resets_expiry_timer_on_repeat_calls(bridge, hass):
    """A second burst before the first timeout fires should push the
    expiry out, not leave two competing timers."""
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    await bridge.mark_app_mesh_active(timeout=10)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()
    await bridge.mark_app_mesh_active(timeout=10)  # resets the clock

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()
    assert bridge._get(-1).app_mesh_active is True  # still active, not yet 10s since reset

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    assert bridge._get(-1).app_mesh_active is False


async def test_mark_app_wifi_active_auto_expires(bridge, hass):
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    await bridge.mark_app_wifi_active(timeout=10)
    assert bridge._get(-1).app_wifi_active is True

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    assert bridge._get(-1).app_wifi_active is False


async def test_get_indicator_led_returns_defaults_before_anything_is_set(bridge):
    state = bridge.get_indicator_led(5)
    assert state.mode == "normal"
    assert state.color == "white"
    assert state.brightness == 100
    assert state.wifi_disconnect_blink is False


async def test_set_indicator_led_field_merges_and_resends_full_state(bridge):
    """The single most important regression test for this feature:
    CyncDevice.set_indicator_led() sends mode/color/brightness/
    wifi_disconnect_blink as one atomic mesh command, so changing just one
    field via one HA entity must still resend the other 3's last-known
    (or default) values, not just the field that changed."""
    node = MagicMock(id=5)
    node.set_indicator_led = AsyncMock()

    await bridge.set_indicator_led_field(node, brightness=42)

    node.set_indicator_led.assert_awaited_once_with(
        mode=2,  # "normal" default
        color=0,  # "white" default
        brightness=42,
        wifi_disconnect_blink=False,
    )
    assert bridge.get_indicator_led(5).brightness == 42

    # A second, independent field change must resend the FIRST change too,
    # not reset it back to the default.
    await bridge.set_indicator_led_field(node, color="red")
    node.set_indicator_led.assert_awaited_with(
        mode=2, color=1, brightness=42, wifi_disconnect_blink=False
    )


async def test_set_indicator_led_field_dispatches_shared_signal(bridge, hass):
    from custom_components.cync_lan.bridge import signal_indicator_led_update
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    calls = []
    async_dispatcher_connect(
        hass, signal_indicator_led_update("test_entry", 5), lambda: calls.append(True)
    )

    node = MagicMock(id=5)
    node.set_indicator_led = AsyncMock()
    await bridge.set_indicator_led_field(node, mode="always_on")
    await hass.async_block_till_done()

    assert calls == [True]


async def test_seed_indicator_led_field_updates_cache_without_commanding_hardware(bridge):
    """Restore-on-startup path: must update the cache and notify sibling
    entities WITHOUT ever calling node.set_indicator_led - otherwise every
    HA restart would silently re-issue live mesh commands (including
    toggling wifi_disconnect_blink) to every device just from
    RestoreEntity/RestoreNumber seeding, even on hardware that never
    actually changed."""
    node = MagicMock(id=5)
    node.set_indicator_led = AsyncMock()

    bridge.seed_indicator_led_field(node, brightness=77)

    assert bridge.get_indicator_led(5).brightness == 77
    node.set_indicator_led.assert_not_awaited()


async def test_seed_indicator_led_field_dispatches_shared_signal(bridge, hass):
    from custom_components.cync_lan.bridge import signal_indicator_led_update
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    calls = []
    async_dispatcher_connect(
        hass, signal_indicator_led_update("test_entry", 5), lambda: calls.append(True)
    )

    node = MagicMock(id=5)
    bridge.seed_indicator_led_field(node, mode="always_off")
    await hass.async_block_till_done()

    assert calls == [True]


async def test_mark_app_wifi_active_independent_expiry_from_mesh_active(bridge, hass):
    """Separate timers for separate signals - one expiring must not touch
    the other's state."""
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    await bridge.mark_app_mesh_active(timeout=5)
    await bridge.mark_app_wifi_active(timeout=100)

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()
    assert bridge._get(-1).app_mesh_active is False
    assert bridge._get(-1).app_wifi_active is True
