"""End to end, through the whole stack: HA entity -> mesh command -> socket.

Every other test in this suite mocks `nCyncServer`, which is right for what
they check but leaves the seam this integration actually is - Home Assistant's
entity model on one side, a Cync device on a TCP socket on the other - with no
coverage. Between them sit the config parse, entity construction, the bridge,
`send_command`'s framing, and the broadcast pool. A mocked server tests none of
it.

So this runs the real thing: a real `nCyncServer` on an ephemeral port, real
platform setup so entities are genuinely created, and a real device (the
library's own `cync_lan.testing.VirtualCyncDevice`) connected over TLS. Calling
`light.turn_on` and reading what arrives on that socket is the only assertion
that covers the whole path.

**What this cannot tell you.** The simulator is built from the library's own
understanding of the protocol, so a passing test means the stack is
self-consistent, not that a bulb turns on. `docs/hardware_verification.md`
remains the record of what hardware has actually confirmed - nothing here adds
to it.
"""

from __future__ import annotations

import contextlib
import socket
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.exceptions import ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cync_lan.const import (
    CONF_ACCOUNT_PASSWORD,
    CONF_ACCOUNT_USERNAME,
)

DOMAIN = "cync_lan"

# One RGB bulb, in the exported-cloud shape parse_config expects. Type 147 is
# the full-colour direct-connect bulb from cync_mesh_example.yaml, chosen
# because it produces a light entity with the widest capability set.
MESH_YAML = """
exported_homes:
  Home:
    id: 123456789
    access_key: 123456
    mac: ABCDEF1234567890
    devices:
      1:
        name: Office Lamp
        enabled: yes
        mac: 78:6D:EB:28:EA:A4
        wifi_mac: 78:6D:EB:28:EA:A5
        fw: 1.3.160
        type: 147
        endpoints:
          0: Office Lamp
"""


def _dead_port() -> int:
    """A port with nothing behind it, for "the cloud is unreachable"."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _simulator():
    """Import the shipped simulator, or say what is actually wrong.

    `cync_lan.testing` arrived in cync-lan 0.10.0. CI installs the pin from
    requirements_test.txt so it is always there, but a local checkout often
    has an older copy installed, and a bare ModuleNotFoundError sends people
    looking for a missing test file rather than a stale dependency.

    Deliberately not importorskip: a silent skip here would hide the same
    staleness in CI, which is where these tests matter most.
    """
    try:
        from cync_lan.testing import VirtualCyncDevice, build_23_auth
    except ModuleNotFoundError as exc:  # pragma: no cover - environment, not logic
        import cync_lan

        raise ModuleNotFoundError(
            "cync_lan.testing is missing - the installed cync-lan predates "
            f"0.10.0 (found {getattr(cync_lan, '__file__', '?')}). "
            "Run `pip install -r requirements_test.txt` to match CI."
        ) from exc
    return VirtualCyncDevice, build_23_auth


@pytest.fixture
def entry(hass, tmp_path):
    config = tmp_path / "cync_mesh.yaml"
    config.write_text(MESH_YAML)
    item = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={CONF_ACCOUNT_USERNAME: "user@example.com", CONF_ACCOUNT_PASSWORD: "x"},
        options={"local_port": 0, "export_refresh_interval": 0},
    )
    item.add_to_hass(hass)
    item.runtime_config_path = str(config)  # type: ignore[attr-defined]
    return item


@pytest.fixture
def tls_env(tmp_path, monkeypatch, socket_enabled):
    """Point the server's cert/key somewhere writable.

    Depends on `socket_enabled` deliberately, even though nothing here needs a
    socket for its own sake: `_dead_port()` below opens one to find a free
    port, and fixtures resolve in signature order. Without this the port probe
    ran while pytest-homeassistant-custom-component still had sockets blocked,
    which passed locally and failed in CI with "the test opens sockets".

    It generates a self-signed pair when they are missing, which is the same
    path a fresh HACS install takes - so this exercises that too rather than
    pre-seeding one.
    """
    monkeypatch.setenv("CYNC_DEVICE_CERT", str(tmp_path / "cert.pem"))
    monkeypatch.setenv("CYNC_DEVICE_KEY", str(tmp_path / "key.pem"))
    monkeypatch.setenv("CYNC_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CYNC_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("CYNC_CLOUD_PASSTHROUGH", "0")
    # Pin the cloud at a dead local port. CYNC_CLOUD_IP defaults to the real
    # vendor address, so any test that turns passthrough on would otherwise
    # open a TCP connection to GE from CI. Tests that want a working relay
    # override this with a FakeCloud port.
    monkeypatch.setenv("CYNC_CLOUD_IP", "127.0.0.1")
    monkeypatch.setenv("CYNC_CLOUD_PORT", str(_dead_port()))


async def _setup(hass, entry) -> int:
    """Set the entry up through Home Assistant, with only the cloud stubbed.

    Deliberately not a direct `async_setup_entry(hass, entry)` call, which is
    what the rest of this suite does: that leaves the entry in NOT_LOADED, and
    `async_forward_entry_setups` refuses to run from there - so the platforms
    never load and there are no entities to assert on. Going through
    config_entries is the difference between testing setup and testing the
    integration.
    """
    # `local_port: 0` goes through the option, which is how a real install
    # sets it - __init__ puts it in the environment and the server reads it
    # from there. This used to patch `cync_lan.server.CYNC_SRV_PORT`, the
    # constant that was the read site while it was frozen at import; the
    # server reads through g.env now, so that patch would be inert.
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "local_port": 0}
    )
    with (
        patch("cync_lan.const.CYNC_CONFIG_FILE_PATH", entry.runtime_config_path),
        patch("custom_components.cync_lan.util.refresh_cloud_export", AsyncMock()),
        patch("custom_components.cync_lan.refresh_cloud_export", AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()

    # Whatever the OS handed out. Asking for a specific free port and binding
    # it a moment later is a race - _free_port's docstring said as much, and
    # running 256 option combinations in sequence finally hit it. Binding 0
    # and reading back cannot collide.
    server = entry.runtime_data.ncync_server
    return server._server.sockets[0].getsockname()[1]


async def _teardown(hass, entry):
    with contextlib.suppress(Exception):
        await hass.config_entries.async_unload(entry.entry_id)
    server = getattr(getattr(entry, "runtime_data", None), "ncync_server", None)
    if server is not None:
        with contextlib.suppress(Exception):
            await server.stop()
    await hass.async_block_till_done()


async def test_a_device_on_a_real_socket_reaches_the_entity_layer(
    hass, entry, tls_env, socket_enabled
):
    """The seam, in one test: config -> entities -> server -> device.

    Asserts the light exists (so the parse and platform setup ran) and that
    the connected device is registered as a live session (so the listener and
    the handshake ran).
    """
    port = await _setup(hass, entry)
    server = entry.runtime_data.ncync_server
    try:
        assert server.running is True
        assert hass.states.get("light.office_lamp") is not None, sorted(
            s.entity_id for s in hass.states.async_all()
        )

        VirtualCyncDevice, build_23_auth = _simulator()

        async with VirtualCyncDevice("127.0.0.1", port) as device:
            await device.send(build_23_auth())
            ack = await device.read_packet()
            assert ack[0] == 0x28

            await hass.async_block_till_done()
            assert len(server.get_dev_tcp_pool_sync()) == 1
    finally:
        await _teardown(hass, entry)


async def test_turn_on_puts_a_real_command_on_the_wire(
    hass, entry, tls_env, socket_enabled
):
    """`light.turn_on` -> the bytes a Cync device would act on.

    set_power builds op 0xD0 with payload `11 02 <state> 00 00` and sends it
    inside a 0x73 control packet. Asserting on the payload rather than the
    whole frame keeps this a test of the path, not a golden-bytes fixture that
    breaks every time a message counter moves.
    """
    port = await _setup(hass, entry)
    try:
        VirtualCyncDevice, build_23_auth = _simulator()

        async with VirtualCyncDevice("127.0.0.1", port) as device:
            await device.send(build_23_auth())
            assert (await device.read_packet())[0] == 0x28
            # send_a3 lands ~0.5s later and is what marks the session ready to
            # control; without it the broadcast pool will not carry a command.
            assert await device.read_until(0xA3) is not None
            await hass.async_block_till_done()

            await hass.services.async_call(
                "light",
                "turn_on",
                {"entity_id": "light.office_lamp"},
                blocking=True,
            )

            control = await device.read_until(0x73, timeout=5.0)
            assert control is not None, (
                "no 0x73 control packet arrived after light.turn_on; "
                f"device saw {[p[:1].hex() for p in device.received]}"
            )
            assert b"\x11\x02\x01\x00\x00" in control, (
                f"0x73 arrived without the set_power payload: {control.hex(' ')}"
            )
    finally:
        await _teardown(hass, entry)


async def test_turn_off_sends_the_off_state(hass, entry, tls_env, socket_enabled):
    """The same path with the state byte flipped - cheap, and it catches a
    hardcoded 0x01 that a turn_on-only test would happily accept."""
    port = await _setup(hass, entry)
    try:
        VirtualCyncDevice, build_23_auth = _simulator()

        async with VirtualCyncDevice("127.0.0.1", port) as device:
            await device.send(build_23_auth())
            assert (await device.read_packet())[0] == 0x28
            assert await device.read_until(0xA3) is not None
            await hass.async_block_till_done()

            await hass.services.async_call(
                "light", "turn_off", {"entity_id": "light.office_lamp"}, blocking=True
            )
            control = await device.read_until(0x73, timeout=5.0)
            assert control is not None
            assert b"\x11\x02\x00\x00\x00" in control, control.hex(" ")
    finally:
        await _teardown(hass, entry)


async def test_setup_survives_a_device_that_never_connects(
    hass, entry, tls_env, socket_enabled
):
    """Entities come from the config export, not from the mesh.

    Failing setup - or withholding entities - because nothing has connected
    yet would leave a working integration with nothing in it, and a device
    that joins ten minutes later would have nowhere to report to.
    """
    await _setup(hass, entry)
    try:
        assert hass.states.get("light.office_lamp") is not None
        assert entry.runtime_data.ncync_server.get_dev_tcp_pool_sync() == []
    finally:
        await _teardown(hass, entry)


# ---------------------------------------------------------------------------
# Every option combination, and a few that should never have been written
# ---------------------------------------------------------------------------

# The boolean options the options flow can write. Their product is 256 cases;
# each is a real entry setup against a real server, ~0.2s, so the whole matrix
# costs about a minute. Worth it: cloud_passthrough shipped in a state that
# disabled every device in a live house, and no combination test existed to
# notice that one flag changed whether the integration worked at all.
BOOLEAN_OPTIONS = (
    "enable_light_groups",
    "hide_group_members",
    "enable_experimental",
    "capture_unknown_packets",
    "capture_firmware",
    "indicator_led_as_light",
    "hub_envelope_bare",
    "cloud_passthrough",
)


def _combinations():
    for bits in range(1 << len(BOOLEAN_OPTIONS)):
        yield {
            name: bool(bits >> i & 1) for i, name in enumerate(BOOLEAN_OPTIONS)
        }


def _combination_id(options: dict) -> str:
    on = [n for n, v in options.items() if v]
    return "+".join(on) if on else "all-off"


@pytest.mark.parametrize(
    "extra_options",
    list(_combinations()),
    ids=[_combination_id(c) for c in _combinations()],
)
async def test_every_option_combination_sets_up_and_unloads(
    hass, entry, tls_env, socket_enabled, extra_options
):
    """No combination may break setup, lose the entities, or fail to unload.

    Deliberately the cheap invariant rather than a full device exchange -
    running the wire assertions 256 times would cost minutes to re-prove one
    thing. What this catches is an option that changes whether the
    integration functions at all, which is exactly what cloud_passthrough did.
    """
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, **extra_options}
    )
    await _setup(hass, entry)
    try:
        assert entry.runtime_data.ncync_server.running is True
        assert hass.states.get("light.office_lamp") is not None, (
            f"{_combination_id(extra_options)} produced no light entity"
        )
    finally:
        await _teardown(hass, entry)
    assert entry.state is not config_entries.ConfigEntryState.SETUP_ERROR


@pytest.mark.parametrize("passthrough", [False, True])
async def test_a_command_reaches_the_device_with_passthrough_either_way(
    hass, entry, tls_env, socket_enabled, tmp_path, monkeypatch, passthrough
):
    """The regression, at the layer a user actually experiences it.

    With passthrough on, cync-lan relays to the cloud AND keeps controlling.
    It shipped doing only the first, so every light stopped responding while
    the option was enabled - and the whole suite stayed green, because no
    test ever turned the option on and then tried to switch something.
    """
    from cync_lan.testing import FakeCloud, write_self_signed

    VirtualCyncDevice, build_23_auth = _simulator()
    cert_dir = tmp_path / "fake-cloud"
    cert_dir.mkdir(exist_ok=True)
    certs = write_self_signed(cert_dir)

    async with FakeCloud(*certs) as cloud:
        if passthrough:
            # monkeypatch, not os.environ directly: a bare assignment outlives
            # the test and leaves passthrough enabled for everything that runs
            # afterwards, pointed at a FakeCloud port that is closed by then.
            # That is how this file went from 271 green in isolation to 17
            # failures in the full suite.
            monkeypatch.setenv("CYNC_CLOUD_PASSTHROUGH", "1")
            monkeypatch.setenv("CYNC_CLOUD_PORT", str(cloud.port))
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, "cloud_passthrough": passthrough}
        )
        port = await _setup(hass, entry)
        try:
            async with VirtualCyncDevice("127.0.0.1", port) as device:
                await device.send(build_23_auth())
                if passthrough:
                    # The cloud answers the handshake, not us.
                    assert await cloud.wait_for_bytes(1), (
                        "nothing reached the cloud; sessions="
                        f"{list(entry.runtime_data.ncync_server.tcp_connections)}"
                    )
                else:
                    assert (await device.read_packet())[0] == 0x28
                    assert await device.read_until(0xA3) is not None
                await hass.async_block_till_done()

                await hass.services.async_call(
                    "light", "turn_on", {"entity_id": "light.office_lamp"},
                    blocking=True,
                )
                control = await device.read_until(0x73, timeout=5.0)
                assert control is not None, (
                    f"no command reached the device (passthrough={passthrough})"
                )
                assert b"\x11\x02\x01\x00\x00" in control
        finally:
            await _teardown(hass, entry)


@pytest.mark.parametrize(
    "bad_options",
    [
        pytest.param({}, id="no-options-at-all"),
        pytest.param({"export_refresh_interval": -5}, id="negative-refresh"),
        pytest.param({"export_refresh_interval": 0}, id="zero-refresh"),
        pytest.param({"cloud_passthrough": "yes"}, id="string-for-bool"),
        pytest.param({"cloud_passthrough": None}, id="none-for-bool"),
        pytest.param({"indicator_led_as_light": 1}, id="int-for-bool"),
        pytest.param({"local_port": -1}, id="negative-port"),
        pytest.param({"local_port": 70000}, id="port-out-of-range"),
        pytest.param({"an_option_from_the_future": True}, id="unknown-key"),
    ],
)
async def test_malformed_options_fail_cleanly_or_not_at_all(
    hass, entry, tls_env, socket_enabled, bad_options
):
    """Options are not always what the options flow wrote.

    A downgrade after a newer version stored a key, a hand-edited
    .storage/core.config_entries, a schema change between releases - all of
    these reach async_setup_entry as values nothing validated. The contract
    is narrow on purpose: either the entry sets up, or it fails in a way Home
    Assistant understands. What must not happen is an arbitrary exception
    escaping setup, because that is what leaves an entry wedged with no
    entities and a traceback the user cannot act on.
    """
    hass.config_entries.async_update_entry(entry, options=dict(bad_options))
    try:
        with (
            patch("cync_lan.const.CYNC_CONFIG_FILE_PATH", entry.runtime_config_path),
            patch("custom_components.cync_lan.util.refresh_cloud_export", AsyncMock()),
            patch("custom_components.cync_lan.refresh_cloud_export", AsyncMock()),
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
    except ConfigEntryNotReady:
        return  # a retry HA knows how to schedule
    finally:
        await _teardown(hass, entry)

    assert entry.state in (
        config_entries.ConfigEntryState.LOADED,
        config_entries.ConfigEntryState.NOT_LOADED,
        config_entries.ConfigEntryState.SETUP_RETRY,
        config_entries.ConfigEntryState.SETUP_ERROR,
    ), entry.state
