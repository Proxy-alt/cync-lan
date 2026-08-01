"""Tests for the sensor platform: motion-sensor native schedule slots."""

from __future__ import annotations

from types import SimpleNamespace
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.sensor import (
    CyncLanIpAddressSensor,
    CyncLanMotionScheduleSensor,
    CyncLanRelaySourceSensor,
    async_setup_entry,
)


def _fake_node(**overrides):
    node = MagicMock()
    node.id = 5
    node.name = "Test Sensor"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.has_wifi = True
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
    """No motion-schedule sensors get created for these 3 devices, but the
    connection-diagnostic sensor (IP address, since has_wifi=True here)
    is unconditional for every supported device regardless of motion
    sensor/schedule status - 2 of the 3 devices are supported."""
    g = SimpleNamespace()
    unsupported = _fake_node(metadata=None)
    no_motion = _fake_node(id=1, has_motion_sensor=False)
    no_schedule = _fake_node(id=2)
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {0: unsupported, 1: no_motion, 2: no_schedule}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = g.ncync_server
    entry.runtime_data.groups = {}

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    # Count only the type under test - the platform also adds last-seen,
    # device-id and the bridge's connected-devices sensor.
    ip_sensors = [e for e in added if isinstance(e, CyncLanIpAddressSensor)]
    assert len(ip_sensors) == 2


async def test_setup_entry_creates_one_sensor_per_slot(hass):
    g = SimpleNamespace()
    node = _fake_node()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {5: node}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = g.ncync_server
    entry.runtime_data.groups = {
        32770: {
            "name": "Utility Room",
            "device_ids": [5],
            "sensor_schedules": {"daytime": _DAYTIME_SLOT, "sleep": _DISABLED_SLOT},
        }
    }

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    # One schedule sensor per decoded slot...
    schedule_sensors = [e for e in added if isinstance(e, CyncLanMotionScheduleSensor)]
    assert {e.unique_id for e in schedule_sensors} == {
        "entry1_5_schedule_32770_daytime",
        "entry1_5_schedule_32770_sleep",
    }
    # ...alongside the connection diagnostic this WiFi device also gets.
    assert any(e.unique_id == "entry1_5_ip_address" for e in added)


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


async def test_setup_entry_creates_ip_address_sensor_for_wifi_devices(hass):
    g = SimpleNamespace()
    node = _fake_node(has_wifi=True, bt_only=False)
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {5: node}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = g.ncync_server
    entry.runtime_data.groups = {}

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    ip_sensors = [e for e in added if isinstance(e, CyncLanIpAddressSensor)]
    assert len(ip_sensors) == 1
    assert ip_sensors[0].unique_id == "entry1_5_ip_address"
    # a WiFi device gets no relay-source sensor
    assert not any(isinstance(e, CyncLanRelaySourceSensor) for e in added)


async def test_setup_entry_creates_relay_source_sensor_for_bt_only_devices(hass):
    g = SimpleNamespace()
    node = _fake_node(has_wifi=False, bt_only=True)
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {5: node}

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = g.ncync_server
    entry.runtime_data.groups = {}

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    relay_sensors = [e for e in added if isinstance(e, CyncLanRelaySourceSensor)]
    assert len(relay_sensors) == 1
    assert relay_sensors[0].unique_id == "entry1_5_relay_source"
    assert not any(isinstance(e, CyncLanIpAddressSensor) for e in added)


def test_ip_address_sensor_reads_tcp_session_ip():
    bridge = MagicMock()
    node = _fake_node()
    node.tcp_session = MagicMock(ip_address="192.168.1.50")
    entity = CyncLanIpAddressSensor(bridge, "entry1", node)
    assert entity.native_value == "192.168.1.50"
    assert entity.translation_key == "diagnostic_ip_address"
    assert entity.entity_category == "diagnostic"


def test_ip_address_sensor_none_when_no_active_session():
    bridge = MagicMock()
    node = _fake_node()
    node.tcp_session = None
    entity = CyncLanIpAddressSensor(bridge, "entry1", node)
    assert entity.native_value is None


def test_relay_source_sensor_reads_relaying_devices_name():
    bridge = MagicMock()
    node = _fake_node()
    relay_session = MagicMock()
    relay_session.node.name = "Living Room Lamp"
    node.relay_source = relay_session
    entity = CyncLanRelaySourceSensor(bridge, "entry1", node)
    assert entity.native_value == "Living Room Lamp"
    assert entity.translation_key == "diagnostic_relay_source"
    assert entity.entity_category == "diagnostic"


def test_relay_source_sensor_none_when_never_relayed():
    bridge = MagicMock()
    node = _fake_node()
    node.relay_source = None
    entity = CyncLanRelaySourceSensor(bridge, "entry1", node)
    assert entity.native_value is None


# ---------------------------------------------------------------------------
# per-device diagnostics
# ---------------------------------------------------------------------------


async def test_last_seen_starts_empty_and_records_inbound_evidence(hass):
    from cync_lan.structs import EntityState

    from custom_components.cync_lan.sensor import CyncLanLastSeenSensor

    bridge = CyncLanBridge(hass, "entry1")
    sensor = CyncLanLastSeenSensor(bridge, "entry1", _fake_node())

    # Nothing heard yet: unavailable rather than a misleading timestamp.
    assert sensor.native_value is None
    assert sensor.available is False

    await bridge.parse_entity_state(EntityState(name="x", dev_id=5, power=1))

    assert sensor.native_value is not None
    assert sensor.available is True


async def test_last_seen_survives_the_device_going_offline(hass):
    """The entity exists to answer "offline since when?", so it must keep
    reporting after the device stops responding - unlike every other entity,
    which correctly goes unavailable."""
    from cync_lan.structs import EntityState

    from custom_components.cync_lan.sensor import CyncLanLastSeenSensor

    bridge = CyncLanBridge(hass, "entry1")
    sensor = CyncLanLastSeenSensor(bridge, "entry1", _fake_node())
    await bridge.parse_entity_state(EntityState(name="x", dev_id=5, power=1))
    seen_at = sensor.native_value

    await bridge.pub_online(5, False)

    assert bridge.is_online(5) is False
    assert sensor.available is True
    assert sensor.native_value == seen_at


async def test_last_seen_is_not_advanced_by_going_offline(hass):
    """Only inbound evidence counts. An offline push must not look like the
    device just checked in."""
    from custom_components.cync_lan.sensor import CyncLanLastSeenSensor

    bridge = CyncLanBridge(hass, "entry1")
    sensor = CyncLanLastSeenSensor(bridge, "entry1", _fake_node())

    await bridge.pub_online(5, False)

    assert sensor.native_value is None


async def test_device_id_sensor_reports_the_mesh_id_and_stays_available(hass):
    from custom_components.cync_lan.sensor import CyncLanDeviceIdSensor

    bridge = CyncLanBridge(hass, "entry1")
    sensor = CyncLanDeviceIdSensor(bridge, "entry1", _fake_node())

    await bridge.pub_online(5, False)

    assert sensor.native_value == 5
    # A config fact, not a device fact - readable even when offline, which is
    # exactly when someone is filing a report that needs it.
    assert sensor.available is True
    assert sensor.entity_registry_enabled_default is False


async def test_connected_devices_sensor_counts_sessions(hass):
    from custom_components.cync_lan.sensor import CyncLanConnectedDevicesSensor

    runtime_data = SimpleNamespace(
        ncync_server=SimpleNamespace(tcp_connections={"10.0.0.1": object()})
    )
    sensor = CyncLanConnectedDevicesSensor("entry1", runtime_data)

    assert sensor.native_value == 1


async def test_connected_devices_sensor_reports_zero_when_nothing_connected(hass):
    """Zero is the signature of DNS redirection not being in place - the most
    common setup failure, otherwise only surfaced by a repair issue that waits
    ten minutes."""
    from custom_components.cync_lan.sensor import CyncLanConnectedDevicesSensor

    runtime_data = SimpleNamespace(ncync_server=SimpleNamespace(tcp_connections={}))
    sensor = CyncLanConnectedDevicesSensor("entry1", runtime_data)

    assert sensor.native_value == 0


async def test_connected_devices_sensor_degrades_instead_of_raising(hass):
    from custom_components.cync_lan.sensor import CyncLanConnectedDevicesSensor

    sensor = CyncLanConnectedDevicesSensor("entry1", SimpleNamespace())

    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# hub query sensors
# ---------------------------------------------------------------------------


def _query_entry(hass, experimental: bool):
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {"enable_experimental": experimental}
    entry.runtime_data.bridge = CyncLanBridge(hass, "entry1")
    entry.runtime_data.ncync_server = MagicMock()
    entry.runtime_data.ncync_server.node_devices = {}
    entry.runtime_data.groups = {}
    return entry


@pytest.mark.parametrize("experimental", [False, True])
async def test_hub_query_sensors_follow_the_experimental_gate(hass, experimental):
    """Read-only, but they still put a command on the mesh and their reply
    channel is unconfirmed - so they sit behind the same gate."""
    from custom_components.cync_lan.sensor import (
        CyncLanHubClockSensor,
        CyncLanHubFirmwareSensor,
    )

    added = []
    await async_setup_entry(hass, _query_entry(hass, experimental), lambda e: added.extend(e))

    present = any(
        isinstance(e, (CyncLanHubFirmwareSensor, CyncLanHubClockSensor)) for e in added
    )
    assert present is experimental


async def test_hub_firmware_sensor_reports_version_and_attributes(hass):
    from custom_components.cync_lan.sensor import CyncLanHubFirmwareSensor

    sensor = CyncLanHubFirmwareSensor("entry1")
    with patch(
        "cync_lan.devices.query_hub_info",
        new=AsyncMock(return_value={"firmware_version": "1.2.3", "mac": "AABB", "setup_code": "X1"}),
    ):
        await sensor.async_update()

    assert sensor.native_value == "1.2.3"
    assert sensor.extra_state_attributes["setup_code"] == "X1"


async def test_hub_clock_sensor_returns_an_aware_datetime(hass):
    """SensorDeviceClass.TIMESTAMP rejects naive datetimes, and the reply on
    this path carries no timezone."""
    import datetime as _dt

    from custom_components.cync_lan.sensor import CyncLanHubClockSensor

    sensor = CyncLanHubClockSensor("entry1")
    with patch(
        "cync_lan.devices.query_device_time",
        new=AsyncMock(return_value=_dt.datetime(2026, 7, 25, 14, 30, 5)),
    ):
        await sensor.async_update()

    assert sensor.native_value is not None
    assert sensor.native_value.tzinfo is not None


async def test_hub_query_sensors_keep_their_value_on_timeout(hass):
    """The reply channel is unconfirmed, so an occasional miss is expected -
    it must not look like the hub vanished."""
    from custom_components.cync_lan.sensor import CyncLanHubFirmwareSensor

    sensor = CyncLanHubFirmwareSensor("entry1")
    with patch(
        "cync_lan.devices.query_hub_info",
        new=AsyncMock(return_value={"firmware_version": "1.2.3", "mac": "A", "setup_code": "B"}),
    ):
        await sensor.async_update()
    with patch("cync_lan.devices.query_hub_info", new=AsyncMock(return_value=None)):
        await sensor.async_update()

    assert sensor.native_value == "1.2.3"


async def test_hub_query_sensors_do_not_use_ha_polling(hass):
    """These were `should_poll = True` with no interval set anywhere, which
    means HA's 30-second default. Each poll puts a real command on the mesh
    and blocks up to 10s on a reply that may never come - on real hardware
    that was a timeout warning every 30 seconds, all day."""
    from custom_components.cync_lan.sensor import (
        HUB_QUERY_SCAN_INTERVAL,
        CyncLanHubClockSensor,
        CyncLanHubFirmwareSensor,
    )

    for sensor in (CyncLanHubFirmwareSensor("entry1"), CyncLanHubClockSensor("entry1")):
        assert sensor.should_poll is False

    # Long enough that it cannot be HA's default back by another name.
    assert HUB_QUERY_SCAN_INTERVAL.total_seconds() >= 300


async def test_hub_query_sensor_refreshes_on_its_own_interval(hass):
    """Self-timed, so the interval has to actually be wired to a refresh."""
    from custom_components.cync_lan.sensor import (
        HUB_QUERY_SCAN_INTERVAL,
        CyncLanHubClockSensor,
    )

    sensor = CyncLanHubClockSensor("entry1")
    with patch(
        "custom_components.cync_lan.sensor.async_track_time_interval"
    ) as track:
        with patch.object(sensor, "hass", hass, create=True), patch.object(
            sensor, "async_on_remove", MagicMock(), create=True
        ), patch.object(sensor, "_async_refresh", AsyncMock()):
            await sensor.async_added_to_hass()

    assert track.call_count == 1
    assert track.call_args[0][2] == HUB_QUERY_SCAN_INTERVAL
