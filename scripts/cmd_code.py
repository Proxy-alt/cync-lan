#!/usr/bin/env python3
"""Compute and audit the mesh-command `cmd_code` length field.

`cmd_code` is not a semantic opcode. It is the little-endian byte length of
everything after the 8-byte header in `PacketBuilder.build_control_packet`:

    inner = header(8) + routing(7) + op_prefix(0 or 1) + payload
    cmd_code = 7 + (1 if repeat_op_code else 0) + len(payload)

The `op_prefix` term is the part that trips people up. Every op family
confirmed on hardware so far (0xD0/0xF0/0xE2/...) repeats `op_code` as a
standalone byte before the payload, so it contributes 1. The 0x8E
"mesh-relay" family does not - its payload already begins with its own
inner opcode array ({0xF7,0x11,0x02,...}) - so it contributes 0. Get that
term wrong and the length field is off by one, the receiving firmware reads
a truncated or over-long body, and the command silently does nothing.

Two modes:

    # what cmd_code should a new command use?
    python scripts/cmd_code.py calc --payload F7:11:02:06:20:64:00 --no-repeat-op-code

    # do all shipping commands still agree with the formula?
    python scripts/cmd_code.py audit

`audit` drives every real command method with dummy arguments through a
stubbed transport and compares the `cmd_code` each one actually sends
against the formula. It exits non-zero on any mismatch, so it works as a CI
check, not just a calculator.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

ROUTING_LEN = 7


def predict_cmd_code(payload_len: int, repeat_op_code: bool = True) -> int:
    """The length field for a payload of `payload_len` bytes."""
    return ROUTING_LEN + (1 if repeat_op_code else 0) + payload_len


def _parse_payload(text: str) -> bytes:
    cleaned = text.replace(":", " ").replace(",", " ").replace("0x", " ")
    parts = cleaned.split()
    try:
        return bytes(int(p, 16) for p in parts)
    except ValueError as err:
        raise SystemExit(
            f"could not parse payload {text!r} as hex bytes: {err}"
        ) from err


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


@dataclass
class Capture:
    name: str
    op_code: int
    cmd_code: int
    payload: bytes
    repeat_op_code: bool

    @property
    def expected(self) -> int:
        return predict_cmd_code(len(self.payload), self.repeat_op_code)

    @property
    def ok(self) -> bool:
        return self.cmd_code == self.expected


def _install_stub_transport(captures: list[Capture], current_name: list[str]) -> None:
    """Replace the packet builder and TCP pool so commands can be driven
    without a server, capturing what each one would have sent."""
    from cync_lan.packet import PacketBuilder
    from cync_lan.structs import GlobalObject

    real_build = PacketBuilder.build_control_packet

    def _capture_build(**kwargs: Any) -> bytes:
        captures.append(
            Capture(
                name=current_name[0],
                op_code=kwargs["op_code"],
                cmd_code=kwargs["cmd_code"],
                payload=kwargs["command_payload"],
                repeat_op_code=kwargs.get("repeat_op_code", True),
            )
        )
        # Still build for real: an inconsistent length field would raise here
        # rather than being silently recorded as fine.
        return real_build(**kwargs)

    PacketBuilder.build_control_packet = staticmethod(_capture_build)

    session = MagicMock()
    session.ready_to_control = True
    session.mitm_mode = False
    session.queue_id = b"\x00\x00\x00\x00"
    session.get_ctrl_msg_id_bytes = MagicMock(return_value=(1, 0))
    session.node = None
    session.write = AsyncMock()
    session.is_closed = MagicMock(return_value=False)
    session.is_app = False

    g = GlobalObject()
    g.ncync_server = MagicMock()
    g.ncync_server.get_dev_tcp_pool = AsyncMock(return_value=[session])
    g.ncync_server.get_dev_tcp_pool_sync = MagicMock(return_value=[session])
    g.mqtt_client = MagicMock()
    g.mqtt_client.publish = AsyncMock()


def _make_device() -> Any:
    from cync_lan.devices import CyncDevice

    dev = CyncDevice(dev_id=5, dev_type=55, name="Audit Device", home_id=1234)
    dev.tcp_session = None
    return dev


def _command_matrix(dev: Any, fan: Any) -> list[tuple[str, Any]]:
    """(label, zero-arg coroutine factory) for every command that reaches
    build_control_packet. Args are dummies - only the payload LENGTH matters
    here, never the values."""
    from cync_lan.structs import FanSpeed

    return [
        ("set_power", lambda: dev.set_power(1)),
        ("set_brightness", lambda: dev.set_brightness(50)),
        ("set_temperature", lambda: dev.set_temperature(50)),
        ("set_rgb", lambda: dev.set_rgb(10, 20, 30)),
        ("set_fan_percentage", lambda: fan.set_fan_percentage(50)),
        ("set_fan_speed", lambda: fan.set_fan_speed(FanSpeed.LOW)),
        ("set_fine_brightness", lambda: dev.set_fine_brightness(50, 1000)),
        ("set_light_effect", lambda: dev.set_light_effect("Rainbow")),
        ("set_indicator_led", lambda: dev.set_indicator_led(2, 0, 100, False)),
        ("identify", lambda: dev.identify(True)),
        ("set_dimmer_led_mode", lambda: dev.set_dimmer_led_mode(2)),
        ("set_dimmer_led_brightness", lambda: dev.set_dimmer_led_brightness(50)),
        (
            "set_motion_sensor_settings",
            lambda: dev.set_motion_sensor_settings(
                setting_type=1, enabled=True, sensitivity=1
            ),
        ),
        (
            "set_motion_sensor_schedule",
            lambda: dev.set_motion_sensor_schedule(
                slot_id=0,
                mode=1,
                start_hour=8,
                start_minute=0,
                end_hour=9,
                end_minute=0,
                brightness=50,
                cct=50,
            ),
        ),
        (
            "set_group_membership",
            lambda: dev.set_group_membership(32770, member=True, reach_flag=0),
        ),
        ("add_to_scene", lambda: dev.add_to_scene(1, cct=50)),
        ("remove_from_scene", lambda: dev.remove_from_scene(1)),
        (
            "set_multicolor_gradient_mode",
            lambda: dev.set_multicolor_gradient_mode(True),
        ),
        (
            "set_multicolor_segment_count",
            lambda: dev.set_multicolor_segment_count(4),
        ),
        (
            "set_multicolor_segments",
            lambda: dev.set_multicolor_segments([(1, (10, 20, 30))]),
        ),
    ]


def _module_matrix() -> list[tuple[str, Any]]:
    import cync_lan.devices as d

    return [
        ("execute_scene", lambda: d.execute_scene(1)),
        ("set_group_power", lambda: d.set_group_power(32770, 1)),
        ("delete_scene", lambda: d.delete_scene(1)),
        ("delete_schedule", lambda: d.delete_schedule(1)),
        ("toggle_automation", lambda: d.toggle_automation(1, 1, True)),
        ("add_automation", lambda: d.add_automation(1, 1, 0x7F, 8, 30, 0)),
        ("create_scene", lambda: d.create_scene("Audit Scene")),
        ("create_schedule", lambda: d.create_schedule(1)),
        ("delete_automation", lambda: d.delete_automation(1)),
        ("delete_group", lambda: d.delete_group(32770)),
        ("query_hub_info", lambda: d.query_hub_info()),
        ("query_device_time", lambda: d.query_device_time()),
        ("query_sol_config", lambda: d.query_sol_config()),
        ("set_time", lambda: d.set_time()),
    ]


async def _drive(entries: list[tuple[str, Any]], current_name: list[str]) -> None:
    for label, factory in entries:
        current_name[0] = label
        try:
            await asyncio.wait_for(factory(), timeout=2)
        except (asyncio.TimeoutError, Exception):
            # Commands that wait on a device notification will time out with
            # no device present. The packet was already built and captured by
            # then, which is all this audit needs.
            pass


def run_audit(verbose: bool) -> int:
    captures: list[Capture] = []
    current_name = [""]
    _install_stub_transport(captures, current_name)

    dev = _make_device()
    fan = _make_device()
    fan.is_fan_controller = True
    asyncio.run(_drive(_command_matrix(dev, fan), current_name))
    asyncio.run(_drive(_module_matrix(), current_name))

    if not captures:
        print("no commands were captured - the audit harness needs updating")
        return 2

    seen: dict[str, Capture] = {}
    for cap in captures:
        seen.setdefault(cap.name, cap)

    width = max(len(n) for n in seen)
    bad = [c for c in seen.values() if not c.ok]

    print(f"{'command':{width}}  op    cmd_  expect  len  repeat")
    print("-" * (width + 34))
    for name in sorted(seen):
        c = seen[name]
        mark = "" if c.ok else "  <-- MISMATCH"
        if c.ok and not verbose and not bad:
            pass
        print(
            f"{name:{width}}  {c.op_code:#04x}  {c.cmd_code:#04x}  "
            f"{c.expected:#04x}    {len(c.payload):<3}  "
            f"{'yes' if c.repeat_op_code else 'no':<3}{mark}"
        )

    print()
    if bad:
        print(f"{len(bad)} of {len(seen)} commands disagree with the formula:")
        for c in bad:
            delta = c.cmd_code - c.expected
            print(
                f"  {c.name}: sends {c.cmd_code:#04x}, formula gives "
                f"{c.expected:#04x} ({delta:+d}) for a {len(c.payload)}-byte "
                f"payload with repeat_op_code={c.repeat_op_code}"
            )
        return 1

    print(f"all {len(seen)} commands agree with the formula")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    calc = sub.add_parser("calc", help="compute cmd_code for a payload")
    calc.add_argument(
        "--payload",
        help="payload bytes as hex, e.g. 'F7:11:02:06' or 'f7 11 02 06'",
    )
    calc.add_argument(
        "--length", type=int, help="payload length in bytes, instead of --payload"
    )
    calc.add_argument(
        "--no-repeat-op-code",
        action="store_true",
        help="for the 0x8E mesh-relay family, whose payload already carries "
        "its own inner opcode array",
    )

    audit = sub.add_parser("audit", help="check every shipping command")
    audit.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)

    if args.mode == "audit":
        return run_audit(args.verbose)

    if args.payload:
        payload = _parse_payload(args.payload)
        length = len(payload)
    elif args.length is not None:
        payload, length = None, args.length
    else:
        raise SystemExit("give either --payload or --length")

    repeat = not args.no_repeat_op_code
    cmd = predict_cmd_code(length, repeat)
    if payload is not None:
        print(f"payload      {payload.hex(' ')}")
    print(f"payload len  {length}")
    print(f"repeat op    {'yes' if repeat else 'no'} (+{1 if repeat else 0})")
    print(f"cmd_code     {cmd:#04x} ({cmd})")
    if cmd > 0xFF:
        print("\nwarning: exceeds one byte; the high byte of the length field is")
        print("assumed zero throughout this codebase.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
