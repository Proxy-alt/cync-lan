"""Tests for src/cync_lan/devices.py's new mesh commands and the
send_command/broadcast_control_command refactor.

Non-HA-dependent (imports `cync_lan.devices` directly, same pattern as
test_cloud_api.py), living alongside the rest of the suite so the same
`pytest tests/components/cync_lan/` invocation picks them up.
"""

from __future__ import annotations

import struct
from unittest.mock import AsyncMock, MagicMock, patch

from cync_lan.const import FACTORY_EFFECTS_BYTES, LIGHT_RUN_MODE_EFFECTS
from cync_lan.devices import (
    CyncDevice,
    _EXPERIMENTAL_CMDS_WARNED,
    _warn_experimental_cmd_code,
    broadcast_control_command,
    execute_scene,
)
from cync_lan.packet import PacketBuilder
from cync_lan.structs import GlobalObject


def _fake_node(**overrides):
    node = MagicMock()
    node.id = 5
    node.lp = "test:"
    node.is_sol_lamp = False
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


class _FakeBridgeDevice:
    """Minimal stand-in for a CyncTCPSession, enough for
    broadcast_control_command's TCP-pool loop to run against."""

    def __init__(self):
        self.ready_to_control = True
        self.mitm_mode = False
        self.ip_address = "127.0.0.1"
        self.queue_id = b"\x00\x01\x02\x03"
        self.node = None
        self.messages = MagicMock()
        self.messages.control = {}
        self._ctrl_byte = 0
        self.written = []

    def get_ctrl_msg_id_bytes(self):
        self._ctrl_byte = (self._ctrl_byte + 1) % 256
        return [self._ctrl_byte, 0]

    async def write(self, data: bytes):
        self.written.append(data)
        return True


async def _broadcast_and_capture(**kwargs) -> bytes:
    """Run broadcast_control_command against one fake bridge device and
    return the raw packet bytes it wrote."""
    from cync_lan.structs import ControlMessageCallback

    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])
    m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
    await broadcast_control_command(m_cb=m_cb, lp="test:", **kwargs)
    assert len(fake_bridge.written) == 1
    return fake_bridge.written[0]


async def test_broadcast_control_command_matches_packet_builder():
    """End-to-end: broadcast_control_command's output for a known
    op/cmd_/target/payload matches PacketBuilder's own confirmed control
    packet + outer packet construction directly - this is what
    CyncDevice.send_command's refactor into a thin wrapper depends on
    being correct."""
    payload = struct.pack(">BBBBB", 0x11, 0x02, 1, 0x00, 0x00)  # set_power(1)-shaped
    written = await _broadcast_and_capture(
        op=0xD0, cmd_=0x0D, target_id=5, sub_id=0, payload=payload
    )

    # Reconstruct the expected packet the same way PacketBuilder would,
    # using the same msg_id the fake bridge's first get_ctrl_msg_id_bytes()
    # call returns (1).
    expected_inner = PacketBuilder.build_control_packet(
        msg_id=1, target_id=5, sub_id=0, op_code=0xD0, cmd_code=0x0D, command_payload=payload
    )
    expected_outer = PacketBuilder.build_outer_packet(
        packet_type=0x73, queue_id=b"\x00\x01\x02\x03", inner_packet=expected_inner
    )
    assert written == expected_outer


async def test_broadcast_control_command_no_eligible_connections_is_a_noop():
    from cync_lan.structs import ControlMessageCallback

    g = GlobalObject()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[])
    m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
    # Must not raise.
    await broadcast_control_command(
        op=0xD0, cmd_=0x0D, target_id=5, sub_id=0, payload=b"", m_cb=m_cb, lp="test:"
    )


async def test_light_run_mode_effects_byte_identical_for_shared_presets():
    """set_light_effect's LIGHT_RUN_MODE_EFFECTS must reproduce the exact
    same (modeCode=0x01, index, nonce) as set_lightshow's
    FACTORY_EFFECTS_BYTES for every existing preset name - this extension
    must be wire-identical for current users, not just additive."""
    for name, (index, nonce) in FACTORY_EFFECTS_BYTES.items():
        mode_code, idx2, nonce2 = LIGHT_RUN_MODE_EFFECTS[name]
        assert mode_code == 0x01
        assert idx2 == index
        assert nonce2 == nonce


async def test_set_lightshow_and_set_light_effect_send_identical_payload():
    """Both methods must produce the same wire payload for a preset name
    they share, even though set_lightshow is now a thin wrapper around the
    same _send_light_run_mode helper set_light_effect uses."""
    GlobalObject().mqtt_client = MagicMock()
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_lightshow("rainbow")
    lightshow_call = node.send_command.call_args

    node.send_command.reset_mock()
    await node.set_light_effect("rainbow")
    effect_call = node.send_command.call_args

    assert lightshow_call.args[0] == effect_call.args[0]  # op
    assert lightshow_call.args[1] == effect_call.args[1]  # cmd_
    assert lightshow_call.args[3] == effect_call.args[3]  # payload


async def test_set_fine_brightness_payload_shape():
    GlobalObject().mqtt_client = MagicMock()
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_fine_brightness(50, 2000)

    args = node.send_command.call_args.args
    assert args[0] == 0xE2  # op
    assert args[1] == 0x0F  # predicted cmd_
    payload = args[3]
    assert payload == struct.pack(">BBB", 0x11, 0x02, 0x08) + struct.pack(">HH", 500, 2000)


async def test_set_fine_brightness_rejects_invalid_brightness():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_fine_brightness(101, 1000)
    node.send_command.assert_not_awaited()


async def test_set_fine_brightness_clamps_fade_ms():
    GlobalObject().mqtt_client = MagicMock()
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_fine_brightness(50, 999999)

    payload = node.send_command.call_args.args[3]
    assert payload == struct.pack(">BBB", 0x11, 0x02, 0x08) + struct.pack(">HH", 500, 65535)


async def test_set_indicator_led_payload_shape():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_indicator_led(mode=2, color=1, brightness=80, wifi_disconnect_blink=True)

    args = node.send_command.call_args.args
    assert args[0] == 0xF7  # op
    assert args[1] == 0x0E  # predicted cmd_
    assert args[3] == struct.pack(">BBBBBB", 0x11, 0x02, 0x06, (2 << 4) | 1, 80, 1)


async def test_set_indicator_led_rejects_invalid_inputs():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_indicator_led(mode=5, color=1, brightness=80)
    await node.set_indicator_led(mode=0, color=9, brightness=80)
    await node.set_indicator_led(mode=0, color=0, brightness=0)
    node.send_command.assert_not_awaited()


async def test_set_motion_sensor_settings_wires_into_send_command():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_motion_sensor_settings(setting_type=1, enabled=True)

    args = node.send_command.call_args.args
    assert args[0] == 0xF7  # op
    assert args[1] == 0x13  # predicted cmd_ (corrected from an earlier miscount)
    assert args[3] == CyncDevice._build_motion_sensor_settings_payload(1, enabled=True)


async def test_execute_scene_payload_shape():
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    await execute_scene(5)

    assert len(fake_bridge.written) == 1
    inner_payload = struct.pack(">BBBB", 0x11, 0x02, 5, 0x01)
    expected_inner = PacketBuilder.build_control_packet(
        msg_id=1, target_id=0x00, sub_id=0, op_code=0xEF, cmd_code=0x0C,
        command_payload=inner_payload,
    )
    expected_outer = PacketBuilder.build_outer_packet(
        packet_type=0x73, queue_id=b"\x00\x01\x02\x03", inner_packet=expected_inner
    )
    assert fake_bridge.written[0] == expected_outer


async def test_execute_scene_rejects_out_of_range_id():
    g = GlobalObject()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[])
    await execute_scene(256)
    g.ncync_server.get_dev_tcp_pool.assert_not_awaited()


def test_version_str_preserves_dotted_cloud_string():
    """entity.py's build_device_info reads node.version_str for HA's
    sw_version - it must stay a proper "1.2.3" string, not collapse to
    the lossy int `version` uses internally for wire-protocol comparisons."""
    node = CyncDevice(dev_id=5, fw_version="1.2.3")
    assert node.version == 123
    assert node.version_str == "1.2.3"


def test_version_str_falls_back_to_str_version_when_never_set_as_string():
    node = CyncDevice(dev_id=5)
    node.version = 42  # e.g. set directly as an int somewhere
    assert node.version_str == "42"


def test_version_str_none_when_no_firmware_known():
    node = CyncDevice(dev_id=5)
    assert node.version is None
    assert node.version_str is None


def test_version_str_unaffected_by_empty_or_unknown_firmware():
    node = CyncDevice(dev_id=5, fw_version="")
    assert node.version_str is None

    node2 = CyncDevice(dev_id=5, fw_version="Unknown")
    assert node2.version_str is None


def test_warn_experimental_cmd_code_fires_once_per_name():
    _EXPERIMENTAL_CMDS_WARNED.discard("test_cmd_unique_name")
    import cync_lan.devices as devices_module

    with patch.object(devices_module.logger, "warning") as mock_warn:
        _warn_experimental_cmd_code("lp:", "test_cmd_unique_name")
        _warn_experimental_cmd_code("lp:", "test_cmd_unique_name")
        mock_warn.assert_called_once()
    _EXPERIMENTAL_CMDS_WARNED.discard("test_cmd_unique_name")
