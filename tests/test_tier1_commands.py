"""Tests for the identify / dimmer-LED / set-time commands.

These pin the confirmed parts - opcode arrays, payload bytes and packet
counts - so a refactor cannot quietly change what reaches hardware. The
cmd_codes are predicted and covered separately by scripts/cmd_code.py.
"""

from __future__ import annotations

import datetime
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

import cync_lan.devices as devices
from cync_lan.packet import PacketBuilder
from cync_lan.structs import GlobalObject


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []
    real = PacketBuilder.build_control_packet

    def _capture(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(PacketBuilder, "build_control_packet", staticmethod(_capture))
    session = MagicMock()
    session.ready_to_control = True
    session.mitm_mode = False
    session.queue_id = b"\x00\x00\x00\x00"
    session.get_ctrl_msg_id_bytes = MagicMock(return_value=(1, 0))
    session.node = None
    session.write = AsyncMock()

    g = GlobalObject()
    prev = (g.ncync_server, g.mqtt_client)
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[session])
    g.mqtt_client = MagicMock()
    g.mqtt_client.publish = AsyncMock()
    yield calls
    g.ncync_server, g.mqtt_client = prev


def _device():
    return devices.CyncDevice(dev_id=5, dev_type=55, name="Test", home_id=1)


# --------------------------------------------------------------------------
# identify
# --------------------------------------------------------------------------


async def test_identify_start_and_stop_differ_only_in_the_last_byte(sent):
    dev = _device()
    await dev.identify(True)
    await dev.identify(False)

    start, stop = sent[0]["command_payload"], sent[1]["command_payload"]
    assert start == bytes([0xF7, 0x11, 0x02, 0x03, 0x01])
    assert stop == bytes([0xF7, 0x11, 0x02, 0x03, 0x02])


async def test_identify_uses_the_mesh_relay_envelope(sent):
    """Same dispatch as set_indicator_led - the one command in this family
    confirmed working on real hardware."""
    await _device().identify()

    assert sent[0]["op_code"] == 0x8E
    assert sent[0]["repeat_op_code"] is False


# --------------------------------------------------------------------------
# dimmer level-bar LEDs
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(devices.DIMMER_LED_BRIEFLY_DISPLAY, 1), (devices.DIMMER_LED_ALWAYS_ON, 2)],
)
async def test_dimmer_led_mode_sends_the_enum_byte(sent, mode, expected):
    await _device().set_dimmer_led_mode(mode)

    assert sent[0]["command_payload"] == bytes([0xF7, 0x11, 0x02, 0x62, expected])


async def test_dimmer_led_mode_rejects_a_value_the_enum_does_not_have(sent):
    """DimmingLedsIndicatorMode has exactly two values - notably no "off"."""
    await _device().set_dimmer_led_mode(0)

    assert sent == []


async def test_dimmer_led_brightness_sends_preview_then_save(sent):
    """The app does this in two packets and so must this: only Preview
    carries a level, so a lone Save would commit whatever was already being
    previewed."""
    await _device().set_dimmer_led_brightness(60)

    assert len(sent) == 2
    assert sent[0]["command_payload"] == bytes([0xF7, 0x11, 0x02, 0x63, 0x01, 0xF0, 60])
    assert sent[1]["command_payload"] == bytes([0xF7, 0x11, 0x02, 0x63, 0x02])


@pytest.mark.parametrize("level", [-1, 101])
async def test_dimmer_led_brightness_rejects_out_of_range(sent, level):
    await _device().set_dimmer_led_brightness(level)

    assert sent == []


# --------------------------------------------------------------------------
# set_time
# --------------------------------------------------------------------------


async def test_set_time_payload_shape(sent):
    when = datetime.datetime(
        2026, 7, 25, 12, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=-5))
    )
    await devices.set_time(when, us_style_dst=False)

    payload = sent[0]["command_payload"]
    assert sent[0]["op_code"] == 0x40
    assert len(payload) == 17
    assert struct.unpack_from("<i", payload, 0)[0] == int(when.timestamp())
    assert struct.unpack_from("<b", payload, 4)[0] == -5
    assert payload[5] == 0  # whole-hour offset leaves no remainder
    assert payload[6] == 0  # dst flag off
    assert payload[7:9] == bytes([0x01, 0x00])
    assert payload[9:] == bytes(8)


async def test_set_time_reproduces_the_apps_america_branch(sent):
    """The app writes minutes=0, flag=1 for America/* zones - a string prefix
    test, not a DST calculation. Reproduced rather than corrected."""
    when = datetime.datetime(
        2026, 7, 25, 12, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=-4))
    )
    await devices.set_time(when, us_style_dst=True)

    payload = sent[0]["command_payload"]
    assert payload[5] == 0
    assert payload[6] == 1


async def test_set_time_handles_a_half_hour_offset(sent):
    """Whole-hour zones hide a truncation bug; a 30-minute offset does not."""
    when = datetime.datetime(
        2026,
        7,
        25,
        12,
        0,
        0,
        tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30)),
    )
    await devices.set_time(when, us_style_dst=False)

    payload = sent[0]["command_payload"]
    assert struct.unpack_from("<b", payload, 4)[0] == 5
    assert payload[5] == 30


async def test_set_time_defaults_to_now(sent):
    await devices.set_time()

    assert sent[0]["op_code"] == 0x40
    assert len(sent[0]["command_payload"]) == 17
