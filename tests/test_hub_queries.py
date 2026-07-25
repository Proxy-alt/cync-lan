"""Tests for the hub query/delete commands.

Every one has a confirmed op_code and request payload read from the
decompiled app, and a PREDICTED cmd_code. These tests pin the parts that are
confirmed - the opcode and the exact bytes on the wire - so a refactor cannot
quietly change what gets sent to someone's hardware.
"""

from __future__ import annotations

import asyncio
import datetime
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

import cync_lan.devices as devices
from cync_lan.packet import PacketBuilder
from cync_lan.structs import GlobalObject


@pytest.fixture
def sent(monkeypatch):
    """Capture what each command puts on the wire."""
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
    prev_server, prev_mqtt = g.ncync_server, g.mqtt_client
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[session])
    g.mqtt_client = MagicMock()
    g.mqtt_client.publish = AsyncMock()
    yield calls
    g.ncync_server, g.mqtt_client = prev_server, prev_mqtt


def _reply(op_code: int, payload: bytes):
    """Resolve the pending wait for `op_code` as if the hub had answered."""

    async def _resolve():
        for _ in range(50):
            fut = devices._PENDING_XLINK_RESPONSES.get(op_code)
            if fut is not None and not fut.done():
                fut.set_result(payload)
                return
            await asyncio.sleep(0)

    return _resolve()


# ---------------------------------------------------------------------------
# fire-and-forget deletes
# ---------------------------------------------------------------------------


async def test_delete_automation_sends_the_confirmed_frame(sent):
    await devices.delete_automation(0x1234)

    assert sent[0]["op_code"] == 0x97
    # 6-byte buffer, only the u16 written - the 4 trailing zeros are really
    # on the wire, not an artifact of how the app allocates.
    assert sent[0]["command_payload"] == struct.pack("<H", 0x1234) + bytes(4)
    assert len(sent[0]["command_payload"]) == 6


async def test_delete_group_sends_the_group_mesh_address(sent):
    await devices.delete_group(32770)

    assert sent[0]["op_code"] == 0x32
    assert sent[0]["command_payload"] == struct.pack("<H", 32770)


async def test_delete_group_rejects_an_out_of_range_address(sent):
    """The field is a UShort; a caller passing a group *index* instead of a
    MeshAddress would otherwise silently truncate."""
    await devices.delete_group(70000)

    assert sent == []


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------


async def test_query_hub_info_decodes_four_fixed_fields(sent):
    payload = b"".join(
        f.encode().ljust(16, b"\x00") for f in ("1", "2.3", "AABBCCDDEEFF", "SETUP123")
    )
    task = asyncio.create_task(devices.query_hub_info(timeout=2))
    await _reply(0x4B, payload)
    result = await task

    assert sent[0]["op_code"] == 0x4B
    assert sent[0]["command_payload"] == bytes(64)
    assert result == {
        "firmware_version": "1.2.3",
        "mac": "AABBCCDDEEFF",
        "setup_code": "SETUP123",
    }


async def test_query_device_time_decodes_the_xlink_layout(sent):
    payload = struct.pack("<H", 2026) + bytes([7, 25, 14, 30, 5])
    task = asyncio.create_task(devices.query_device_time(timeout=2))
    await _reply(0x46, payload)
    result = await task

    assert sent[0]["op_code"] == 0x46
    assert result == datetime.datetime(2026, 7, 25, 14, 30, 5)


async def test_query_device_time_rejects_an_impossible_date(sent):
    """A hub with a corrupt clock must not raise out of a diagnostic read."""
    payload = struct.pack("<H", 2026) + bytes([13, 45, 99, 99, 99])
    task = asyncio.create_task(devices.query_device_time(timeout=2))
    await _reply(0x46, payload)

    assert await task is None


async def test_query_sol_config_decodes_three_booleans(sent):
    task = asyncio.create_task(devices.query_sol_config(timeout=2))
    await _reply(0xAD, bytes([1, 0, 1]))
    result = await task

    assert sent[0]["op_code"] == 0xAD
    assert result == {
        "show_clock": True,
        "show_timer": False,
        "show_mic_privacy_mode_light": True,
    }


@pytest.mark.parametrize(
    ("fn", "op_code"),
    [
        ("query_hub_info", 0x4B),
        ("query_device_time", 0x46),
        ("query_sol_config", 0xAD),
    ],
)
async def test_queries_return_none_on_timeout(sent, fn, op_code):
    """A timeout is an expected outcome, not an error - whether this
    notification channel rides the intercepted TCP relay is unresolved."""
    assert await getattr(devices, fn)(timeout=0.05) is None
    assert sent[0]["op_code"] == op_code


@pytest.mark.parametrize(
    ("fn", "op_code", "short"),
    [
        ("query_hub_info", 0x4B, b"too short"),
        ("query_device_time", 0x46, b"\x01\x02"),
        ("query_sol_config", 0xAD, b"\x01"),
    ],
)
async def test_queries_return_none_on_a_short_reply(sent, fn, op_code, short):
    task = asyncio.create_task(getattr(devices, fn)(timeout=2))
    await _reply(op_code, short)

    assert await task is None


async def test_every_query_sends_an_all_zero_request(sent):
    """The app writes nothing into these buffers, so the allocation size is
    the wire size."""
    for coro in (
        devices.query_hub_info(timeout=0.05),
        devices.query_device_time(timeout=0.05),
        devices.query_sol_config(timeout=0.05),
    ):
        await coro
    for call in sent:
        assert call["command_payload"] == bytes(64)
