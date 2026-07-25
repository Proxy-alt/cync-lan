"""Tests for src/cync_lan/devices.py's new mesh commands and the
send_command/broadcast_control_command refactor.

Non-HA-dependent (imports `cync_lan.devices` directly, same pattern as
test_cloud_api.py), living alongside the rest of the suite so the same
`pytest tests/components/cync_lan/` invocation picks them up.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import cync_lan.devices as devices
from cync_lan.const import FACTORY_EFFECTS_BYTES, LIGHT_RUN_MODE_EFFECTS
from cync_lan.devices import (
    _EXPERIMENTAL_CMDS_WARNED,
    _PENDING_XLINK_RESPONSES,
    CyncDevice,
    _await_xlink_notification,
    _get_experimental_logger,
    _log_experimental,
    _warn_experimental_cmd_code,
    _warn_experimental_group_targeting,
    _warn_experimental_transport_unconfirmed,
    add_automation,
    broadcast_control_command,
    create_scene,
    create_schedule,
    delete_scene,
    delete_schedule,
    execute_scene,
    set_group_power,
    toggle_automation,
    try_resolve_xlink_notification,
)
from cync_lan.packet import PacketBuilder
from cync_lan.structs import GlobalObject


def _reset_experimental_logger():
    """_get_experimental_logger() lazily caches a module-level singleton
    (see devices.py) tied to a real logging.Logger registered under a
    fixed name - tests that want a fresh handler pointed at a tmp_path
    must clear both the module's cached reference and any handlers
    already attached to that named logger, or handlers accumulate across
    tests (harmless for correctness since each test's tmp_path is unique,
    but leaks file handles over a long test run)."""
    devices._experimental_logger = None
    named = logging.getLogger("cync_lan.experimental")
    for h in list(named.handlers):
        named.removeHandler(h)
        h.close()


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
        msg_id=1,
        target_id=5,
        sub_id=0,
        op_code=0xD0,
        cmd_code=0x0D,
        command_payload=payload,
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
    assert payload == struct.pack(">BBB", 0x11, 0x02, 0x08) + struct.pack(
        ">HH", 500, 2000
    )


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
    assert payload == struct.pack(">BBB", 0x11, 0x02, 0x08) + struct.pack(
        ">HH", 500, 65535
    )


async def test_set_indicator_led_payload_shape():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_indicator_led(
        mode=2, color=1, brightness=80, wifi_disconnect_blink=True
    )

    args, kwargs = node.send_command.call_args
    assert args[0] == 0x8E  # op - real mesh-relay op, not the misread 0xF7
    assert args[1] == 0x0E  # predicted cmd_
    assert args[3] == struct.pack(
        ">BBBBBBB", 0xF7, 0x11, 0x02, 0x06, (2 << 4) | 1, 80, 1
    )
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
    assert args[3] == struct.pack(
        ">B", 0xF7
    ) + CyncDevice._build_motion_sensor_settings_payload(1, enabled=True)
    assert kwargs["repeat_op_code"] is False


async def test_set_motion_sensor_schedule_cct_payload_shape():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_motion_sensor_schedule(
        slot_id=1,  # Daytime
        mode=3,  # simple
        start_hour=6,
        start_minute=30,
        end_hour=18,
        end_minute=0,
        brightness=80,
        cct=50,
    )

    args, kwargs = node.send_command.call_args
    assert args[0] == 0x8E  # op - mesh-relay, not the misread 0xF7
    assert args[1] == 0x14  # predicted cmd_ (7 + 13-byte payload)
    flags = 1 | 0x10  # slot_id=1, SIMPLE=0x10, no rgb flag
    assert args[3] == struct.pack(
        ">BBBBBBBBBBBBB",
        0xF7,
        0x11,
        0x02,
        0x0B,
        flags,
        6,
        30,
        18,
        0,
        80,
        50,
        0x00,
        0x00,
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
        start_hour=22,
        start_minute=0,
        end_hour=5,
        end_minute=59,
        brightness=10,
        rgb=(255, 128, 0),
    )

    args, kwargs = node.send_command.call_args
    flags = 3 | 0x80 | 0x40  # slot_id=3, DISABLED=0x80, rgb flag=0x40
    assert args[3] == struct.pack(
        ">BBBBBBBBBBBBB",
        0xF7,
        0x11,
        0x02,
        0x0B,
        flags,
        22,
        0,
        5,
        59,
        10,
        255,
        128,
        0,
    )


async def test_set_motion_sensor_schedule_occupancy_mode_sets_no_bit():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_motion_sensor_schedule(
        slot_id=0,
        mode=1,  # occupancy - no mode bit set
        start_hour=0,
        start_minute=0,
        end_hour=0,
        end_minute=0,
        brightness=0,
        cct=0,
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
    await node.set_motion_sensor_schedule(
        0, 3, 0, 0, 0, 0, 101, cct=50
    )  # bad brightness
    await node.set_motion_sensor_schedule(0, 3, 0, 0, 0, 0, 50)  # neither cct nor rgb
    await node.set_motion_sensor_schedule(
        0, 3, 0, 0, 0, 0, 50, cct=50, rgb=(1, 1, 1)
    )  # both
    await node.set_motion_sensor_schedule(
        0, 3, 0, 0, 0, 0, 50, rgb=(256, 0, 0)
    )  # bad rgb channel
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
        msg_id=1,
        target_id=0x00,
        sub_id=0,
        op_code=0x8E,
        cmd_code=0x0C,
        command_payload=inner_payload,
        repeat_op_code=False,
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
        msg_id=0x20,
        target_id=0xFF,
        sub_id=0xFF,
        op_code=0x8E,
        cmd_code=0x0B,
        command_payload=bytes([0xF7, 0x11, 0x02, 0x21]),
        repeat_op_code=False,
    )
    assert packet == bytes.fromhex(
        "7e 20 00 00 00 f8 8e 0b 00 20 00 00 00 00 ff ff f7 11 02 21 e2 7e".replace(
            " ", ""
        )
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
        msg_id=1,
        target_id=0x00,
        sub_id=0,
        op_code=0x1F,
        cmd_code=9,
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
        msg_id=1,
        target_id=0x00,
        sub_id=0,
        op_code=0x94,
        cmd_code=9,
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
        msg_id=1,
        target_id=0x00,
        sub_id=0,
        op_code=0x93,
        cmd_code=7 + 52,
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
        msg_id=1,
        target_id=0x00,
        sub_id=0,
        op_code=0x93,
        cmd_code=7 + 52,
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
        msg_id=1,
        target_id=0x02,
        sub_id=0x80,
        op_code=0xD0,
        cmd_code=0x0D,
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
        msg_id=1,
        target_id=0x00,
        sub_id=0x00,
        op_code=0xD0,
        cmd_code=0x0D,
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
    payload = (
        struct.pack(">B", 0xD7)
        + struct.pack(">BBB", 0x11, 0x02, 1)
        + struct.pack("<H", 32770)
        + struct.pack(">B", 0x00)
    )
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
    assert args[3] == struct.pack(">BBB", 0x11, 0x02, 1) + struct.pack(
        "<H", 32770
    ) + struct.pack(">B", 0x00)
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
    assert args[3] == struct.pack(">BBB", 0x11, 0x02, 0) + struct.pack(
        "<H", 32770
    ) + struct.pack(">B", 0x87)


async def test_set_group_membership_rejects_invalid_inputs():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.send_command = AsyncMock()

    await node.set_group_membership(70000, member=True)  # out of range
    await node.set_group_membership(32770, member=True, reach_flag=0x01)  # invalid flag
    node.send_command.assert_not_awaited()


def _build_xlink_frame(
    msg_id: int, direction: int, op_code: int, payload: bytes
) -> bytes:
    """Reference encoder for tests only - cync-lan itself never emits a
    genuine Xlink/Frame frame, see xlink_legacy.py's module docstring."""
    body = struct.pack("<BH", op_code, len(payload)) + payload
    checksum = sum(body) % 256
    inner = (
        struct.pack("<I", msg_id)
        + struct.pack("B", direction)
        + body
        + struct.pack("B", checksum)
    )
    stuffed = bytearray()
    for b in inner:
        if b in (0x7E, 0x7D):
            stuffed.append(0x7D)
            stuffed.append(b ^ 0x20)
        else:
            stuffed.append(b)
    return bytes([0x7E]) + bytes(stuffed) + bytes([0x7E])


async def test_try_resolve_xlink_notification_resolves_pending_future():
    from cync_lan.packet.xlink_legacy import Direction

    fut = asyncio.get_event_loop().create_future()
    _PENDING_XLINK_RESPONSES[0x10] = fut
    try:
        payload = struct.pack(">B", 0) + struct.pack("<H", 7)
        frame_bytes = _build_xlink_frame(1, Direction.RSP, 0x10, payload)

        handled = try_resolve_xlink_notification(frame_bytes)

        assert handled is True
        assert fut.done()
        assert fut.result() == payload
    finally:
        _PENDING_XLINK_RESPONSES.pop(0x10, None)


async def test_try_resolve_xlink_notification_ignores_unrelated_op_code():
    """No one is waiting for op_code 0x92 - must return False so the caller
    falls through to its existing unknown-packet logging, not silently
    swallow real diagnostic traffic."""
    from cync_lan.packet.xlink_legacy import Direction

    payload = struct.pack(">B", 0) + struct.pack("<H", 7)
    frame_bytes = _build_xlink_frame(1, Direction.RSP, 0x92, payload)

    assert try_resolve_xlink_notification(frame_bytes) is False


async def test_try_resolve_xlink_notification_returns_false_for_non_frame_bytes():
    assert (
        try_resolve_xlink_notification(b"\xfa\xdb\x13\x00\x00\x00\x00\x00\x01") is False
    )


async def test_await_xlink_notification_returns_payload_when_resolved():
    async def _resolve_soon():
        await asyncio.sleep(0.01)
        fut = _PENDING_XLINK_RESPONSES[0x10]
        fut.set_result(b"\x00\x07\x00")

    asyncio.create_task(_resolve_soon())
    result = await _await_xlink_notification(0x10, timeout=1.0)

    assert result == b"\x00\x07\x00"
    assert 0x10 not in _PENDING_XLINK_RESPONSES  # cleaned up after resolving


async def test_await_xlink_notification_times_out_gracefully():
    """A timeout must be a normal None return, not an exception - the
    transport question for this whole notification channel is genuinely
    unresolved, so "nothing ever arrived" is an expected outcome."""
    result = await _await_xlink_notification(0x99, timeout=0.05)

    assert result is None
    assert 0x99 not in _PENDING_XLINK_RESPONSES  # cleaned up after timeout


async def test_await_xlink_notification_serializes_concurrent_calls():
    """Two concurrent calls for the SAME op_code must not race/clobber each
    other's pending future - the lock forces the second to wait its turn."""
    results = []

    async def _resolve_after(op_code: int, value: bytes, delay: float):
        await asyncio.sleep(delay)
        fut = _PENDING_XLINK_RESPONSES.get(op_code)
        if fut and not fut.done():
            fut.set_result(value)

    async def _caller(tag: str):
        result = await _await_xlink_notification(0x55, timeout=2.0)
        results.append((tag, result))

    task_a = asyncio.create_task(_caller("first"))
    await asyncio.sleep(0.01)  # let the first call register its pending future
    asyncio.create_task(_resolve_after(0x55, b"first-response", 0.02))
    task_b = asyncio.create_task(_caller("second"))
    await asyncio.sleep(0.05)
    asyncio.create_task(_resolve_after(0x55, b"second-response", 0.02))

    await asyncio.gather(task_a, task_b)

    assert dict(results) == {"first": b"first-response", "second": b"second-response"}


async def test_create_scene_payload_shape_and_success_response():
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    with patch(
        "cync_lan.devices._await_xlink_notification",
        new=AsyncMock(return_value=struct.pack(">B", 0) + struct.pack("<H", 42)),
    ):
        scene_id = await create_scene("Movie Night")

    assert scene_id == 42
    assert len(fake_bridge.written) == 1
    expected_payload = (
        "Movie Night".encode("utf-8").ljust(30, b"\x00")
        + struct.pack("<H", 0)
        + bytes(18)
    )
    expected_inner = PacketBuilder.build_control_packet(
        msg_id=1,
        target_id=0x00,
        sub_id=0x00,
        op_code=0x10,
        cmd_code=7 + len(expected_payload),
        command_payload=expected_payload,
    )
    expected_outer = PacketBuilder.build_outer_packet(
        packet_type=0x73, queue_id=b"\x00\x01\x02\x03", inner_packet=expected_inner
    )
    assert fake_bridge.written[0] == expected_outer


async def test_create_scene_truncates_and_pads_name():
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    with patch(
        "cync_lan.devices._await_xlink_notification", new=AsyncMock(return_value=None)
    ):
        await create_scene("x" * 40)  # longer than 30 bytes

    sent_name = fake_bridge.written[0]
    # Confirm the 30-byte name field is exactly 30 'x' bytes, not 40.
    assert (b"x" * 30) in sent_name
    assert (b"x" * 31) not in sent_name


async def test_create_scene_returns_none_on_timeout():
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    with patch(
        "cync_lan.devices._await_xlink_notification", new=AsyncMock(return_value=None)
    ):
        scene_id = await create_scene("Movie Night")

    assert scene_id is None


async def test_create_scene_returns_none_on_error_code():
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    with patch(
        "cync_lan.devices._await_xlink_notification",
        new=AsyncMock(return_value=struct.pack(">B", 1) + struct.pack("<H", 0)),
    ):
        scene_id = await create_scene("Movie Night")

    assert scene_id is None


async def test_create_scene_end_to_end_via_real_notification_resolution():
    """Exercises the REAL _await_xlink_notification/try_resolve_xlink_notification
    path (not mocked) for full confidence in the actual correlation plumbing,
    not just create_scene()'s own response-handling logic."""
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    async def _deliver_response():
        await asyncio.sleep(0.01)
        payload = struct.pack(">B", 0) + struct.pack("<H", 7)
        frame = _build_xlink_frame(1, 0xF9, 0x10, payload)
        assert try_resolve_xlink_notification(frame) is True

    asyncio.create_task(_deliver_response())
    scene_id = await create_scene("Movie Night", timeout=2.0)

    assert scene_id == 7


async def test_create_schedule_payload_shape_and_success_response():
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    with patch(
        "cync_lan.devices._await_xlink_notification",
        new=AsyncMock(return_value=struct.pack(">B", 0) + struct.pack("<H", 13)),
    ):
        schedule_id = await create_schedule(300, enabled=True)

    assert schedule_id == 13
    expected_payload = (
        struct.pack("<I", 300)
        + bytes(26)
        + struct.pack("<H", 0)
        + struct.pack(">B", 1)
        + struct.pack(">B", 0)
        + bytes(16)
    )
    assert len(expected_payload) == 50
    expected_inner = PacketBuilder.build_control_packet(
        msg_id=1,
        target_id=0x00,
        sub_id=0x00,
        op_code=0x92,
        cmd_code=7 + len(expected_payload),
        command_payload=expected_payload,
    )
    expected_outer = PacketBuilder.build_outer_packet(
        packet_type=0x73, queue_id=b"\x00\x01\x02\x03", inner_packet=expected_inner
    )
    assert fake_bridge.written[0] == expected_outer


async def test_create_schedule_rejects_invalid_scene_id():
    g = GlobalObject()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[])

    result = await create_schedule(-1)

    assert result is None
    g.ncync_server.get_dev_tcp_pool.assert_not_awaited()


async def test_create_schedule_returns_none_on_timeout():
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    with patch(
        "cync_lan.devices._await_xlink_notification", new=AsyncMock(return_value=None)
    ):
        result = await create_schedule(300)

    assert result is None


async def test_add_automation_payload_shape():
    g = GlobalObject()
    fake_bridge = _FakeBridgeDevice()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[fake_bridge])

    # Monday(0x02)+Wednesday(0x08)+Friday(0x20) = 0x2A, 07:30:00 = 27000s
    await add_automation(schedule_id=13, scene_id=300, day_mask=0x2A, hour=7, minute=30)

    expected_payload = (
        struct.pack("<H", 13)
        + struct.pack("<H", 300)
        + struct.pack(">B", 0x2A)
        + struct.pack("<i", 7 * 3600 + 30 * 60)
        + struct.pack("<H", 300)
    )
    assert len(expected_payload) == 11
    expected_inner = PacketBuilder.build_control_packet(
        msg_id=1,
        target_id=0x00,
        sub_id=0x00,
        op_code=0x95,
        cmd_code=7 + len(expected_payload),
        command_payload=expected_payload,
    )
    expected_outer = PacketBuilder.build_outer_packet(
        packet_type=0x73, queue_id=b"\x00\x01\x02\x03", inner_packet=expected_inner
    )
    assert fake_bridge.written[0] == expected_outer


async def test_add_automation_rejects_invalid_inputs():
    g = GlobalObject()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[])

    await add_automation(70000, 300, 0x01, 7, 30)  # bad schedule_id
    await add_automation(13, 300, 0x80, 7, 30)  # bad day_mask (bit 7 set)
    await add_automation(13, 300, 0x01, 24, 30)  # bad hour
    await add_automation(13, 300, 0x01, 7, 60)  # bad minute
    await add_automation(13, 300, 0x01, 7, 30, second=60)  # bad second

    g.ncync_server.get_dev_tcp_pool.assert_not_awaited()


async def test_add_to_scene_cct_payload_shape():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.metadata = None  # is_sol_lamp -> False
    node.send_command = AsyncMock()

    await node.add_to_scene(scene_id=3, cct=80)

    args, kwargs = node.send_command.call_args
    assert args[0] == 0x8E
    expected_payload = (
        struct.pack(">B", 0xEE)
        + struct.pack(">BBB", 0x11, 0x02, 1)
        + struct.pack(">B", 3)
        + struct.pack(">BBBBBB", 0, 0, 80, 0, 0, 0)
        + struct.pack(">BB", 0xFF, 0xFF)
    )
    assert len(expected_payload) == 13
    assert args[3] == expected_payload
    assert kwargs == {"repeat_op_code": False}


async def test_add_to_scene_rgb_payload_shape():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.metadata = None
    node.send_command = AsyncMock()

    await node.add_to_scene(scene_id=3, rgb=(255, 128, 0), fade=0x02)

    args, kwargs = node.send_command.call_args
    expected_payload = (
        struct.pack(">B", 0xEE)
        + struct.pack(">BBB", 0x11, 0x02, 1)
        + struct.pack(">B", 3)
        + struct.pack(">BBBBBB", 0, 0, 0xFE, 255, 128, 0)
        + struct.pack(">BB", 0x02, 0xFF)
    )
    assert args[3] == expected_payload


async def test_add_to_scene_rejects_sol_lamp_device():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.metadata = MagicMock(opcodes=MagicMock(sol_lamp=True))
    node.send_command = AsyncMock()

    await node.add_to_scene(scene_id=3, cct=80)

    node.send_command.assert_not_awaited()


async def test_add_to_scene_rejects_invalid_inputs():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.metadata = None
    node.send_command = AsyncMock()

    await node.add_to_scene(scene_id=300, cct=80)  # scene_id out of 1-byte range
    await node.add_to_scene(scene_id=3)  # neither cct nor rgb
    await node.add_to_scene(scene_id=3, cct=80, rgb=(1, 2, 3))  # both given
    await node.add_to_scene(scene_id=3, cct=150)  # cct out of range
    await node.add_to_scene(scene_id=3, rgb=(1, 2, 300))  # rgb channel out of range

    node.send_command.assert_not_awaited()


async def test_remove_from_scene_non_sol_lamp_payload_shape():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.metadata = None  # is_sol_lamp -> False
    node.send_command = AsyncMock()

    await node.remove_from_scene(scene_id=3)

    args, kwargs = node.send_command.call_args
    assert args[0] == 0x8E
    expected_payload = struct.pack(">BBBB", 0xEE, 0x11, 0x02, 0x00) + struct.pack(
        ">B", 3
    )
    assert len(expected_payload) == 5
    assert args[1] == 7 + len(expected_payload)
    assert args[3] == expected_payload
    assert kwargs == {"repeat_op_code": False}


async def test_remove_from_scene_sol_lamp_payload_shape():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.metadata = MagicMock(opcodes=MagicMock(sol_lamp=True))
    node.send_command = AsyncMock()

    await node.remove_from_scene(scene_id=3)

    args, kwargs = node.send_command.call_args
    assert args[0] == 0xEE
    expected_payload = struct.pack(">BBB", 0x11, 0x02, 0x00) + struct.pack(">B", 3)
    assert len(expected_payload) == 4
    assert args[1] == 7 + len(expected_payload) + 1
    assert args[3] == expected_payload
    assert kwargs == {}


async def test_remove_from_scene_rejects_invalid_scene_id():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.id = 5
    node.metadata = None
    node.send_command = AsyncMock()

    await node.remove_from_scene(scene_id=300)
    await node.remove_from_scene(scene_id=-1)

    node.send_command.assert_not_awaited()


async def test_set_multicolor_gradient_mode_enabled_payload_shape():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.send_command = AsyncMock()

    await node.set_multicolor_gradient_mode(enabled=True)

    args, kwargs = node.send_command.call_args
    assert args[0] == 0x8E
    expected_payload = struct.pack(">BBBB", 0xF7, 0x11, 0x02, 0x4E) + struct.pack(
        ">BB", 0x00, 1
    )
    assert args[1] == 7 + len(expected_payload)
    assert args[3] == expected_payload
    assert kwargs == {"repeat_op_code": False}


async def test_set_multicolor_gradient_mode_disabled_payload_shape():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.send_command = AsyncMock()

    await node.set_multicolor_gradient_mode(enabled=False)

    args, kwargs = node.send_command.call_args
    expected_payload = struct.pack(">BBBB", 0xF7, 0x11, 0x02, 0x4E) + struct.pack(
        ">BB", 0x00, 0
    )
    assert args[3] == expected_payload


async def test_set_multicolor_segment_count_payload_shape():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.send_command = AsyncMock()

    await node.set_multicolor_segment_count(count=12)

    args, kwargs = node.send_command.call_args
    assert args[0] == 0x8E
    expected_payload = struct.pack(">BBBB", 0xF7, 0x11, 0x02, 0x4E) + struct.pack(
        ">BB", 0xFF, 12
    )
    assert args[1] == 7 + len(expected_payload)
    assert args[3] == expected_payload
    assert kwargs == {"repeat_op_code": False}


async def test_set_multicolor_segment_count_rejects_invalid_count():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.send_command = AsyncMock()

    await node.set_multicolor_segment_count(count=256)
    await node.set_multicolor_segment_count(count=-1)

    node.send_command.assert_not_awaited()


async def test_set_multicolor_segments_one_segment_pads_second_slot():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.send_command = AsyncMock()

    await node.set_multicolor_segments([(5, (255, 0, 0))])

    args, kwargs = node.send_command.call_args
    assert args[0] == 0x8E
    expected_payload = (
        struct.pack(">BBBB", 0xF7, 0x11, 0x02, 0x4E)
        + struct.pack(">B", 1)
        + struct.pack(">BBBB", 5, 255, 0, 0)
        + b"\xff\xff\xff\xff"
    )
    assert args[1] == 7 + len(expected_payload)
    assert args[3] == expected_payload
    assert kwargs == {"repeat_op_code": False}


async def test_set_multicolor_segments_two_segments_payload_shape():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.send_command = AsyncMock()

    await node.set_multicolor_segments([(1, (255, 0, 0)), (2, (0, 255, 0))])

    args, kwargs = node.send_command.call_args
    expected_payload = (
        struct.pack(">BBBB", 0xF7, 0x11, 0x02, 0x4E)
        + struct.pack(">B", 1)
        + struct.pack(">BBBB", 1, 255, 0, 0)
        + struct.pack(">BBBB", 2, 0, 255, 0)
    )
    assert args[3] == expected_payload


async def test_set_multicolor_segments_position_none_uses_0xff_sentinel():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.send_command = AsyncMock()

    await node.set_multicolor_segments([(None, (10, 20, 30))])

    args, kwargs = node.send_command.call_args
    expected_slot = struct.pack(">BBBB", 0xFF, 10, 20, 30)
    payload = args[3]
    assert payload[5:9] == expected_slot


async def test_set_multicolor_segments_color_none_writes_zero_rgb():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.send_command = AsyncMock()

    await node.set_multicolor_segments([(5, None)])

    args, kwargs = node.send_command.call_args
    expected_slot = struct.pack(">BBBB", 5, 0, 0, 0)
    payload = args[3]
    assert payload[5:9] == expected_slot


async def test_set_multicolor_segments_rejects_too_many_segments():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.send_command = AsyncMock()

    await node.set_multicolor_segments([(1, (0, 0, 0)), (2, (0, 0, 0)), (3, (0, 0, 0))])
    await node.set_multicolor_segments([])

    node.send_command.assert_not_awaited()


async def test_set_multicolor_segments_rejects_invalid_position():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.send_command = AsyncMock()

    await node.set_multicolor_segments([(0, (0, 0, 0))])
    await node.set_multicolor_segments([(121, (0, 0, 0))])

    node.send_command.assert_not_awaited()


async def test_set_multicolor_segments_rejects_invalid_color():
    node = CyncDevice.__new__(CyncDevice)
    node.lp = "test:"
    node.send_command = AsyncMock()

    await node.set_multicolor_segments([(5, (256, 0, 0))])

    node.send_command.assert_not_awaited()


def test_get_experimental_logger_writes_every_call(tmp_path):
    log_path = tmp_path / "experimental_features.log"
    _reset_experimental_logger()
    with patch.object(devices, "CYNC_EXPERIMENTAL_LOG_PATH", str(log_path)):
        elogger = _get_experimental_logger()
        elogger.info("first call")
        elogger.info("second call")
        for h in elogger.handlers:
            h.flush()
    _reset_experimental_logger()

    contents = log_path.read_text()
    assert "first call" in contents
    assert "second call" in contents


def test_log_experimental_writes_on_every_invocation_not_just_first(tmp_path):
    """Unlike the console warn-once behavior (_EXPERIMENTAL_CMDS_WARNED),
    _log_experimental() must record every single call to the dedicated
    file - a bug report needs to see every time an experimental command
    actually ran, not just the first."""
    log_path = tmp_path / "experimental_features.log"
    _reset_experimental_logger()
    with patch.object(devices, "CYNC_EXPERIMENTAL_LOG_PATH", str(log_path)):
        _log_experimental("lp:", "some_command", "test reason")
        _log_experimental("lp:", "some_command", "test reason")
        for h in _get_experimental_logger().handlers:
            h.flush()
    _reset_experimental_logger()

    contents = log_path.read_text()
    assert contents.count("some_command") == 2


def test_warn_experimental_cmd_code_logs_every_call_to_experimental_file(tmp_path):
    log_path = tmp_path / "experimental_features.log"
    _reset_experimental_logger()
    _EXPERIMENTAL_CMDS_WARNED.discard("test_cmd_code_logging")
    with patch.object(devices, "CYNC_EXPERIMENTAL_LOG_PATH", str(log_path)):
        _warn_experimental_cmd_code("lp:", "test_cmd_code_logging")
        _warn_experimental_cmd_code("lp:", "test_cmd_code_logging")
        for h in _get_experimental_logger().handlers:
            h.flush()
    _reset_experimental_logger()
    _EXPERIMENTAL_CMDS_WARNED.discard("test_cmd_code_logging")

    contents = log_path.read_text()
    assert contents.count("test_cmd_code_logging") == 2


def test_warn_experimental_group_targeting_logs_to_experimental_file(tmp_path):
    log_path = tmp_path / "experimental_features.log"
    _reset_experimental_logger()
    _EXPERIMENTAL_CMDS_WARNED.discard("test_group_targeting_logging")
    with patch.object(devices, "CYNC_EXPERIMENTAL_LOG_PATH", str(log_path)):
        _warn_experimental_group_targeting("lp:", "test_group_targeting_logging")
        for h in _get_experimental_logger().handlers:
            h.flush()
    _reset_experimental_logger()
    _EXPERIMENTAL_CMDS_WARNED.discard("test_group_targeting_logging")

    assert "test_group_targeting_logging" in log_path.read_text()


def test_warn_experimental_transport_unconfirmed_logs_to_experimental_file(tmp_path):
    log_path = tmp_path / "experimental_features.log"
    _reset_experimental_logger()
    _EXPERIMENTAL_CMDS_WARNED.discard("test_transport_logging")
    with patch.object(devices, "CYNC_EXPERIMENTAL_LOG_PATH", str(log_path)):
        _warn_experimental_transport_unconfirmed("lp:", "test_transport_logging")
        for h in _get_experimental_logger().handlers:
            h.flush()
    _reset_experimental_logger()
    _EXPERIMENTAL_CMDS_WARNED.discard("test_transport_logging")

    assert "test_transport_logging" in log_path.read_text()


def _fake_session(node=None):
    """A real CyncTCPSession, with reader/writer immediately cleared so
    close() skips their actual socket-teardown plumbing - isolates the
    test to close()'s device-offline-marking behavior specifically."""
    from cync_lan.devices import CyncTCPSession

    session = CyncTCPSession(
        reader=MagicMock(), writer=MagicMock(), ip_address="127.0.0.1"
    )
    session.writer = None
    session.reader = None
    session.node = node
    return session


async def test_close_marks_its_own_node_offline():
    """The bug this fixes: a TCP session ending (device lost power, network
    dropped, or a deliberate reconnect like MITM-mode toggling) is the most
    direct signal available for THIS device's own availability - it owns
    this connection, unlike a BTLE-mesh-relayed device whose presence is
    only ever inferred from another device's relayed status broadcasts.
    Previously close() never touched CyncDevice.online at all, so a device
    that simply stopped appearing in any mesh broadcast (rather than being
    reported WITH a stale/"not recently seen" flag) stayed marked online
    forever, showing stale last-known state in HA."""
    g = GlobalObject()
    g.ncync_server = MagicMock()
    g.ncync_server.tcp_connections = {}
    g.mqtt_client = MagicMock()
    g.mqtt_client.remove_mitm_button = AsyncMock()

    node = _fake_node()
    node.online = True
    session = _fake_session(node=node)

    await session.close()

    assert node.online is False


async def test_close_is_a_noop_for_online_when_no_node_is_set():
    """A TCP session that never identified a device yet (e.g. a connection
    dropped before handshake completed) has no CyncDevice to mark
    offline - close() must not crash in that case."""
    g = GlobalObject()
    g.ncync_server = MagicMock()
    g.ncync_server.tcp_connections = {}
    g.mqtt_client = MagicMock()
    g.mqtt_client.remove_mitm_button = AsyncMock()

    session = _fake_session(node=None)

    await session.close()  # must not raise

    g.mqtt_client.remove_mitm_button.assert_not_awaited()


async def test_parse_83_device_state_records_relay_source():
    """Diagnostic entity support: whichever TCP session parses a status
    update for a device is recorded as that device's relay_source - the
    only presence signal available for a BTLE-mesh-only device, which
    never has a tcp_session of its own (see entity.py's
    CyncLanRelaySourceSensor in the HA integration)."""
    g = GlobalObject()
    node = _fake_node()
    node.metadata = None
    node.type = 0
    node.handle_entity_update = AsyncMock()
    g.ncync_server = MagicMock()
    g.ncync_server.node_devices = {5: node}

    session = _fake_session(node=None)

    packet_data = bytearray(26)
    packet_data[14] = 5  # dev_id
    packet_data[19:26] = bytes(
        [1, 1, 100, 50, 0, 0, 0]
    )  # recently_seen/power/bri/tmp/r/g/b

    await session._parse_83_device_state(
        bytes(packet_data), checksum=0, calc_chksum=0, lp="test:"
    )

    assert node.relay_source is session
    node.handle_entity_update.assert_awaited_once()
