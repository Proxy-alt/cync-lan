import asyncio
import datetime
import logging
import logging.handlers
import os
import random
import ssl
import struct
import sys
import time
from functools import partial
from pathlib import Path
from typing import Coroutine, Dict, List, Optional, Tuple, Union, Callable

from cync_lan.const import (
    CYNC_CLOUD_IP,
    CYNC_CMD_BROADCASTS,
    CYNC_EXPERIMENTAL_LOG_PATH,
    CYNC_LOG_NAME,
    CYNC_MAX_TCP_CONN,
    CYNC_MITM_LOG_DIR,
    CYNC_MITM_DEV_LOGGER,
    CYNC_RAW,
    CYNC_TCP_WHITELIST,
    CYNC_UNSUPPORTED_LOG_PATH,
    CYNC_UNSUPPORTED_RAW_DEBUG,
    DATA_BOUNDARY,
    FACTORY_EFFECTS_BYTES,
    LIGHT_RUN_MODE_EFFECTS,
    RAW_MSG,
    STREAM_CHUNK_SIZE,
    TCP_BLACKHOLE_DELAY, CYNC_MITM_APP_LOGGER,
)
from cync_lan.metadata.model_info import (
    MULTI_ENDPOINT_TYPES,
    DeviceClassification,
    DeviceTypeInfo,
    device_type_map,
)
from cync_lan.packet import PacketBuilder
from cync_lan.structs import (
    CacheData,
    ControlMessageCallback,
    EntityState,
    FanSpeed,
    GlobalObject,
    MessageCache,
    Tasks, ConnectionType,
)
from cync_lan.utils import bytes2list, extract_firmware_dynamically, format_socat_style

__all__ = [
    "CyncDevice",
    "CyncTCPSession",
    "broadcast_control_command",
    "execute_scene",
    "set_group_power",
    "delete_scene",
    "delete_schedule",
    "toggle_automation",
    "create_scene",
    "create_schedule",
    "add_automation",
    "try_resolve_xlink_notification",
]
logger = logging.getLogger(CYNC_LOG_NAME)
g = GlobalObject()

_unsupported_logger: Optional[logging.Logger] = None
_experimental_logger: Optional[logging.Logger] = None

# ============================================================================
# NAVIGATION INDEX - quick jump points for this ~3450-line file (grep a name
# below, or jump straight to its line number; not exhaustive, just the
# sections/methods most useful for a quick lookup without reading the whole
# file top to bottom).
# ----------------------------------------------------------------------------
# Module-level helpers (unsupported/unknown device capture logging):
#   _get_unsupported_logger              line 131
#   capture_unsupported_device           line 153
#   capture_unknown_packet               line 200
#   _warn_experimental_cmd_code          line 218 (logs once per command name
#     that its cmd_code is PREDICTED, not confirmed - see docs/mesh_opcodes.md)
#   broadcast_control_command            line 239 (module-level: builds +
#     broadcasts a control packet to an arbitrary target_id - extracted from
#     CyncDevice.send_command, which is now a thin wrapper around this,
#     because execute_scene below has no CyncDevice to be `self`)
#   execute_scene                        line 318 (EXPERIMENTAL: home-wide
#     scene activation, 0xEF - no per-device target, target_id=0x00)
#
# class CyncDevice (line 347) - in-memory representation of one physical Cync
# device (light/switch/plug/fan/sensor/hvac): classification, cached state,
# and outbound command methods.
#   __init__                             line 374
#   Classification properties: is_sol_lamp(413) is_hvac(422) is_light(489)
#     is_switch(527) is_plug(557) is_fan_controller(575) has_motion_sensor(589)
#     is_dimmable(600) supports_rgb(608) supports_temperature(622) bt_only(475)
#     has_wifi(483) has_multi_entities(571)
#   Cached-state properties (proxy to the primary EntityState in self.entities):
#     online(636) state(652) brightness(696) temperature(709) red(722)
#     green(735) blue(748) rgb(761) version(439) mac(467)
#   handle_entity_update                 line 780 (status packet -> online tracking -> MQTT)
#   handle_motion_update                 line 834 (motion trigger -> MQTT, bypasses staleness logic)
#   get_ctrl_msg_id_bytes                line 850
#   send_command                         line 867 (thin wrapper around the
#     module-level broadcast_control_command, target_id=self.id)
#   CyncDevice command methods: set_fan_percentage(876) set_fan_speed(901)
#     set_power(926) set_brightness(946) set_fine_brightness(973, EXPERIMENTAL)
#     set_temperature(1010) set_rgb(1037) _send_light_run_mode(1063, shared
#     sender for the 0xE2/0x07 command family) set_lightshow(1090,
#     LightShow-only presets) set_light_effect(1103, general - all 5 modes
#     via LIGHT_RUN_MODE_EFFECTS) _build_motion_sensor_settings_payload(1117)
#     set_motion_sensor_settings(1178, EXPERIMENTAL, wires the payload above
#     into a real send) set_indicator_led(1207, EXPERIMENTAL)
#
# class CyncTCPSession (line 1258) - one TCP connection (device or Cync app):
# reader/writer lifecycle, MITM cloud proxying, and inbound packet parsing.
#   __init__                             line 1277
#   existing_init                        line 1323 (re-init path when a device reconnects)
#   MITM / cloud proxy: start_proxy(1353) start_mitm(1378) is_proxy_good(1402)
#     stop_proxy(1435) stop_mitm(1481) _cloud_proxy_task(1492) _setup_mitm_logger(1519)
#   CyncTCPSession connection handling: blackhole(1566) can_connect(1597)
#     start_tasks(1634) get_ctrl_msg_id_bytes(1674) connection_watcher_task(2613)
#     callback_cleanup_task(2652) receive_task(2691) read(2723) write(2753)
#     close(2806) is_closed(2891)
#     Properties: reader(2860) writer(2868) closing(2876) closed(2884)
#   Packet parsing (active path, in call order):
#     parse_raw_data                     line 1691 (reassembles the TCP byte
#       stream into whole packets, handles partial/resync, feeds parse_packet)
#     parse_packet                       line 1827 (dispatches by header byte:
#       0x43/0x83/0x73/app-request/unknown)
#     _dispatch_device_request           line 1885
#     _handle_43_packet                  line 1940
#     _handle_83_packet                  line 1997
#     _parse_83_device_state             line 2140
#     _handle_73_mesh_control            line 2246
#     _process_73_mesh_info              line 2367
#     ask_for_mesh_info                  line 2562
#     send_a3                            line 2592
#   parse_packet_OLD                     line 2896 (superseded legacy parser -
#     grep confirms nothing calls it; parse_packet above is the live path.
#     Kept in-file for reference only.)
# ============================================================================


def _get_unsupported_logger() -> logging.Logger:
    """Lazily set up the dedicated, always-separate log file for unsupported/unknown
    device captures (see CYNC_UNSUPPORTED_RAW_DEBUG). Mirrors the per-connection MITM
    logger setup, but as a single shared logger so all sightings land in one file."""
    global _unsupported_logger
    if _unsupported_logger is not None:
        return _unsupported_logger
    ulogger = logging.getLogger("cync_lan.unsupported")
    ulogger.setLevel(logging.DEBUG)
    ulogger.propagate = False
    log_path = Path(CYNC_UNSUPPORTED_LOG_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(message)s", datefmt="%Y/%m/%d %H:%M:%S"
    )
    file_handler = logging.handlers.TimedRotatingFileHandler(log_path, when="midnight")
    file_handler.setFormatter(formatter)
    ulogger.addHandler(file_handler)
    _unsupported_logger = ulogger
    return ulogger


def _get_experimental_logger() -> logging.Logger:
    """Lazily set up the dedicated, always-on log file for experimental_*
    command/service invocations (see CYNC_EXPERIMENTAL_LOG_PATH). Unlike
    _get_unsupported_logger()'s capture (opt-in via
    CYNC_UNSUPPORTED_RAW_DEBUG), this one is never gated behind a feature
    flag - every experimental command records itself here the moment it
    runs, so the file is always ready to attach to a bug report without
    the user needing to have pre-enabled anything. See
    _warn_experimental_cmd_code/_warn_experimental_group_targeting/
    _warn_experimental_transport_unconfirmed, which write to this on
    every call (not just once-per-process like their console warning).

    Since this runs on every experimental command with no opt-in flag,
    it must never be able to block/break the actual command being sent -
    if CYNC_EXPERIMENTAL_LOG_PATH's directory can't be created (bad
    permissions, read-only filesystem, etc.), this logs one error to the
    main logger and falls back to a no-op logger (NullHandler) instead of
    raising, rather than taking down whatever command triggered it."""
    global _experimental_logger
    if _experimental_logger is not None:
        return _experimental_logger
    elogger = logging.getLogger("cync_lan.experimental")
    elogger.setLevel(logging.DEBUG)
    elogger.propagate = False
    try:
        log_path = Path(CYNC_EXPERIMENTAL_LOG_PATH)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        formatter = logging.Formatter(
            "%(asctime)s.%(msecs)03d %(message)s", datefmt="%Y/%m/%d %H:%M:%S"
        )
        file_handler = logging.handlers.TimedRotatingFileHandler(log_path, when="midnight")
        file_handler.setFormatter(formatter)
        elogger.addHandler(file_handler)
    except OSError as e:
        logger.error(
            f"Could not set up the experimental-features log file at "
            f"{CYNC_EXPERIMENTAL_LOG_PATH}: {e}. Experimental command usage will "
            f"only appear in the main log from now on."
        )
        elogger.addHandler(logging.NullHandler())
    _experimental_logger = elogger
    return elogger


def capture_unsupported_device(
    ip_lp: str,
    dev_id: int,
    from_pkt: str,
    ctrl_bytes: Optional[bytes],
    raw: bytes,
):
    """Write one sighting of an unrecognized/unsupported device to the dedicated
    unsupported-devices log, tagged with whatever identity info is available.
    No-op unless CYNC_UNSUPPORTED_RAW_DEBUG is set - independent of CYNC_RAW_DEBUG,
    so this can be left running for an extended capture without full raw-debug noise.

    Filters:
      - Feature flag off                       → no-op
      - Known + supported device               → no-op (nothing unusual)
      - dev_id == 0 (mesh broadcast pseudo-ID) → no-op (protocol artifact, not a device)

    About dev_id == 0
    -----------------
    In the Cync BTLE mesh protocol, valid addressable device IDs occupy the
    1–255 range.  ID 0 is reserved as a broadcast / group pseudo-address used
    by the mesh network itself.  Every bridge re-broadcasts a status update for
    dev_id=0 on every state-change cycle (fa db 13 packets with the group's
    aggregate power state).  These are expected, benign, and appear in very high
    volume — logging them would drown the unsupported-devices file with hundreds
    of false-positives per minute.
    """
    if not CYNC_UNSUPPORTED_RAW_DEBUG:
        return
    if dev_id == 0:
        return  # mesh broadcast/group pseudo-ID, not an actual device
    node_repr = g.ncync_server.node_devices.get(dev_id)
    if node_repr is not None:
        if node_repr.metadata is not None and node_repr.metadata.supported:
            return  # a real, supported device - nothing unusual to capture
        name = node_repr.name
        dev_type = node_repr.type
    else:
        name = "(not in local config)"
        dev_type = "?"
    ctrl_str = ctrl_bytes.hex(" ") if ctrl_bytes else "n/a"
    _get_unsupported_logger().debug(
        f"{ip_lp} dev_id={dev_id} name='{name}' type={dev_type} from_pkt={from_pkt} "
        f"ctrl_bytes={ctrl_str}\nHEX: {raw.hex(' ')}\nINT: {list(raw)}"
    )


def capture_unknown_packet(ip_lp: str, reason: str, raw: bytes):
    """Write a genuinely unrecognized packet to the dedicated unsupported-devices log
    - one that doesn't even parse far enough to have a dev_id (an unrecognized
    top-level header byte, or unrecognized ctrl_bytes on a 0x83/0x73 packet). This is
    the more important case for something like a standalone sensor that might not use
    any packet shape cync-lan already knows about at all. See capture_unsupported_device
    for the dev_id-based case. No-op unless CYNC_UNSUPPORTED_RAW_DEBUG is set.
    """
    if not CYNC_UNSUPPORTED_RAW_DEBUG:
        return
    _get_unsupported_logger().debug(
        f"{ip_lp} UNKNOWN ({reason})\nHEX: {raw.hex(' ')}\nINT: {list(raw)}"
    )


_EXPERIMENTAL_CMDS_WARNED: set = set()


def _log_experimental(lp: str, name: str, reason: str) -> None:
    """Writes one line to the dedicated experimental-features log file
    (CYNC_EXPERIMENTAL_LOG_PATH) EVERY time an experimental command runs -
    unlike the console warnings below, which only fire once per process
    per command name. Pull this file (not the main log) when reporting a
    bug about any experimental_* command/service: it's a focused record
    of exactly which experimental functionality ran, when, and why it's
    experimental, without the volume of full raw/debug logging."""
    _get_experimental_logger().info(f"{lp} {name} - {reason}")


def _warn_experimental_cmd_code(lp: str, name: str) -> None:
    """Log once per process per command name that this command uses a
    cmd_code PREDICTED (not confirmed against a real packet capture) by
    the length formula documented in docs/mesh_opcodes.md's "TCP relay
    envelope research" section - validated 3/3 against already-confirmed
    production commands (set_power/set_brightness/set_rgb/set_lightshow),
    but the source class that formula came from is itself flagged
    @Deprecated in the decompiled app, so treat it as a strong prediction,
    not a certainty. If this command doesn't behave as expected, please
    report it (device model + what happened) - see docs/mesh_opcodes.md.

    Every call (not just the first per process) is also recorded in the
    dedicated experimental-features log file - see _log_experimental().
    """
    _log_experimental(lp, name, "predicted (not confirmed) cmd_code")
    if name in _EXPERIMENTAL_CMDS_WARNED:
        return
    _EXPERIMENTAL_CMDS_WARNED.add(name)
    logger.warning(
        f"{lp} EXPERIMENTAL: '{name}' uses a predicted (not confirmed) cmd_code - "
        f"see docs/mesh_opcodes.md's 'TCP relay envelope research'. If this doesn't "
        f"work as expected, please report it (device model + observed behavior)."
    )


def _warn_experimental_group_targeting(lp: str, name: str) -> None:
    """Log once per process per command name that this command targets a
    group's MeshAddress (32768-65535) rather than a single device - unlike
    every other EXPERIMENTAL command in this file, op_code/cmd_code here
    are NOT predictions (this reuses an already-confirmed command exactly).
    What's unconfirmed is the ADDRESSING: whether device firmware actually
    responds to a group-range target as "the whole group," which has never
    been tested against real hardware - see docs/mesh_opcodes.md's "Groups
    control" section.

    Every call (not just the first per process) is also recorded in the
    dedicated experimental-features log file - see _log_experimental().
    """
    _log_experimental(lp, name, "unconfirmed group-address targeting")
    if name in _EXPERIMENTAL_CMDS_WARNED:
        return
    _EXPERIMENTAL_CMDS_WARNED.add(name)
    logger.warning(
        f"{lp} EXPERIMENTAL: '{name}' targets a group MeshAddress, which has never "
        f"been confirmed to work against real device firmware - see "
        f"docs/mesh_opcodes.md's 'Groups control' section. If this doesn't work as "
        f"expected, please report it (device model + observed behavior)."
    )


def _warn_experimental_transport_unconfirmed(lp: str, name: str) -> None:
    """Log once per process per command name that this command's real
    envelope (in the decompiled app) is a PPP/HDLC-style, 0x7E-delimited-
    and-byte-stuffed frame (XlinkTranslatorKt.m14449a()/Xlink.m14391a()) -
    structurally unlike cync-lan's own confirmed TCP wire format, and
    traced to code already flagged @Deprecated as possibly the phone app's
    OLDER command channel. Whether this command rides over the same TCP
    relay cync-lan intercepts at all, or is BLE-GATT-specific, is
    genuinely unresolved - not just an unconfirmed cmd_code/target, but an
    open question about which transport carries it in practice. See
    docs/cync_automations.md's "HA -> Cync (writing)" section.

    Every call (not just the first per process) is also recorded in the
    dedicated experimental-features log file - see _log_experimental().
    """
    _log_experimental(lp, name, "unconfirmed real transport (may not be TCP at all)")
    if name in _EXPERIMENTAL_CMDS_WARNED:
        return
    _EXPERIMENTAL_CMDS_WARNED.add(name)
    logger.warning(
        f"{lp} EXPERIMENTAL: '{name}''s real envelope in the app is structurally "
        f"different from cync-lan's own wire format, and whether it even rides over "
        f"the same TCP relay at all is unconfirmed - see "
        f"docs/cync_automations.md's 'HA -> Cync (writing)' section. If this doesn't "
        f"work as expected, please report it (device model + observed behavior)."
    )


# ============================================================================
# Legacy Xlink/Frame HDLC notification correlation (create_scene/
# create_schedule's hub-allocated-ID response) - see
# src/cync_lan/packet/xlink_legacy.py's module docstring for full context.
#
# cync-lan's OUTGOING side for this whole command family (delete_scene,
# delete_schedule, toggle_automation, and now create_scene/create_schedule)
# does NOT emit a genuine Xlink/Frame HDLC frame - it sends the confirmed
# inner-payload bytes through cync-lan's own PacketBuilder envelope instead,
# per established precedent. That means there is no real msgId field cync-lan
# controls for the hub to echo back if it responds in its own native format.
# Correlation here is therefore by op_code ONLY, serialized per op_code via a
# lock (so a second concurrent create_scene() call simply waits its turn
# rather than racing to claim an ambiguous response) - not the (op_code,
# msgId) pairing the real app itself uses, since cync-lan has no msgId of its
# own to match against.
_PENDING_XLINK_RESPONSES: Dict[int, "asyncio.Future"] = {}
_XLINK_RESPONSE_LOCKS: Dict[int, asyncio.Lock] = {}


def _get_xlink_response_lock(op_code: int) -> asyncio.Lock:
    lock = _XLINK_RESPONSE_LOCKS.get(op_code)
    if lock is None:
        lock = _XLINK_RESPONSE_LOCKS[op_code] = asyncio.Lock()
    return lock


def try_resolve_xlink_notification(packet_data: bytes) -> bool:
    """Called from the receive loop's "unknown ctrl_bytes" fallback (both
    CyncTCPSession._handle_83_packet and _handle_73_mesh_control - the two
    places cync-lan's own confirmed 0x7E-bound-inner-data convention lands
    content it doesn't recognize). Attempts to decode `packet_data` as a
    legacy Xlink/Frame HDLC notification (see decode_xlink_frame) and, if
    its op_code matches an outstanding create_scene()/create_schedule()
    call, resolves that call's pending future with the raw notification
    payload.

    Returns True if the frame was successfully decoded AND consumed by a
    pending request - the caller should treat the packet as handled, not
    fall through to its existing unknown-packet logging. Returns False for
    every other case (not a well-formed Xlink frame at all, or a
    well-formed frame whose op_code nobody is currently waiting for) -
    callers fall through to their existing logging unchanged in that case,
    so this is purely additive and never suppresses real diagnostic
    visibility into genuinely unrecognized traffic.
    """
    from cync_lan.packet import decode_xlink_frame

    frame = decode_xlink_frame(packet_data)
    if frame is None:
        return False
    fut = _PENDING_XLINK_RESPONSES.get(frame.op_code)
    if fut is None or fut.done():
        return False
    fut.set_result(frame.payload)
    return True


async def _await_xlink_notification(op_code: int, timeout: float = 10.0) -> Optional[bytes]:
    """Register a pending wait for a legacy Xlink/Frame HDLC notification
    with this op_code, then wait up to `timeout` seconds (10s matches the
    real app's own StatusNotificationQueryCommand default). Returns the raw
    notification payload, or None on timeout - a timeout is a normal,
    expected outcome here (not an error) given the still-unconfirmed
    question of whether this notification channel rides the same TCP relay
    cync-lan intercepts at all; see _warn_experimental_transport_unconfirmed.
    """
    async with _get_xlink_response_lock(op_code):
        fut = asyncio.get_event_loop().create_future()
        _PENDING_XLINK_RESPONSES[op_code] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            _PENDING_XLINK_RESPONSES.pop(op_code, None)


async def broadcast_control_command(
    op: int,
    cmd_: int,
    target_id: int,
    sub_id: int,
    payload: bytes,
    m_cb: ControlMessageCallback,
    lp: str,
    repeat_op_code: bool = True,
) -> None:
    """Build and broadcast a control packet to the TCP pool, targeting an
    arbitrary `target_id`.

    Extracted from CyncDevice.send_command() (a thin wrapper around this,
    always passing target_id=self.id) so commands that don't target a
    specific device - e.g. a home-wide scene activation, which has no
    CyncDevice to be `self` - can still go out through the same broadcast
    path. Pure extraction: byte-for-byte identical behavior to the
    pre-refactor send_command for any caller that does pass a real
    device's id.

    repeat_op_code: forwarded to PacketBuilder.build_control_packet - see
    its docstring. False for the 0x8E "mesh-relay" op family.
    """
    tasks = []
    tcp_pool = await g.ncync_server.get_dev_tcp_pool()
    if not tcp_pool:
        logger.debug(f"{lp} no eligible TCP connections available for command broadcast")
        return

    tcp_connections: List["CyncTCPSession"] = random.sample(
        tcp_pool,
        k=min(CYNC_CMD_BROADCASTS, len(tcp_pool)),
    )
    for bridge_device in tcp_connections:
        if bridge_device.ready_to_control or bridge_device.mitm_mode:
            cmsg_id = bridge_device.get_ctrl_msg_id_bytes()[0]

            inner_pkt = PacketBuilder.build_control_packet(
                msg_id=cmsg_id,
                target_id=target_id,
                sub_id=sub_id,
                op_code=op,
                cmd_code=cmd_,
                command_payload=payload,
                repeat_op_code=repeat_op_code,
            )

            full_packet = PacketBuilder.build_outer_packet(
                packet_type=0x73,
                queue_id=bridge_device.queue_id,
                inner_packet=inner_pkt
            )

            # bridge_device.node can still be None here: ready_to_control is set
            # (in send_a3()) before the bridge's first MeshInfo response sets
            # node, so a command can race ahead of self-identification. These
            # are debug-only strings, but an unguarded .node.id crashed the
            # whole MQTT receive loop (uncaught AttributeError propagating out
            # of send_command), taking down MQTT state updates and command
            # handling until a manual restart.
            bridge_node_id = bridge_device.node.id if bridge_device.node else "unidentified"

            if bridge_device.mitm_mode is True:
                logger.debug(
                    f"{lp} MITM mode active for this device: {bridge_device.ip_address} (ID: {bridge_node_id})"
                    f" not writing data >>> \n\n{full_packet.hex(" ")}")
            else:
                m_cb.id = cmsg_id
                m_cb.message = full_packet
                m_cb.sent_at = time.time()
                bridge_device.messages.control[cmsg_id] = m_cb
                tasks.append(bridge_device.write(full_packet))
                str_appnd = "..."
                if CYNC_RAW:
                    str_appnd = (f' state to device ({bridge_device.ip_address}[{bridge_node_id}|queue_id: {bridge_device.queue_id.hex(" ")}):\n'
                                 f'HEX: {full_packet.hex(" ")}\n'
                                 f'INT: {bytes2list(full_packet)}\n')
                    logger.debug(f"{lp} Sending{str_appnd}")

    if tasks:
        await asyncio.gather(*tasks)


async def set_group_power(group_id: int, state: int) -> None:
    """EXPERIMENTAL: turns every device in a group on/off in one broadcast,
    by targeting the group's own MeshAddress instead of a single device.

    Unlike every other EXPERIMENTAL command in this module, op_code/cmd_code
    here are NOT predictions - this reuses set_power's already-confirmed
    0xD0/0x0D exactly, byte-for-byte (see the "Confirmed, already shipping
    in production" table in docs/mesh_opcodes.md). What's unconfirmed is the
    ADDRESSING itself.

    A real-hardware trace found `broadcast_control_command`'s `target_id`
    and `sub_id` parameters were never two independent fields - together
    they already ARE the outer envelope's one 2-byte little-endian
    MeshAddress (`target_id` = low byte, `sub_id` = high byte). Every
    existing single-device command just always hardcodes sub_id=0x00,
    which happens to also be correct for non-multi-gang devices, so nobody
    noticed the high byte was live. Group addresses (32768-65535, exactly
    the `group_id` key cync-lan's own parsed `groups` dict already uses -
    see cloud_api.py's `raw_group["groupID"]`) fit entirely inside that same
    16-bit space - splitting one into (target_id, sub_id) needs no
    PacketBuilder changes at all, since `_require_u8` already accepts any
    byte 0-255 in both fields.

    What's NOT confirmed: whether device firmware actually treats a
    group-range target as "respond as the whole group" - the one real 0x8E
    packet capture available used broadcast 0xFFFF, which is byte-identical
    under both the old and new addressing model and doesn't independently
    prove this. Separately, the app's own multi-device command path looks
    like a per-device fan-out loop, not evidence it ever relies on a true
    group broadcast. See docs/mesh_opcodes.md's "Groups control" section.

    group_id: the full 32768+ group MeshAddress, matching cync-lan's own
    groups dict key exactly (not an offset from 32768).
    """
    lp = "set_group_power:"
    _warn_experimental_group_targeting(lp, "set_group_power")
    if not (0 <= group_id <= 0xFFFF):
        logger.error(f"{lp} Invalid group_id: {group_id} must be 0-65535")
        return
    if state not in (0, 1):
        logger.error(f"{lp} Invalid state: {state} must be 0 or 1")
        return
    target_id = group_id & 0xFF
    sub_id = (group_id >> 8) & 0xFF
    op = 0xD0
    cmd_ = 0x0D
    payload = struct.pack(">BBBBB", 0x11, 0x02, state, 0x00, 0x00)
    m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
    await broadcast_control_command(op, cmd_, target_id, sub_id, payload, m_cb, lp)


async def execute_scene(scene_id: int) -> None:
    """EXPERIMENTAL: activates a saved Cync Scene (a named multi-device
    state snapshot, see docs/cync_automations.md).

    Corrected after a real-hardware test of the sibling command
    set_indicator_led (same op family) came back a total no-op: tracing
    ExecuteSceneCommand's actual send path in the decompiled app
    (XlinkDeviceManager.CommandDelegate.h(), same as
    SetStatusIndicatorSettingsCommand/SetMotionSensorSettingsCommand) shows
    the real outer op is a hardcoded 0x8E "mesh-relay" op, not the
    0xEF/0xF7-family bytes those command classes' own opcode arrays
    (misleadingly close to a real op_code field) start with - those bytes
    are actually the payload's own leading byte. Independently confirmed
    against a real captured packet (docs/debugging_sessions/3 devices/
    Plug - Toggle Power/Plug.md) whose checksum only balances with op=0x8E
    and no repeated op_code byte before the payload - see
    PacketBuilder.build_control_packet's repeat_op_code param.

    Scenes are home-wide, not tied to any specific device - there's no
    CyncDevice to be `self` here, unlike every other command in this
    module, so this goes straight through broadcast_control_command()
    with target_id=0x00 (MeshAddress's documented "none/self/unassigned"
    sentinel, see docs/mesh_opcodes.md - the real capture used a
    target_id/sub_id of 0xFF/0xFF instead for its own broadcast-style
    command, so this is still an additional, separate, unconfirmed guess
    on top of the now-corrected op/cmd_/payload).

    cmd_ (0x0C) is PREDICTED, not confirmed - see
    _warn_experimental_cmd_code and docs/mesh_opcodes.md.
    """
    lp = "execute_scene:"
    _warn_experimental_cmd_code(lp, "execute_scene")
    if not (0 <= scene_id <= 255):
        logger.error(f"{lp} Invalid scene_id: {scene_id} must be 0-255")
        return
    op = 0x8E
    cmd_ = 0x0C
    # Payload: 0xEF (leading discriminator byte, previously mistaken for the
    # outer op) + 0x11, 0x02, scene_id, 0x01 - 5 bytes.
    payload = struct.pack(">BBBBB", 0xEF, 0x11, 0x02, scene_id, 0x01)
    m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
    await broadcast_control_command(
        op, cmd_, 0x00, 0x00, payload, m_cb, lp, repeat_op_code=False
    )


async def delete_scene(scene_id: int) -> None:
    """EXPERIMENTAL: deletes a saved Cync Scene.

    Unlike execute_scene, the outer op_code here (0x1F) is directly
    confirmed - DeleteSceneHubCommand.java writes it verbatim as
    XlinkCommandCode.HUB_DELETE_SCENE, not hidden behind a hardcoded-0x8E
    relay the way SetStatusIndicatorSettingsCommand/etc. were. What's
    unconfirmed is different and deeper: the real app builds this
    command's actual wire frame via XlinkTranslatorKt.m14449a()/
    Xlink.m14391a() - a PPP/HDLC-style, 0x7E-delimited-and-byte-stuffed
    frame structurally unlike cync-lan's own confirmed TCP wire format,
    traced to code already flagged @Deprecated as possibly the phone
    app's OLDER command channel (see docs/cync_automations.md's "HA ->
    Cync (writing)" section). Whether this command even rides over the
    same TCP relay cync-lan intercepts at all is genuinely unresolved -
    this sends the confirmed payload through cync-lan's own PacketBuilder
    envelope anyway, as a real-hardware experiment. See
    _warn_experimental_transport_unconfirmed.

    Payload: sceneId as a 2-byte little-endian int (confirmed via
    DeleteSceneHubCommand.java's WriteBuffer.m14444d() call - not 1 byte
    like execute_scene's own sceneId field, a real inconsistency in the
    app's own code, not an assumption here).

    target_id/sub_id: no MeshAddress field found anywhere in the real
    app's payload for this command - 0x00/0x00 used as a guessed
    sentinel, same precedent as execute_scene.

    cmd_ is PREDICTED via the length formula, not confirmed.
    """
    lp = "delete_scene:"
    _warn_experimental_cmd_code(lp, "delete_scene")
    _warn_experimental_transport_unconfirmed(lp, "delete_scene")
    if not (0 <= scene_id <= 0xFFFF):
        logger.error(f"{lp} Invalid scene_id: {scene_id} must be 0-65535")
        return
    op = 0x1F
    payload = struct.pack("<H", scene_id)
    cmd_ = 7 + len(payload)
    m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
    await broadcast_control_command(op, cmd_, 0x00, 0x00, payload, m_cb, lp)


async def delete_schedule(schedule_id: int) -> None:
    """EXPERIMENTAL: deletes a saved Cync Schedule (a time+day-of-week
    trigger that fires a scene). See delete_scene()'s docstring for the
    full explanation of what's confirmed (op_code) vs. genuinely
    unresolved (which transport this rides over) - identical situation,
    different command.

    Payload: scheduleId as a 2-byte little-endian int (confirmed via
    DeleteScheduleHubCommand.java's WriteBuffer.m14444d() call).

    target_id/sub_id: 0x00/0x00 guessed sentinel, same precedent as
    execute_scene/delete_scene.

    cmd_ is PREDICTED via the length formula, not confirmed.
    """
    lp = "delete_schedule:"
    _warn_experimental_cmd_code(lp, "delete_schedule")
    _warn_experimental_transport_unconfirmed(lp, "delete_schedule")
    if not (0 <= schedule_id <= 0xFFFF):
        logger.error(f"{lp} Invalid schedule_id: {schedule_id} must be 0-65535")
        return
    op = 0x94
    payload = struct.pack("<H", schedule_id)
    cmd_ = 7 + len(payload)
    m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
    await broadcast_control_command(op, cmd_, 0x00, 0x00, payload, m_cb, lp)


async def toggle_automation(schedule_id: int, scene_id: int, enabled: bool) -> None:
    """EXPERIMENTAL: enables/disables a saved Cync Schedule without
    deleting it. See delete_scene()'s docstring for the general
    explanation of what's confirmed (op_code) vs. this command family's
    real-vs-BLE transport question.

    UPDATE, follow-up research: traced this command's one real call site,
    RoutinesService.applyScheduleEnabled() - it picks between
    ToggleAutomationHubCommand (this payload) and a sibling
    ToggleAutomationCommand based on whether a WiFi hub device controller
    exists for the schedule's location, NOT a version/feature gate. When
    one exists, dispatch goes through hubDeviceController.mo14149i() - the
    exact same call path already used by the other Hub Scenes/Schedules
    commands (create/delete scene/schedule) elsewhere in that same file.
    Since cync-lan's own target hardware (WiFi bulbs/plugs bridging BLE
    mesh devices) always has a WiFi hub in this sense, this is the branch
    that applies. This raises confidence that this payload is the right
    one for cync-lan's use case - by structural analogy to the other
    already-shipped Hub commands sharing the identical dispatch path - but
    it does NOT independently confirm the actual wire bytes (still built
    via the PPP/HDLC-style Xlink.a() framer, still not proven to ride the
    same TCP relay cync-lan intercepts). Treat as "same confidence as the
    other 2 wired-in Hub commands," not as fully resolved.

    Payload: 52 bytes total, confirmed field-by-field via
    ToggleAutomationHubCommand.java's WriteBuffer calls (each a
    fixed-width method, no width ambiguity):
      [0:2]   scheduleId, uint16 LE
      [2:6]   sceneId, uint32 LE (the SAME underlying field is written
              2-byte-wide in delete_scene() and 4-byte-wide here - a real
              app-code inconsistency, not a bug in this implementation)
      [6:32]  26 zero-padding bytes
      [32:34] 0, uint16 LE
      [34]    enabled flag, 1 byte (0/1)
      [35]    0, 1 byte
      [36:52] 16 zero bytes

    target_id/sub_id: 0x00/0x00 guessed sentinel, same precedent as
    execute_scene/delete_scene/delete_schedule.

    cmd_ is PREDICTED via the length formula, not confirmed.
    """
    lp = "toggle_automation:"
    _warn_experimental_cmd_code(lp, "toggle_automation")
    _warn_experimental_transport_unconfirmed(lp, "toggle_automation")
    if not (0 <= schedule_id <= 0xFFFF):
        logger.error(f"{lp} Invalid schedule_id: {schedule_id} must be 0-65535")
        return
    if not (0 <= scene_id <= 0xFFFFFFFF):
        logger.error(f"{lp} Invalid scene_id: {scene_id} must be 0-4294967295")
        return
    op = 0x93
    payload = (
        struct.pack("<H", schedule_id)
        + struct.pack("<I", scene_id)
        + bytes(26)
        + struct.pack("<H", 0)
        + struct.pack(">B", 1 if enabled else 0)
        + struct.pack(">B", 0)
        + bytes(16)
    )
    cmd_ = 7 + len(payload)
    m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
    await broadcast_control_command(op, cmd_, 0x00, 0x00, payload, m_cb, lp)


async def create_scene(name: str, timeout: float = 10.0) -> Optional[int]:
    """EXPERIMENTAL: creates a new, empty Cync Scene, returning the
    Hub-allocated scene_id - or None if no response arrives within
    `timeout` seconds (see below; a normal, expected outcome, not an
    error). Use CyncDevice.add_to_scene() afterward to give the new scene
    per-device state.

    Confirmed via CreateSceneHubCommand.java: real op_code
    HUB_CREATE_SCENE = 0x10 (XlinkCommandCode.java) - not the 0x8E-relay
    family. Payload (CreateSceneRequest.getF38055a() via PackKt.m14453a()):
    a String30-encoded name (UTF-8 bytes, truncated/zero-padded to exactly
    30 bytes - Java's Arrays.copyOf semantics: shorter names are
    zero-padded, longer names silently truncated at the byte level, which
    can split a multi-byte UTF-8 character if the name is long - matches
    the real app's own behavior exactly, not a bug here) + a 2-byte
    little-endian iconId (always 0 - the real app never sets a non-zero
    icon via this path) + 18 zero-padding bytes = 50 bytes total.

    Unlike delete_scene/delete_schedule/toggle_automation, this command's
    whole purpose is to get an ID cync-lan doesn't get to choose - the Hub
    allocates one and reports it back asynchronously via
    HubCreateSceneNotification. This is the first command in this file
    that reads a response back off the wire at all - see
    src/cync_lan/packet/xlink_legacy.py and _await_xlink_notification()
    for the (also experimental, also transport-unconfirmed) decoder this
    relies on. Confirmed response payload
    (HubCreateSceneNotification.XlinkParser): 1-byte errorCode + 2-byte
    little-endian scene_id.

    cmd_ is PREDICTED via the length formula, not confirmed.
    """
    lp = "create_scene:"
    _warn_experimental_cmd_code(lp, "create_scene")
    _warn_experimental_transport_unconfirmed(lp, "create_scene")
    name_bytes = name.encode("utf-8")[:30].ljust(30, b"\x00")
    op = 0x10
    payload = name_bytes + struct.pack("<H", 0) + bytes(18)
    cmd_ = 7 + len(payload)
    m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
    await broadcast_control_command(op, cmd_, 0x00, 0x00, payload, m_cb, lp)

    response = await _await_xlink_notification(op, timeout=timeout)
    if response is None:
        logger.warning(
            f"{lp} No response received within {timeout}s - either this "
            f"notification channel doesn't ride the TCP relay cync-lan "
            f"intercepts (see docstring), or the create itself failed silently."
        )
        return None
    if len(response) < 3:
        logger.error(f"{lp} Malformed response (expected >=3 bytes, got {len(response)})")
        return None
    error_code = response[0]
    scene_id = struct.unpack_from("<H", response, 1)[0]
    if error_code != 0:
        logger.error(f"{lp} Hub reported errorCode={error_code} for scene {name!r}")
        return None
    logger.info(f"{lp} Created scene {name!r} -> scene_id={scene_id}")
    return scene_id


async def create_schedule(
    scene_id: int, enabled: bool = True, timeout: float = 10.0
) -> Optional[int]:
    """EXPERIMENTAL: allocates a new Cync Schedule bound to an existing
    scene_id, returning the Hub-allocated schedule_id - or None on timeout,
    same shape/caveats as create_scene() (read that docstring first).

    This command ONLY allocates a bare schedule_id and records the target
    scene_id + enabled flag - it does NOT set a day-of-week/time trigger.
    Confirmed via CreateScheduleHubCommand.java
    (RoutinesService.m14800Q(), "getNextScheduleId") - call
    add_automation() immediately after this to give the schedule an
    actual trigger; without that follow-up call the schedule exists but
    never fires (matches the real app's own two-command sequence exactly
    - see docs/cync_automations.md's Recommendation item 5).

    Payload: 50 bytes, confirmed field-by-field via
    CreateScheduleHubCommand.java's WriteBuffer calls: sceneId (4-byte LE,
    offset 0-3) + 26 zero-padding bytes (offset 4-29) + a zero uint16
    (offset 30-31) + the enabled flag (1 byte, offset 32) + a zero byte
    (offset 33) + 16 zero-padding bytes (offset 34-49).

    Confirmed response payload (HubCreateScheduleNotification.XlinkParser,
    identical shape to create_scene()'s): 1-byte errorCode + 2-byte
    little-endian schedule_id.

    cmd_ is PREDICTED via the length formula, not confirmed.
    """
    lp = "create_schedule:"
    _warn_experimental_cmd_code(lp, "create_schedule")
    _warn_experimental_transport_unconfirmed(lp, "create_schedule")
    if not (0 <= scene_id <= 0xFFFFFFFF):
        logger.error(f"{lp} Invalid scene_id: {scene_id} must be 0-4294967295")
        return None
    op = 0x92
    payload = (
        struct.pack("<I", scene_id)
        + bytes(26)
        + struct.pack("<H", 0)
        + struct.pack(">B", 1 if enabled else 0)
        + struct.pack(">B", 0)
        + bytes(16)
    )
    cmd_ = 7 + len(payload)
    m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
    await broadcast_control_command(op, cmd_, 0x00, 0x00, payload, m_cb, lp)

    response = await _await_xlink_notification(op, timeout=timeout)
    if response is None:
        logger.warning(
            f"{lp} No response received within {timeout}s - either this "
            f"notification channel doesn't ride the TCP relay cync-lan "
            f"intercepts (see create_scene()'s docstring), or the create "
            f"itself failed silently."
        )
        return None
    if len(response) < 3:
        logger.error(f"{lp} Malformed response (expected >=3 bytes, got {len(response)})")
        return None
    error_code = response[0]
    schedule_id = struct.unpack_from("<H", response, 1)[0]
    if error_code != 0:
        logger.error(f"{lp} Hub reported errorCode={error_code} for scene_id={scene_id}")
        return None
    logger.info(
        f"{lp} Created schedule -> schedule_id={schedule_id} (scene_id={scene_id})"
    )
    return schedule_id


async def add_automation(
    schedule_id: int,
    scene_id: int,
    day_mask: int,
    hour: int,
    minute: int,
    second: int = 0,
) -> None:
    """EXPERIMENTAL: sets a Schedule's actual trigger - which days it runs
    on, at what local time, and which Scene it fires. Must be called
    after create_schedule() has allocated a schedule_id; a schedule with
    no add_automation() call never fires (see create_schedule()'s
    docstring). No response is expected for this command (unlike
    create_scene()/create_schedule()) - confirmed via
    AddAutomationHubCommand.java extending UnitDeviceCommand, not
    StatusNotificationQueryCommand<T> like its two siblings.

    Confirmed via AddAutomationHubCommand.java (real op_code (byte)-107 =
    0x95, XlinkCommandCode.java): 11-byte WriteBuffer payload: scheduleId
    (2-byte LE) + sceneId (2-byte LE - the SAME underlying field
    execute_scene writes 1-byte-wide and toggle_automation writes
    4-byte-wide; yet another real app-code width inconsistency across
    this command family, not a bug here) + a day-of-week bitmask (1 byte:
    Sunday=bit0(0x01) through Saturday=bit6(0x40), OR'd together) + a
    4-byte little-endian time value + sceneId again (2-byte LE).

    The time value here only supports a FIXED local time-of-day - matches
    this feature's intended scope (time/day triggers only). The real
    app's own ScheduleTime.Local case computes
    LocalDateTime.toEpochSecond(UTC) against the schedule's actual
    calendar date, but the receiving side is confirmed to only care about
    the time-of-day component (day-of-week is already separately encoded
    via the bitmask byte) - so this implementation always uses the Unix
    epoch (1970-01-01 UTC) as the reference date, making the encoded
    value exactly `hour*3600 + minute*60 + second`. Sunrise/Sunset
    triggers use a completely different, offset-less encoding in the real
    app (fixed sentinel bytes -15/-16, sign-extended into the same 4-byte
    field - no configurable before/after offset found anywhere in this
    payload) - NOT implemented here, out of scope for a time/day-only
    feature.

    day_mask: 0-127 (7 bits, Sunday=bit0 through Saturday=bit6).

    cmd_ is PREDICTED via the length formula, not confirmed.
    """
    lp = "add_automation:"
    _warn_experimental_cmd_code(lp, "add_automation")
    _warn_experimental_transport_unconfirmed(lp, "add_automation")
    if not (0 <= schedule_id <= 0xFFFF):
        logger.error(f"{lp} Invalid schedule_id: {schedule_id} must be 0-65535")
        return
    if not (0 <= scene_id <= 0xFFFF):
        logger.error(f"{lp} Invalid scene_id: {scene_id} must be 0-65535")
        return
    if not (0 <= day_mask <= 0x7F):
        logger.error(f"{lp} Invalid day_mask: {day_mask} must be 0-127 (Sun..Sat bits)")
        return
    if not (0 <= hour <= 23):
        logger.error(f"{lp} Invalid hour: {hour} must be 0-23")
        return
    if not (0 <= minute <= 59):
        logger.error(f"{lp} Invalid minute: {minute} must be 0-59")
        return
    if not (0 <= second <= 59):
        logger.error(f"{lp} Invalid second: {second} must be 0-59")
        return
    op = 0x95
    time_value = hour * 3600 + minute * 60 + second
    payload = (
        struct.pack("<H", schedule_id)
        + struct.pack("<H", scene_id)
        + struct.pack(">B", day_mask)
        + struct.pack("<i", time_value)
        + struct.pack("<H", scene_id)
    )
    cmd_ = 7 + len(payload)
    m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
    await broadcast_control_command(op, cmd_, 0x00, 0x00, payload, m_cb, lp)


class CyncDevice:
    """
    A class to represent a physical Cync device
    """

    lp = "CyncDevice:"
    id: int = None
    ip_address: str = None
    type: Optional[int] = None
    _supports_rgb: Optional[bool] = None
    _supports_temperature: Optional[bool] = None
    _is_light: Optional[bool] = None
    _is_switch: Optional[bool] = None
    _is_plug: Optional[bool] = None
    _is_fan_controller: Optional[bool] = None
    _is_hvac: Optional[bool] = None
    _mac: Optional[str] = None
    wifi_mac: Optional[str] = None
    hvac: Optional[dict] = None
    _online: bool = False
    metadata: Optional[DeviceTypeInfo] = None
    entities: Optional[Dict[int, EntityState]] = None
    last_valid_state_ts: float = 0
    num_late_states: int = 0
    mqtt_metadata = None
    tcp_session: Optional["CyncTCPSession"] = None
    # Which TCP-connected device most recently relayed a status update for
    # THIS device - distinct from tcp_session above (this device's OWN
    # direct connection, only set for WiFi-capable devices that identify
    # themselves). A BTLE-mesh-only device (bt_only=True) has no
    # tcp_session of its own; its only presence signal is another
    # device's relayed mesh broadcast, so this is the one place that
    # records which WiFi hub is currently doing that relaying - set at
    # every status-update parse site (see _parse_83_device_state,
    # _handle_73_mesh_control, _process_73_mesh_info), read by the HA
    # integration's diagnostic "Connected via"/"IP address" entities.
    relay_source: Optional["CyncTCPSession"] = None

    def __init__(
        self,
        dev_id: int,
        dev_type: Optional[int] = None,
        name: Optional[str] = None,
        mac: Optional[str] = None,
        wifi_mac: Optional[str] = None,
        fw_version: Optional[str] = None,
        home_id: Optional[int] = None,
        hvac: Optional[dict] = None,
        entities: Optional[Dict[int, "EntityState"]] = None,
    ):
        self.control_bytes = bytes([0x00, 0x00])
        if dev_id is None:
            raise ValueError("ID must be provided to constructor")
        self.id = dev_id
        self.entities: Optional[Dict[int, "EntityState"]] = entities
        self.type = dev_type
        self.metadata: DeviceTypeInfo = (
            device_type_map[self.type] if dev_type in device_type_map else None
        )
        self.home_id: Optional[int] = home_id
        self._mac = mac
        self.wifi_mac = wifi_mac
        self._version: Optional[str] = None
        self._version_str: Optional[str] = None
        self.version = fw_version
        if name is None:
            name = f"device_{dev_id}"
        self.name = name
        self.lp = f"{self.name}({dev_id}):"
        if hvac is not None:
            self.hvac = hvac
            self._is_hvac = True

    @property
    def hass_id(self):
        return f"{self.home_id}-{self.id}"

    @property
    def is_sol_lamp(self) -> bool:
        """Return True for older XLink Wi-Fi-direct devices (e.g. C by GE Sol, type 80).

        These devices require 0xD2 for brightness and 0xE2 (sub-cmd 0x05) for CCT,
        rather than the 0xF0 opcodes used by newer Cync mesh devices.
        """
        return bool(self.metadata and self.metadata.opcodes.sol_lamp)

    @property
    def is_hvac(self) -> bool:
        if self._is_hvac is not None:
            return self._is_hvac
        if self.type is None:
            return False
        return (
            self.type in self.Capabilities["HEAT"]
            or self.type in self.Capabilities["COOL"]
            or self.type in self.DeviceTypes["THERMOSTAT"]
        )

    @is_hvac.setter
    def is_hvac(self, value: bool) -> None:
        if isinstance(value, bool):
            self._is_hvac = value

    @property
    def version(self) -> Optional[str]:
        return self._version

    @version.setter
    def version(self, value: Union[str, int]) -> None:
        if value is None:
            return
        if isinstance(value, int):
            self._version = value
        elif isinstance(value, str):
            if value == "":
                logger.debug(
                    f"{self.lp} in CyncDevice.version().setter, the firmwareVersion "
                    f"extracted from the cloud is an empty string!"
                )
            elif value.casefold() == "unknown":
                logger.debug(f"{self.lp} This is a sub-device")
            else:
                try:
                    _x = int(value.replace(".", "").replace("\0", "").strip())
                except ValueError as ve:
                    logger.exception(
                        f"{self.lp} Failed to convert firmware version to int: {ve}"
                    )
                else:
                    self._version = _x
                    self._version_str = value.strip()

    @property
    def version_str(self) -> Optional[str]:
        """Human-readable firmware version (e.g. "1.2.3"), as reported by the
        Cync cloud export. `version` collapses this to a bare int for legacy
        callers, discarding the dotted formatting."""
        if self._version_str:
            return self._version_str
        return str(self._version) if self._version is not None else None

    @property
    def mac(self) -> str:
        return str(self._mac) if self._mac is not None else None

    @mac.setter
    def mac(self, value: str) -> None:
        self._mac = str(value)

    @property
    def bt_only(self) -> bool:
        if self.wifi_mac == "00:01:02:03:04:05":
            return True
        if self.metadata:
            return self.metadata.protocol.TCP is False
        return False

    @property
    def has_wifi(self) -> bool:
        if self.metadata:
            return self.metadata.protocol.TCP
        return False

    @property
    def is_light(self):
        if self._is_light is not None:
            return self._is_light
        if self.metadata:
            if self.metadata.type == DeviceClassification.LIGHT:
                self._is_light = True
            elif self.metadata.type == DeviceClassification.SWITCH:
                # A dimmable switch is dimming a light, not a fan - Cync
                # sells fan speed control as its own dedicated "Fan
                # Controller" product (capabilities.fan), so any other
                # dimmable switch type is safe to assume is a light dimmer.
                # HA's `switch` domain has no brightness concept at all, so
                # leaving these routed to switch.py (as a bare
                # DeviceClassification.SWITCH check would) silently drops
                # dimming entirely - confirmed via a real user report after
                # this session's earlier SWITCH reclassification (which was
                # correct for capability data like color/tunable_white, but
                # shouldn't have affected which HA platform these route to).
                caps = self.metadata.capabilities
                self._is_light = bool(
                    caps and caps.dimmable and not caps.fan and not caps.plug
                )
            else:
                self._is_light = False
        else:
            self._is_light = False
        return self._is_light

    @is_light.setter
    def is_light(self, value: bool) -> None:
        if isinstance(value, bool):
            self._is_light = value
        else:
            logger.error(
                f"{self.lp} is_light must be a boolean value, got {type(value)} instead"
            )

    @property
    def is_switch(self) -> bool:
        if self._is_switch is not None:
            return self._is_switch
        if self.metadata:
            if self.metadata.type != DeviceClassification.SWITCH:
                return False
            # Mirror is_light's dimmable carve-out: a dimmable switch
            # routes through light.py instead (see is_light above), so it
            # must not also claim is_switch here - that would create a
            # second, binary-only entity for the same physical device.
            # Same reasoning for a fan controller (capabilities.fan) - it
            # gets its own richer entity on the fan platform (fan.py).
            # switch.py's own setup filter already excludes
            # is_fan_controller separately as a defensive check, but
            # is_switch should mean "plain binary switch, nothing else" on
            # its own so any other caller can trust it without having to
            # remember to re-check is_fan_controller too.
            return not self.is_light and not self.is_fan_controller
        return False

    @is_switch.setter
    def is_switch(self, value: bool) -> None:
        if isinstance(value, bool):
            self._is_switch = value
        else:
            logger.error(
                f"{self.lp} is_switch must be a boolean value, got {type(value)} instead"
            )

    @property
    def is_plug(self) -> bool:
        if self._is_plug is not None:
            return self._is_plug
        if self.metadata:
            if self.metadata.type == DeviceClassification.SWITCH:
                if self.metadata.capabilities:
                    return self.metadata.capabilities.plug
        return False

    @is_plug.setter
    def is_plug(self, value: bool) -> None:
        self._is_plug = value

    @property
    def has_multi_entities(self) -> bool:
        return len(self.entities) > 1

    @property
    def is_fan_controller(self):
        if self._is_fan_controller is not None:
            return self._is_fan_controller
        if self.metadata:
            if self.metadata.type == DeviceClassification.SWITCH:
                if self.metadata.capabilities:
                    return self.metadata.capabilities.fan
        return False

    @is_fan_controller.setter
    def is_fan_controller(self, value: bool) -> None:
        self._is_fan_controller = value

    @property
    def has_motion_sensor(self) -> bool:
        """True for a standalone motion sensor (type SENSOR) as well as a light/switch
        with a built-in occupancy sensor (e.g. types 37/49/56) - unlike is_plug/
        is_fan_controller this isn't gated to one DeviceClassification, since it can
        apply as either a device's only capability or an addition to a light/switch.
        """
        if self.metadata and self.metadata.capabilities:
            return bool(self.metadata.capabilities.motion_sensor)
        return False

    @property
    def is_dimmable(self) -> bool:
        if self.metadata:
            if self.metadata.type == DeviceClassification.LIGHT:
                if self.metadata.capabilities:
                    return self.metadata.capabilities.dimmable
        return False

    @property
    def supports_rgb(self) -> bool:
        if self._supports_rgb is not None:
            return self._supports_rgb
        if self.metadata:
            if self.metadata.type == DeviceClassification.LIGHT:
                if self.metadata.capabilities:
                    return self.metadata.capabilities.color
        return False

    @supports_rgb.setter
    def supports_rgb(self, value: bool) -> None:
        self._supports_rgb = value

    @property
    def supports_temperature(self) -> bool:
        if self._supports_temperature is not None:
            return self._supports_temperature
        if self.metadata:
            if self.metadata.type == DeviceClassification.LIGHT:
                if self.metadata.capabilities:
                    return self.metadata.capabilities.tunable_white
        return False

    @supports_temperature.setter
    def supports_temperature(self, value: bool) -> None:
        self._supports_temperature = value

    @property
    def online(self):
        return self._online

    @online.setter
    def online(self, value: bool):
        if not isinstance(value, bool):
            raise TypeError(f"Online status must be a boolean, got: {type(value)}")
        if value != self._online:
            self._online = value
            g.tasks.append(
                asyncio.get_running_loop().create_task(
                    g.mqtt_client.pub_online(self.id, value)
                )
            )

    @property
    def state(self):
        # Lazy evaluation: Only runs next() if get(0) returns None.
        # The 'None' inside next() prevents StopIteration crashes.
        ep = self.entities.get(0) or next(iter(self.entities.values()), None)
        if not ep:
            return 0
        # Note: using ep.power based on your new EndpointState class
        return ep.power

    @state.setter
    def state(self, value: Union[int, bool, str]):
        """
        Set the state of the device.
        Accepts int, bool, or str. 0, 'f', 'false', 'off', 'no', 'n' are off. 1, 't', 'true', 'on', 'yes', 'y' are on.
        """
        _t = (1, "t", "true", "on", "yes", "y")
        _f = (0, "f", "false", "off", "no", "n")
        if isinstance(value, str):
            value = value.casefold()
        elif isinstance(value, (bool, float)):
            value = int(value)
        elif isinstance(value, int):
            # Sol and some devices report state as 0-100 instead of 0/1
            # Treat any non-zero int as ON
            value = 1 if value > 0 else 0
        else:
            raise TypeError(f"Invalid type for state: {type(value)}")

        if value in _t:
            value = 1
        elif value in _f:
            value = 0
        else:
            raise ValueError(f"Invalid value for state: {value}")

        ep = self.entities.get(0) or next(iter(self.entities.values()), None)
        if not ep:
            logger.error(f"{self.lp} Cannot set state, self.endpoints is empty!")
            return

        if value != ep.power:
            ep.power = value

    @property
    def brightness(self):
        ep = self.entities.get(0, next(iter(self.entities.values())))
        return ep.brightness

    @brightness.setter
    def brightness(self, value: int):
        if value < 0 or value > 255:
            raise ValueError(f"Brightness must be between 0 and 255, got: {value}")
        ep = self.entities.get(0, next(iter(self.entities.values())))
        if value != ep.brightness:
            ep.brightness = value

    @property
    def temperature(self):
        ep = self.entities.get(0, next(iter(self.entities.values())))
        return ep.temperature

    @temperature.setter
    def temperature(self, value: int):
        if value < 0 or value > 255:
            raise ValueError(f"Temperature must be between 0 and 255, got: {value}")
        ep = self.entities.get(0, next(iter(self.entities.values())))
        if value != ep.temperature:
            ep.temperature = value

    @property
    def red(self):
        ep = self.entities.get(0, next(iter(self.entities.values())))
        return ep.red

    @red.setter
    def red(self, value: int):
        if value < 0 or value > 255:
            raise ValueError(f"Red must be between 0 and 255, got: {value}")
        ep = self.entities.get(0, next(iter(self.entities.values())))
        if value != ep.red:
            ep.red = value

    @property
    def green(self):
        ep = self.entities.get(0, next(iter(self.entities.values())))
        return ep.green

    @green.setter
    def green(self, value: int):
        if value < 0 or value > 255:
            raise ValueError(f"Green must be between 0 and 255, got: {value}")
        ep = self.entities.get(0, next(iter(self.entities.values())))
        if value != ep.green:
            ep.green = value

    @property
    def blue(self):
        ep = self.entities.get(0, next(iter(self.entities.values())))
        return ep.blue

    @blue.setter
    def blue(self, value: int):
        if value < 0 or value > 255:
            raise ValueError(f"Blue must be between 0 and 255, got: {value}")
        ep = self.entities.get(0, next(iter(self.entities.values())))
        if value != ep.blue:
            ep.blue = value

    @property
    def rgb(self):
        """Return the RGB color as a list"""
        ep = self.entities.get(0, next(iter(self.entities.values())))
        return [ep.red, ep.green, ep.blue]

    @rgb.setter
    def rgb(self, value: List[int]):
        if len(value) != 3:
            raise ValueError(f"RGB value must be a list of 3 integers, got: {value}")
        ep = self.entities.get(0, next(iter(self.entities.values())))
        if value != [ep.red, ep.green, ep.blue]:
            ep.red, ep.green, ep.blue = value

    def __repr__(self):
        return f"<CyncDevice: {self.id}>"

    def __str__(self):
        return f"CyncDevice:{self.id}:"

    async def handle_entity_update(
        self,
        e_state: EntityState,
        from_pkt: Optional[str] = None,
    ) -> bool:
        """Extracted status packet parsing, handles MQTT publishing and device state changes."""
        ts = time.time()
        is_recent = bool(e_state.recently_seen)
        sub_fmt_str = (
            " '{}' ({})".format(e_state.name, e_state.sub_id) if e_state.sub_id > 0 else ""
        )
        if not is_recent:
            if self.metadata is not None:
                if not self.metadata.supported:
                    return False

            logger.debug(f"{self.lp}{sub_fmt_str} seems to have STALE data (no BT mesh activity)")
            self.num_late_states += 1
            tcp_pool = await g.ncync_server.get_dev_tcp_pool()
            tcp_count = len(tcp_pool) or 1
            # With one TCP node, stale data immediately marks offline.
            # With multiple TCP nodes, wait until stale reports match node count.
            should_mark_offline = tcp_count == 1 or self.num_late_states >= tcp_count
            if should_mark_offline:
                if self.online:
                    self.online = False
                    logger.warning(
                        f"{self.lp}{sub_fmt_str} marked OFFLINE "
                        f"(stale state count {self.num_late_states} / num tcp nodes {tcp_count})"
                    )
                else:
                    logger.warning(
                        f"{self.lp}{sub_fmt_str} is still marked as {'ONLINE' if self.online else 'OFFLINE'} -> "
                        f"(stale state count {self.num_late_states} / num tcp nodes {tcp_count})"
                    )
                return True
            else:
                logger.debug(f"{self.lp} recently seeen: {is_recent} but its marked as shouldnt be offline: "
                             f"stale state count {self.num_late_states} / num tcp nodes {tcp_count}\nTCP pool: {tcp_pool}")

        if not self.online:
            logger.info(
                f"{self.lp}{" '{}' ({})".format(e_state.name, e_state.sub_id) if e_state.sub_id > 0 else ''} "
                f"is marked ONLINE."
            )
            self.online = True
        # valid states are used to gauge CyncLAN health, if no valid states are received within a configured time limit
        # the bridge device 'Should restart?' sensor will be turned on. Trying to catch an edge case where CyncLAN stalls
        g.last_valid_state_ts = self.last_valid_state_ts = ts
        self.num_late_states = 0
        self.entities[e_state.sub_id] = e_state
        g.ncync_server.node_devices[self.id] = self
        return await g.mqtt_client.parse_entity_state(e_state, from_pkt=from_pkt)

    async def handle_motion_update(self, motion: bool, from_pkt: Optional[str] = None) -> bool:
        """Publish a motion-sensor trigger state.

        Deliberately does NOT route through handle_entity_update: that function's
        recently_seen handling means "is this update fresh" (marks the device
        offline after enough stale reports), but for a motion-sensor-capable device
        the same bit position instead means "is motion currently detected" - treating
        the two the same would misinterpret every "motion cleared" event as the
        device going offline.
        """
        if not self.online:
            logger.info(f"{self.lp} is marked ONLINE.")
            self.online = True
        g.ncync_server.node_devices[self.id] = self
        return await g.mqtt_client.publish_motion_state(self, motion, from_pkt=from_pkt)

    def get_ctrl_msg_id_bytes(self):
        """
        Control packets need a number that gets incremented, it is used as a type of msg ID and
        in calculating the checksum. Result is mod 256 in order to keep it within 0-255.
        """
        lp = f"{self.lp}get_ctrl_msg_id_bytes:"
        id_byte, rollover_byte = self.control_bytes
        # logger.debug(f"{lp} Getting control message ID bytes: ctrl_byte={id_byte} rollover_byte={rollover_byte}")
        id_byte += 1
        if id_byte > 255:
            id_byte = id_byte % 256
            rollover_byte += 1

        self.control_bytes = [id_byte, rollover_byte]
        # logger.debug(f"{lp} new data: ctrl_byte={id_byte} rollover_byte={rollover_byte} // {self.control_bytes=}")
        return self.control_bytes

    async def send_command(
        self,
        op: int,
        cmd_: int,
        sub_id: int,
        payload: bytes,
        m_cb: ControlMessageCallback,
        lp: str,
        repeat_op_code: bool = True,
    ):
        """Thin wrapper around the module-level broadcast_control_command(),
        always targeting this device's own id. See that function for the
        real implementation - factored out so commands with no specific
        device target (e.g. a home-wide scene activation) can use the same
        broadcast path without needing a CyncDevice instance to be `self`.
        """
        await broadcast_control_command(
            op, cmd_, self.id, sub_id, payload, m_cb, lp, repeat_op_code=repeat_op_code
        )

    async def set_fan_percentage(self, perc: int) -> bool:
        """
            Translate a preset fan speed into a Cync brightness value and send it to the device.
        :param perc:
        :return:
        """
        lp = f"{self.lp}set_fan_perc:"
        if not self.is_fan_controller:
            logger.warning(
                f"{lp} Device '{self.name}' ({self.id}) is not a fan controller, cannot set fan percent"
            )
            return False
        try:
            await self.set_brightness(
                perc,
                callback=partial(g.mqtt_client.update_fan_percent, self, perc)
            )
        except asyncio.CancelledError as ce:
            raise ce
        except Exception as e:
            logger.debug(f"{self.lp} Exception occurred while setting fan percent: {e}")
            return False
        else:
            return True

    async def set_fan_speed(self, speed: FanSpeed) -> bool:
        """
            Translate a preset fan speed into a Cync brightness value and send it to the device.
        :param speed:
        :return:
        """
        lp = f"{self.lp}set_fan_speed:"
        if not self.is_fan_controller:
            logger.warning(
                f"{lp} Device '{self.name}' ({self.id}) is not a fan controller, cannot set fan speed."
            )
            return False
        try:
            await self.set_brightness(
                speed.to_perc(),
                callback=partial(g.mqtt_client.update_fan_speed, self, speed)
            )
        except asyncio.CancelledError as ce:
            raise ce
        except Exception as e:
            logger.debug(f"{self.lp} Exception occurred while setting fan speed: {e}")
            return False
        else:
            return True

    async def set_power(self, state: int, sub_id: Optional[int] = None):
        lp = f"{self.lp}set_power:"
        if state not in (0, 1):
            logger.error(f"{lp} Invalid state! must be 0 or 1")
            return

        op = 0xD0
        cmd_ = 0x0D
        _sub_id = sub_id if sub_id is not None else 0x00
        payload = struct.pack(">BBBBB", 0x11, 0x02, state, 0x00, 0x00)
        m_cb = ControlMessageCallback(
            msg_id=0x00,
            message=None,
            sent_at=0.0,
            callback=partial(
                g.mqtt_client.update_entity_power, self, state, _sub_id
            ),
        )
        await self.send_command(op, cmd_, _sub_id, payload, m_cb, lp)

    async def set_brightness(self, bri: int, sub_id: Optional[int] = None, callback = None):
        lp = f"{self.lp}set_brightness:"
        if not (0 <= bri <= 100):
            logger.error(f"{lp} Invalid brightness: {bri} must be 0-100")
            return

        op = 0xD2 if self.is_sol_lamp else 0xF0
        cmd_ = 0x10
        _sub_id = sub_id if sub_id is not None else 0x00

        # Payload: 0x11 (command), 0x02, 0x01, brightness, padding
        if self.is_sol_lamp:
            payload = struct.pack(">BBBBB", 0x11, 0x02, bri, 0x00, 0x00)
        else:
            # 8 bytes, all unsigned chars
            payload = struct.pack(">BBBBBBBB", 0x11, 0x02, 0x01, bri, 0xFF, 0xFF, 0xFF, 0xFF)
        if callback is None:
            callback = partial(g.mqtt_client.update_brightness, self, bri)
        m_cb = ControlMessageCallback(
            msg_id=0x00,
            message=None,
            sent_at=0.0,
            callback=callback,
        )

        await self.send_command(op, cmd_, _sub_id, payload, m_cb, lp)

    async def set_fine_brightness(
        self, bri: int, fade_ms: int, sub_id: Optional[int] = None
    ) -> None:
        """EXPERIMENTAL: sub-percent brightness with a fade/transition time,
        via 0xE2 sub-command 0x08 (SetFineBrightnessCommand, confirmed
        payload shape) - unlike set_brightness(), which has no fade concept.

        cmd_ (0x0F) is PREDICTED, not confirmed against a real packet
        capture - via the length formula in docs/mesh_opcodes.md's "TCP
        relay envelope research" (validated 3/3 against already-confirmed
        production commands, but the source class it's derived from is
        itself flagged @Deprecated in the decompiled app). See
        _warn_experimental_cmd_code.
        """
        lp = f"{self.lp}set_fine_brightness:"
        _warn_experimental_cmd_code(lp, "set_fine_brightness")
        if not (0 <= bri <= 100):
            logger.error(f"{lp} Invalid brightness: {bri} must be 0-100")
            return

        op = 0xE2
        cmd_ = 0x0F
        _sub_id = sub_id if sub_id is not None else 0x00
        fade_ms = max(0, min(65535, fade_ms))
        # Payload: 0x11, 0x02, 0x08, brightness*10 (u16 BE, tenths of a
        # percent), fade_ms (u16 BE) - 7 bytes.
        payload = struct.pack(">BBB", 0x11, 0x02, 0x08) + struct.pack(
            ">HH", round(bri * 10), fade_ms
        )
        m_cb = ControlMessageCallback(
            msg_id=0x00,
            message=None,
            sent_at=0.0,
            callback=partial(g.mqtt_client.update_brightness, self, bri),
        )
        await self.send_command(op, cmd_, _sub_id, payload, m_cb, lp)

    async def set_temperature(self, temp: int, sub_id: Optional[int] = None):
        lp = f"{self.lp}set_temperature:"
        if temp < 0 or (temp > 100 and temp not in (129, 254)):
            logger.error(f"{lp} Invalid temperature! must be 0-100")
            return

        op = 0xE2 if self.is_sol_lamp else 0xF0
        cmd_ = 0x10
        # cmd_ = 0x0D works for all commands to the dual outlet plug
        _sub_id = sub_id if sub_id is not None else 0x00

        if self.is_sol_lamp:
            # Payload: 0x11, 0x02, 0x05, temp, 0x00 (5 bytes)
            payload = struct.pack(">BBBBB", 0x11, 0x02, 0x05, temp, 0x00)
        else:
            # Payload: 0x11, 0x02, 0x01, 0xFF, temp, 0x00, 0x00, 0x00 (8 bytes)
            payload = struct.pack(
                ">BBBBBBBB", 0x11, 0x02, 0x01, 0xFF, temp, 0x00, 0x00, 0x00
            )
        m_cb = ControlMessageCallback(
            msg_id=0x00,
            message=None,
            sent_at=0.0,
            callback=partial(g.mqtt_client.update_temperature, self, temp),
        )
        await self.send_command(op, cmd_, _sub_id, payload, m_cb, lp)

    async def set_rgb(
            self, red: int, green: int, blue: int, sub_id: Optional[int] = None
    ):
        lp = f"{self.lp}set_rgb:"
        if not (0 <= red <= 255) or not (0 <= green <= 255) or not (0 <= blue <= 255):
            logger.error(f"{lp} Invalid RGB value! channels must be 0-255")
            return

        op = 0xF0
        cmd_ = 0x10
        _sub_id = sub_id if sub_id is not None else 0x00

        # Payload: 0x11, 0x02, 0x01, 0xFF, 0xFE, red, green, blue (8 bytes)
        payload = struct.pack(
            ">BBBBBBBB", 0x11, 0x02, 0x01, 0xFF, 0xFE, red, green, blue
        )
        m_cb = ControlMessageCallback(
            msg_id=0x00,
            message=None,
            sent_at=0.0,
            callback=partial(
                g.mqtt_client.update_rgb, self, (red, green, blue)
            ),
        )
        await self.send_command(op, cmd_, _sub_id, payload, m_cb, lp)

    async def _send_light_run_mode(
        self, mode_code: int, index: int, nonce: int, sub_id: Optional[int] = None
    ) -> None:
        """Shared sender for the 0xE2 sub-0x07 "light-run-mode" command
        family: [modeCode, index, nonce]. modeCode selects Static(0x00)/
        LightShow(0x01)/MusicShow(0x02)/Reveal(0x03)/MultiColor(0x04) - see
        docs/mesh_opcodes.md. `nonce` is confirmed genuinely random and
        unvalidated by the receiving device (SetLightRunModeCommand.java
        writes Random.nextInt() there on every real send) - callers may
        safely pass a constant.
        """
        lp = f"{self.lp}set_light_effect:"
        op = 0xE2
        cmd_ = 0x0E
        _sub_id = sub_id if sub_id is not None else 0x00
        payload = struct.pack(">BBBBBB", 0x11, 0x02, 0x07, mode_code, index, nonce)
        m_cb = ControlMessageCallback(
            msg_id=0x00,
            message=None,
            sent_at=0.0,
            # set to black to make it standout
            callback=partial(
                g.mqtt_client.update_rgb, self, (0, 0, 0)
            ),
        )
        await self.send_command(op, cmd_, _sub_id, payload, m_cb, lp)

    async def set_lightshow(self, show: str, sub_id: Optional[int] = None):
        """LightShow-only (modeCode 0x01) factory presets. Kept for
        backward compatibility - see set_light_effect() for the more
        general command covering all of Static/LightShow/MusicShow/
        Reveal/MultiColor via LIGHT_RUN_MODE_EFFECTS."""
        lp = f"{self.lp}set_lightshow:"
        show = show.casefold()
        if show not in FACTORY_EFFECTS_BYTES:
            logger.error(f"{lp} Invalid effect: {show}")
            return
        index, nonce = FACTORY_EFFECTS_BYTES[show]
        await self._send_light_run_mode(0x01, index, nonce, sub_id)

    async def set_light_effect(self, effect: str, sub_id: Optional[int] = None):
        """The general light-run-mode command: any preset across Static/
        LightShow/MusicShow/Reveal/MultiColor (LIGHT_RUN_MODE_EFFECTS,
        src/cync_lan/const.py) rather than just the LightShow-only presets
        set_lightshow() supports."""
        lp = f"{self.lp}set_light_effect:"
        effect = effect.casefold()
        if effect not in LIGHT_RUN_MODE_EFFECTS:
            logger.error(f"{lp} Invalid effect: {effect}")
            return
        mode_code, index, nonce = LIGHT_RUN_MODE_EFFECTS[effect]
        await self._send_light_run_mode(mode_code, index, nonce, sub_id)

    async def set_multicolor_gradient_mode(
        self, enabled: bool, sub_id: Optional[int] = None
    ) -> None:
        """EXPERIMENTAL: toggles gradient mode for a custom MultiColor
        scheme (per-segment RGB programming for segmented/dynamic light
        strips - a separate feature from the factory show/scheme presets
        set_light_effect() covers). This is one of 3 confirmed wire
        primitives for programming a custom scheme -
        set_multicolor_segment_count()/set_multicolor_segments() are the
        other two. cync-lan does NOT orchestrate the full multi-send
        sequence a real custom scheme needs (what order the real app
        sends these in, and any timing between them, was not traced from
        the decompiled source) - only the 3 confirmed primitives are
        exposed here; call them yourself.

        Confirmed via SetMultiColorSegmentsCommand.java
        (Data.GradientMode.mo14104a()): opcode array
        `{0xF7,0x11,0x02,0x4E}` (misread-as-op-code - real op is the same
        `0x8E` "mesh-relay" substitution as add_to_scene/
        set_indicator_led/etc, dispatched via the identical
        XlinkCommandDelegate.DefaultImpls.c()->h() path) + sub-payload
        `[0x00, enabled (1-byte bool)]` = 6 bytes total.

        cmd_ is PREDICTED via the length formula, not confirmed.
        """
        lp = f"{self.lp}set_multicolor_gradient_mode:"
        _warn_experimental_cmd_code(lp, "set_multicolor_gradient_mode")
        _sub_id = sub_id if sub_id is not None else 0x00
        m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
        payload = struct.pack(">BBBB", 0xF7, 0x11, 0x02, 0x4E) + struct.pack(
            ">BB", 0x00, 1 if enabled else 0
        )
        cmd_ = 7 + len(payload)
        await self.send_command(0x8E, cmd_, _sub_id, payload, m_cb, lp, repeat_op_code=False)

    async def set_multicolor_segment_count(
        self, count: int, sub_id: Optional[int] = None
    ) -> None:
        """EXPERIMENTAL: sets the total logical segment count for a
        custom MultiColor scheme - see set_multicolor_gradient_mode()'s
        docstring for the overall multi-send-sequence caveat this shares.

        Confirmed via SetMultiColorSegmentsCommand.java
        (Data.SegmentCount.mo14104a()): opcode array
        `{0xF7,0x11,0x02,0x4E}` + sub-payload `[0xFF, count (1 byte)]` = 6
        bytes total.

        count: 0-255 (1-byte field) - no narrower bound confirmed; the
        real app's own MAX_SEGMENT_COUNT constant's compiled value wasn't
        visible in the decompiled source, only its name.

        cmd_ is PREDICTED via the length formula, not confirmed.
        """
        lp = f"{self.lp}set_multicolor_segment_count:"
        _warn_experimental_cmd_code(lp, "set_multicolor_segment_count")
        if not (0 <= count <= 0xFF):
            logger.error(f"{lp} Invalid count: {count} must be 0-255")
            return
        _sub_id = sub_id if sub_id is not None else 0x00
        m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
        payload = struct.pack(">BBBB", 0xF7, 0x11, 0x02, 0x4E) + struct.pack(">BB", 0xFF, count)
        cmd_ = 7 + len(payload)
        await self.send_command(0x8E, cmd_, _sub_id, payload, m_cb, lp, repeat_op_code=False)

    async def set_multicolor_segments(
        self,
        segments: List[Tuple[Optional[int], Optional[Tuple[int, int, int]]]],
        sub_id: Optional[int] = None,
    ) -> None:
        """EXPERIMENTAL: sets up to 2 segments' position+RGB color in one
        send, for a custom MultiColor scheme - see
        set_multicolor_gradient_mode()'s docstring for the overall
        multi-send-sequence caveat this shares. For more than 2 segments
        total, the CALLER must chunk into groups of <= 2 and call this
        repeatedly - ordering/timing requirements across multiple calls
        were not traced from the decompiled source and are NOT confirmed.

        Confirmed via SetMultiColorSegmentsCommand.java
        (Data.SegmentData.mo14104a()) and MultiColorSegmentData.java/
        MultiColorSegmentDataKt.m14270a(): opcode array
        `{0xF7,0x11,0x02,0x4E}` + a fixed leading byte (always `1` in the
        real app - hardcoded in `SegmentData`'s own constructor, not
        itself configurable there) + up to 2 segment blocks, each
        `[position, R, G, B]` (4 bytes) - unset trailing slots (fewer
        than 2 segments given) are padded with 4 bytes of 0xFF.

        Per segment, position and color are INDEPENDENTLY nullable -
        confirmed directly from `MultiColorSegmentData`'s own two
        separately-nullable fields (`Integer`/`RgbColor`, both
        `@Nullable`), not assumed to be all-or-nothing together:
        position is 1-120 (`SegmentData`'s own bounds check divides
        against a literal `120`, not inferred) or `None` for the 0xFF
        "unset position" sentinel; color is `None` for "no color"
        (written as `0,0,0`, per `MultiColorSegmentData`'s own null
        branch), otherwise `(r, g, b)` each 0-255.

        segments: 1 or 2 `(position, rgb)` tuples, `rgb` itself either
        `None` or an `(r, g, b)` tuple.

        cmd_ is PREDICTED via the length formula, not confirmed.
        """
        lp = f"{self.lp}set_multicolor_segments:"
        _warn_experimental_cmd_code(lp, "set_multicolor_segments")
        if not (1 <= len(segments) <= 2):
            logger.error(f"{lp} Invalid segments: must be 1 or 2 tuples, got {len(segments)}")
            return
        slot_bytes = b""
        for position, rgb in segments:
            if position is not None and not (1 <= position <= 120):
                logger.error(f"{lp} Invalid position: {position} must be 1-120 or None")
                return
            if rgb is not None:
                r, g, b = rgb
                if not all(0 <= c <= 255 for c in (r, g, b)):
                    logger.error(
                        f"{lp} Invalid color: {rgb}, each channel must be 0-255"
                    )
                    return
            else:
                r = g = b = 0
            pos_byte = position if position is not None else 0xFF
            slot_bytes += struct.pack(">BBBB", pos_byte, r, g, b)
        slot_bytes += b"\xff\xff\xff\xff" * (2 - len(segments))
        payload = (
            struct.pack(">BBBB", 0xF7, 0x11, 0x02, 0x4E) + struct.pack(">B", 1) + slot_bytes
        )
        cmd_ = 7 + len(payload)
        _sub_id = sub_id if sub_id is not None else 0x00
        m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
        await self.send_command(0x8E, cmd_, _sub_id, payload, m_cb, lp, repeat_op_code=False)

    async def set_group_membership(
        self,
        group_id: int,
        member: bool,
        reach_flag: int = 0x00,
        sub_id: Optional[int] = None,
    ) -> None:
        """EXPERIMENTAL: adds/removes THIS device to/from a group's mesh
        pub/sub address - group MEMBERSHIP management, not group control
        (see module-level set_group_power() for controlling an existing
        group's state/power).

        Confirmed via ControlDeviceGroupCommand.java (decompiled app,
        base class for AddDeviceGroupCommand/RemoveDeviceGroupCommand):
        op_code array `{-41,17,2}` = `{0xD7,0x11,0x02}` (`:120`). Payload
        (`x()` lines 190-214): action byte (ADD=1/REMOVE=0) + 2-byte
        little-endian group address + a GroupReachFlag byte (RX=0x87 for
        receive-only reachability, RXTX=0x00 for normal full participation
        - defaults to RXTX, matching how every other already-wired command
        in this file addresses devices).

        **UPDATE, corrected after follow-up research**: the command is
        genuinely dual-path, branching on
        `xlinkCommandDelegate.getDeviceType().getProductType().f31219d`
        (`ControlDeviceGroupCommand.java:192`) - confirmed this flag means
        "is this device's ProductType Sol or C-Reach" (an "is this a Hub
        product" flag in the SDK's own terms, `ProductType.java:195-199`),
        NOT a generic hub-relay/BLE-vs-WiFi distinction as first assumed.
        cync-lan already tracks the exact same distinction via
        `is_sol_lamp` (`metadata.opcodes.sol_lamp`, currently true only for
        device type 80). The ORIGINAL version of this method always took
        the `is_sol_lamp=True` branch (real `op=0xD7` via the trustworthy
        `XlinkCommandDelegate.f()`/`mo14054f()` envelope) - correct only
        for that rare product family. For every other real device
        (`is_sol_lamp=False`, virtually all real Cync hardware), the SDK
        instead routes through `h()`/`mo14056h` - the same `0x8E`
        "mesh-relay" substitution bug as set_indicator_led/
        set_motion_sensor_settings/etc. - meaning the embedded `0xD7` is
        actually just the payload's own leading discriminator byte, not
        the real outer op. See docs/mesh_opcodes.md's "Groups control"
        section.

        cmd_ is PREDICTED via the length formula in both branches (see
        docs/mesh_opcodes.md's "TCP relay envelope research") - not itself
        confirmed against a live capture, unlike the op_code/dispatch
        method. Unlike set_group_power, this targets an individual
        device's own address (self.id) - the payload's 2-byte group
        address is data, not the addressing field; it's the app telling
        one specific device "start/stop listening on this group's pub/sub
        address," not a broadcast to the group.

        group_id: 32768-65535 (cync-lan's own group-address range, same
        range as set_group_power's group_id).
        member: True to add this device to the group, False to remove it.
        reach_flag: 0x00 (RXTX, default) or 0x87 (RX-only).
        """
        lp = f"{self.lp}set_group_membership:"
        _warn_experimental_cmd_code(lp, "set_group_membership")
        if not (32768 <= group_id <= 65535):
            logger.error(f"{lp} Invalid group_id: {group_id} must be 32768-65535")
            return
        if reach_flag not in (0x00, 0x87):
            logger.error(f"{lp} Invalid reach_flag: {reach_flag} must be 0x00 or 0x87")
            return
        _sub_id = sub_id if sub_id is not None else 0x00
        action = 1 if member else 0
        group_bytes = struct.pack("<H", group_id)
        m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
        if self.is_sol_lamp:
            # Confirmed real op_code, trustworthy f()/mo14054f envelope -
            # the SDK's own "is this device a Hub product" flag is true.
            op = 0xD7
            cmd_ = 0x0E
            payload = (
                struct.pack(">BBB", 0x11, 0x02, action)
                + group_bytes
                + struct.pack(">B", reach_flag)
            )
            await self.send_command(op, cmd_, _sub_id, payload, m_cb, lp)
        else:
            # Common case: same 0x8E-relay substitution bug as
            # set_indicator_led/set_motion_sensor_settings/etc. - 0xD7
            # moves from "outer op" to "payload's leading discriminator
            # byte", mirroring execute_scene's established pattern.
            op = 0x8E
            payload = (
                struct.pack(">B", 0xD7)
                + struct.pack(">BBB", 0x11, 0x02, action)
                + group_bytes
                + struct.pack(">B", reach_flag)
            )
            cmd_ = 7 + len(payload)
            await self.send_command(op, cmd_, _sub_id, payload, m_cb, lp, repeat_op_code=False)

    async def add_to_scene(
        self,
        scene_id: int,
        cct: Optional[int] = None,
        rgb: Optional[Tuple[int, int, int]] = None,
        fade: int = 0xFF,
        sub_id: Optional[int] = None,
    ) -> None:
        """EXPERIMENTAL: adds/updates THIS device's captured state within
        an existing scene (by scene_id, from module-level create_scene()).
        Call once per device you want the scene to control.

        Confirmed via AddDeviceSceneCommand.java's non-hub-routed path -
        the common case, gated the same way as set_group_membership()'s
        `is_sol_lamp` branch (this command uses the identical
        `getProductType().f31219d` "is this a Hub product" flag). The rare
        Sol/C-Reach path uses a structurally DIFFERENT WriteBuffer/
        FrameCode payload with a real MeshAddress target, not just a
        different op_code/discriminator swap like set_group_membership's
        two branches - not implemented here (logs an error instead of
        guessing at an unconfirmed format).

        Payload (non-hub-routed, m14018x()/Companion.m14019a()):
        `[0xEE,0x11,0x02]` (misread-as-op-code discriminator - real op is
        the same `0x8E` mesh-relay substitution as set_indicator_led/
        set_motion_sensor_settings/etc.) + actionType (1 byte, always 1 =
        "ADD_ACTION"/regular here - the 17="Show" variant used for
        LightShow/MusicShow/MultiColor scene actions is not implemented)
        + sceneId (1 byte - this command truncates to 1 byte, unlike
        delete_scene's 2-byte/toggle_automation's 4-byte sceneId fields,
        yet another real width inconsistency confirmed directly from
        AddDeviceSceneCommand.java, not assumed) + mode (1 byte, always 0
        = "regular/static color" here) + param (1 byte, always 0 for
        non-Show actions) + colorType (1 byte: CCT percentage 0-100, or
        0xFE for RGB) + R/G/B (3 bytes, 0 unless RGB) + fade (1 byte,
        ScheduleFade enum - NO_FADE=0xFF default) + a fixed trailing 0xFF
        byte = 13 bytes total.

        KNOWN LIMITATION, confirmed by tracing the wire format rather than
        assumed: this action shape has NO brightness field anywhere in
        it - only a CCT-or-RGB color. A Cync Scene captured this way
        always implies "device on, at whatever brightness it was last
        set to," not a specific captured brightness level. This is a real
        property of the wire format as traced, not an oversight in this
        implementation. Device-off states within a scene were not traced/
        confirmed and are not supported here.

        Exactly one of cct/rgb must be given.

        cmd_ is PREDICTED via the length formula, not confirmed.
        """
        lp = f"{self.lp}add_to_scene:"
        _warn_experimental_cmd_code(lp, "add_to_scene")
        _warn_experimental_transport_unconfirmed(lp, "add_to_scene")
        if self.is_sol_lamp:
            logger.error(
                f"{lp} Not implemented for this device's product family "
                f"(Sol/C-Reach 'Hub product' devices use a structurally "
                f"different payload - see docstring)."
            )
            return
        if not (0 <= scene_id <= 0xFF):
            logger.error(f"{lp} Invalid scene_id: {scene_id} must be 0-255 (1-byte field)")
            return
        if (cct is None) == (rgb is None):
            logger.error(f"{lp} Exactly one of cct or rgb must be given")
            return
        if cct is not None and not (0 <= cct <= 100):
            logger.error(f"{lp} Invalid cct: {cct} must be 0-100")
            return
        if rgb is not None:
            r, g, b = rgb
            if not all(0 <= c <= 255 for c in (r, g, b)):
                logger.error(f"{lp} Invalid rgb: {rgb}, each channel must be 0-255")
                return
        else:
            r = g = b = 0
        if not (0 <= fade <= 0xFF):
            logger.error(f"{lp} Invalid fade: {fade} must be 0-255")
            return
        op = 0x8E
        action_type = 1  # ADD_ACTION (regular color state, not a Show/MultiColor)
        mode = 0  # regular/static color
        param = 0
        color_type = cct if cct is not None else 0xFE
        payload = (
            struct.pack(">B", 0xEE)
            + struct.pack(">BBB", 0x11, 0x02, action_type)
            + struct.pack(">B", scene_id)
            + struct.pack(">BBBBBB", mode, param, color_type, r, g, b)
            + struct.pack(">BB", fade, 0xFF)
        )
        cmd_ = 7 + len(payload)
        _sub_id = sub_id if sub_id is not None else 0x00
        m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
        await self.send_command(op, cmd_, _sub_id, payload, m_cb, lp, repeat_op_code=False)

    async def remove_from_scene(self, scene_id: int, sub_id: Optional[int] = None) -> None:
        """EXPERIMENTAL: removes THIS device's captured state from an
        existing scene (by scene_id) - the counterpart to add_to_scene().
        Unlike add_to_scene(), BOTH product-family branches are
        implemented here: RemoveDeviceSceneCommand.java's hub-routed path
        turned out to reuse the same trustworthy mo14054f() envelope
        already confirmed for set_group_membership()'s is_sol_lamp branch,
        not the more complex manually-built WriteBuffer/FrameCode frame
        that blocked add_to_scene()'s hub path - so there's no unconfirmed
        format to guess at here.

        Confirmed via RemoveDeviceSceneCommand.java (decompiled app):
        opcode array f34625q = `{0xEE,0x11,0x02,0x00}` (note the fixed
        trailing 0x00 - unlike add_to_scene()'s sibling command, there is
        no actionType/mode/color/fade field at all, since removing a
        device from a scene has nothing to configure) + sceneId (1 byte).

        Non-hub-routed (common case, `is_sol_lamp=False`): dispatched via
        the same `0x8E` "mesh-relay" substitution bug as add_to_scene/
        set_indicator_led/etc - the array's own leading `0xEE` is NOT the
        real outer op_code (that's the hardcoded `0x8E`), so it's included
        as literal payload data: `op=0x8E`,
        `payload=[0xEE,0x11,0x02,0x00,sceneId]` (5 bytes),
        `repeat_op_code=False`.

        Hub-routed (`is_sol_lamp=True`): dispatched via the trustworthy
        `mo14054f()` envelope with a genuine real `op_code=0xEE` - the
        array's own leading `0xEE` byte is the same byte the real app
        passes as `mo14054f()`'s op_code argument, then re-embeds inside
        its own payload array (`ArraysKt.plus(f34625q, sceneId)` is passed
        to `mo14054f((byte) -18, ...)` unmodified). cync-lan's own
        PacketBuilder already inserts a repeated op_code byte for this
        exact real-op case (`repeat_op_code=True`, the default - see
        set_group_membership()'s identical is_sol_lamp branch), so the
        payload given here omits that leading byte to avoid sending it
        twice: `op=0xEE`, `payload=[0x11,0x02,0x00,sceneId]` (4 bytes).

        Dispatched through the same `mo14054f()`/`mo14056h()` methods
        already proven to carry real TCP-relay traffic (unlike
        create_scene/create_schedule/add_automation's Hub-notification
        transport question) - only `cmd_` is PREDICTED via the length
        formula, not confirmed against a live capture.
        """
        lp = f"{self.lp}remove_from_scene:"
        _warn_experimental_cmd_code(lp, "remove_from_scene")
        if not (0 <= scene_id <= 0xFF):
            logger.error(f"{lp} Invalid scene_id: {scene_id} must be 0-255 (1-byte field)")
            return
        _sub_id = sub_id if sub_id is not None else 0x00
        m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
        if self.is_sol_lamp:
            op = 0xEE
            payload = struct.pack(">BBB", 0x11, 0x02, 0x00) + struct.pack(">B", scene_id)
            cmd_ = 7 + len(payload) + 1  # +1: repeated op_code byte (repeat_op_code default True)
            await self.send_command(op, cmd_, _sub_id, payload, m_cb, lp)
        else:
            op = 0x8E
            payload = struct.pack(">BBBB", 0xEE, 0x11, 0x02, 0x00) + struct.pack(">B", scene_id)
            cmd_ = 7 + len(payload)
            await self.send_command(op, cmd_, _sub_id, payload, m_cb, lp, repeat_op_code=False)

    @staticmethod
    def _build_motion_sensor_settings_payload(
        setting_type: int,
        enabled: Optional[bool] = None,
        sensitivity: Optional[int] = None,
        delay_seconds: int = 0,
        deactivation_seconds: int = 0,
    ) -> bytes:
        """Build the payload's mesh-command portion (VendorID + sub-opcode +
        params) for a motion/ambient-light sensor settings write - NOT the
        full wire payload; set_motion_sensor_settings() prepends the 0xF7
        discriminator byte this originally-misread-as-op-code value turned
        out to actually be (see that method's docstring).

        Sourced from decompiling the current Cync Android app
        (MotionSensorSetting.java / SetMotionSensorSettingsCommand.java) -
        the shape below (SetMotionSensorSettingsCommand's own opcode array
        `{-9,17,2,7}`, i.e. `{0xF7,0x11,0x02,0x07}`) is confirmed against
        the decompiled app, cross-checked independently twice. What was
        NOT initially confirmed: that first 0xF7 byte was assumed to be
        cync-lan's own outer `op` argument (the "opcode + fixed VendorID
        prefix" pattern set_power/set_brightness use) - a real-hardware
        test proved that wrong (see set_motion_sensor_settings()).

        `cmd_` (the second envelope argument send_command()/build_control_packet()
        expects - see set_power/set_brightness/set_rgb for that pattern) was
        long unconfirmed: it has no equivalent field in any of the decompiled
        per-command byte arrays (those only cover the *inner* mesh command,
        not cync-lan's own outer envelope). It's since been PREDICTED (0x13)
        via the length formula in docs/mesh_opcodes.md's "TCP relay envelope
        research" (validated 3/3 against already-confirmed production
        commands, but the source class it's derived from is itself flagged
        @Deprecated in the decompiled app) - see set_motion_sensor_settings(),
        which wires this payload into a real, EXPERIMENTAL send.

        Wire format: VendorID (0x11 0x02) + sub-opcode (0x07, fixed - marks
        "sensor settings" as a command family) + an 8-byte params struct:
          [0]   type discriminator: 1=motion sensor, 2=ambient light sensor
          [1]   State write: enable flag (0/1). Parameters write: always 0.
          [2]   State write: 0. Parameters write: sensitivity enum byte.
          [3:5] State write: 0x0000. Parameters write: delaySeconds, big-endian u16.
          [5:7] State write: 0x0000. Parameters write: deactivationSeconds, big-endian u16.
          [7]   State write: 0xFF. Parameters write: 0x00.
        (State vs Parameters are two distinct app-side commands that share this
        8-byte shape; which one this builds is selected by whether `enabled`
        is passed.)

        setting_type: 1=motion sensor, 2=ambient light sensor
        """
        if setting_type not in (1, 2):
            raise ValueError("setting_type must be 1 (motion) or 2 (ambient light)")
        if enabled is not None:
            params = struct.pack(
                ">BBBHHB", setting_type, 1 if enabled else 0, 0, 0, 0, 0xFF
            )
        else:
            params = struct.pack(
                ">BBBHHB",
                setting_type,
                0,
                sensitivity or 0,
                delay_seconds,
                deactivation_seconds,
                0x00,
            )
        return struct.pack(">BBB", 0x11, 0x02, 0x07) + params

    async def set_motion_sensor_settings(
        self,
        setting_type: int,
        enabled: Optional[bool] = None,
        sensitivity: Optional[int] = None,
        delay_seconds: int = 0,
        deactivation_seconds: int = 0,
        sub_id: Optional[int] = None,
    ) -> None:
        """EXPERIMENTAL: writes motion/ambient-light sensor tuning via
        sub-command 0x07 - see _build_motion_sensor_settings_payload() for
        the confirmed inner-payload shape and the cmd_ prediction's
        provenance. See _warn_experimental_cmd_code.

        Corrected after a real-hardware test of this command's sibling
        set_indicator_led (same command family) came back a total no-op:
        the outer op is actually the hardcoded 0x8E "mesh-relay" op (traced
        in the decompiled app's XlinkDeviceManager.CommandDelegate.h()),
        not 0xF7 - that byte is the payload's own leading discriminator
        byte, not cync-lan's outer envelope op. Independently confirmed
        against a real captured packet (docs/debugging_sessions/3 devices/
        Plug - Toggle Power/Plug.md) - see
        PacketBuilder.build_control_packet's repeat_op_code param.

        Operational prerequisite, RESOLVED against decompiled source: the
        real Cync app requires physically waking the sensor first (press
        and hold its off button ~5s until the LED turns green) before it
        accepts settings edits. Confirmed this "discoverable" gate is just
        the device's ordinary mesh online/offline status (real app checks
        MotionSensorServiceDefault.isOnline(), same StateFlow every device
        type reports) - not a BLE/GATT discoverability scan, and no
        programmatic wake command exists anywhere in the app's source. The
        real app's own writeSettings/writeSchedule silently return fake
        success without transmitting anything if the target is offline -
        callers of this method should check bridge/online status first
        (the equivalent signal cync-lan already tracks) rather than send
        blind. See docs/mesh_opcodes.md's "Operational prerequisite"
        section for the full research trail.
        """
        lp = f"{self.lp}set_motion_sensor_settings:"
        _warn_experimental_cmd_code(lp, "set_motion_sensor_settings")
        try:
            inner_payload = self._build_motion_sensor_settings_payload(
                setting_type, enabled, sensitivity, delay_seconds, deactivation_seconds
            )
        except ValueError as e:
            logger.error(f"{lp} {e}")
            return
        op = 0x8E
        cmd_ = 0x13
        payload = struct.pack(">B", 0xF7) + inner_payload
        _sub_id = sub_id if sub_id is not None else 0x00
        m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
        await self.send_command(op, cmd_, _sub_id, payload, m_cb, lp, repeat_op_code=False)

    # MotionSensorResponseMode.java's ordinals (the natural/public input value)
    # mapped to their real wire-format flags-byte bits - NOT the same numbers,
    # confirmed via SetMotionSensorScheduleCommand.m14101x()'s if/else-if chain,
    # not derived from the ordinals themselves. OCCUPANCY sets no bit at all
    # (the implicit "else" case).
    _MOTION_SCHEDULE_MODE_BITS = {0: 0x80, 1: 0x00, 2: 0x20, 3: 0x10}  # DISABLED/OCCUPANCY/VACANCY/SIMPLE

    async def set_motion_sensor_schedule(
        self,
        slot_id: int,
        mode: int,
        start_hour: int,
        start_minute: int,
        end_hour: int,
        end_minute: int,
        brightness: int,
        cct: Optional[int] = None,
        rgb: Optional[Tuple[int, int, int]] = None,
        sub_id: Optional[int] = None,
    ) -> None:
        """EXPERIMENTAL: writes one of a group's 4 fixed motion-sensor
        schedule slots (see docs/cync_automations.md for the full native
        data model this writes, and docs/mesh_opcodes.md's "Motion-sensor
        schedule write" section for the wire format this implements).

        Confirmed via SetMotionSensorScheduleCommand.m14101x() to route
        through the exact same op-family bug already fixed (and confirmed
        working on real hardware) for set_indicator_led/
        set_motion_sensor_settings - the real outer op is the hardcoded
        0x8E "mesh-relay" op, not 0xF7 (the payload's own leading
        discriminator byte). Unlike those two siblings, this specific
        command has never itself been tested against real hardware -
        cmd_ (0x14) is PREDICTED via the length formula, not confirmed.

        Sent to an individual device (self.id), not a group MeshAddress -
        confirmed the real app fans this out per member device of the
        group rather than ever sending it once to a synthesized group
        address (MotionSensorServiceDefault.java's writeSchedule()).

        slot_id: 0=Morning, 1=Daytime, 2=Evening, 3=Sleep (matches
        docs/cync_automations.md's cloud-JSON slot numbering exactly).
        mode: 0=disabled, 1=occupancy, 2=vacancy, 3=simple
        (MotionSensorResponseMode.java ordinals - vacancy exists at the
        wire level but wasn't traced to a reachable UI path in the app).
        start_hour/end_hour: 0-23. start_minute/end_minute: 0-59.
        brightness: 0-100.
        cct: 0-100 (0=warmest, 100=coolest) - mutually exclusive with rgb.
        rgb: (r, g, b), each 0-255 - mutually exclusive with cct.
        Exactly one of cct/rgb must be given.

        Operational prerequisite, RESOLVED against decompiled source: same
        physical-wake requirement as set_motion_sensor_settings above
        (hold the sensor's off button ~5s until the LED turns green) - see
        that method's docstring and docs/mesh_opcodes.md's "Operational
        prerequisite" section for the full research trail (confirmed:
        ordinary online/offline status, not a BLE scan; no programmatic
        wake exists; real app silently no-ops this command family when
        the target is offline rather than erroring).
        """
        lp = f"{self.lp}set_motion_sensor_schedule:"
        _warn_experimental_cmd_code(lp, "set_motion_sensor_schedule")
        if slot_id not in (0, 1, 2, 3):
            logger.error(f"{lp} Invalid slot_id: {slot_id} must be 0-3")
            return
        if mode not in self._MOTION_SCHEDULE_MODE_BITS:
            logger.error(f"{lp} Invalid mode: {mode} must be 0-3")
            return
        if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23):
            logger.error(f"{lp} Invalid hour: start_hour/end_hour must be 0-23")
            return
        if not (0 <= start_minute <= 59 and 0 <= end_minute <= 59):
            logger.error(f"{lp} Invalid minute: start_minute/end_minute must be 0-59")
            return
        if not (0 <= brightness <= 100):
            logger.error(f"{lp} Invalid brightness: {brightness} must be 0-100")
            return
        if (cct is None) == (rgb is None):
            logger.error(f"{lp} Exactly one of cct or rgb must be given")
            return

        flags = slot_id | self._MOTION_SCHEDULE_MODE_BITS[mode]
        if rgb is not None:
            r, g, b = rgb
            if not all(0 <= c <= 255 for c in (r, g, b)):
                logger.error(f"{lp} Invalid rgb: each channel must be 0-255")
                return
            flags |= 0x40
            color = (r, g, b)
        else:
            if not (0 <= cct <= 100):
                logger.error(f"{lp} Invalid cct: {cct} must be 0-100")
                return
            color = (cct, 0x00, 0x00)

        op = 0x8E
        cmd_ = 0x14
        payload = struct.pack(
            ">BBBBBBBBBBBBB",
            0xF7, 0x11, 0x02, 0x0B,
            flags,
            start_hour, start_minute,
            end_hour, end_minute,
            brightness,
            *color,
        )
        _sub_id = sub_id if sub_id is not None else 0x00
        m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
        await self.send_command(op, cmd_, _sub_id, payload, m_cb, lp, repeat_op_code=False)

    async def set_indicator_led(
        self,
        mode: int,
        color: int,
        brightness: int,
        wifi_disconnect_blink: bool = False,
        sub_id: Optional[int] = None,
    ) -> None:
        """EXPERIMENTAL: sets the device's small status/indicator LED
        (mode/color/brightness) via sub-command 0x06
        (SetStatusIndicatorSettingsCommand, confirmed payload shape) -
        distinct from the device's main light output.

        mode: 0=always on, 1=always off, 2=normal (confirmed,
        LEDIndicatorMode.java). color: 0=white, 1=red, 2=green, 3=blue -
        a 4-value enum, not full RGB (confirmed, LEDIndicatorColor.java).
        brightness: 1-100. wifi_disconnect_blink: blink the indicator when
        WiFi is disconnected.

        A real-hardware test of this exact command with the original guess
        (`op=0xF7`) came back a total no-op (device did nothing, no error -
        fire-and-forget, no ACK checked). Root cause: SetStatusIndicatorSettingsCommand's
        own "opcode array" `{0xF7,0x11,0x02,0x06}` was misread as cync-lan's
        outer `op` (0xF7) + a payload starting `0x11,0x02,0x06,...`.
        Tracing the actual send path in the decompiled app
        (XlinkDeviceManager.CommandDelegate.h()) showed the real outer op
        is a hardcoded 0x8E "mesh-relay" op used across several command
        types (indicator LED, motion sensor settings, scenes) - the 0xF7
        is just that array's own leading byte, i.e. part of the payload,
        not cync-lan's envelope op. Independently corroborated against a
        real captured packet (docs/debugging_sessions/3 devices/
        Plug - Toggle Power/Plug.md): its checksum only balances with
        op=0x8E and no repeated op_code byte before the payload - see
        PacketBuilder.build_control_packet's repeat_op_code param.

        CONFIRMED WORKING on real hardware with the corrected op/payload
        shape below (op=0x8E, cmd_=0x0E, repeat_op_code=False) - no longer
        flagged via _warn_experimental_cmd_code, since both op_code and
        cmd_code are now proven, not predicted. See docs/mesh_opcodes.md.
        """
        lp = f"{self.lp}set_indicator_led:"
        if mode not in (0, 1, 2):
            logger.error(f"{lp} Invalid mode: {mode} must be 0 (always on), 1 (always off), or 2 (normal)")
            return
        if color not in (0, 1, 2, 3):
            logger.error(f"{lp} Invalid color: {color} must be 0-3 (white/red/green/blue)")
            return
        if not (1 <= brightness <= 100):
            logger.error(f"{lp} Invalid brightness: {brightness} must be 1-100")
            return

        op = 0x8E
        cmd_ = 0x0E
        _sub_id = sub_id if sub_id is not None else 0x00
        # Payload: 0xF7 (leading discriminator byte, previously mistaken
        # for the outer op) + 0x11, 0x02, 0x06, (mode<<4)|color,
        # brightness, wifi_disconnect_flag - 7 bytes.
        payload = struct.pack(
            ">BBBBBBB",
            0xF7,
            0x11,
            0x02,
            0x06,
            (mode << 4) | color,
            brightness,
            1 if wifi_disconnect_blink else 0,
        )
        m_cb = ControlMessageCallback(msg_id=0x00, message=None, sent_at=0.0, callback=None)
        await self.send_command(op, cmd_, _sub_id, payload, m_cb, lp, repeat_op_code=False)


class CyncTCPSession:
    """
    A class to interact with a Cync TCP connection (device or mobile app) via an async socket reader/writer.
    """

    lp: str = "TCP:"
    tasks: Tasks
    reader: Optional[asyncio.StreamReader]
    writer: Optional[asyncio.StreamWriter]
    mitm_mode: bool = False
    messages: MessageCache
    read_cache = []
    needs_more_data = False
    is_app: bool
    node: Optional[CyncDevice] = None
    dev_last_packet_ts: Optional[float] = None
    proxy_last_packet_ts: Optional[float] = None
    mitm_button_added: bool

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        ip_address: str,
    ):
        if not ip_address:
            raise ValueError(
                f"A valid IP address must be provided to {CyncTCPSession.__class__.__name__} constructor"
            )
        self.lp = f"{ip_address}:"
        self.tasks = Tasks()
        self.mitm_button_added = False
        self.is_app = False
        self.name: Optional[str] = None
        self.first_83_packet_checksum: Optional[int] = None
        self.ready_to_control = False
        self.version: Optional[int] = None
        self.version_str: Optional[str] = None
        self.protocol_version: Optional[int] = None
        self.protocol_version_str: Optional[str] = None
        self.device_type_id: Optional[int] = None
        self.device_timestamp: Optional[str] = None
        self.messages = MessageCache()
        self.xa3_msg_id: bytes = bytes([0x00, 0x00, 0x00])
        self.queue_id: bytes = b""
        self.ip_address: Optional[str] = ip_address
        self.read_lock = asyncio.Lock()
        self.write_lock = asyncio.Lock()
        self._reader: asyncio.StreamReader = reader
        self._writer: asyncio.StreamWriter = writer
        self._closing = False
        self.control_bytes = [0x00, 0x00]
        self.mitm_mode = False
        self.mitm_bytes_to_cloud = 0
        self.mitm_bytes_from_cloud = 0
        self.mitm_logger: Optional[logging.Logger] = None
        self.log_start_time = None
        self.cloud_reader: asyncio.StreamReader = None
        self.cloud_writer: asyncio.StreamWriter = None
        self.allowed_to_connect: bool = False
        self._closed = False
        self.closing = False
        self.mitm_button_added = False


    async def existing_init(self):
        """Used when replacing an existing TCP connection, when a device reconnects"""
        lp = f"{self.lp}existing init:"
        self._closed = False
        self.closing = False
        self.xa3_msg_id: bytes = bytes([0x00, 0x00, 0x00])
        self.queue_id: bytes = b""
        self.first_83_packet_checksum: Optional[int] = None
        self.ready_to_control = False
        self.protocol_version_str: Optional[str] = None
        self.version: Optional[int] = None
        self.version_str: Optional[str] = None
        self.protocol_version: Optional[int] = None
        self.device_type_id: Optional[int] = None
        self.device_timestamp: Optional[str] = None
        self.messages = MessageCache()
        self.lp = f"{self.ip_address}:"
        self.tasks = Tasks()
        if self.mitm_mode is True:
            logger.debug(f"{lp} MITM mode is active, making sure the proxy is started...")
            if self.node:
                await g.mqtt_client.add_mitm_button(self.node)
            if not self.is_proxy_good():
                await self.stop_proxy()
                await asyncio.sleep(0.25)
                await self.start_proxy()
            else:
                logger.debug(f"{lp} Proxy connection is up!")


    async def start_proxy(self):
        lp = f"{self.lp}start proxy:"
        try:
            ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            logger.info(
                f"{lp} Connecting to Cync Cloud via IP ({CYNC_CLOUD_IP}:23779)..."
            )
            self.cloud_reader, self.cloud_writer = await asyncio.open_connection(
                CYNC_CLOUD_IP, 23779, ssl=ssl_context
            )
            self.tasks.proxy_task = asyncio.create_task(
                self._cloud_proxy_task(),
                name=f"proxy_task-{self.ip_address}_ID:{self.node.id}",
            )
            self.tasks.proxy_conn_watcher = asyncio.create_task(
                self.connection_watcher_task(ConnectionType.proxy),
                name=f"proxy_connection_watcher-{self.ip_address}"
            )
        except Exception as e:
            logger.error(f"{lp} Failed to start MITM: {e}")
            await self.stop_proxy()

    async def start_mitm(self):
        """Connect to Cync Cloud and start proxying."""
        lp = f"{self.lp}mitm:start:"
        if self.mitm_mode and self.cloud_writer:
            logger.debug(
                f"{lp} MITM is already set to True and active, skipping starting of mitm mode..."
            )
            return
        self._setup_mitm_logger()
        try:
            await self.start_proxy()
            logger.info(
                f"{lp} MITM mode enabled, closing TCP connection to force device to reconnect and handshake with cloud through this proxy..."
            )
            # close the conneciton but dont remove the mitm button
            asyncio.create_task(self.close(False))
        except asyncio.CancelledError:
            logger.debug(f"{lp} start_mitm task was cancelled...")
        except Exception as e:
            logger.exception(f"{lp} Failed to start MITM: {e}")

        else:
            self.mitm_mode = True

    def is_proxy_good(self):
        if self.tasks.proxy_task and not self.tasks.proxy_task.done():
            logger.debug(f"{self.lp} Proxy task is active!")
        elif not self.tasks.proxy_task:
            logger.debug(f"{self.lp} Proxy receive task is None, need to restart...")
            return False
        elif self.tasks.proxy_task.done():
            logger.debug(f"{self.lp} Proxy receive task is done, need to restart...")
            return False

        if self.tasks.proxy_conn_watcher and not self.tasks.proxy_conn_watcher.done():
            logger.debug(f"{self.lp} Proxy connection watcher task is active!")
        elif not self.tasks.proxy_conn_watcher:
            logger.debug(f"{self.lp} Proxy connection watcher task is None, need to restart...")
            return False
        elif self.tasks.proxy_conn_watcher.done():
            logger.debug(f"{self.lp} Proxy connection watcher task is done, need to restart...")
            return False

        if self.cloud_reader:
            logger.debug(f"{self.lp} Cloud reader is active!")
        else:
            logger.debug(f"{self.lp} Cloud reader is None, need to restart...")
            return False

        if self.cloud_writer:
            logger.debug(f"{self.lp} Cloud writer is active!")
        else:
            logger.debug(f"{self.lp} Cloud writer is None, need to restart...")
            return False

        return True

    async def stop_proxy(self):
        lp = f"{self.lp}stop proxy:"
        if self.tasks.proxy_task and not self.tasks.proxy_task.done():
            logger.debug(f"{lp} Cancelling proxy task...")
            self.tasks.proxy_task.cancel()
            try:
                await self.tasks.proxy_task
            except Exception:
                pass
        self.tasks.proxy_task = None
        logger.debug(f"{lp} Proxy task stopped")

        if self.tasks.proxy_conn_watcher and not self.tasks.proxy_conn_watcher.done():
            logger.debug(f"{lp} Cancelling proxy connection watcher task...")
            self.tasks.proxy_conn_watcher.cancel()
            try:
                await self.tasks.proxy_conn_watcher
            except Exception:
                pass
            self.tasks.proxy_conn_watcher = None
            logger.debug(f"{lp} Proxy connection watcher task stopped")

        if self.cloud_reader:
            try:
                self.cloud_reader.feed_eof()
                logger.debug(f"{lp} Fed eof to cloud_reader")
            except Exception as e:
                logger.debug(f"{lp} Cloud reader feed_eof error (ignored): {e}")
        self.cloud_reader = None

        if self.cloud_writer:
            logger.debug(f"{lp} Closing cloud writer...")
            try:
                self.cloud_writer.close()
                await asyncio.wait_for(self.cloud_writer.wait_closed(), timeout=5.0)
                logger.debug(f"{lp} Cloud writer closed cleanly")
            except asyncio.TimeoutError:
                logger.warning(f"{lp} Cloud writer.wait_closed() timed out, continuing anyway")
            except Exception as e:
                logger.debug(f"{lp} Cloud writer close error (ignored): {e}")
        self.cloud_writer = None

        self.mitm_bytes_to_cloud = 0
        self.mitm_bytes_from_cloud = 0
        logger.debug(f"{lp} Proxy closed!")

    async def stop_mitm(self):
        """Close cloud connection and stop proxying."""
        lp = f"{self.lp}close mitm:"
        logger.debug(f"{lp} closing...")
        # if self.mitm is True or self.cloud_reader or self.cloud_writer:
        await self.stop_proxy()
        self.mitm_mode = False
        self.mitm_logger = None
        logger.info(f"{self.lp} MITM Mode disabled, forcing disconnect to enable normal operation...")
        await self.close()

    async def _cloud_proxy_task(self):
        """Reads from cloud and writes to device."""
        lp = f"{self.lp}mitm:proxy:"
        logger.debug(f"{lp} listening for data from the Cync cloud...")
        try:
            while self.cloud_reader:
                data = await self.cloud_reader.read(STREAM_CHUNK_SIZE)
                if not data:
                    pass
                else:
                    self.proxy_last_packet_ts = time.time()
                    self.mitm_logger.debug(
                        format_socat_style(
                            data, "from_cloud", self.ip_address, self.mitm_bytes_from_cloud
                        )
                    )
                    self.mitm_bytes_from_cloud += len(data)
                    self.writer.write(data)
                    await self.writer.drain()
        except asyncio.CancelledError:
            logger.debug(f"{lp} Task {name} CANCELED cleanly, re-raising...")
            raise
        except Exception as e:
            logger.error(f"{lp} Error in cloud proxy: {e}")

        logger.debug(f"{lp} FINISHED")

    def _setup_mitm_logger(self):
        """Initializes a rotating file logger for this specific connection."""
        lp = f"{self.lp}mitm logger:"
        if self.mitm_logger:
            logger.debug(
                f"{lp} Already setup for Node: '{self.name}' (ID: {self.node.id})"
            )
            return
        # Differentiate between App (by IP) and Device (by ID)
        conn_type = "dev"
        if self.is_app:
            conn_type = "app"
            identifier = f"{conn_type}_{self.ip_address.replace('.', '-')}"
        elif self.node:
            identifier = f"{conn_type}_{self.node.id}"
        logger_name = f"MITM {conn_type}:{self.ip_address}"
        mitm_logger = logging.getLogger(logger_name)
        self.mitm_logger = mitm_logger
        if self.is_app and not g.env.app_mitm_logging:
            logger.warning(f"{lp} This is an App TCP connection and global App MITM logging is disabled, "
                           f"not logging this proxied connection...")
            return
        self.log_start_time = datetime.datetime.now().strftime("%Y%m%d")
        log_dir = Path(CYNC_MITM_LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(log_dir, 0o777)
        log_file = log_dir / f"mitm_{identifier}-{self.log_start_time}.log"
        formatter = logging.Formatter(
            "%(asctime)s.%(msecs)03d [%(name)s] %(message)s", datefmt="%Y/%m/%d %H:%M:%S"
        )

        self.mitm_logger.setLevel(logging.DEBUG)
        self.mitm_logger.propagate = False

        file_handler = logging.handlers.TimedRotatingFileHandler(log_file, when="midnight")
        file_handler.setFormatter(formatter)
        self.mitm_logger.addHandler(file_handler)
        if (CYNC_MITM_DEV_LOGGER and not self.is_app) or (CYNC_MITM_APP_LOGGER and self.is_app):
            stdout_handler = logging.StreamHandler(sys.stdout)
            stdout_handler.setLevel(logging.DEBUG)
            stdout_handler.setFormatter(formatter)
            self.mitm_logger.addHandler(stdout_handler)
        os.chmod(log_file, 0o777)
        logger.debug(
            f"Created a MITM logger for node: '{self.name}' (ID: {self.node.id}) -> {log_file}"
        )

    async def blackhole(self, reason: str, should_sleep: bool):
        lp = f"{self.lp}"
        if self.reader is None and self.writer is None:
            # Already blackholed/closed - calling this twice on the same
            # session used to hit self.reader.feed_eof() on a None reader,
            # raising AttributeError every time, logged as "Error closing
            # reader/writer: 'NoneType' object has no attribute 'feed_eof'".
            # Confirmed via a real user's logs showing this thousands of
            # times, alongside the same reconnect-storm pattern the
            # get_dev_tcp_pool fix above addresses. Idempotent no-op now.
            logger.debug(f"{lp} blackhole() called on an already-closed session, no-op")
            return False
        if should_sleep is True:
            await asyncio.sleep(TCP_BLACKHOLE_DELAY)
        try:
            if self.reader is not None:
                self.reader.feed_eof()
            if self.writer is not None:
                self.writer.close()
                task = asyncio.create_task(self.writer.wait_closed())
                await asyncio.wait([task], timeout=5)
        except asyncio.CancelledError as ce:
            logger.debug(f"{lp} Task cancelled: {ce}")
            raise ce
        except Exception as e:
            logger.error(f"{lp} Error closing reader/writer: {e}", exc_info=True)
        finally:
            self.reader = None
            self.writer = None
        return False

    async def can_connect(self) -> bool:
        """Based on TCP_WHITELIST and MAX_TCP_CONN, should only be used on Cync device connections"""
        lp = f"{self.lp}:can connect:"
        tcp_dev_len = len(g.ncync_server.tcp_connections)
        num_attempts = g.ncync_server.tcp_conn_attempts[self.ip_address]
        if self.mitm_mode:
            logger.debug(f"{lp} MITM active, skipping connection check...")
            self.allowed_to_connect = True

        elif (
            (g.ncync_server.shutting_down is True)
            or (tcp_dev_len >= CYNC_MAX_TCP_CONN)
            or (CYNC_TCP_WHITELIST and self.ip_address not in CYNC_TCP_WHITELIST)
        ):
            reason = ""
            if g.ncync_server.shutting_down is True:
                reason = "CyncLAN server is shutting down, "
            _sleep = False
            if tcp_dev_len >= CYNC_MAX_TCP_CONN:
                reason = f"CyncLAN server max ({tcp_dev_len}/{CYNC_MAX_TCP_CONN}) TCP connections reached, "
                _sleep = True
            elif CYNC_TCP_WHITELIST and self.ip_address not in CYNC_TCP_WHITELIST:
                reason = f"IP not in CyncLAN server whitelist -> {CYNC_TCP_WHITELIST}, "
                _sleep = True
            # show a reminder every 20 reconnections
            tst_ = (num_attempts == 1) or (num_attempts % 20 == 1)
            lmsg = f"{lp} {reason}rejecting new connection..."
            if tst_:
                logger.warning(lmsg)
            self.allowed_to_connect = False
            await self.blackhole(reason, _sleep)

        else:
            self.allowed_to_connect = True

        return self.allowed_to_connect

    async def start_tasks(self):
        """Start background tasks safely, ensuring old ones are killed first."""

        if self.tasks.receive and not self.tasks.receive.done():
            self.tasks.receive.cancel()
            try:
                await self.tasks.receive
            except asyncio.CancelledError:
                pass

        if (
            self.tasks.callback_cleanup
            and not self.tasks.callback_cleanup.done()
        ):
            self.tasks.callback_cleanup.cancel()
            try:
                await self.tasks.callback_cleanup
            except asyncio.CancelledError:
                pass

        if (
            self.tasks.dev_conn_watcher
            and not self.tasks.dev_conn_watcher.done()
        ):
            self.tasks.dev_conn_watcher.cancel()
            try:
                await self.tasks.dev_conn_watcher
            except asyncio.CancelledError:
                pass


        # python will garbage collect the task if you dont keep a reference
        self.tasks.receive = asyncio.create_task(
            self.receive_task(), name=f"receive_task-{self.ip_address}"
        )
        self.tasks.callback_cleanup = asyncio.create_task(
            self.callback_cleanup_task(), name=f"callback_cleanup-{self.ip_address}"
        )


    def get_ctrl_msg_id_bytes(self) -> List[int, int]:
        """
        Control packets need a number that gets incremented, it is used as a type of msg ID and
        in calculating the checksum. Result is mod 256 in order to keep it within 0-255.
        """
        lp = f"{self.lp}get_ctrl_msg_id_bytes:"
        id_byte, rollover_byte = self.control_bytes
        # logger.debug(f"{lp} Getting control message ID bytes: ctrl_byte={id_byte} rollover_byte={rollover_byte}")
        id_byte += 1
        if id_byte > 255:
            id_byte = id_byte % 256
            rollover_byte += 1

        self.control_bytes = [id_byte, rollover_byte]
        # logger.debug(f"{lp} new data: ctrl_byte={id_byte} rollover_byte={rollover_byte} // {self.control_bytes=}")
        return self.control_bytes

    async def parse_raw_data(self, data: bytes):
        """Extract single packets from raw data stream using metadata."""
        self.dev_last_packet_ts = ts = time.time()
        lp = f"{self.lp}extract:"
        if not data:
            logger.debug(f"{lp} No data to parse?")
            return
        if self.mitm_mode:
            # Log for devices or log for apps only if global toggle is ON
            if self.cloud_writer:
                should_log = not self.is_app or g.env.app_mitm_logging
                if should_log and self.mitm_logger:
                    self.mitm_logger.debug(
                        format_socat_style(
                            data, "to_cloud", self.ip_address, self.mitm_bytes_to_cloud
                        )
                    )
                self.cloud_writer.write(data)
                await self.cloud_writer.drain()
                self.mitm_bytes_to_cloud += len(data)
            else:
                logger.warning(
                    f"{lp} MITM mode enabled but the cloud writer is: {self.cloud_writer}"
                )
        raw_input = data
        data_to_cache = CacheData()
        data_to_cache.timestamp = ts
        data_to_cache.all_data = raw_input
        if self.needs_more_data:
            logger.debug(
                f"{lp} partial packet (needs_more_data), appending to previous data..."
            )
            if not self.read_cache:
                raise RuntimeError(f"{lp} No previous cache data to extract from!")

            cache: CacheData = self.read_cache[-1]
            data = cache.data + data
            data_to_cache.raw_data = data
            logger.debug(
                f"{lp} Data assembly: prev={cache.data_len}/{cache.needed_len} "
                f"curr={len(raw_input)} combined={len(data)}"
            )
            if CYNC_RAW:
                logger.debug(f"DBG>>>{lp}NEW DATA:\n{data}\n")
            self.needs_more_data = False

        loop_count = 0
        while data:
            loop_count += 1
            loop_lp = f"{lp}loop {loop_count}:"
            data_len = len(data)
            length_needed = data_len
            if data[0] in PacketBuilder.ALL_HEADERS:
                if data_len > 4:
                    # [0:Header] [1] [2] [3:Multiplier] [4:Length]
                    pkt_len_multiplier = data[3]
                    packet_length = data[4]
                    # Length of payload + 5 bytes for the header itself
                    length_needed = (pkt_len_multiplier * 256) + packet_length + 5
                else:
                    # Not enough bytes yet to read the multiplier/length fields
                    # (offsets 3-4). length_needed was defaulted to data_len above,
                    # which made length_needed > data_len false, so this fragment
                    # got treated as a "complete" packet instead of waiting for
                    # more data - a TCP read split exactly here would misalign
                    # every following packet in the next read (confirmed via a
                    # real capture: a 2-byte "73 00" fragment got processed
                    # immediately, and the next read's continuation bytes were
                    # then misread as a new packet starting with a stray 0x00
                    # header, discarding several real device status updates).
                    # 5 is the minimum needed to compute a real length_needed.
                    length_needed = 5
                    logger.debug(
                        f"DBG>>>{loop_lp} Packet length is less than 4 bytes"
                    )
            else:
                logger.warning(
                    f"{loop_lp} Unknown packet header: {data[0].to_bytes(1, 'big').hex(' ')}"
                )
                capture_unknown_packet(
                    loop_lp,
                    f"packet header {data[0]:#04x}",
                    data,
                )
                # Resync instead of discarding the rest of the buffer: length_needed
                # is still just data_len here, so without this the whole remaining
                # buffer - which can contain a real, otherwise-valid packet stream
                # sitting right after a few stray/misaligned bytes - would be handed
                # to parse_packet as one blob and silently dropped. Confirmed via a
                # real capture: "00 00 84 7e 73 ..." led with 4 junk bytes followed
                # by a fully valid MeshInfo burst covering ~40 devices that would
                # otherwise have been lost entirely. Scan forward for the next
                # recognized header and resume from there.
                resync_idx = 1
                while (
                    resync_idx < data_len
                    and data[resync_idx] not in PacketBuilder.ALL_HEADERS
                ):
                    resync_idx += 1
                logger.debug(
                    f"{loop_lp} Resyncing: dropping {resync_idx} leading byte(s)"
                )
                data = data[resync_idx:]
                continue

            if length_needed > data_len:
                self.needs_more_data = True
                logger.warning(
                    f"{loop_lp} Packet requires more data! "
                    f"need={length_needed}, have={data_len}. Storing for next read..."
                )
                data_to_cache.needed_len = length_needed
                data_to_cache.data_len = data_len
                data_to_cache.data = data
                if CYNC_RAW:
                    logger.debug(f"{loop_lp} New data to cache: {data_to_cache}")
                break

            extracted_packet = data[:length_needed]
            data = data[length_needed:]
            await self.parse_packet(extracted_packet)
            if data and CYNC_RAW:
                logger.debug(f"{loop_lp} Remaining data to parse: {len(data)} bytes")

        self.read_cache.append(data_to_cache)
        # Keep only the last 10 entries if the cache exceeds 20
        if len(self.read_cache) > 20:
            self.read_cache = self.read_cache[-10:]
        if CYNC_RAW:
            logger.debug(
                f"{lp} END OF RAW READING of {len(raw_input)} bytes\n"
                f"BYTES: {raw_input}\n"
                f"HEX: {raw_input.hex(' ')}\n"
                f"INT: {bytes2list(raw_input)}\n\n"
            )

    async def parse_packet(self, data: bytes):
        """Parse what type of packet based on header (first 12 bytes)."""
        if len(data) < 5:
            # logger.warning(f"{self.lp} Packet too short to contain header: {data.hex(' ')}")
            return
        packet_header = data[:12]
        pkt_type = packet_header[0]

        # Calculate length based on protocol (multiplier * 256 + length)
        pkt_multiplier = packet_header[3] * 256
        packet_length = packet_header[4] + pkt_multiplier

        # queue_id = packet_header[5:10]
        # 4 bytes
        queue_id = packet_header[5:9]
        # bytes
        msg_id = packet_header[9:12]

        packet_data = data[12:] if len(data) > 12 else None
        lp = f"{self.lp}0x{pkt_type:02x}:"

        # Route to the appropriate handler
        if PacketBuilder.is_device_request(pkt_type):
            if self.allowed_to_connect is False:
                await self.can_connect()
            await self._dispatch_device_request(
                pkt_type, data, packet_data, queue_id, msg_id, packet_length, lp
            )
        elif PacketBuilder.is_app_request(pkt_type):

            if not self.is_app:
                logger.info(
                    f"{lp} Device has been identified as the Cync mobile app, enabling proxying to the Cync cloud for all App connections..."
                )
                self.is_app = True
                # This fires for any recognized app login version (0x10, 0x13,
                # ...), unlike mark_app_mesh_active which only fires on BTLE
                # mesh proximity - the app being on WiFi at all doesn't mean
                # it's physically near a device, so this is a distinct,
                # broader "app is active" signal (see g.mqtt_client's
                # mark_app_wifi_active).
                await g.mqtt_client.mark_app_wifi_active()
                await self.blackhole("is app", True)
                # g.ncync_server.app_tcp_connections[self.ip_address] = g.ncync_server.tcp_connections.pop(self.ip_address)
                # # update app / node / tcp conn stats
                # g.ncync_server._update_app_stats()
                #
                # # always proxy apps, app mitm logging to file is configurable
                # # This way its easier to add factory reset devices to your account if you have network wide DNS redirection
                # # still working on a way to detect a device that is being provisioned, then we can auto-proxy so it will
                # # be added to the cloud device list, meaning a user with network-wide DNS redirection doesnt need
                # # to disable it to add new dvices
                # await self.start_mitm()
        else:
            logger.debug(
                f"{lp} sent UNKNOWN HEADER! Don't know how to respond! {data.hex(' ')}"
            )

    async def _dispatch_device_request(
        self,
        pkt_type: int,
        raw_data: bytes,
        packet_data: Optional[bytes],
        queue_id: bytes,
        msg_id: bytes,
        packet_length: int,
        lp: str,
    ):
        """Routes device requests to their specific parsing logic."""
        if pkt_type == 0x23:
            self.queue_id = raw_data[6:10]
            self.tasks.dev_conn_watcher = asyncio.create_task(
                self.connection_watcher_task(ConnectionType.device), name=f"connection_watcher-{self.ip_address}"
            )
            logger.debug(
                f"{lp} Device IDENTIFICATION KEY: '{self.queue_id.hex(' ')}'\nRAW HEX: {raw_data.hex(' ')}"
            )
            if not self.mitm_mode:
                await self.write(PacketBuilder.build_23_ack())
                await asyncio.sleep(0.5)
                await self.send_a3()

        elif pkt_type == 0xC3:
            if not self.mitm_mode:
                logger.debug(f"{lp} CONNECTION REQUEST, replying...")
                await self.write(PacketBuilder.build_c3_ack())

        elif pkt_type == 0xD3:
            if not self.mitm_mode:
                await self.write(PacketBuilder.build_d3_ack())

        elif pkt_type == 0xA3:
            logger.debug(
                f"{lp} APP ANNOUNCEMENT packet: {packet_data.hex(' ') if packet_data else 'None'}"
            )
            if not self.mitm_mode:
                ack = PacketBuilder.build_a3_ack(queue_id, bytes(msg_id))
                await self.write(ack)

        elif pkt_type == 0x43:
            await self._handle_43_packet(packet_data, msg_id, packet_length, lp)

        elif pkt_type == 0x83:
            await self._handle_83_packet(
                packet_data, msg_id, packet_header=raw_data[:12], lp=lp
            )

        elif pkt_type == 0x73:
            await self._handle_73_mesh_control(packet_data, queue_id, msg_id, lp)

        elif pkt_type in (0xAB, 0x7B, 0x78):
            pass  # ACKs and other simple responses that don't require parsing or acknowledging receipt

    async def _handle_43_packet(
        self, packet_data: Optional[bytes], msg_id: bytes, packet_length: int, lp: str
    ):
        """Parses timestamps and broadcast status."""
        if packet_data:
            if packet_data[:2] == b"\xc7\x90":
                # --- Timestamp Parsing ---
                ts_idx = 3
                # Gross hack for versions 3.x - 4.x
                ts_end_idx = (
                    -2 if (self.version and 30000 <= self.version <= 40000) else -1
                )
                ts = packet_data[ts_idx:ts_end_idx]

                if ts:
                    ts_ascii = ts.decode("ascii", errors="replace")
                    if ts_ascii[-1] != "," and not ts_ascii[-1].isdigit():
                        ts_ascii = ts_ascii[:-1]

                    logger.debug(
                        f"{lp} Device sent TIMESTAMP -> {ts_ascii} - replying..."
                    )
                    self.device_timestamp = ts_ascii
                else:
                    logger.debug(
                        f"{lp} Could not decode timestamp from: {packet_data.hex(' ')}"
                    )

            else:
                # --- Broadcast Status Parsing ---
                # Status structs are always 19 bytes. A previous heuristic bumped this
                # to 20 whenever a 0x2e byte appeared anywhere in the packet, but that
                # byte is just an ordinary field value on some devices (e.g. an index/
                # marker byte), not a length indicator - confirmed via a real capture
                # of a 2-device broadcast where the flat 19-byte stride correctly
                # decoded both devices (matching their independently-reported 0x73
                # status) while the 0x2e heuristic silently dropped the second one.
                struct_len = 19
                extractions = []

                for i in range(0, packet_length, struct_len):
                    extracted = packet_data[i : i + struct_len]
                    if len(extracted) == struct_len:
                        status_struct = extracted[3:10]
                        status_struct += b"\x01"
                        extractions.append((extracted.hex(" "), list(status_struct)))

                if CYNC_RAW:
                    logger.debug(
                        f"{lp} Extracted data and STATUS struct => {extractions}"
                    )

        # Always ACK a 0x43 ping/status
        if not self.mitm_mode:
            ack = PacketBuilder.build_43_ack(bytes(msg_id))
            await self.write(ack)

    async def _handle_83_packet(
        self, packet_data: Optional[bytes], msg_id: bytes, packet_header: bytes, lp: str
    ):
        """Parses firmware info and 0x7e bound internal status streams."""
        if self.is_app:
            logger.debug(f"{lp} device is app, skipping packet...")
            return

        if not packet_data:
            logger.warning(f"{lp} packet with no data?????")

        else:
            # Unbound Firmware Packet
            if packet_data[0] == 0x00:
                try:
                    fw_type, fw_ver, fw_str = extract_firmware_dynamically(packet_data)
                    if fw_type == "device":
                        self.version, self.version_str = fw_ver, fw_str
                    else:
                        self.protocol_version, self.protocol_version_str = fw_ver, fw_str
                except Exception as e:
                    logger.debug(f"{lp} exception during firmware parsing: {e}")

            # 0x7e Bound Internal Status
            elif packet_data[0] == DATA_BOUNDARY:
                checksum = packet_data[-2]
                ctrl_bytes = packet_data[5:7]
                # inner_data = packet_data[6:-2]
                inner_data = packet_data[6:-2]
                calc_chksum = sum(inner_data) % 256

                if ctrl_bytes == b"\xfa\xdb" and packet_data[7] == 0x13:
                    await self._parse_83_device_state(
                        packet_data, checksum, calc_chksum, lp
                    )
                elif ctrl_bytes == b"\xf9\x52":
                    # Same full MeshInfo dump _handle_73_mesh_control already parses,
                    # just wrapped in a 0x83 outer packet instead of 0x73 - confirmed
                    # via a real capture (a full multi-device dump with recognizable
                    # dev_ids). send_ack=False: this function sends its own 0x83 ack
                    # unconditionally below regardless of branch, so the 0x73-specific
                    # mesh_status_ack would be the wrong format here and redundant.
                    end_bndry_idx = packet_data[1:].find(DATA_BOUNDARY) + 1
                    inner_struct = packet_data[1:end_bndry_idx]
                    await self._process_73_mesh_info(
                        inner_struct, self.queue_id, lp, send_ack=False
                    )
                elif ctrl_bytes == b"\xfa\xdb":
                    # Other fa db sub-types (packet_data[7] != 0x13) are DEVICE_STATUS
                    # ANNOUNCE frames whose payload isn't exactly the 19-byte single-
                    # device MeshLightStatus struct - packet_data[7] is the low byte of
                    # the little-endian payload-length field (0x13=19 for the common
                    # case), not a semantic sub-type tag. A non-0x13 value here most
                    # likely means a multi-struct bulk status dump (n*19 bytes), not an
                    # app-BTLE-connect event - confirmed via the app's own Frame/
                    # XlinkCommandCode wire format; no evidence tying this specifically
                    # to the app connecting was found in the decompiled app. Still
                    # benign/not actionable either way.
                    await g.mqtt_client.mark_app_mesh_active()
                    if CYNC_RAW:
                        logger.debug(
                            f"{lp} ctrl struct ({ctrl_bytes.hex(' ')} sub-type "
                            f"{packet_data[7]:#04x} // checksum valid: "
                            f"{checksum == calc_chksum}), safe to ignore\n\nHEX: "
                            f"{packet_data[1:-1].hex(' ')}\nINT: {list(packet_data[1:-1])}"
                        )
                elif ctrl_bytes == b"\xfa\xd9":
                    # seems to be some sort of bulk status msg. seen when updating devices firmware,
                    # it seemed to broadcast each devices percentage complete status
                    devices = []
                    try:
                        payload_len = packet_data[7]
                        device_count = packet_data[9]
                        # Devices start at index 10, each block is 4 bytes
                        idx = 10
                        for _ in range(device_count):
                            dev_id = packet_data[idx]
                            sub_id = packet_data[idx + 1]
                            status_type = packet_data[idx + 2]
                            value = packet_data[idx + 3]
                            devices.append({
                                "node_id": dev_id, "sub_id": sub_id,
                                "type": status_type, "value": value
                            })
                            idx += 4
                        return devices
                    except IndexError as e:
                        return []
                elif ctrl_bytes == b"\xfa\x8e":
                    # PASSTHROUGH_8E: a generic wrapper the app's own XlinkCommandCode
                    # table uses to relay ANY Telink BLE-mesh notification over the
                    # WiFi/hub link - confirmed legitimately fires on WiFi connection/
                    # RSSI/IP changes, OTA/firmware status pushes, mesh address/group/
                    # scene changes, and periodic RGB/mesh-status updates from devices
                    # in the mesh. Not specifically tied to HASS/MQTT reconnecting (no
                    # evidence found for that in the decompiled app); cync-lan's own
                    # reconnect/re-announce traffic incidentally triggering some of
                    # these relays is a more likely explanation for that correlation.
                    # Benign/expected either way, not actionable.
                    if CYNC_RAW:
                        logger.debug(
                            f"{lp} ctrl struct ({ctrl_bytes.hex(' ')} // checksum valid: "
                            f"{checksum == calc_chksum}), safe to ignore\n\nHEX: "
                            f"{packet_data[1:-1].hex(' ')}\nINT: {list(packet_data[1:-1])}"
                        )
                elif ctrl_bytes in (b"\xfa\xaf", b"\xfa\xf0"):
                    # fa af = MeshStatusProxyHeartbeatCommand's wire encoding (opcode
                    # 0xAF), a heartbeat toggling whether the phone app is acting as a
                    # BTLE-mesh-status proxy - confirmed via the app's own command
                    # class, not just a generic connect/disconnect signal.
                    # fa f0 = COMBO_CONTROL (opcode 0xF0), the same power/brightness/
                    # color "combo set" command used elsewhere in this codebase
                    # (_parse_83_device_state's 0xdb docstring). The app's own
                    # notification dispatcher has no handler registered for an
                    # incoming COMBO_CONTROL frame, so it's not something the
                    # reference client itself interprets as an announce-direction
                    # echo - most likely just relayed/passed-through mesh traffic that
                    # happens to co-occur with fa af bursts because both fire while the
                    # app is actively engaged with the hub, not because they're
                    # mechanically related.
                    # Both benign, not actionable; still useful as a coarse "app is
                    # active" signal even though the specific mechanism above isn't
                    # what was originally assumed.
                    await g.mqtt_client.mark_app_mesh_active()
                    if CYNC_RAW:
                        logger.debug(
                            f"{lp} ctrl struct ({ctrl_bytes.hex(' ')} // checksum valid: "
                            f"{checksum == calc_chksum}), safe to ignore\n\nHEX: "
                            f"{packet_data[1:-1].hex(' ')}\nINT: {list(packet_data[1:-1])}"
                        )
                elif try_resolve_xlink_notification(packet_data):
                    # A legacy Xlink/Frame HDLC notification (see
                    # try_resolve_xlink_notification's docstring) - e.g. the
                    # hub-allocated scene_id/schedule_id response to
                    # create_scene()/create_schedule(). Structurally
                    # unrelated to this function's own 0x7E-bound-inner-data
                    # convention above; just happens to share the same
                    # leading 0x7E byte, which is why it lands here rather
                    # than a dedicated branch.
                    pass
                else:
                    if CYNC_RAW:
                        logger.warning(
                            f"{lp} UNKNOWN packet data (ctrl_bytes: {ctrl_bytes.hex(' ')} // checksum valid: "
                            f"{checksum == calc_chksum})\n\nHEX: {packet_data[1:-1].hex(' ')}\nINT: {list(packet_data[1:-1])}"
                        )
                    capture_unknown_packet(
                        lp, f"0x83 ctrl_bytes {ctrl_bytes.hex(' ')}", packet_data[1:-1]
                    )

        if not self.mitm_mode:
            await self.write(PacketBuilder.build_83_ack(msg_id))

    async def _parse_83_device_state(
        self,
        packet_data: bytes,
        checksum: int,
        calc_chksum: int,
        lp: str,
        from_pkt: str = "0x83",
    ):
        """Parse the fa db 13 internal-status struct.

        Confirmed against Telink's "Communication Protocol for Telink BLE Mesh
        Light APP" spec (AN-BLE-15120202-E3) - Cync's mesh firmware is built
        directly on Telink's BLE Mesh SDK, and this struct is Telink's documented
        MeshLightStatus notify payload (cmd=0xdb, VendorID=0x11 0x02) wrapped in
        Cync's own fa/13 outer marker. Byte-exact mapping verified against a real
        capture (dev_id 10, pow=1, bri=70): packet_data[16:19] == db 11 02 is
        Telink's [cmd, VendorID_lo, VendorID_hi]; packet_data[14] is Telink's
        "dup address" field (offset 6, == src address in Telink's documented
        non-encrypted mode - this project never enables Telink's mesh encryption).
        packet_data[19:25] are Telink's 6 LED PWM channel bytes (offsets 11-16),
        repurposed by Cync as flag/brightness/temp/RG rather than literal PWM:
        led1->recently_seen, led2->power, led3->brightness, led4->temperature,
        led5->red, led6->green. packet_data[25] (blue) isn't a Telink LED channel
        at all - it's Telink's first RESERVED byte (offset 17), repurposed by Cync
        to fit a 3rd color channel since Telink's demo struct only has 6 PWM slots.
        Telink's remaining reserved byte / ttc / hops fields (offsets 18-20) are
        present in the wire format but unused here.
        """
        if len(packet_data) < 26:
            raise ValueError("Packet too short for standard status update")
        try:
            dev_id = packet_data[14]
            recently_seen, power, bri, tmp, r, gr, b = struct.unpack(">BBBBBBB", packet_data[19:26])
            parsed_state = EntityState(
                **{
                    "name": "",
                    "dev_id": dev_id,
                    "recently_seen": recently_seen,
                    "power": power,
                    "brightness": bri,
                    "temperature": tmp,
                    "red": r,
                    "green": gr,
                    "blue": b,
                }
            )
        except struct.error as e:
            logger.error(f"{lp} Failed to unpack status packet: {e}")
            return

        cync_device: CyncDevice = g.ncync_server.node_devices.get(dev_id)
        capture_unsupported_device(
            lp, dev_id, from_pkt=from_pkt, ctrl_bytes=b"\xfa\xdb", raw=packet_data[1:-1]
        )
        if not cync_device:
            # dev_id is likely a Cync room/group pseudo-ID (not exported as a controllable
            # device), broadcast by every physical device in the mesh on each status update.
            # Expected/benign, not actionable -> debug, not warning.
            logger.debug(
                f"{lp} Received internal STATUS for unknown device [group/room?, safe to ignore]: {parsed_state}"
            )
            return
        cync_device.relay_source = self

        if cync_device.metadata and cync_device.metadata.type == DeviceClassification.SENSOR:
            # Standalone motion sensor: recently_seen carries the actual trigger flag
            # for this device type (confirmed via a real capture), not staleness -
            # route through the dedicated path instead of handle_entity_update.
            logger.debug(f"{lp} Motion STATUS for {cync_device.name}: {bool(recently_seen)}")
            await cync_device.handle_motion_update(bool(recently_seen), from_pkt=from_pkt)
            return

        if cync_device.type in MULTI_ENDPOINT_TYPES:
            if cync_device.type == 67:
                # bri used as bitmask
                for e_state_ in cync_device.entities.values():
                    bit_shift = e_state_.sub_id - 1
                    e_state_.power = (
                        1 if (parsed_state.brightness & (1 << bit_shift)) else 0
                    )
                    e_state_.recently_seen = recently_seen
                    logger.debug(f"{lp} Internal STATUS for {e_state_}")
                    await cync_device.handle_entity_update(e_state_, from_pkt=from_pkt)
        else:
            parsed_state.name = cync_device.name
            logger.debug(f"{lp} Internal STATUS for {parsed_state}")
            await cync_device.handle_entity_update(
                parsed_state, from_pkt=from_pkt
            )

        # Checksum Stream Logic, the LED light controller sends 0x83 in a stream of data with checksum mismatches
        # if list(packet_data[9:12]) == [17, 17, 17]:
        #     if self.first_83_packet_checksum is None:
        #         self.first_83_packet_checksum = checksum
        #         if calc_chksum != checksum:
        #             logger.warning(
        #                 f"{lp} [LED Controller?] Checksum mismatch in INITIAL STATUS STREAM - FIRST packet data [safe to ignore]..."
        #             )
        #     else:
        #         if checksum == self.first_83_packet_checksum:
        #             calc_chksum = self.first_83_packet_checksum
        #         else:
        #             self.first_83_packet_checksum = None
        #
        # if calc_chksum != checksum:
        #     pass

    async def _handle_73_mesh_control(
        self, packet_data: Optional[bytes], queue_id: bytes, msg_id: bytes, lp: str
    ):
        """Parses mesh info arrays and fires callbacks for control acknowledgements."""
        if self.is_app:
            logger.debug(f"{lp} device is app, skipping packet...")
            return

        if not packet_data:
            logger.warning(f"{lp} packet with no data?!?")
        else:
            if packet_data[0] == DATA_BOUNDARY:
                ctrl_bytes = packet_data[5:7]
                end_bndry_idx = packet_data[1:].find(DATA_BOUNDARY) + 1
                inner_struct = packet_data[1:end_bndry_idx]

                if ctrl_bytes == b"\xf9\x52":
                    await self._process_73_mesh_info(inner_struct, queue_id, lp)

                elif ctrl_bytes == b"\xfa\xdb" and packet_data[7] == 0x13:
                    # Same real single-device internal status struct _handle_83_packet
                    # already parses, just delivered wrapped in a 0x73 outer packet
                    # instead of 0x83 - confirmed via a real capture (dev_id decoded
                    # to a known device with sensible power/brightness values). This
                    # had no handling here at all before, silently discarding every
                    # status update sent this way (a substantial volume - 1800+ in one
                    # capture session).
                    checksum = packet_data[-2]
                    inner_data = packet_data[6:-2]
                    calc_chksum = sum(inner_data) % 256
                    await self._parse_83_device_state(
                        packet_data, checksum, calc_chksum, lp, from_pkt="0x73"
                    )

                elif ctrl_bytes == b"\xfa\x54" and len(packet_data) >= 16:
                    # Built-in occupancy sensor telemetry from a motion-capable
                    # light/switch (types 37/49/56), distinct from both the
                    # standalone-sensor fa db 13 format and the mesh-wide broadcasts.
                    # Confirmed via a real capture: single-device (not mesh-broadcast,
                    # unlike everything else observed this session), dev_id at offset
                    # 9 (self-reporting), trigger flag at offset 15 toggling in step
                    # with real foot traffic over a multi-hour window.
                    motion_dev_id = packet_data[9]
                    motion_flag = bool(packet_data[15])
                    motion_device = g.ncync_server.node_devices.get(motion_dev_id)
                    if motion_device and motion_device.has_motion_sensor:
                        motion_device.relay_source = self
                        logger.debug(
                            f"{lp} Motion STATUS for {motion_device.name}: {motion_flag}"
                        )
                        await motion_device.handle_motion_update(
                            motion_flag, from_pkt="0x73"
                        )
                    else:
                        capture_unknown_packet(
                            lp,
                            f"0x73 ctrl_bytes fa 54 (dev_id {motion_dev_id} unrecognized)",
                            packet_data[1:-1],
                        )

                elif ctrl_bytes[0] == 0xF9 and ctrl_bytes[1] in (0xD0, 0xD2, 0xF0, 0xE2):
                    # Handle Callbacks for control messages
                    ctrl_msg_id = packet_data[1]
                    success = packet_data[7] == 1
                    msg = self.messages.control.pop(ctrl_msg_id, None)

                    if success and msg:
                        if callable(msg.callback):
                            await msg.callback()
                        else:
                            await msg.callback
                        # logger.debug(f"{lp} Received a command success reply: {msg}")
                    elif success and not msg:
                        logger.debug(
                            f"{lp} CONTROL packet ACK callback NOT found for msg ID: {ctrl_msg_id}"
                        )

                elif ctrl_bytes == b"\xf9\xaf":
                    # Device confirmation of our 0xF8/0xAF mesh-status-ack.  The
                    # server sends "f8 af 02 00 af 01" after each MeshInfo page;
                    # the device echoes back "f9 af 01 00 00" to acknowledge it.
                    # packet_data[7] == 0x01 means the device accepted the ack
                    # successfully.  Nothing actionable beyond logging; the outer
                    # 0x78 ack is sent unconditionally at the end of this handler.
                    if CYNC_RAW:
                        success = packet_data[7] == 1
                        logger.debug(
                            f"{lp} Mesh-status-ack confirmation (f9 af) received, "
                            f"success={success}\nHEX: {packet_data[1:-1].hex(' ')}\n"
                            f"INT: {list(packet_data[1:-1])}"
                        )

                elif ctrl_bytes == b"\xfa\x8e":
                    if packet_data[1] == 0x00:
                        try:
                            fw_type, fw_ver, fw_str = extract_firmware_dynamically(
                                packet_data[1:-1]
                            )
                            if fw_type == "device":
                                self.version, self.version_str = fw_ver, fw_str
                            else:
                                self.protocol_version, self.protocol_version_str = fw_ver, fw_str
                        except Exception as e:
                            logger.debug(f"{lp} Exception during firmware parsing: {e}")

                elif try_resolve_xlink_notification(packet_data):
                    # See the identical branch in _handle_83_packet - a
                    # legacy Xlink/Frame HDLC notification landed here
                    # instead (same 0x7E-bound outer wrapper, unrelated
                    # inner format).
                    pass
                else:
                    # Unlike _handle_83_packet, this had no catch-all at all - an
                    # unrecognized 0x73 ctrl_bytes pattern was previously silently
                    # dropped with zero logging.
                    if CYNC_RAW:
                        logger.debug(
                            f"{lp} UNKNOWN 0x73 ctrl_bytes: {ctrl_bytes.hex(' ')}\n\n"
                            f"HEX: {packet_data[1:-1].hex(' ')}\nINT: {list(packet_data[1:-1])}"
                        )
                    capture_unknown_packet(
                        lp, f"0x73 ctrl_bytes {ctrl_bytes.hex(' ')}", packet_data[1:-1]
                    )

        if not self.mitm_mode:
            # logger.debug(f"DBG>>>> Queue ID = {queue_id.hex(' ')}")
            await self.write(PacketBuilder.build_73_ack(self.queue_id, msg_id))

    async def _process_73_mesh_info(
        self, inner_struct: bytes, queue_id: bytes, lp: str, send_ack: bool = True
    ):
        """Handles the 24-byte paginated mesh info loop.

        send_ack=False when called for a MeshInfo dump delivered via 0x83 instead of
        0x73 (same f9 52 ctrl_bytes/inner structure, different outer wrapper) - the
        0x73-specific mesh_status_ack would be the wrong ack format there, and
        _handle_83_packet already sends its own ack for every 0x83 packet regardless.
        """
        if len(inner_struct) < 15:
            return

        minfo_start_idx = 14
        minfo_length = 24
        if inner_struct[minfo_start_idx] == 0x00:
            minfo_start_idx += 1
        if inner_struct[minfo_start_idx] == 0x00:
            logger.error(
                f"{lp}mesh: dev_id is 0 when using index: {minfo_start_idx}, skipping..."
            )
            return

        packet_devices = inner_struct[8]
        total_devices = inner_struct[12]
        is_new_sequence = getattr(self, "_mesh_expected", 0) == 0 or getattr(
            self, "_mesh_received", 0
        ) >= getattr(self, "_mesh_expected", 0)
        if is_new_sequence:
            self._mesh_expected = total_devices
            self._mesh_received = 0
            logger.debug(
                f"{lp} Starting new MeshInfo parsing sequence. Expecting {total_devices} total devices."
            )
        self._mesh_received += packet_devices

        loop_num = 0
        i = minfo_start_idx
        while i < len(inner_struct):
            loop_num += 1
            mesh_dev_struct = inner_struct[i : i + minfo_length]
            if len(mesh_dev_struct) < minfo_length:
                break

            dev_id = mesh_dev_struct[0]
            entry_len = minfo_length

            if dev_id == 0:
                # When a bridge reports more devices (packet_devices) than the true
                # mesh total, it's folding in duplicate BTLE relay-path observations
                # of the same device, and at least one relay path's entry is 1 byte
                # longer/shorter than the normal 24-byte struct. That desyncs every
                # following slot in the fixed-stride loop, producing a spurious
                # dev_id=0 (reading a neighboring entry's leading zero byte instead
                # of the real dev_id). Try shifting by +/-1 byte; if that lands on a
                # recognized device, adopt the correction and keep going from there.
                for shift in (1, -1):
                    if i + shift < minfo_start_idx:
                        continue
                    shifted = inner_struct[i + shift : i + shift + minfo_length]
                    if (
                        len(shifted) == minfo_length
                        and shifted[0] in g.ncync_server.node_devices
                    ):
                        logger.debug(
                            f"{lp}mesh: resynced at loop_num={loop_num} via "
                            f"{shift:+d} byte shift (dev_id 0 -> {shifted[0]})"
                        )
                        mesh_dev_struct = shifted
                        dev_id = shifted[0]
                        entry_len = minfo_length + shift
                        break

            dev_type_id = mesh_dev_struct[2]
            dev_state, dev_bri, dev_tmp = (
                mesh_dev_struct[8],
                mesh_dev_struct[12],
                mesh_dev_struct[16],
            )
            dev_r, dev_g, dev_b = mesh_dev_struct[20:23]

            if dev_state == 0 and dev_bri > 0:
                dev_bri = 0

            node_repr: Optional["CyncDevice"] = g.ncync_server.node_devices.get(dev_id)
            capture_unsupported_device(
                lp, dev_id, from_pkt="0x73", ctrl_bytes=b"\xf9\x52", raw=mesh_dev_struct
            )
            if node_repr:
                dev_name = node_repr.name
                node_repr.relay_source = self
                if loop_num == 1 and is_new_sequence:
                    # Only the first entry of the FIRST page of a fresh MeshInfo
                    # sequence is the device announcing itself. MeshInfo is
                    # paginated (see is_new_sequence above); loop_num resets to 1
                    # on every subsequent page too, but that page's first entry is
                    # just whatever device the firmware happened to list first, not
                    # necessarily self - checking identity against it produced
                    # spurious "node_id MISMATCH" warnings.
                    # byte 3 (idx 2) is a device type byte but,
                    # it only reports on the first item (itself)
                    # convert to int, and it is the same as deviceType from cloud.
                    if not self.node:
                        self.node = node_repr
                        self.node.tcp_session = self
                        self.name = node_repr.name
                        self.lp = f"{self.ip_address}[{self.node.id}]:"
                        logger.debug(
                            f"{self.lp}0x73: Setting TCP"
                            f" Node ID to: {self.node.id}"
                        )
                        # dynamically add the MITM mode button for nodes that are connected via TCP
                        self.mitm_button_added = True
                        await g.mqtt_client.add_mitm_button(node_repr)
                        # check mqtt mitm button retain state
                        payload = g.mqtt_client.get_startup_topic_state_sync(
                            f"{g.mqtt_client.topic}/status/{node_repr.home_id}-{node_repr.id}/mitm")
                        if payload is not None:
                            # Find the TCP device instance and trigger start/stop
                            tcp_pool = g.ncync_server.get_dev_tcp_pool_sync()
                            for tcp_dev in tcp_pool:
                                if tcp_dev.node:
                                    if tcp_dev.node.id == self.node.id:
                                        if payload.upper() == "ON":
                                            await tcp_dev.start_mitm()

                    elif self.node:
                        if self.mitm_button_added is False:
                            self.mitm_button_added = True
                            await g.mqtt_client.add_mitm_button(node_repr)
                        # NOTE: previously compared self.node.id against dev_id here
                        # and warned on mismatch, on the assumption that a fresh
                        # MeshInfo sequence's first entry is always the requesting
                        # device announcing itself. Confirmed false via repeated
                        # captures: specific bridges consistently report the same
                        # "wrong" first entry across entirely different reconnect
                        # sessions (e.g. bridge 239 always reports 24 first,
                        # bridge 252 always reports 1), meaning entry order is
                        # fixed per-bridge and unrelated to who's asking. There is
                        # no reliable identity signal here, so nothing to check.
                    lp = f"{self.lp}0x73:"
                    if dev_type_id:
                        self.device_type_id = dev_type_id
                    self.name = dev_name

                if node_repr.type in MULTI_ENDPOINT_TYPES:
                    if node_repr.type == 67:
                        # bri byte is a bitmask for on/off state of endpoints
                        # since we know the state of up to 8 endpoints at once, parse them all
                        for e_state_ in node_repr.entities.values():
                            bit_shift = e_state_.sub_id - 1
                            e_state_.power = 1 if (dev_bri & (1 << bit_shift)) else 0
                            e_state_.recently_seen = 1
                            logger.debug(
                                f"{lp} MeshInfo for {node_repr.name} - {e_state_}"
                            )
                            await node_repr.handle_entity_update(
                                e_state_,
                                from_pkt="0x73",
                            )
                else:
                    # Standard single endpoint
                    e_state = EntityState(
                        name=node_repr.name,
                        dev_id=dev_id,
                        power=dev_state,
                        brightness=dev_bri,
                        temperature=dev_tmp,
                        red=dev_r,
                        green=dev_g,
                        blue=dev_b,
                    )
                    logger.debug(f"{lp} MeshInfo for {e_state}")
                    await node_repr.handle_entity_update(
                        e_state,
                        from_pkt="0x73",
                    )

            else:
                logger.warning(
                    f"{lp} Received MeshInfo for unknown device ID: "
                    f"{dev_id} -> You need to export a new config file from the cloud!"
                )
                if g.mqtt_client:
                    g.mqtt_client.report_unknown_device_id(dev_id)

            i += entry_len

        if send_ack and not self.mitm_mode:
            mesh_ack = PacketBuilder.build_mesh_status_ack(self.queue_id)
            await self.write(mesh_ack)

        if getattr(self, "_mesh_received", 0) >= getattr(self, "_mesh_expected", 0):
            self._mesh_expected = 0
            self._mesh_received = 0

    async def ask_for_mesh_info(self):
        """
        Ask the device for mesh info. As far as I can tell, this will return whatever
        devices are connected to the device you are querying. It may also trigger
        the device to send its own status packet.
        """
        lp = f"{self.lp}"
        if self.mitm_mode:
            logger.debug(
                f"{lp} MITM Mode active, not writing to the Cync TCP device..."
            )
            return
        if len(self.queue_id) != 4:
            logger.warning(f"{lp} queue_id is not initialized, skipping mesh info request")
            return
        mesh_info_data = PacketBuilder.build_mesh_info_request(self.queue_id)
        _rdmsg = ""
        if CYNC_RAW is True:
            _rdmsg = f"\nBYTES: {mesh_info_data}\nHEX: {mesh_info_data.hex(' ')}\nINT: {bytes2list(mesh_info_data)}"
        logger.debug(f"{lp} Requesting ALL device(s) MeshInfo{_rdmsg}")
        try:
            await self.write(mesh_info_data)
        except TimeoutError as to_exc:
            logger.error(
                f"{lp} Requesting ALL device(s) status timed out, likely powered off"
            )
            raise to_exc
        except Exception as e:
            logger.error(f"{lp} EXCEPTION: {e}", exc_info=True)

    async def send_a3(self):
        """
        The device will not be controllable until this messagee is sent,
        we also request the known BTLE mesh device ID's and state
        """
        # random 2 bytes + padded byte
        rand_bytes = self.xa3_msg_id = random.getrandbits(16).to_bytes(2, "big")
        rand_bytes += bytes([0x00])
        if len(self.queue_id) != 4:
            logger.warning(f"{self.lp} queue_id is not initialized, skipping 0xA3 control request")
            return
        a3_packet = PacketBuilder.build_a3_control_request(self.queue_id, rand_bytes)
        logger.debug(f"{self.lp} Sending 0xA3 (want to control) packet...")
        await self.write(a3_packet)
        self.ready_to_control = True
        self.xa3_msg_id += random.getrandbits(8).to_bytes(1, "big")
        # send mesh info request
        await asyncio.sleep(1.5)
        await self.ask_for_mesh_info()


    async def connection_watcher_task(self, conn_type: ConnectionType):
        """Go through the callback queue and remove any callbacks that are older than 5 minutes"""
        lp = f"{self.lp}conn_watch:"
        logger.debug(f"{lp} Starting background {conn_type.value} task")
        # most devices send a d3 ping every 20 seconds if no other data has come through
        threshold = 22.5
        delay_seconds = 5
        if not self.is_app:
            return
        try:
            while True:
                await asyncio.sleep(delay_seconds)
                now = time.time()
                lp = f"{self.lp}conn_watch:"
                if conn_type in (ConnectionType.device, ConnectionType.app):
                    name = self.tasks.dev_conn_watcher.get_name()
                    last_packet_ts = self.dev_last_packet_ts
                elif conn_type == ConnectionType.proxy:
                    name = self.tasks.proxy_conn_watcher.get_name()
                    last_packet_ts = self.proxy_last_packet_ts
                    threshold = 40
                if last_packet_ts:
                    elapsed = now - last_packet_ts
                    if elapsed > threshold:
                        logger.debug(f"{lp} This {conn_type.value} connection hasnt received any data in "
                                     f"{elapsed:.1f} seconds, closing...")
                        task = self.close if conn_type in (ConnectionType.device, ConnectionType.app) else self.stop_proxy
                        asyncio.create_task(task())
                        break

        except asyncio.CancelledError:
            logger.debug(f"{lp} Task {name} CANCELLED cleanly, re-raising")
            raise
        except Exception as e:
            logger.error(f"{lp} Unexpected crash: {e}", exc_info=True)

        logger.info(f"{lp} FINISHED")


    async def callback_cleanup_task(self):
        """Go through the callback queue and remove any callbacks that are older than 5 minutes"""
        lp = f"{self.lp}callback_clean:"
        name = self.tasks.callback_cleanup.get_name()
        logger.debug(f"{lp} Starting background task: {name}")
        delay_mins = 5
        delay_seconds = delay_mins * 60

        try:
            while True:
                await asyncio.sleep(delay_seconds)
                if self.mitm_mode:
                    return
                lp = f"{self.lp}callback_clean:"
                now = time.time()
                current_keys = list(self.messages.control.keys())
                logger.info(
                    f"{lp} there are {len(current_keys)} control messages to check"
                ) if len(current_keys) else None
                for ctrl_msg_id in current_keys:
                    ctrl_msg = self.messages.control.get(ctrl_msg_id)
                    if not ctrl_msg:
                        continue

                    timeout = ctrl_msg.sent_at + delay_seconds
                    if now > timeout:
                        logger.info(f"{lp} Removing STALE {ctrl_msg}")
                        ctrl_msg.callback = None
                        self.messages.control.pop(ctrl_msg_id, None)

            logger.info(f"{lp} the while true loop has exited")

        except asyncio.CancelledError:
            logger.debug(f"{lp} Task {name} CANCELLED cleanly, re-raising")
            raise
        except Exception as e:
            logger.error(f"{lp} Unexpected crash: {e}", exc_info=True)
        logger.info(f"{lp} FINISHED")

    async def receive_task(self):
        """Receive data from the device and respond to it. This is the main task for the device."""
        lp = f"{self.lp}rcv data:"
        started_at = time.time()
        name = self.tasks.receive.get_name()
        logger.debug(f"{lp} receive_task CALLED") if CYNC_RAW is True else None
        try:
            while True:
                try:
                    data: bytes = await self.read()
                    lp = f"{self.lp}rcv data:"
                    if data is False:
                        logger.debug(
                            f"{lp} read() returned False, exiting {name} "
                            f"(started at: {datetime.datetime.fromtimestamp(started_at)})..."
                        )
                        break
                    if not data:
                        await asyncio.sleep(0)
                        continue
                    await self.parse_raw_data(data)

                except Exception as e:
                    logger.error(f"{lp} Exception in task {name} LOOP: {e}", exc_info=True)
                    asyncio.create_task(self.close())
                    break
        except asyncio.CancelledError:
            logger.debug(f"{lp} Task {name} CANCELLED cleanly, re-raising...")
            raise

        logger.debug(f"{lp} {name} FINISHED")

    async def read(self, chunk: Optional[int] = None):
        """Read data from the device if there is an open connection"""
        lp = f"{self.lp}read:"
        if self.is_closed() is True:
            logger.debug(f"{lp} Device is closing/closed, exiting read()...")
            return False
        else:
            if chunk is None:
                chunk = STREAM_CHUNK_SIZE
            async with self.read_lock:
                if self.reader:
                    if not self.reader.at_eof():
                        try:
                            raw_data = await self.reader.read(chunk)
                        except Exception as read_exc:
                            logger.error(f"{lp} Base EXCEPTION: {read_exc}")
                            return False
                        else:
                            return raw_data
                    else:
                        logger.debug(
                            f"{lp} reader is at EOF, setting read socket to None..."
                        )
                        self.reader = None
                else:
                    logger.debug(
                        f"{lp} reader is None/empty -> {self.reader = } // TYPE: {type(self.reader)}"
                    )
                    return False

    async def write(self, data: bytes, broadcast: bool = False) -> Optional[bool]:
        """
        Write data to the device if there is an open connection

        :param data: The raw binary data to write to the device
        :param broadcast: If True, write to all TCP devices connected to the server
        """
        if not isinstance(data, bytes):
            raise ValueError(f"Data must be bytes, not type: {type(data)}")
        self
        if self.is_closed():
            logger.debug(f"{self.lp} Device is closing/closed, can't write data")
        else:
            if self.writer is not None:
                async with self.write_lock:
                    # if broadcast is True:inner_struct__
                    #     # replace queue id with the sending device's queue id
                    #     new_data = bytes2list(data)
                    #     new_data[5:9] = dev.queue_id
                    #     data = bytes(new_data)

                    # check if the underlying writer is closing
                    if self._writer.is_closing():
                        if self.is_closed() is False:
                            # this is probably a connection that was closed by the device (turned off), delete it
                            logger.warning(
                                f"{self.lp} underlying writer is closing but, "
                                f"the device itself hasn't called close(). The device probably "
                                f"dropped the connection (lost power). Removing {self.ip_address}"
                            )
                            off_dev = await g.ncync_server.remove_tcp_device(self)
                            asyncio.create_task(off_dev.close())

                        else:
                            logger.debug(
                                f"{self.lp} TCP device is closing, not writing data... "
                            )
                    else:
                        self.writer.write(data)
                        # logger.debug(f"{dev.lp} writing data -> {data}")
                        try:
                            await asyncio.wait_for(self.writer.drain(), timeout=2.0)
                        except TimeoutError as to_exc:
                            logger.error(
                                f"{self.lp} writing data to the device timed out, likely powered off"
                            )
                            raise to_exc
                        else:
                            return True
            else:
                logger.warning(f"{self.lp} writer is None, can't write data!")
            return None

    async def close(self, remove_mitm_button: bool = True):
        lp = f"{self.ip_address}:close:"
        logger.debug(f"{lp} Cancelling device tasks...")
        try:
            self.closing = True
            await self.tasks.cancel_all()
        except Exception as e:
            logger.exception(f"{lp} Exception during device task .cancel_all(): {e}")
        try:
            if self.writer:
                async with self.write_lock:
                    self.writer.close()
                    task = self.writer.wait_closed()
                    await asyncio.wait_for(task, 3.0)
        except (AttributeError, TimeoutError):
            pass
        except Exception as e:
            logger.debug(f"{lp}writer: EXCEPTION: {e}")
        finally:
            self.writer = None

        try:
            if self.reader:
                async with self.read_lock:
                    self.reader.feed_eof()
                    await asyncio.sleep(0.03)
        except AttributeError:
            pass
        except Exception as e:
            logger.exception(f"{lp}reader: EXCEPTION: {e}")
        finally:
            self.reader = None

        if self.node and self.node.online:
            # This TCP session ending is the single most direct, authoritative
            # signal available for THIS device's own availability - it owns this
            # connection (unlike a BTLE-mesh-relayed device, whose presence is
            # inferred from another device's relayed status broadcasts). Every
            # call path that reaches close() already represents this device
            # actually being gone (lost power, network drop, a deliberate
            # reconnect-forcing cycle like MITM mode toggling) - previously this
            # was never propagated to CyncDevice.online at all, so a device that
            # simply stopped appearing in any mesh broadcast (rather than being
            # reported WITH a stale/"not recently seen" flag) stayed marked
            # online/available forever, showing stale last-known state. See
            # write()'s own "device probably dropped the connection (lost
            # power)" detection above for the clearest real-world case this
            # was missing. Deliberate reconnects (MITM mode) briefly flip this
            # to offline too - honest, not a regression: entity-unavailable
            # (silver) means reflecting real connection state, not hiding a
            # brief, real disconnect/reconnect cycle. handle_entity_update()
            # flips it back to online the moment fresh state arrives.
            logger.info(
                f"{lp} TCP session ending - marking device '{self.node.name}' "
                f"(ID: {self.node.id}) OFFLINE."
            )
            self.node.online = False

        if self.node and remove_mitm_button:
            await g.mqtt_client.remove_mitm_button(self.node)
        self.closing = False
        self._closed = True
        if self.ip_address in g.ncync_server.tcp_connections:
            # check if states match
            g_dev = g.ncync_server.tcp_connections.pop(self.ip_address)
            state_closing = self.closing == g_dev.closing
            state_closed = self._closed == g_dev.closed
            if state_closed is False or state_closing is False:
                logger.debug(f"{lp} There is a mismatch between states in the global device and this device: closed: {state_closed | closing: {state_closing}}, replacing...")
                del g_dev
                g.ncync_server.tcp_connections[self.ip_address] = self
            elif g_dev != self:
                logger.debug(f"{lp} There is a python object mismatch between the global device and this one...")
                del g_dev
                g.ncync_server.tcp_connections[self.ip_address] = self
            else:
                g.ncync_server.tcp_connections[self.ip_address] = g_dev

    @property
    def reader(self):
        return self._reader

    @reader.setter
    def reader(self, value: asyncio.StreamReader):
        self._reader = value

    @property
    def writer(self):
        return self._writer

    @writer.setter
    def writer(self, value: asyncio.StreamWriter):
        self._writer = value

    @property
    def closing(self):
        return self._closing

    @closing.setter
    def closing(self, value: bool):
        self._closing = value

    @property
    def closed(self):
        return self._closed

    @closed.setter
    def closed(self, value: bool):
        self._closed = value

    def is_closed(self):
        if self.closed or self.closing:
            return True
        return False

    async def parse_packet_OLD(self, data: bytes):
        """Parse what type of packet based on header (first 4 bytes 0x43, 0x83, 0x73, etc.)"""

        lp = f"{self.lp}parse:0x{data[0]:02x}:"
        packet_data: Optional[bytes] = None
        pkt_header_len = 12
        packet_header = data[:pkt_header_len]
        # logger.debug(f"{lp} Parsing packet header: {packet_header.hex(' ')}") if CYNC_RAW is True else None
        # byte 1 (2, 3 are unknown)
        # pkt_type = int(packet_header[0]).to_bytes(1, "big")
        pkt_type = packet_header[0]
        # byte 4, packet length factor. each value is multiplied by 256 and added to the next byte for packet payload length
        pkt_multiplier = packet_header[3] * 256
        # byte 5
        packet_length = packet_header[4] + pkt_multiplier
        # byte 6-10, unknown but seems to be an identifier that is handed out by the device during handshake
        queue_id = packet_header[5:10]
        # byte 10-12, unknown but seems to be an additional identifier that gets incremented.
        msg_id = packet_header[9:12]
        # check if any data after header
        if len(data) > pkt_header_len:
            packet_data = data[pkt_header_len:]
        else:
            # logger.warning(f"{lp} there is no data after the packet header: [{data.hex(' ')}]")
            pass
        # logger.debug(f"{lp} raw data length: {len(data)} // {data.hex(' ')}")
        # logger.debug(f"{lp} packet_data length: {len(packet_data)} // {packet_data.hex(' ')}")
        if PacketBuilder.is_device_request(pkt_type):
            if pkt_type == 0x23:
                queue_id = data[6:10]
                _dbg_msg = (
                    (
                        f"\nRAW HEX: {data.hex(' ')}\nRAW INT: "
                        f"{str(bytes2list(data)).lstrip('[').rstrip(']').replace(',', '')}"
                    )
                    if CYNC_RAW is True
                    else ""
                )
                logger.debug(
                    f"{lp} Device IDENTIFICATION KEY: '{queue_id.hex(' ')}'{_dbg_msg}"
                )
                self.queue_id = queue_id
                await self.write(PacketBuilder.build_23_ack())
                # MUST SEND a3 before you can ask device for anything over TCP
                # Device sends msg identifier (aka: key), server acks that we have the key and store for future comms.
                await asyncio.sleep(0.5)
                await self.send_a3()
            # device wants to connect before accepting commands
            elif pkt_type == 0xC3:
                # conn_time_str = ""
                ack_c3 = PacketBuilder.build_c3_ack()
                logger.debug(f"{lp} CONNECTION REQUEST, replying...")
                await self.write(ack_c3)
            # Ping/Pong
            elif pkt_type == 0xD3:
                ack_d3 = PacketBuilder.build_d3_ack()
                # logger.debug(f"{lp} Client sent HEARTBEAT, replying with {ack_d3.hex(' ')}")
                await self.write(ack_d3)
            elif pkt_type == 0xA3:
                logger.debug(f"{lp} APP ANNOUNCEMENT packet: {packet_data.hex(' ')}")
                ack = PacketBuilder.build_a3_ack(queue_id, bytes(msg_id))
                logger.debug(f"{lp} Sending ACK -> {ack.hex(' ')}")
                await self.write(ack)
            elif pkt_type == 0xAB:
                # We sent a 0xa3 packet, device is responding with 0xab. msg contains ascii 'xlink_dev'.
                # sometimes this is sent with other data. there may be remaining data to read in the enxt raw msg.
                # TCP msg buffer seems to be 1024 bytes.
                # 0xab packets are 1024 bytes long, so if any data is prepended, the remaining 0xab data will be in the next raw read
                pass
            elif pkt_type == 0x7B:
                # device is acking one of our x73 requests
                pass
            elif pkt_type == 0x43:
                if packet_data:
                    if packet_data[:2] == bytes([0xC7, 0x90]):
                        # [c7 90]
                        # There is some sort of timestamp in the packet, not status
                        # 0x2c = ',' // 0x3a = ':'
                        # iterate packet_data for the : and ,
                        # first there will be year/month/day : hourminute :- ?? , ????? , new , ????? , ????? , ????? ,

                        # full color light strip 3.0.204 has different offsets (packet_data len = 51, 6 bytes more than 1.x.yyy)
                        # has additional 2 bytes at end and in the middle of timestamp there is a new 3 digit entry with a comma (4 bytes + 2 = 6 bytes, which is what were over the old style)
                        # "c7 90 2e 32 30 32 34 30 33 31 30 3a 31 31 31 30 3a 2d 35 39 2c 30 30 31 35 31 2c 30 30 32 2c 30 30 30 30 30 2c 30 30 30 30 30 2c 30 30 30 30 30 2c 43 db"
                        # packet_data = 51
                        # 32 30 32 34 30 33 31 30 3a 31 31 31 30 3a 2d 35 39 2c 30 30 31 35
                        # 20240310:1110:-59,00151,002,00000,00000,00000, 46 bytes long + 3 byte prefix + 2 byte suffix

                        # OLD can just read until end of packet_data
                        # "c7 90 2a 32 30 32 34 30 39 30 31 3a 31 38 35 39 3a 2d 34 32 2c 30 32 33 32 32 2c 30 30 30 30 34 2c 30 30 31 30 33 2c 30 30 30 36 33 2c" OLD
                        # "c7 90 2e 32 30 32 34 30 33 31 30 3a 31 31 31 30 3a 2d 35 39 2c 30 30 31 35 31 2c 30 30 32 2c 30 30 30 30 30 2c 30 30 30 30 30 2c 30 30 30 30 30 2c 43 db" NEW
                        # is 0x2C the end of ts?

                        # [199, 144, 42, 50, 48, 50, 52, 48, 57, 48, 49, 58, 49, 56, 53, 57, 58, 45, 52, 50, 44, 48, 50, 51, 50, 50, 44, 48, 48, 48, 48, 52, 44, 48, 48, 49, 48, 51, 44, 48, 48, 48, 54, 51, 44]

                        # 32 30 32 34 30 39 30 31 3a 31 38 35 39 3a 2d 34 32 2c 30 32 33 32 32 2c 30 30 30 30 34 2c 30 30 31 30 33 2c 30 30 30 36 33
                        # 20240901:1859:-42,02322,00004,00103,00063,
                        # packet_data = 45

                        ts_idx = 3
                        ts_end_idx = -1
                        ts: Optional[bytes] = None
                        # logger.debug(
                        #     f"{lp} Device TIMESTAMP PACKET ({len(bytes.fromhex(packet_data.hex()))}) -> HEX: "
                        #     f"{packet_data.hex(' ')} // INTS: {bytes2list(packet_data)} // "
                        #     f"ASCII: {packet_data.decode(errors='replace')}"
                        # ) if CYNC_RAW is True else None
                        # setting version from config file wouldnt be reliable if the user doesnt bump the version
                        # when updating cync firmware. we can only rely on the version sent by the device.
                        # there is no guarantee the version is sent before checking the timestamp, so use a gross hack.
                        if self.version and (self.version >= 30000 <= 40000):
                            ts_end_idx = -2

                        ts = packet_data[ts_idx:ts_end_idx]
                        if ts:
                            ts_ascii = ts.decode("ascii", errors="replace")
                            # gross hack
                            if ts_ascii[-1] != ",":
                                if not ts_ascii[-1].isdigit():
                                    ts_ascii = ts_ascii[:-1]
                            logger.debug(
                                f"{lp} Device sent TIMESTAMP -> {ts_ascii} - replying..."
                            )
                            self.device_timestamp = ts_ascii
                        else:
                            logger.debug(
                                f"{lp} Could not decode timestamp from: {packet_data.hex(' ')}"
                            )
                    else:
                        # 43 00 00 00 2d 39 87 c8 57 01 01 06| [(06 00 10) {03  C...-9..W.......
                        # 01 64 32 00 00 00 01} ff 07 00 00 00 00 00 00] 07  .d2.............
                        # 00 10 02 01 64 32 00 00 00 01 ff 07 00 00 00 00  ....d2..........
                        # 00 00
                        # status struct is 19 bytes long
                        struct_len = 19
                        extractions = []
                        try:
                            # logger.debug(
                            #     f"{lp} Device sent BROADCAST STATUS packet => '{packet_data.hex(' ')}'"
                            # )if CYNC_RAW is True else None
                            for i in range(0, packet_length, struct_len):
                                extracted = packet_data[i : i + struct_len]
                                if extracted:
                                    # hack so online devices stop being reported as offline
                                    # this may cause issues with cync setups that ONLY use indoor
                                    # plugs as the btle to TCP bridge, as they dont broadcast status data using 0x83
                                    status_struct = extracted[3:10]
                                    status_struct + b"\x01"
                                    # 14 00 10 01 00 00 64 00 00 00 01 15 15 00 00 00 00 00 00
                                    # // [1, 0, 0, 100, 0, 0, 0, 1]
                                    extractions.append(
                                        (extracted.hex(" "), bytes2list(status_struct))
                                    )

                                    # await g.server.parse_status(status_struct, from_pkt='0x43')
                                # broadcast status data
                                # await self.write(data, broadcast=True)
                            (
                                logger.debug(
                                    "%s Extracted data and STATUS struct => %s"
                                    % (lp, extractions)
                                )
                                if CYNC_RAW is True
                                else None
                            )
                        except IndexError:
                            # The device will only send a max of 1kb of data, if the message is longer than 1kb the remainder is sent in the next read
                            # logger.debug(
                            #     f"{lp} IndexError extracting status struct (expected)"
                            # )
                            pass
                        except Exception as e:
                            logger.error(f"{lp} EXCEPTION: {e}")
                # Its one of those queue id/msg id pings? 0x43 00 00 00 ww xx xx xx xx yy yy yy
                # Also notice these messages when another device gets a command
                else:
                    # logger.debug(f"{lp} received a 0x43 packet with no data, interpreting as PING, replying...")
                    pass
                ack = PacketBuilder.build_43_ack(bytes(msg_id))
                # logger.debug(f"{lp} Sending ACK -> {ack.hex(' ')}") if CYNC_RAW is True else None
                await self.write(ack)
                (
                    logger.debug(f"DBG>>>{lp} RAW DATA: {len(data)} BYTES")
                    if CYNC_RAW is True
                    else None
                )
            elif pkt_type == 0x83:
                if self.is_app is True:
                    logger.debug(f"{lp} device is app, skipping packet...")
                else:
                    # When the device sends a packet starting with 0x83, data is wrapped in 0x7e.
                    # firmware version is sent without 0x7e boundaries
                    if packet_data is not None:
                        # logger.debug(f"{lp} Extracted BOUND data ({len(bytes(packet_data))} bytes) => {packet_data.hex(' ')}")

                        # 0x83 inner struct - not always bound by 0x7e (firmware response doesn't have starting boundary, has ending boundary 0x7e)
                        # firmware info, data len = 30 (0x32), fw starts idx 23-27, 20-22 fw type (86 01 0x)
                        #  {83 00 00 00 32} {[39 87 c8 57] [00 03 00]} {00 00 00 00  ....29..W.......
                        #  00 fa 00 20 00 00 00 00 00 00 00 00 ea 00 00 00  ... ............
                        #  86 01 01 31[idx=23 packet_data] 30 33 36 31 00 00 00 00 00 00 00 00  ...10361........
                        #  00 00 00 00 00 [8d] [7e]}                             ......~
                        # firmware packet may only be sent on startup / network reconnection

                        if packet_data[0] == 0x00:
                            fw_type, fw_ver, fw_str = extract_firmware_dynamically(
                                packet_data
                            )
                            if fw_type == "device":
                                self.version = fw_ver
                                self.version_str = fw_str
                            else:
                                self.protocol_version = fw_ver
                                self.protocol_version_str = fw_str

                        elif packet_data[0] == DATA_BOUNDARY:
                            # checksum is 2nd last byte, last byte is 0x7e
                            checksum = packet_data[-2]
                            inner_header = packet_data[1:6]
                            ctrl_bytes = packet_data[5:7]
                            # removes checksum byte and 0x7e
                            inner_data = packet_data[6:-2]
                            calc_chksum = sum(inner_data) % 256

                            # Most devices only report their own state using 0x83, however the LED light strip controllers also report other device state data
                            # over 0x83.
                            # This data can be wrong! sometimes reports wrong state and the RGB colors are slightly different from each device.
                            if ctrl_bytes == bytes([0xFA, 0xDB]):
                                extra_ctrl_bytes = packet_data[7]
                                if extra_ctrl_bytes == 0x13:
                                    # fa db 13 is internal status
                                    # device internal status. state can be off and brightness set to a non 0.
                                    # signifies what brightness when state = on, meaning don't rely on brightness for on/off.
                                    _dbg_msg = ""
                                    if CYNC_RAW is True:
                                        _dbg_msg = (
                                            f"\n\n"
                                            f"PACKET HEADER: {packet_header.hex(' ')}\nHEX: {packet_data[1:-1].hex(' ')}\nINT: {bytes2list(packet_data[1:-1])}"
                                        )

                                    # 83 00 00 00 25 37 96 24 69 00 05 00 7e {21 00 00
                                    #  00} {[fa db] 13} 00 (34 22) 11 05 00 [05] 00 db
                                    #  11 02 01 [00 64 00 00 00 00] 00 00 b3 7e
                                    id_idx = 14
                                    not_stale_idx = 19
                                    state_idx = 20
                                    bri_idx = 21
                                    tmp_idx = 22
                                    r_idx = 23
                                    g_idx = 24
                                    b_idx = 25
                                    dev_id = packet_data[id_idx]
                                    power = packet_data[state_idx]
                                    bri = packet_data[bri_idx]
                                    tmp = packet_data[tmp_idx]
                                    _red = packet_data[r_idx]
                                    _green = packet_data[g_idx]
                                    _blue = packet_data[b_idx]
                                    recently_seen = packet_data[not_stale_idx]
                                    node_repr: CyncDevice = g.ncync_server.node_devices.get(
                                        dev_id
                                    )
                                    if node_repr:
                                        dev_name = node_repr.name
                                        if node_repr.type in MULTI_ENDPOINT_TYPES:
                                            if node_repr.type == 67:
                                                # bri byte is a bitmask for on/off state of endpoints
                                                # since we know the state of up to 8 endpoints at once, parse them all
                                                for (
                                                    e_state_
                                                ) in node_repr.entities.values():
                                                    bit_shift = e_state_.sub_id - 1
                                                    e_state_.power = (
                                                        1
                                                        if (bri & (1 << bit_shift))
                                                        else 0
                                                    )
                                                    logger.debug(
                                                        f"{lp} Internal STATUS for {e_state_}{_dbg_msg}"
                                                    )
                                                    await (
                                                        node_repr.handle_entity_update(
                                                            e_state_, from_pkt="0x83"
                                                        )
                                                    )
                                        else:
                                            # Standard single endpoint
                                            e_state = EntityState(
                                                name=node_repr.name,
                                                dev_id=dev_id,
                                                power=power,
                                                brightness=bri,
                                                temperature=tmp,
                                                red=_red,
                                                green=_green,
                                                blue=_blue,
                                            )
                                            logger.debug(
                                                f"{lp} Internal STATUS for {e_state}{_dbg_msg}"
                                            )
                                            await node_repr.handle_entity_update(
                                                e_state, recently_seen, from_pkt="0x83"
                                            )

                                    else:
                                        # Unknown/disbaled/unsupported device?
                                        logger.warning(
                                            f"{lp} Received internal STATUS for unknown device: {dev_id}"
                                            f" -> p={power} b={bri} t={tmp} | r={_red} g={_green} b={_blue}"
                                        )

                                    # logger.debug(f"DBG>>> {bytes2list(packet_data[9:12]) = } // {bytes2list(packet_data[9:12]) == [17, 17, 17] = }")
                                    # LED controller has this pattern
                                    bad_chksum_msg = ""
                                    if bytes2list(packet_data[9:12]) == [17, 17, 17]:
                                        # LED controller sends its internal state in a stream of 0x83 packets.
                                        # Only the first packet in the stream has the correct checksum. Check other bytes for correct checksums?
                                        # All following 0x83 internal status packets for this stream will have the same checksum as the first packet.
                                        # As soon as we get an internal status without the first packets calculated checksum, we know that series is
                                        # done sending and it will just send regular status packets, my guess is this is the OG TELink chips had small RAM
                                        # and saved memory by sending whole mesh info at once with only dynamic bytes (pwr, bri, tmp, rgb) modified
                                        # where the LED controller uses RTL80(10|20CM) and can instead send synamic data about each device in the BTLE mesh
                                        # meaning the TELink only stored upto X node states, while the RTL can handle more/all, so they switched to a stream
                                        bad_chksum_msg = (
                                            f"{lp} Checksum mismatch, calculated: {calc_chksum} "
                                            f"// received: {checksum}"
                                        )
                                        if self.first_83_packet_checksum is None:
                                            # we want to calc the checksum and store it to compare to other packets in the series
                                            self.first_83_packet_checksum = checksum
                                            if calc_chksum != checksum:
                                                bad_chksum_msg = (
                                                    f"{lp} Checksum mismatch in INITIAL STATUS STREAM - FIRST packet data, "
                                                    f"calculated: {calc_chksum} // received: {checksum} -- open an issue on github"
                                                )

                                        else:
                                            if (
                                                checksum
                                                == self.first_83_packet_checksum
                                            ):
                                                # logger.debug(
                                                #     f"{lp} INITIAL STATUS STREAM packet data (override "
                                                #     f"calculated checksum), old: {calc_chksum} // checksum: "
                                                #     f"{checksum} // saved: {self.first_83_packet_checksum}"
                                                # )
                                                calc_chksum = (
                                                    self.first_83_packet_checksum
                                                )
                                            else:
                                                # assuming stream has ended.
                                                self.first_83_packet_checksum = None

                                    if calc_chksum != checksum:
                                        if not bad_chksum_msg:
                                            bad_chksum_msg = (
                                                f"{lp} Checksum mismatch, calculated: {calc_chksum} "
                                                f"// received: {checksum}"
                                            )
                                        # logger.warning(f"{bad_chksum_msg}\n\nHEX: {packet_data[1:-1].hex(' ')}\nINT: {bytes2list(packet_data[1:-1])}\nEXTRA CTRL BYTE: {hex(extra_ctrl_bytes)}")

                                elif extra_ctrl_bytes == 0x14:
                                    # unknown what this data is
                                    # seems to be sent when the cync app is connecting to a device via BTLE, not connecting to cync-lan via TCP

                                    # chksum_inner_data = list(inner_data)
                                    # chksum_inner_data.pop(4)
                                    # calc_chksum = sum(chksum_inner_data) % 256
                                    # logger.debug(f"{lp} 0xFA 0xDB 0x14 (NOT internal state)\nPACKET HEADER: {packet_header.hex(' ')}\nHEX: {packet_data.hex(' ')}\nINT: {bytes2list(packet_data)}\n")
                                    pass

                            else:
                                # if ctrl_bytes == bytes([0xFA, 0xAF]):
                                #     logger.debug(
                                #         f"{lp} This ctrl struct ({ctrl_bytes.hex(' ')} // checksum valid: "
                                #         f"{checksum == calc_chksum}) is MeshStatusProxyHeartbeatCommand's wire "
                                #         f"encoding (opcode 0xAF) - a heartbeat toggling whether the phone app is "
                                #         f"acting as a BTLE-mesh-status proxy, confirmed via the app's own command "
                                #         f"class.\n\n"
                                #         f"HEX: {packet_data[1:-1].hex(' ')}\nINT: {bytes2list(packet_data[1:-1])}"
                                #     ) if CYNC_RAW is True else None
                                # elif ctrl_bytes == bytes([0xFA, 0xD9]):
                                #     logger.debug(
                                #         f"{lp} Seen this ctrl struct ({ctrl_bytes.hex(' ')} // checksum valid: "
                                #         f"{checksum == calc_chksum}), unknown what it means.\n\n"
                                #         f"HEX: {packet_data[1:-1].hex(' ')}\nINT: {bytes2list(packet_data[1:-1])}"
                                #     ) if CYNC_RAW is True else None
                                # else:
                                if CYNC_RAW:
                                    logger.warning(
                                        f"{lp} UNKNOWN packet data (ctrl_bytes: {ctrl_bytes.hex(' ')} // checksum valid: "
                                        f"{checksum == calc_chksum})\n\nHEX: {packet_data[1:-1].hex(' ')}\nINT: {bytes2list(packet_data[1:-1])}"
                                    )

                    else:
                        logger.warning(
                            f"{lp} packet with no data????? After stripping header, queue and "
                            f"msg id, there is no data to process?????"
                        )
                ack = PacketBuilder.build_83_ack(msg_id)
                # logger.debug(f"{lp} RAW DATA: {data.hex(' ')}")
                # logger.debug(f"{lp} Sending ACK -> {ack.hex(' ')}")
                await self.write(ack)

            elif pkt_type == 0x73:
                # logger.debug(f"{lp} Control packet received: {packet_data.hex(' ')}") if CYNC_RAW is True else None
                if self.is_app is True:
                    logger.debug(f"{lp} device is app, skipping packet...")
                else:
                    if packet_data is not None:
                        # 0x73 should ALWAYS have 0x7e bound data.
                        # check for boundary, all bytes between boundaries are for this request
                        if packet_data[0] == DATA_BOUNDARY:
                            # checksum is 2nd last byte, last byte is 0x7e
                            checksum = packet_data[-2]
                            # inner_header = packet_data[1:6]
                            ctrl_bytes = packet_data[5:7]
                            # removes checksum byte and 0x7e
                            inner_data = packet_data[6:-2]
                            calc_chksum = sum(inner_data) % 256

                            # find next 0x7e and extract the inner struct
                            end_bndry_idx = packet_data[1:].find(DATA_BOUNDARY) + 1
                            inner_struct = packet_data[1:end_bndry_idx]
                            inner_struct_len = len(inner_struct)
                            # ctrl bytes 0xf9, 0x52 indicates this is a mesh info struct
                            # some device firmwares respond with a message received packet before replying with the data
                            # example: 7e 1f 00 00 00 f9 52 01 00 00 53 7e (12 bytes, 0x7e bound. 10 bytes of data)
                            if ctrl_bytes == bytes([0xF9, 0x52]):
                                if inner_struct_len < 15:
                                    if inner_struct_len == 10:
                                        # server sent mesh info request, this seems to be the ack?
                                        # 7e 1f 00 00 00 f9 52 01 00 00 53 7e
                                        # checksum (idx 10) = idx 6 + idx 7 % 256
                                        # seen this with Full Color LED light strip controller firmware version: 3.0.204
                                        succ_idx = 6
                                        minfo_ack_succ = inner_struct[succ_idx]
                                        minfo_ack_chksum = inner_struct[9]
                                        calc_chksum = (
                                            inner_struct[5] + inner_struct[6]
                                        ) % 256
                                        if minfo_ack_succ == 0x01:
                                            # logger.debug(f"{lp} Mesh info request ACK received, success: {minfo_ack_succ}."
                                            #              f" checksum byte = {minfo_ack_chksum}) // Calculated checksum "
                                            #              f"= {calc_chksum}")
                                            if minfo_ack_chksum != calc_chksum:
                                                logger.warning(
                                                    f"{lp} Mesh info request ACK checksum failed! {minfo_ack_chksum} != {calc_chksum}"
                                                )
                                        else:
                                            logger.warning(
                                                f"{lp} Mesh info request ACK failed! success byte: {minfo_ack_succ}"
                                            )

                                    else:
                                        logger.debug(
                                            f"{lp} inner_struct is less than 15 bytes: {inner_struct.hex(' ')}"
                                        )
                                else:
                                    # 15th OR 16th byte of inner struct is start of mesh info, 24 bytes long
                                    minfo_start_idx = 14
                                    minfo_length = 24
                                    if inner_struct[minfo_start_idx] == 0x00:
                                        minfo_start_idx += 1
                                        logger.warning(
                                            f"{lp}mesh: dev_id is 0 when using index: {minfo_start_idx - 1}, "
                                            f"trying index {minfo_start_idx} = {inner_struct[minfo_start_idx]}"
                                        )

                                    if inner_struct[minfo_start_idx] == 0x00:
                                        logger.error(
                                            f"{lp}mesh: dev_id is 0 when using index: {minfo_start_idx}, skipping..."
                                        )
                                    else:
                                        # from what I've seen, the mesh info is 24 bytes long and repeats until the end.
                                        # Reset known device ids, mesh is the final authority on what devices are connected
                                        # there does seem to be pagination 8 = devices in this packet, 12 = total devices in mesh
                                        packet_devices = inner_struct[8]
                                        total_devices = inner_struct[12]

                                        if getattr(
                                            self, "_mesh_expected", 0
                                        ) == 0 or getattr(
                                            self, "_mesh_received", 0
                                        ) >= getattr(self, "_mesh_expected", 0):
                                            # This is a fresh mesh info request (Packet 1)
                                            self.known_device_ids = []
                                            self._mesh_expected = total_devices
                                            self._mesh_received = 0
                                            logger.debug(
                                                f"{lp} Starting new mesh info sequence. Expecting {total_devices} total devices."
                                            )

                                        self._mesh_received += packet_devices
                                        logger.debug(
                                            f"{lp} Processing {packet_devices} devices in this packet. Progress: {self._mesh_received}/{self._mesh_expected}"
                                        )

                                        ids_reported = []
                                        loop_num = 0
                                        _m = []
                                        _raw_m = []
                                        # structs = []
                                        try:
                                            for i in range(
                                                minfo_start_idx,
                                                inner_struct_len,
                                                minfo_length,
                                            ):
                                                loop_num += 1
                                                mesh_dev_struct = inner_struct[
                                                    i : i + minfo_length
                                                ]
                                                dev_id = mesh_dev_struct[0]
                                                # logger.debug(f"{lp} inner_struct[{i}:{i + minfo_length}]={mesh_dev_struct.hex(' ')}")
                                                # parse status from mesh info
                                                #  [05 00 44   01 00 00 44   01 00     00 00 00 64  00 00 00 00   00 00 00 00 00 00 00] - plug (devices are all connected to it via BT)
                                                #  [07 00 00   01 00 00 00   01 01     00 00 00 64  00 00 00 fe   00 00 00 f8 00 00 00] - direct connect full color A19 bulb
                                                #   ID  ? type  ?  ?  ? type  ? state   ?  ?  ? bri  ?  ?  ? tmp   ?  ?  ?  R  G  B  ?
                                                type_idx = 2
                                                state_idx = 8
                                                bri_idx = 12
                                                tmp_idx = 16
                                                r_idx = 20
                                                g_idx = 21
                                                b_idx = 22
                                                dev_type_id = mesh_dev_struct[type_idx]
                                                dev_state = mesh_dev_struct[state_idx]
                                                dev_bri = mesh_dev_struct[bri_idx]
                                                dev_tmp = mesh_dev_struct[tmp_idx]
                                                dev_r = mesh_dev_struct[r_idx]
                                                dev_g = mesh_dev_struct[g_idx]
                                                dev_b = mesh_dev_struct[b_idx]
                                                # in mesh info, brightness can be > 0 when set to off
                                                # however, ive seen devices that are on have a state of 0 but brightness 100
                                                if dev_state == 0 and dev_bri > 0:
                                                    dev_bri = 0
                                                node_repr: Optional["CyncDevice"] = (
                                                    g.ncync_server.node_devices.get(dev_id)
                                                )
                                                if node_repr:
                                                    dev_name = node_repr.name
                                                    if loop_num == 1:
                                                        # byte 3 (idx 2) is a device type byte but,
                                                        # it only reports on the first item (itself)
                                                        # convert to int, and it is the same as deviceType from cloud.
                                                        if not self.node_id:
                                                            self.node_id = dev_id
                                                            self.lp = f"{self.ip_address}[{self.node_id}]:"
                                                            logger.debug(
                                                                f"{self.lp}parse:0x{data[0]:02x}: Setting TCP"
                                                                f" Node ID to: {self.node_id}"
                                                            )

                                                        elif (
                                                            self.node_id
                                                            and self.node_id != dev_id
                                                        ):
                                                            logger.warning(
                                                                f"{lp}parse:0x{data[0]:02x}: node_id MISMATCH "
                                                                f"open an issue on github. current: {self.node_id} "
                                                                f"// proposed: {dev_id}"
                                                            )
                                                        lp = f"{self.lp}parse:0x{data[0]:02x}:"
                                                        self.device_type_id = (
                                                            dev_type_id
                                                        )
                                                        self.name = dev_name

                                                    ids_reported.append(dev_id)
                                                    self.known_device_ids.append(dev_id)

                                                    if (
                                                        node_repr.type
                                                        in MULTI_ENDPOINT_TYPES
                                                    ):
                                                        if node_repr.type == 67:
                                                            # bri byte is a bitmask for on/off state of endpoints
                                                            # since we know the state of up to 8 endpoints at once, parse them all
                                                            for e_state_ in node_repr.entities.values():
                                                                bit_shift = (
                                                                        e_state_.sub_id - 1
                                                                )
                                                                e_state_.power = (
                                                                    1
                                                                    if (
                                                                        dev_bri
                                                                        & (
                                                                            1
                                                                            << bit_shift
                                                                        )
                                                                    )
                                                                    else 0
                                                                )
                                                                logger.debug(
                                                                    f"{lp} Mesh state for {node_repr.name} - {e_state_}"
                                                                )
                                                                await node_repr.handle_entity_update(
                                                                    e_state_,
                                                                    from_pkt="0x73",
                                                                )
                                                    else:
                                                        # Standard single endpoint
                                                        e_state = EntityState(
                                                            name=node_repr.name,
                                                            dev_id=dev_id,
                                                            power=dev_state,
                                                            brightness=dev_bri,
                                                            temperature=dev_tmp,
                                                            red=dev_r,
                                                            green=dev_g,
                                                            blue=dev_b,
                                                        )
                                                        logger.debug(
                                                            f"{lp} Mesh state for {e_state}"
                                                        )
                                                        await node_repr.handle_entity_update(
                                                            e_state,
                                                            from_pkt="0x73",
                                                        )

                                                else:
                                                    # Unknown
                                                    logger.warning(
                                                        f"{lp} Received internal STATUS for unknown device  ID: "
                                                        f"{dev_id} -> You probably need to export a new config file"
                                                    )
                                            # -- END OF mesh info response parsing loop --

                                        except IndexError:
                                            # ran out of data
                                            # logger.debug(f"{lp} IndexError parsing mesh info response (expected)") if CYNC_RAW is True else None
                                            pass
                                        except Exception as e:
                                            logger.exception(
                                                f"{lp} MESH INFO for loop EXCEPTION: {e}"
                                            )
                                        # Send mesh status ack
                                        # 73 00 00 00 14 2d e4 b5 d2 15 2d 00 7e 1e 00 00
                                        #  00 f8 {af 02 00 af 01} 61 7e
                                        # checksum 61 hex = int 97 solved: {af+02+00+af+01} % 256 = 97
                                        mesh_ack = PacketBuilder.build_mesh_status_ack(self.queue_id)
                                        # logger.debug(f"{lp} Sending MESH INFO ACK -> {mesh_ack.hex(' ')}")
                                        await self.write(mesh_ack)
                                        # Only clear the status once all paginated packets have arrived
                                        if getattr(
                                            self, "_mesh_received", 0
                                        ) >= getattr(self, "_mesh_expected", 0):
                                            logger.debug(
                                                f"{lp} Finished receiving all {getattr(self, '_mesh_expected', 0)} "
                                                f"devices in the mesh."
                                            )
                                            self._mesh_expected = 0
                                            self._mesh_received = 0
                            else:
                                (
                                    logger.debug(
                                        f"{lp} control bytes (checksum: {checksum}, verified: "
                                        f"{checksum == calc_chksum}): {ctrl_bytes.hex(' ')} // packet data: "
                                        f"{packet_data.hex(' ')}"
                                    )
                                    if CYNC_RAW
                                    else None
                                )

                                if ctrl_bytes[0] == 0xF9 and ctrl_bytes[1] in (
                                    0xD0,
                                    0xD2,
                                    0xF0,
                                    0xE2,
                                ):
                                    # control packet ack - changed state.
                                    # handle callbacks for messages
                                    # byte 8 is success? 0x01 yes // 0x00 no
                                    # 7e 09 00 00 00 f9 d0 01 00 00 d1 7e <-- original ACK
                                    # 7e 09 00 00 00 f9 d2 01 00 00 d3 7e <-- sol-lamp brightness (set_brightness's
                                    #   is_sol_lamp op=0xD2, devices.py set_brightness()) - was missing from this
                                    #   allow-list entirely, so a sol-lamp brightness change's ack fell through to
                                    #   the UNKNOWN ctrl_bytes branch instead of firing its ControlMessageCallback,
                                    #   leaving HA's brightness state stale until an unrelated mesh broadcast
                                    #   happened to correct it later. Confirmed via decompiled-app cross-reference.
                                    # 7e 09 00 00 00 f9 f0 01 00 00 f1 7e <-- newer LED strip controller
                                    # 7e 09 00 00 00 f9 e2 01 00 00 e3 7e <-- Cync default light show / effect
                                    # bytes 7 - 10 SUM --> (f0) + (01) = checksum (f1) byte 11
                                    ctrl_msg_id = packet_data[1]
                                    ctrl_chksum = sum(packet_data[6:10]) % 256
                                    success = packet_data[7] == 1
                                    msg = self.messages.control.pop(ctrl_msg_id, None)
                                    if success is True and msg is not None:
                                        if callable(msg.callback):
                                            await msg.callback()
                                        else:
                                            await msg.callback
                                    elif success is True and msg is None:
                                        logger.debug(
                                            f"{lp} CONTROL packet ACK (success: {success} / chksum: "
                                            f"{ctrl_chksum == packet_data[10]}) callback NOT found for msg ID: "
                                            f"{ctrl_msg_id}"
                                        )
                                # newer firmware devices seen in led light strip so far,
                                # send their firmware version data in a 0x7e bound struct.
                                # I've also seen these ctrl bytes in the msg that other devices send in FA AF
                                # the struct is 31 bytes long with the 0x7e boundaries, unbound it is 29 bytes long
                                elif ctrl_bytes == bytes([0xFA, 0x8E]):
                                    if packet_data[1] == 0x00:
                                        logger.debug(
                                            f"{lp} Device sent ({ctrl_bytes.hex(' ')}) BOUND firmware version data"
                                        )
                                        fw_type, fw_ver, fw_str = (
                                            extract_firmware_dynamically(
                                                packet_data[1:-1]
                                            )
                                        )
                                        if fw_type == "device":
                                            self.version = fw_ver
                                            self.version_str = fw_str
                                        else:
                                            self.protocol_version = fw_ver
                                            self.protocol_version_str = fw_str
                                    else:
                                        if CYNC_RAW is True:
                                            # PASSTHROUGH_8E: a generic wrapper the app's own
                                            # XlinkCommandCode table uses to relay any Telink
                                            # BLE-mesh notification over the WiFi/hub link -
                                            # confirmed legitimately fires on WiFi/RSSI/IP
                                            # changes, OTA/firmware status, mesh address/group/
                                            # scene changes, and periodic RGB/mesh-status
                                            # updates - not specifically an app-BTLE-connect
                                            # event (no evidence found for that in the
                                            # decompiled app).
                                            logger.debug(
                                                f"{lp} This ctrl struct ({ctrl_bytes.hex(' ')} // checksum valid: "
                                                f"{checksum == calc_chksum}) is a generic Telink-mesh-notification "
                                                f"passthrough (see PASSTHROUGH_8E)"
                                                f"\n\nHEX: {packet_data[1:-1].hex(' ')}\nINT: "
                                                f"{bytes2list(packet_data[1:-1])}"
                                            )

                                else:
                                    logger.debug(
                                        f"{lp} UNKNOWN CTRL_BYTES: {ctrl_bytes.hex(' ')} // EXTRACTED DATA -> "
                                        f"HEX: {packet_data[1:-1].hex(' ')}\nINT: {bytes2list(packet_data[1:-1])}"
                                    )
                        else:
                            logger.debug(
                                f"{lp} packet with no boundary found????? After stripping header, queue and "
                                f"msg id, there is no data to process?????"
                            )

                    else:
                        logger.warning(
                            f"{lp} packet with no data????? After stripping 12 bytes header (5), queue (4) and "
                            f"msg id (3), there is no data to process!?!"
                        )
                ack = PacketBuilder.build_73_ack(queue_id, msg_id)
                # logger.debug(f"{lp} Sending ACK -> {ack.hex(' ')}")
                await self.write(ack)

        elif PacketBuilder.is_app_request(pkt_type):
            if self.is_app is False:
                logger.info(
                    f"{lp} Device has been identified as the cync mobile app, blackholing..."
                )
                self.is_app = True

        # unknown data we don't know the header for
        else:
            logger.debug(
                f"{lp} sent UNKNOWN HEADER! Don't know how to respond!{RAW_MSG}"
            )