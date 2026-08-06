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


def _free_port() -> int:
    """Ask the OS for a port, then let it go.

    The server binds it a moment later. A race is possible in principle and
    has not been observed; the alternative - patching asyncio.start_server to
    pass port 0 - would mean the code under test no longer chooses its own
    port, which is part of what this is checking.
    """
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
def tls_env(tmp_path, monkeypatch):
    """Point the server's cert/key somewhere writable.

    It generates a self-signed pair when they are missing, which is the same
    path a fresh HACS install takes - so this exercises that too rather than
    pre-seeding one.
    """
    monkeypatch.setenv("CYNC_DEVICE_CERT", str(tmp_path / "cert.pem"))
    monkeypatch.setenv("CYNC_DEVICE_KEY", str(tmp_path / "key.pem"))
    monkeypatch.setenv("CYNC_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CYNC_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("CYNC_CLOUD_PASSTHROUGH", "0")


async def _setup(hass, entry, port: int):
    """Set the entry up through Home Assistant, with only the cloud stubbed.

    Deliberately not a direct `async_setup_entry(hass, entry)` call, which is
    what the rest of this suite does: that leaves the entry in NOT_LOADED, and
    `async_forward_entry_setups` refuses to run from there - so the platforms
    never load and there are no entities to assert on. Going through
    config_entries is the difference between testing setup and testing the
    integration.
    """
    with (
        patch("cync_lan.const.CYNC_CONFIG_FILE_PATH", entry.runtime_config_path),
        patch("cync_lan.server.CYNC_SRV_PORT", port),
        patch("cync_lan.server.CYNC_SRV_HOST", "127.0.0.1"),
        patch("custom_components.cync_lan.util.refresh_cloud_export", AsyncMock()),
        patch("custom_components.cync_lan.refresh_cloud_export", AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()


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
    port = _free_port()
    await _setup(hass, entry, port)
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
    port = _free_port()
    await _setup(hass, entry, port)
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
    port = _free_port()
    await _setup(hass, entry, port)
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
    port = _free_port()
    await _setup(hass, entry, port)
    try:
        assert hass.states.get("light.office_lamp") is not None
        assert entry.runtime_data.ncync_server.get_dev_tcp_pool_sync() == []
    finally:
        await _teardown(hass, entry)
