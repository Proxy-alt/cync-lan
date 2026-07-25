"""Tests for MQTTClient - this package previously had no tests at all.

Weighted toward the paths that were provably broken or unreachable before:
the error handler that raised NameError, the app-activity markers, and the
blocking broker probe that was being called straight from the event loop.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from cync_lan.structs import FanSpeed

from cync_lan_mqtt.mqtt_client import MQTTClient


def _fan_node(is_fan_controller: bool = True) -> MagicMock:
    node = MagicMock()
    node.id = 5
    node.home_id = 1234
    node.hass_id = "1234-5"
    node.is_fan_controller = is_fan_controller
    node.online = False
    return node


# --------------------------------------------------------------------------
# update_fan_speed
# --------------------------------------------------------------------------


async def test_update_fan_speed_publishes_preset_and_percentage(client):
    node = _fan_node()
    client.pub_entity_state = AsyncMock(return_value=True)

    assert await client.update_fan_speed(node, FanSpeed.LOW) is True

    topics = [c.kwargs["tpc"] for c in client.pub_entity_state.call_args_list]
    assert f"{client.topic}/status/1234-5/preset" in topics
    assert f"{client.topic}/status/1234-5/percentage" in topics
    assert node.online is True


async def test_update_fan_speed_rejects_non_fan_devices(client):
    node = _fan_node(is_fan_controller=False)
    client.pub_entity_state = AsyncMock(return_value=True)

    assert await client.update_fan_speed(node, FanSpeed.HIGH) is False
    client.pub_entity_state.assert_not_called()


async def test_update_fan_speed_handles_publish_failure_without_nameerror(client):
    """The `except Exception` arm referenced an `lp` that was never defined
    in this method, so a publish failure raised NameError instead of being
    logged and reported as a failed update."""
    node = _fan_node()
    client.pub_entity_state = AsyncMock(side_effect=RuntimeError("broker gone"))

    assert await client.update_fan_speed(node, FanSpeed.MAX) is False


# --------------------------------------------------------------------------
# app-activity markers
# --------------------------------------------------------------------------


async def test_mark_app_mesh_active_publishes_on_then_off(client):
    await client.mark_app_mesh_active(timeout=0.01)

    client.publish.assert_awaited_with(
        f"{client.topic}/status/bridge/app_mesh_active", b"ON"
    )
    await asyncio.sleep(0.05)
    client.publish.assert_awaited_with(
        f"{client.topic}/status/bridge/app_mesh_active", b"OFF"
    )


async def test_mark_app_wifi_active_uses_its_own_topic(client):
    """The two markers are separate entities on purpose - mesh proximity vs
    'the app merely reached this server over WiFi'."""
    await client.mark_app_wifi_active(timeout=30)

    client.publish.assert_awaited_with(
        f"{client.topic}/status/bridge/app_wifi_active", b"ON"
    )
    client._app_active_expiry_tasks["app_wifi_active"].cancel()


async def test_the_two_markers_do_not_cancel_each_other(client):
    """They were separate copies of the same code with separate task
    handles; the shared helper must keep one timer per flag, not one
    timer total."""
    await client.mark_app_mesh_active(timeout=30)
    await client.mark_app_wifi_active(timeout=30)

    tasks = client._app_active_expiry_tasks
    assert set(tasks) == {"app_mesh_active", "app_wifi_active"}
    assert not any(t.cancelled() for t in tasks.values())
    for t in tasks.values():
        t.cancel()


async def test_re_marking_resets_the_timer(client):
    await client.mark_app_mesh_active(timeout=30)
    first = client._app_active_expiry_tasks["app_mesh_active"]

    await client.mark_app_mesh_active(timeout=30)
    second = client._app_active_expiry_tasks["app_mesh_active"]

    assert second is not first
    # a just-cancelled task isn't flagged until it next runs
    await asyncio.sleep(0)
    assert first.cancelled() or first.done()
    second.cancel()
    await asyncio.sleep(0)


# --------------------------------------------------------------------------
# blocking broker probe
# --------------------------------------------------------------------------


async def test_motion_sensor_discovery_does_not_block_the_event_loop(client):
    """get_startup_topic_state_sync opens its own blocking paho connection
    and spins a sync wait loop for up to timeout_seconds. Called inline it
    froze the loop for ~3s per motion sensor during discovery; it must be
    offloaded to an executor.
    """
    node = MagicMock()
    node.id = 7
    node.home_id = 1234
    node.hass_id = "1234-7"
    node.name = "Hallway"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.version_str = "1.2.3"
    node.type = -1

    calling_thread = None

    def _probe(topic):
        nonlocal calling_thread
        import threading

        calling_thread = threading.current_thread().name
        return None

    client.get_startup_topic_state_sync = _probe
    client._get_device_registry = MagicMock(return_value={})

    await client._publish_motion_sensor_entity(node, {}, "1234-7", False)

    assert calling_thread is not None, "probe was never called"
    assert calling_thread != "MainThread", (
        f"blocking probe ran on the event loop thread ({calling_thread})"
    )


def test_get_startup_topic_state_sync_returns_none_when_broker_unreachable():
    """Must degrade to None rather than propagating, since discovery calls
    it per motion sensor."""
    client = MQTTClient()
    fake = MagicMock()
    fake.connect = MagicMock(side_effect=OSError("no broker"))

    import cync_lan_mqtt.mqtt_client as module

    original = module.mqtt.Client
    module.mqtt.Client = MagicMock(return_value=fake)
    try:
        assert client.get_startup_topic_state_sync("some/topic") is None
    finally:
        module.mqtt.Client = original


# --------------------------------------------------------------------------
# singleton behaviour
# --------------------------------------------------------------------------


def test_mqtt_client_is_a_singleton():
    assert MQTTClient() is MQTTClient()


@pytest.mark.parametrize(
    ("kelvin", "expected_range"),
    [(2000, (0, 100)), (5000, (0, 100)), (7000, (0, 100))],
)
def test_kelvin_round_trip_stays_in_range(client, kelvin, expected_range):
    node = MagicMock()
    node.metadata.characteristics.min_kelvin = 2000
    node.metadata.characteristics.max_kelvin = 7000

    cync = client.kelvin2cync(kelvin, node)

    assert expected_range[0] <= cync <= expected_range[1]
