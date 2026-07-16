"""End-to-end verification that entity-translations actually resolves.

Setting `_attr_translation_key` is only half the story - HA resolves the
final display name through the integration's loaded strings.json content
at runtime, which requires real platform setup (unique to this test file;
test_binary_sensor.py's unit tests instantiate entities directly and can't
exercise this path - see its comment on why .name isn't asserted there).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.const import CONF_ACCOUNT_PASSWORD, CONF_ACCOUNT_USERNAME

DOMAIN = "cync_lan"


def _mock_server(node_devices):
    server = MagicMock()
    server.running = False
    server.node_devices = node_devices
    server.host = "0.0.0.0"
    server.port = 23779

    async def _start():
        server.running = True

    server.start = AsyncMock(side_effect=_start)
    server.stop = AsyncMock()
    return server


def _fake_secondary_motion_node():
    node = MagicMock()
    node.id = 5
    node.name = "Hallway Switch"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.metadata = MagicMock(supported=True, characteristics=None)
    node.metadata.model_string = "Some Model"
    node.metadata.capabilities.sensor_device_class = "motion"
    node.metadata.capabilities.dynamic = False
    node.has_motion_sensor = True
    node.is_light = True
    node.is_switch = False
    node.supports_temperature = False
    node.supports_rgb = False
    return node


async def test_secondary_motion_sensor_translated_name_resolves(hass, tmp_path):
    """The full "Hallway Switch Motion" friendly name comes from
    has_entity_name (device name) + translation_key="motion" resolving to
    strings.json's entity.binary_sensor.motion.name ("Motion") - not a
    hardcoded string anywhere in the entity code."""
    cfg_file = tmp_path / "cync_mesh.yaml"
    cfg_file.write_text("devices: {}")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={CONF_ACCOUNT_USERNAME: "user@example.com", CONF_ACCOUNT_PASSWORD: "x"},
        options={"local_port": 23779, "export_refresh_interval": 0},
    )
    entry.add_to_hass(hass)

    node = _fake_secondary_motion_node()
    server = _mock_server({5: node})

    with patch("custom_components.cync_lan._BIND_POLL_INTERVAL", 0.001), patch(
        "custom_components.cync_lan._BIND_TIMEOUT", 0.5
    ), patch("cync_lan.const.CYNC_CONFIG_FILE_PATH", str(cfg_file)), patch(
        "cync_lan.server.nCyncServer", return_value=server
    ), patch("cync_lan.utils.parse_config", new=AsyncMock(return_value={5: node})):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get(f"binary_sensor.hallway_switch_motion")
    assert state is not None, (
        "expected an entity_id derived from the device name + translated "
        f"suffix; actual binary_sensor entities: "
        f"{[s.entity_id for s in hass.states.async_all('binary_sensor')]}"
    )
    assert state.attributes["friendly_name"] == "Hallway Switch Motion"


async def test_grouped_schedule_sensor_placeholder_name_resolves(hass, tmp_path):
    """translation_placeholders is a new mechanism for this codebase
    (sensor.py's disambiguated case, when a device belongs to 2+ groups
    with schedule data) - worth verifying against real loaded strings.json
    content, not just asserting _attr_translation_placeholders is set (see
    test_sensor.py's unit tests for that)."""
    cfg_file = tmp_path / "cync_mesh.yaml"
    cfg_file.write_text("devices: {}")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={CONF_ACCOUNT_PASSWORD: "x", CONF_ACCOUNT_USERNAME: "user@example.com"},
        options={"local_port": 23779, "export_refresh_interval": 0},
    )
    entry.add_to_hass(hass)

    node = _fake_secondary_motion_node()
    server = _mock_server({5: node})

    daytime_slot = {
        "slot_id": 1, "enabled": True, "mode": "simple",
        "start_time": "06:00", "end_time": "08:59",
        "brightness": 100, "cct": 50, "display_name": "Daytime",
    }
    groups = {
        1: {"name": "Parent", "device_ids": [5], "sensor_schedules": {"daytime": daytime_slot}},
        2: {"name": "Subgroup", "device_ids": [5], "sensor_schedules": {"daytime": daytime_slot}},
    }

    with patch("custom_components.cync_lan._BIND_POLL_INTERVAL", 0.001), patch(
        "custom_components.cync_lan._BIND_TIMEOUT", 0.5
    ), patch("cync_lan.const.CYNC_CONFIG_FILE_PATH", str(cfg_file)), patch(
        "cync_lan.server.nCyncServer", return_value=server
    ), patch("cync_lan.utils.parse_config", new=AsyncMock(return_value={5: node})), patch(
        "cync_lan.utils.parse_groups", new=AsyncMock(return_value=groups)
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # unique_id is entry_id_dev_id_schedule_groupid_slotname - group_id=1 (Parent)
    entity_id = f"sensor.hallway_switch_parent_daytime_schedule"
    state = hass.states.get(entity_id)
    assert state is not None, (
        f"actual sensor entities: {[s.entity_id for s in hass.states.async_all('sensor')]}"
    )
    assert state.attributes["friendly_name"] == "Hallway Switch Parent Daytime schedule"
