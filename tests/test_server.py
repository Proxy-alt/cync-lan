"""Tests for nCyncServer - previously 0% covered.

nCyncServer is a singleton (__new__ returns the same instance forever), so
every test here goes through the reset_server fixture rather than assuming a
clean object. That singleton-plus-class-level-mutable-defaults shape is
exactly what produced the "shutting_down stayed True after a reload, so
every device was rejected for 5+ hours" bug the __init__ comments describe.
"""

from __future__ import annotations

import asyncio
import contextlib
import ssl
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from cync_lan.devices import CyncTCPSession
from cync_lan.server import nCyncServer
from cync_lan.structs import GlobalObject


@pytest.fixture(autouse=True)
def reset_server():
    """Drop the singleton between tests so state can't leak across them.

    Also points the module-level GlobalObject at this server: CyncTCPSession
    reaches for g.ncync_server during construction, so without it a bare
    nCyncServer() is not actually usable.

    g.mqtt_client is cleared for the same reason, and it is not theoretical:
    both GlobalObject and nCyncServer are process-wide singletons, so
    whichever test file ran last leaves its own mocks installed on them.
    These tests passed alone and failed in the full suite until this reset
    existed, because another module had left behind a plain MagicMock whose
    `publish` cannot be awaited.
    """
    nCyncServer._instance = None
    g = GlobalObject()
    previous_server, previous_mqtt = g.ncync_server, g.mqtt_client
    g.mqtt_client = None
    yield
    g.ncync_server, g.mqtt_client = previous_server, previous_mqtt
    nCyncServer._instance = None


@pytest.fixture
def server() -> nCyncServer:
    srv = nCyncServer({})
    GlobalObject().ncync_server = srv
    return srv


async def _drain(server: nCyncServer) -> None:
    """Cancel the background tasks add_tcp_device() started.

    Registering a connection with no prior session builds a REAL
    CyncTCPSession, and add_tcp_device() immediately calls start_tasks() on
    it - a receive loop and a callback-cleanup loop that outlive the test
    unless something cancels them.
    """
    for session in list(server.tcp_connections.values()):
        tasks = getattr(session, "tasks", None)
        if tasks is None:
            continue
        for name in ("receive", "callback_cleanup", "connection_watcher"):
            task = getattr(tasks, name, None)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


def _session(*, closed=False, is_app=False, ip="10.0.0.1") -> MagicMock:
    """A stand-in session.

    spec=CyncTCPSession matters: remove_tcp_device/_register_new_connection
    branch on isinstance(device, CyncTCPSession), and a bare MagicMock fails
    that check and silently takes the "unknown device" path instead.
    """
    s = MagicMock(spec=CyncTCPSession)
    s.is_closed = MagicMock(return_value=closed)
    s.is_app = is_app
    s.ip_address = ip
    s.mitm_mode = False
    s.close = AsyncMock()
    s.start_tasks = AsyncMock()
    s.existing_init = AsyncMock()
    s.can_connect = AsyncMock(return_value=True)
    s.allowed_to_connect = True
    return s


# --------------------------------------------------------------------------
# Device pool filtering
# --------------------------------------------------------------------------


def test_pool_excludes_closed_and_app_sessions(server):
    """Regression for the `or`/`and` De Morgan bug: with `or`, a closed
    device session stayed in the broadcast pool forever, producing one
    "writer is None, can't write data!" per command per dead session."""
    live = _session(ip="10.0.0.1")
    dead = _session(closed=True, ip="10.0.0.2")
    app = _session(is_app=True, ip="10.0.0.3")
    dead_app = _session(closed=True, is_app=True, ip="10.0.0.4")
    for s in (live, dead, app, dead_app):
        server.tcp_connections[s.ip_address] = s

    assert server.get_dev_tcp_pool_sync() == [live]


async def test_async_pool_matches_sync_pool(server):
    """The async variant must not drift from the sync one - they were two
    independent copies of the same comprehension, and the bug above was
    fixed in only one of them."""
    live = _session(ip="10.0.0.1")
    dead = _session(closed=True, ip="10.0.0.2")
    server.tcp_connections = {s.ip_address: s for s in (live, dead)}

    assert await server.get_dev_tcp_pool() == server.get_dev_tcp_pool_sync() == [live]


# --------------------------------------------------------------------------
# Singleton re-init
# --------------------------------------------------------------------------


def test_reinit_resets_shutdown_and_connection_state():
    """A second construction (entry reload) must not inherit stop()'s
    leftovers. shutting_down staying True meant can_connect() rejected
    every future connection for the life of the process."""
    server = nCyncServer({1: MagicMock()})
    server.shutting_down = True
    server.running = True
    server.tcp_connections["10.0.0.1"] = _session()
    server.tcp_conn_attempts["10.0.0.1"] = 7

    again = nCyncServer({2: MagicMock()})

    assert again is server, "nCyncServer is expected to be a singleton"
    assert again.shutting_down is False
    assert again.running is False
    assert again.tcp_connections == {}
    assert again.tcp_conn_attempts == {}
    assert set(again.node_devices) == {2}


def test_port_is_an_int(server):
    """asyncio.start_server needs a real int; the attribute was annotated
    `str` while being assigned the int from const."""
    assert isinstance(server.port, int)


# --------------------------------------------------------------------------
# add/remove bookkeeping
# --------------------------------------------------------------------------


async def test_add_tcp_device_registers_and_starts_tasks(server):
    dev = _session(ip="10.0.0.9")

    await server.add_tcp_device(dev)

    assert server.tcp_connections["10.0.0.9"] is dev
    dev.start_tasks.assert_awaited_once()


async def test_remove_tcp_device_by_address_and_by_object(server):
    dev = _session(ip="10.0.0.9")
    server.tcp_connections["10.0.0.9"] = dev

    assert await server.remove_tcp_device("10.0.0.9") is dev
    assert server.tcp_connections == {}

    server.tcp_connections["10.0.0.9"] = dev
    assert await server.remove_tcp_device(dev) is dev
    assert server.tcp_connections == {}


async def test_remove_unknown_device_is_a_noop(server):
    assert await server.remove_tcp_device("192.168.1.1") is None


# --------------------------------------------------------------------------
# Connection registration
# --------------------------------------------------------------------------


def _rw(ip="10.0.0.5"):
    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=(ip, 12345))
    return reader, writer


async def test_register_new_connection_counts_attempts_per_ip(server):
    """can_connect() reads this counter to rate-limit its warnings."""
    reader, writer = _rw("10.0.0.5")

    await server._register_new_connection(reader, writer)
    await server._register_new_connection(reader, writer)
    await _drain(server)

    assert server.tcp_conn_attempts["10.0.0.5"] == 2


async def test_register_new_connection_creates_a_session(server):
    reader, writer = _rw("10.0.0.5")

    await server._register_new_connection(reader, writer)
    created = server.tcp_connections.get("10.0.0.5")
    await _drain(server)

    assert created is not None


async def test_reconnect_replaces_and_closes_the_previous_session(server):
    old = _session(ip="10.0.0.5")
    server.tcp_connections["10.0.0.5"] = old
    reader, writer = _rw("10.0.0.5")

    await server._register_new_connection(reader, writer)

    old.close.assert_awaited_once()
    old.existing_init.assert_awaited_once()
    assert server.tcp_connections["10.0.0.5"] is old
    assert old.reader is reader and old.writer is writer


async def test_reconnect_of_a_disallowed_device_is_re_evaluated_and_dropped(server):
    """can_connect() is async; it was once called without await, so this
    comparison was always False and a rejected device got silently
    resurrected on every reconnect instead of being re-checked."""
    old = _session(ip="10.0.0.5")
    old.allowed_to_connect = False
    old.can_connect = AsyncMock(return_value=False)
    server.tcp_connections["10.0.0.5"] = old
    reader, writer = _rw("10.0.0.5")

    await server._register_new_connection(reader, writer)

    old.can_connect.assert_awaited_once()
    assert server.tcp_connections == {}


async def test_mitm_session_is_not_closed_on_reconnect(server):
    old = _session(ip="10.0.0.5")
    old.mitm_mode = True
    server.tcp_connections["10.0.0.5"] = old
    reader, writer = _rw("10.0.0.5")

    await server._register_new_connection(reader, writer)

    old.close.assert_not_awaited()


# --------------------------------------------------------------------------
# TLS material
# --------------------------------------------------------------------------


def test_ensure_self_signed_cert_creates_a_usable_pair(tmp_path: Path):
    """A pip/HACS install has no build step to pre-generate these, so
    start() used to die with FileNotFoundError on load_cert_chain."""
    cert = tmp_path / "nested" / "cert.pem"
    key = tmp_path / "nested" / "key.pem"

    nCyncServer._ensure_self_signed_cert(str(cert), str(key))

    assert cert.exists() and key.exists()
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(str(cert), str(key))  # raises if the pair is bad


def test_ensure_self_signed_cert_does_not_regenerate(tmp_path: Path):
    """Regenerating on every start would hand devices a new identity each
    time the server restarts."""
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    nCyncServer._ensure_self_signed_cert(str(cert), str(key))
    first = cert.read_bytes()

    nCyncServer._ensure_self_signed_cert(str(cert), str(key))

    assert cert.read_bytes() == first


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


async def test_stop_closes_every_device_then_the_server(server):
    a, b = _session(ip="10.0.0.1"), _session(ip="10.0.0.2")
    server.tcp_connections = {"10.0.0.1": a, "10.0.0.2": b}
    server._server = MagicMock()
    server._server.is_serving = MagicMock(return_value=True)
    server._server.wait_closed = AsyncMock()

    await server.stop()

    a.close.assert_awaited_once()
    b.close.assert_awaited_once()
    server._server.close.assert_called_once()
    assert server.shutting_down is True


async def test_stop_survives_a_device_that_fails_to_close(server):
    """One bad session must not strand the others still connected."""
    bad, good = _session(ip="10.0.0.1"), _session(ip="10.0.0.2")
    bad.close = AsyncMock(side_effect=RuntimeError("boom"))
    server.tcp_connections = {"10.0.0.1": bad, "10.0.0.2": good}
    server._server = None

    await server.stop()

    good.close.assert_awaited_once()


async def test_stop_propagates_cancellation(server):
    dev = _session()
    dev.close = AsyncMock(side_effect=asyncio.CancelledError())
    server.tcp_connections = {"10.0.0.1": dev}

    with pytest.raises(asyncio.CancelledError):
        await server.stop()


async def test_start_leaves_running_false_when_the_bind_fails(server):
    """The HA integration polls `running` to decide setup succeeded, so a
    failed bind must not leave it True."""
    server.create_ssl_context = AsyncMock(return_value=MagicMock())

    import cync_lan.server as server_mod

    original = server_mod.asyncio.start_server
    server_mod.asyncio.start_server = AsyncMock(
        side_effect=OSError("address already in use")
    )
    try:
        await server.start()
    finally:
        server_mod.asyncio.start_server = original

    assert server.running is False


def test_construction_does_not_require_a_current_event_loop():
    """nCyncServer must be constructible with no loop set.

    __init__ used to stash asyncio.get_event_loop() into a self.loop that
    nothing ever read. get_event_loop() raises when no loop is current -
    exactly the state pytest-asyncio leaves behind after an async test - so
    that dead attribute failed 16 tests in CI on 3.12 and 3.14 while passing
    locally, where an ambient plugin happened to leave a loop set.
    """
    import asyncio

    previous = None
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        pass
    asyncio.set_event_loop(None)
    try:
        server = nCyncServer({})
        assert server.tcp_connections == {}
        assert not hasattr(server, "loop"), (
            "self.loop is dead weight - anything needing the loop should call "
            "get_running_loop() at the point of use"
        )
    finally:
        asyncio.set_event_loop(previous)
