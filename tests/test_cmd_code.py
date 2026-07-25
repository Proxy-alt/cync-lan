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


def expected_cmd_code(
    payload_len: int, repeat_op_code: bool, include_routing: bool = True
) -> int:
    return (
        (ROUTING_LEN if include_routing else 0)
        + (1 if repeat_op_code else 0)
        + payload_len
    )


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


# --- the alternate ("bare") hub envelope -------------------------------------
#
# CYNC_HUB_ENVELOPE="bare" drops the 7-byte routing block, because every hub
# command class in the decompiled app bypasses the method that prepends it.
# Unproven for our wire, so it is opt-in - see docs/hub_envelope_ab_test.md.
# What these tests pin down is that the *shape stays self-consistent* in both
# modes: a length field that disagrees with the body it describes is a worse
# failure than either envelope being the wrong choice, and it is the failure
# a split-brain edit would produce.


@pytest.fixture
def bare_envelope(monkeypatch):
    monkeypatch.setenv("CYNC_HUB_ENVELOPE", "bare")


async def test_envelope_flag_is_read_per_command_not_at_import(captured, monkeypatch):
    """Flipping the flag must take effect without re-importing anything.

    This is what lets the Home Assistant toggle apply on a config-entry
    reload instead of a full restart, which is the difference between the
    A/B being run and being abandoned halfway.
    """
    monkeypatch.setenv("CYNC_HUB_ENVELOPE", "routed")
    await _run(devices.delete_scene(1))
    assert captured[0]["include_routing"] is True

    captured.clear()
    monkeypatch.setenv("CYNC_HUB_ENVELOPE", "bare")
    await _run(devices.delete_scene(1))
    assert captured[0]["include_routing"] is False


@pytest.mark.parametrize(("name", "factory", "op_code"), HUB_COMMANDS)
async def test_bare_envelope_drops_routing_and_shortens_length(
    captured, bare_envelope, name, factory, op_code
):
    await _run(factory())

    assert captured, f"{name} never reached build_control_packet"
    sent = captured[0]
    assert sent["op_code"] == op_code
    assert sent["include_routing"] is False, f"{name} still sent the routing block"
    assert sent["cmd_code"] == expected_cmd_code(
        len(sent["command_payload"]), True, include_routing=False
    )


@pytest.mark.parametrize(("name", "factory", "op_code"), HUB_COMMANDS)
async def test_bare_envelope_is_exactly_seven_shorter(
    captured, monkeypatch, name, factory, op_code
):
    """The two envelopes must differ by the routing block and nothing else."""
    monkeypatch.setenv("CYNC_HUB_ENVELOPE", "routed")
    await _run(factory())
    routed = captured[0]

    captured.clear()
    monkeypatch.setenv("CYNC_HUB_ENVELOPE", "bare")
    await _run(factory())
    bare = captured[0]

    assert routed["command_payload"] == bare["command_payload"]
    assert routed["op_code"] == bare["op_code"]
    assert routed["cmd_code"] - bare["cmd_code"] == ROUTING_LEN


@pytest.mark.parametrize("envelope", ["routed", "bare"])
@pytest.mark.parametrize(("name", "factory", "op_code"), HUB_COMMANDS)
async def test_length_field_matches_real_body_in_both_envelopes(
    captured, monkeypatch, envelope, name, factory, op_code
):
    """The invariant that actually matters, checked against real bytes.

    Rather than re-deriving the formula, build the packet the command asked
    for and measure it: cmd_code must equal the number of bytes after the
    8-byte header, minus the 0x7E delimiters and trailing checksum.
    """
    monkeypatch.setenv("CYNC_HUB_ENVELOPE", envelope)
    await _run(factory())

    assert captured, f"{name} never reached build_control_packet"
    sent = captured[0]
    packet = PacketBuilder.build_control_packet(**sent)
    # 0x7E + inner + checksum + 0x7E
    inner = packet[1:-2]
    assert sent["cmd_code"] == len(inner) - 8, (
        f"{name} in {envelope!r} mode declares cmd_code={sent['cmd_code']} "
        f"but its body after the header is {len(inner) - 8} bytes"
    )


async def test_unknown_envelope_value_falls_back_to_routed(captured, monkeypatch):
    """Anything that is not exactly "bare" must behave as it always has -
    a typo in the option must not silently produce a third shape."""
    monkeypatch.setenv("CYNC_HUB_ENVELOPE", "Bare-ish typo")
    await _run(devices.delete_scene(1))

    sent = captured[0]
    assert sent["include_routing"] is True
    assert sent["cmd_code"] == expected_cmd_code(len(sent["command_payload"]), True)
