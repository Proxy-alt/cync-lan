"""The `cmd_code` field is a length, and it must match the body it describes.

`PacketBuilder.build_control_packet` lays out:

    inner = header(8) + routing(7) + op_prefix(0 or 1) + payload

and `cmd_code` is the length of everything after the header. The `op_prefix`
term is 1 for the classic op families (0xD0/0xF0/0xE2/...), which emit
`op_code` as a standalone byte, and 0 for the 0x8E mesh-relay family, whose
payload already begins with its own inner opcode array.

Every hub command (create/delete scene, create/delete schedule,
add/toggle automation) computed `7 + len(payload)` - the 0x8E form - while
sending with repeat_op_code defaulting to True, so the length field was one
byte short of the real body. See scripts/cmd_code.py, which audits this
across every command and is what caught it.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import cync_lan.devices as devices
from cync_lan.packet import PacketBuilder
from cync_lan.structs import GlobalObject

ROUTING_LEN = 7


def expected_cmd_code(payload_len: int, repeat_op_code: bool) -> int:
    return ROUTING_LEN + (1 if repeat_op_code else 0) + payload_len


@pytest.fixture
def captured(monkeypatch):
    """Drive commands through a stub transport, recording what each sends."""
    calls: list[dict] = []
    real_build = PacketBuilder.build_control_packet

    def _capture(**kwargs):
        calls.append(kwargs)
        return real_build(**kwargs)

    monkeypatch.setattr(PacketBuilder, "build_control_packet", staticmethod(_capture))

    session = MagicMock()
    session.ready_to_control = True
    session.mitm_mode = False
    session.queue_id = b"\x00\x00\x00\x00"
    session.get_ctrl_msg_id_bytes = MagicMock(return_value=(1, 0))
    session.node = None
    session.write = AsyncMock()

    g = GlobalObject()
    previous_server, previous_mqtt = g.ncync_server, g.mqtt_client
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[session])
    g.mqtt_client = MagicMock()
    g.mqtt_client.publish = AsyncMock()
    yield calls
    g.ncync_server, g.mqtt_client = previous_server, previous_mqtt


async def _run(coro):
    """Hub commands that await a device notification will time out with no
    device present; the packet is already built by then."""
    try:
        await asyncio.wait_for(coro, timeout=1)
    except (asyncio.TimeoutError, Exception):
        pass


HUB_COMMANDS = [
    ("delete_scene", lambda: devices.delete_scene(1), 0x1F),
    ("delete_schedule", lambda: devices.delete_schedule(1), 0x94),
    ("toggle_automation", lambda: devices.toggle_automation(1, 1, True), 0x93),
    ("add_automation", lambda: devices.add_automation(1, 1, 0x7F, 8, 30, 0), 0x95),
    ("create_scene", lambda: devices.create_scene("Test"), 0x10),
    ("create_schedule", lambda: devices.create_schedule(1), 0x92),
]


@pytest.mark.parametrize(("name", "factory", "op_code"), HUB_COMMANDS)
async def test_hub_command_length_field_matches_body(captured, name, factory, op_code):
    await _run(factory())

    assert captured, f"{name} never reached build_control_packet"
    sent = captured[0]
    assert sent["op_code"] == op_code
    # This family emits the op_prefix byte, so the length must account for it.
    assert sent.get("repeat_op_code", True) is True
    assert sent["cmd_code"] == expected_cmd_code(len(sent["command_payload"]), True), (
        f"{name}'s length field is off by "
        f"{sent['cmd_code'] - expected_cmd_code(len(sent['command_payload']), True)}"
    )


MESH_RELAY_COMMANDS = [
    ("execute_scene", lambda: devices.execute_scene(1)),
]


@pytest.mark.parametrize(("name", "factory"), MESH_RELAY_COMMANDS)
async def test_mesh_relay_command_length_field_excludes_op_prefix(
    captured, name, factory
):
    """The 0x8E family must NOT add 1 - no op_prefix byte is emitted."""
    await _run(factory())

    assert captured, f"{name} never reached build_control_packet"
    sent = captured[0]
    assert sent["op_code"] == 0x8E
    assert sent["repeat_op_code"] is False
    assert sent["cmd_code"] == expected_cmd_code(len(sent["command_payload"]), False)


async def test_indicator_led_still_matches_its_hardware_confirmed_value(captured):
    """set_indicator_led is the one command confirmed working on real
    hardware: op 0x8E, cmd_code 0x0E, 7-byte payload, no op_prefix. It is the
    anchor for the whole formula, so it gets its own explicit check."""
    dev = devices.CyncDevice(dev_id=5, dev_type=55, name="Test", home_id=1)
    await _run(dev.set_indicator_led(2, 0, 100, False))

    assert captured
    sent = captured[0]
    assert sent["op_code"] == 0x8E
    assert sent["repeat_op_code"] is False
    assert len(sent["command_payload"]) == 7
    assert sent["cmd_code"] == 0x0E


def test_formula_reproduces_the_shipping_confirmed_values():
    """The three values the docs derive the formula from."""
    assert expected_cmd_code(5, True) == 0x0D  # set_power
    assert expected_cmd_code(8, True) == 0x10  # set_brightness / set_rgb
    assert expected_cmd_code(6, True) == 0x0E  # set_lightshow
    assert expected_cmd_code(7, False) == 0x0E  # set_indicator_led (hardware)
