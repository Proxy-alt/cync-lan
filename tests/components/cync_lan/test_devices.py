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
    _warn_experimental_group_targeting,
    broadcast_control_command,
    delete_scene,
    delete_schedule,
    execute_scene,
    set_group_power,
    toggle_automation,
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

    args, kwargs = node.send_command.call_args
    assert args[0] == 0x8E  # op - real mesh-relay op, not the misread 0xF7
    assert args[1] == 0x0E  # predicted cmd_
    assert args[3] == struct.pack(">BBBBBBB", 0xF7, 0x11, 0x02, 0x06, (2 << 4) | 1, 80, 1)
    assert kwargs["repeat_op_code"] is False


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

    args, kwargs = node.send_command.call_args
    assert args[0] == 0x8E  # op - real mesh-relay op, not the misread 0xF7
    assert args[1] == 0x13  # predicted cmd_ (corrected from an earlier miscount)
    assert args[3] == struct.pack(">B", 0xF7) + CyncDevice._build_motion_sensor_settings_payload(
        1, enabled=True
    )
    assert kwargs["repeat_op_code"] is False


async def test_set_motion_sensor_schedule_cct_payload_shape():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_motion_sensor_schedule(
        slot_id=1,  # Daytime
        mode=3,  # simple
        start_hour=6, start_minute=30, end_hour=18, end_minute=0,
        brightness=80,
        cct=50,
    )

    args, kwargs = node.send_command.call_args
    assert args[0] == 0x8E  # op - mesh-relay, not the misread 0xF7
    assert args[1] == 0x14  # predicted cmd_ (7 + 13-byte payload)
    flags = 1 | 0x10  # slot_id=1, SIMPLE=0x10, no rgb flag
    assert args[3] == struct.pack(
        ">BBBBBBBBBBBBB",
        0xF7, 0x11, 0x02, 0x0B,
        flags,
        6, 30, 18, 0,
        80,
        50, 0x00, 0x00,
    )
    assert kwargs["repeat_op_code"] is False


async def test_set_motion_sensor_schedule_rgb_sets_flag_bit():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_motion_sensor_schedule(
        slot_id=3,  # Sleep
        mode=0,  # disabled
        start_hour=22, start_minute=0, end_hour=5, end_minute=59,
        brightness=10,
        rgb=(255, 128, 0),
    )

    args, kwargs = node.send_command.call_args
    flags = 3 | 0x80 | 0x40  # slot_id=3, DISABLED=0x80, rgb flag=0x40
    assert args[3] == struct.pack(
        ">BBBBBBBBBBBBB",
        0xF7, 0x11, 0x02, 0x0B,
        flags,
        22, 0, 5, 59,
        10,
        255, 128, 0,
    )


async def test_set_motion_sensor_schedule_occupancy_mode_sets_no_bit():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_motion_sensor_schedule(
        slot_id=0, mode=1,  # occupancy - no mode bit set
        start_hour=0, start_minute=0, end_hour=0, end_minute=0,
        brightness=0, cct=0,
    )

    args, _ = node.send_command.call_args
    flags = args[3][4]
    assert flags == 0x00  # slot_id=0 | occupancy(no bit)


async def test_set_motion_sensor_schedule_rejects_invalid_inputs():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_motion_sensor_schedule(4, 3, 0, 0, 0, 0, 50, cct=50)  # bad slot_id
    await node.set_motion_sensor_schedule(0, 9, 0, 0, 0, 0, 50, cct=50)  # bad mode
    await node.set_motion_sensor_schedule(0, 3, 24, 0, 0, 0, 50, cct=50)  # bad hour
    await node.set_motion_sensor_schedule(0, 3, 0, 60, 0, 0, 50, cct=50)  # bad minute
    await node.set_motion_sensor_schedule(0, 3, 0, 0, 0, 0, 101, cct=50)  # bad brightness
    await node.set_motion_sensor_schedule(0, 3, 0, 0, 0, 0, 50)  # neither cct nor rgb
    await node.set_motion_sensor_schedule(0, 3, 0, 0, 0, 0, 50, cct=50, rgb=(1, 1, 1))  # both
    await node.set_motion_sensor_schedule(0, 3, 0, 0, 0, 0, 50, rgb=(256, 0, 0))  # bad rgb channel
    node.send_command.assert_not_awaited()


async def test_execute_scene_payload_shape():
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    await execute_scene(5)

    assert len(fake_bridge.written) == 1
    inner_payload = struct.pack(">BBBBB", 0xEF, 0x11, 0x02, 5, 0x01)
    expected_inner = PacketBuilder.build_control_packet(
        msg_id=1, target_id=0x00, sub_id=0, op_code=0x8E, cmd_code=0x0C,
        command_payload=inner_payload, repeat_op_code=False,
    )
    expected_outer = PacketBuilder.build_outer_packet(
        packet_type=0x73, queue_id=b"\x00\x01\x02\x03", inner_packet=expected_inner
    )
    assert fake_bridge.written[0] == expected_outer


async def test_build_control_packet_matches_real_captured_packet():
    """Byte-for-byte regression against a genuine captured packet
    (docs/debugging_sessions/3 devices/Plug - Toggle Power/Plug.md), not
    just self-consistency with our own PacketBuilder - this is the
    evidence that op=0x8E and repeat_op_code=False are correct for the
    mesh-relay command family (indicator LED / motion sensor settings /
    scenes), after set_indicator_led silently did nothing on real
    hardware with the previous op=0xF7 guess."""
    packet = PacketBuilder.build_control_packet(
        msg_id=0x20, target_id=0xFF, sub_id=0xFF, op_code=0x8E, cmd_code=0x0B,
        command_payload=bytes([0xF7, 0x11, 0x02, 0x21]), repeat_op_code=False,
    )
    assert packet == bytes.fromhex(
        "7e 20 00 00 00 f8 8e 0b 00 20 00 00 00 00 ff ff f7 11 02 21 e2 7e".replace(" ", "")
    )


async def test_execute_scene_rejects_out_of_range_id():
    g = GlobalObject()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[])
    await execute_scene(256)
    g.ncync_server.get_dev_tcp_pool.assert_not_awaited()


async def test_delete_scene_payload_shape():
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    await delete_scene(300)  # >255, exercises the 2-byte-not-1-byte field

    assert len(fake_bridge.written) == 1
    inner_payload = struct.pack("<H", 300)
    expected_inner = PacketBuilder.build_control_packet(
        msg_id=1, target_id=0x00, sub_id=0, op_code=0x1F, cmd_code=9,
        command_payload=inner_payload,
    )
    expected_outer = PacketBuilder.build_outer_packet(
        packet_type=0x73, queue_id=b"\x00\x01\x02\x03", inner_packet=expected_inner
    )
    assert fake_bridge.written[0] == expected_outer


async def test_delete_scene_rejects_out_of_range_id():
    g = GlobalObject()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[])
    await delete_scene(70000)
    g.ncync_server.get_dev_tcp_pool.assert_not_awaited()


async def test_delete_schedule_payload_shape():
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    await delete_schedule(42)

    assert len(fake_bridge.written) == 1
    inner_payload = struct.pack("<H", 42)
    expected_inner = PacketBuilder.build_control_packet(
        msg_id=1, target_id=0x00, sub_id=0, op_code=0x94, cmd_code=9,
        command_payload=inner_payload,
    )
    expected_outer = PacketBuilder.build_outer_packet(
        packet_type=0x73, queue_id=b"\x00\x01\x02\x03", inner_packet=expected_inner
    )
    assert fake_bridge.written[0] == expected_outer


async def test_toggle_automation_payload_shape():
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    await toggle_automation(42, 300, True)

    assert len(fake_bridge.written) == 1
    inner_payload = (
        struct.pack("<H", 42)
        + struct.pack("<I", 300)
        + bytes(26)
        + struct.pack("<H", 0)
        + b"\x01\x00"
        + bytes(16)
    )
    assert len(inner_payload) == 52
    expected_inner = PacketBuilder.build_control_packet(
        msg_id=1, target_id=0x00, sub_id=0, op_code=0x93, cmd_code=7 + 52,
        command_payload=inner_payload,
    )
    expected_outer = PacketBuilder.build_outer_packet(
        packet_type=0x73, queue_id=b"\x00\x01\x02\x03", inner_packet=expected_inner
    )
    assert fake_bridge.written[0] == expected_outer


async def test_toggle_automation_disabled_flag_byte():
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    await toggle_automation(1, 1, False)

    inner_payload = (
        struct.pack("<H", 1)
        + struct.pack("<I", 1)
        + bytes(26)
        + struct.pack("<H", 0)
        + b"\x00\x00"
        + bytes(16)
    )
    expected_inner = PacketBuilder.build_control_packet(
        msg_id=1, target_id=0x00, sub_id=0, op_code=0x93, cmd_code=7 + 52,
        command_payload=inner_payload,
    )
    expected_outer = PacketBuilder.build_outer_packet(
        packet_type=0x73, queue_id=b"\x00\x01\x02\x03", inner_packet=expected_inner
    )
    assert fake_bridge.written[0] == expected_outer


async def test_toggle_automation_rejects_out_of_range_ids():
    g = GlobalObject()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[])
    await toggle_automation(70000, 1, True)
    await toggle_automation(1, 2**32, True)
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


async def test_set_group_power_splits_group_id_into_target_and_sub_id():
    """The single most important regression test for this feature: target_id
    and sub_id are not independent fields - together they ARE the outer
    envelope's 2-byte MeshAddress (target_id=low byte, sub_id=high byte).
    A group_id of 32770 (0x8002) must split into target_id=0x02, sub_id=0x80."""
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    await set_group_power(32770, 1)

    assert len(fake_bridge.written) == 1
    payload = struct.pack(">BBBBB", 0x11, 0x02, 1, 0x00, 0x00)
    expected_inner = PacketBuilder.build_control_packet(
        msg_id=1, target_id=0x02, sub_id=0x80, op_code=0xD0, cmd_code=0x0D,
        command_payload=payload,
    )
    expected_outer = PacketBuilder.build_outer_packet(
        packet_type=0x73, queue_id=b"\x00\x01\x02\x03", inner_packet=expected_inner
    )
    assert fake_bridge.written[0] == expected_outer


async def test_set_group_power_reuses_confirmed_set_power_op_and_cmd():
    """op_code/cmd_code here are NOT predictions - must be byte-identical to
    the already-confirmed, already-shipping set_power command."""
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    await set_group_power(0, 0)

    inner_payload = struct.pack(">BBBBB", 0x11, 0x02, 0, 0x00, 0x00)
    expected_inner = PacketBuilder.build_control_packet(
        msg_id=1, target_id=0x00, sub_id=0x00, op_code=0xD0, cmd_code=0x0D,
        command_payload=inner_payload,
    )
    expected_outer = PacketBuilder.build_outer_packet(
        packet_type=0x73, queue_id=b"\x00\x01\x02\x03", inner_packet=expected_inner
    )
    assert fake_bridge.written[0] == expected_outer


async def test_set_group_power_rejects_invalid_group_id_and_state():
    g = GlobalObject()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[])

    await set_group_power(70000, 1)  # out of range
    g.ncync_server.get_dev_tcp_pool.assert_not_awaited()

    await set_group_power(32770, 2)  # invalid state
    g.ncync_server.get_dev_tcp_pool.assert_not_awaited()


def test_warn_experimental_group_targeting_fires_once_per_name():
    _EXPERIMENTAL_CMDS_WARNED.discard("test_group_cmd_unique_name")
    import cync_lan.devices as devices_module

    with patch.object(devices_module.logger, "warning") as mock_warn:
        _warn_experimental_group_targeting("lp:", "test_group_cmd_unique_name")
        _warn_experimental_group_targeting("lp:", "test_group_cmd_unique_name")
        mock_warn.assert_called_once()
    _EXPERIMENTAL_CMDS_WARNED.discard("test_group_cmd_unique_name")


def test_warn_experimental_cmd_code_fires_once_per_name():
    _EXPERIMENTAL_CMDS_WARNED.discard("test_cmd_unique_name")
    import cync_lan.devices as devices_module

    with patch.object(devices_module.logger, "warning") as mock_warn:
        _warn_experimental_cmd_code("lp:", "test_cmd_unique_name")
        _warn_experimental_cmd_code("lp:", "test_cmd_unique_name")
        mock_warn.assert_called_once()
    _EXPERIMENTAL_CMDS_WARNED.discard("test_cmd_unique_name")


async def test_set_group_membership_add_payload_shape_common_case():
    """The common case - virtually every real device (is_sol_lamp=False) -
    takes the 0x8E-relay-bug path, NOT the direct-0xD7 path. This is the
    branch that was missing before the is_sol_lamp fix; get it wrong and
    the command silently no-ops against nearly all real hardware."""
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.metadata = None  # is_sol_lamp -> False
    node.send_command = AsyncMock()

    await node.set_group_membership(32770, member=True)

    args, kwargs = node.send_command.call_args
    assert args[0] == 0x8E  # op - 0x8E-relay substitution, not the embedded 0xD7
    payload = struct.pack(">B", 0xD7) + struct.pack(">BBB", 0x11, 0x02, 1) + struct.pack(
        "<H", 32770
    ) + struct.pack(">B", 0x00)
    assert args[3] == payload
    assert args[1] == 7 + len(payload)  # predicted cmd_
    assert kwargs == {"repeat_op_code": False}


async def test_set_group_membership_remove_payload_shape_common_case():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.metadata = None  # is_sol_lamp -> False
    node.send_command = AsyncMock()

    await node.set_group_membership(32770, member=False, reach_flag=0x87)

    args, kwargs = node.send_command.call_args
    assert args[0] == 0x8E
    assert args[3] == struct.pack(">B", 0xD7) + struct.pack(
        ">BBB", 0x11, 0x02, 0
    ) + struct.pack("<H", 32770) + struct.pack(">B", 0x87)
    assert kwargs == {"repeat_op_code": False}


async def test_set_group_membership_add_payload_shape_sol_lamp():
    """The rare case - is_sol_lamp=True (e.g. device type 80) - is the only
    device family confirmed to use the direct, trustworthy 0xD7 op_code
    path (no repeat_op_code override, since the embedded op_code genuinely
    is the real one here)."""
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.metadata = MagicMock(opcodes=MagicMock(sol_lamp=True))
    node.send_command = AsyncMock()

    await node.set_group_membership(32770, member=True)

    args, kwargs = node.send_command.call_args
    assert args[0] == 0xD7
    assert args[1] == 0x0E  # predicted cmd_ (8 + 6-byte payload)
    assert args[3] == struct.pack(">BBB", 0x11, 0x02, 1) + struct.pack("<H", 32770) + struct.pack(
        ">B", 0x00
    )
    assert kwargs == {}


async def test_set_group_membership_remove_payload_shape_sol_lamp():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.metadata = MagicMock(opcodes=MagicMock(sol_lamp=True))
    node.send_command = AsyncMock()

    await node.set_group_membership(32770, member=False, reach_flag=0x87)

    args, kwargs = node.send_command.call_args
    assert args[0] == 0xD7
    assert args[3] == struct.pack(">BBB", 0x11, 0x02, 0) + struct.pack("<H", 32770) + struct.pack(
        ">B", 0x87
    )


async def test_set_group_membership_rejects_invalid_inputs():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_group_membership(70000, member=True)  # out of range
    await node.set_group_membership(32770, member=True, reach_flag=0x01)  # invalid flag
    node.send_command.assert_not_awaited()
