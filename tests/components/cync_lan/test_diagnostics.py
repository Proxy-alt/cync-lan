"""Tests for diagnostics.py.

Two things matter most here and are tested hardest: nothing sensitive leaks
into a file users paste into public issues, and the dump never raises. A
diagnostics download that fails leaves the reporter with nothing to attach,
which is worse than a field reading "unavailable".
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.diagnostics import async_get_config_entry_diagnostics


def _fake_node(**overrides):
    node = MagicMock()
    node.id = 5
    node.name = "Test Light"
    node.type = 48
    node.home_id = 1234
    node.version_str = "1.2.3"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.metadata = MagicMock(supported=True)
    node.metadata.type = "light"
    node.metadata.model_string = "Some Model"
    node.is_light = True
    node.is_switch = False
    node.is_plug = False
    node.is_fan_controller = False
    node.is_dimmable = True
    node.is_sol_lamp = False
    node.is_hvac = False
    node.has_motion_sensor = False
    node.supports_rgb = True
    node.supports_temperature = True
    node.has_multi_entities = False
    node.has_wifi = True
    node.entities = None
    node.tcp_session = None
    node.relay_source = None
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


def _fake_session(ip="10.0.0.5", dev_id=5):
    s = MagicMock()
    s.node = SimpleNamespace(id=dev_id)
    s.is_closed = MagicMock(return_value=False)
    s.ready_to_control = True
    s.mitm_mode = False
    s.is_app = False
    s.allowed_to_connect = True
    s.ip_address = ip
    return s


def _entry(hass, *, nodes=None, sessions=None, options=None):
    bridge = CyncLanBridge(hass, "entry1")
    server = MagicMock()
    server.node_devices = nodes if nodes is not None else {5: _fake_node()}
    server.running = True
    server.shutting_down = False
    server.host = "0.0.0.0"
    server.port = 23779
    server.tcp_connections = sessions if sessions is not None else {}
    server.tcp_conn_attempts = {"10.0.0.5": 2}

    entry = MagicMock()
    entry.data = {"account_username": "user@example.com", "account_password": "secret"}
    entry.options = options or {"local_port": 23779}
    entry.state = "loaded"
    entry.runtime_data = SimpleNamespace(
        bridge=bridge,
        ncync_server=server,
        groups={32770: {"name": "Kitchen", "device_ids": [5]}},
        scenes={3: {"name": "Movie"}},
        schedules={7: {"name": "Wake", "scene_id": 3, "enabled": True}},
    )
    return entry, bridge


# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------


async def test_credentials_and_hardware_ids_are_redacted(hass):
    entry, _ = _entry(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"]["data"]["account_password"] == "**REDACTED**"
    assert result["entry"]["data"]["account_username"] == "**REDACTED**"
    assert result["devices"][0]["mac"] == "**REDACTED**"
    assert result["devices"][0]["wifi_mac"] == "**REDACTED**"


async def test_environment_secrets_are_redacted(hass, monkeypatch):
    """The environment block is the whole point of including it, but it also
    carries the account password and the token-cache encryption key."""
    monkeypatch.setenv("CYNC_ACCOUNT_PASSWORD", "hunter2")
    monkeypatch.setenv("CYNC_SECRET_KEY", "super-secret-key")
    monkeypatch.setenv("CYNC_PORT", "23779")
    entry, _ = _entry(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["environment"]["CYNC_ACCOUNT_PASSWORD"] == "**REDACTED**"
    assert result["environment"]["CYNC_SECRET_KEY"] == "**REDACTED**"
    # Non-secret values must survive, or the block is useless.
    assert result["environment"]["CYNC_PORT"] == "23779"


async def test_no_secret_value_appears_anywhere_in_the_dump(hass, monkeypatch):
    """Belt and braces: scan the serialised output for the literal secrets,
    so a future field that happens to echo one is caught."""
    import json

    monkeypatch.setenv("CYNC_ACCOUNT_PASSWORD", "p4ssw0rd-canary")
    monkeypatch.setenv("CYNC_SECRET_KEY", "secretkey-canary")
    entry, _ = _entry(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)
    blob = json.dumps(result, default=str)

    for canary in ("p4ssw0rd-canary", "secretkey-canary", "secret", "AA:BB:CC:DD:EE:FF"):
        assert canary not in blob, f"{canary!r} leaked into diagnostics"


# ---------------------------------------------------------------------------
# content
# ---------------------------------------------------------------------------


async def test_versions_are_reported(hass):
    """The first question of any bug report."""
    entry, _ = _entry(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    versions = result["versions"]
    assert versions["home_assistant"]
    assert versions["python"]
    # Real values, not placeholders.
    assert not str(versions["cync_lan_library"]).startswith("<")
    assert not str(versions["integration"]).startswith("<")


async def test_device_capabilities_are_reported(hass):
    """Platform routing is derived from these, so a device showing up as the
    wrong entity type is diagnosed from the inputs, not the conclusion."""
    entry, _ = _entry(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    caps = result["devices"][0]["capabilities"]
    assert caps["is_light"] is True
    assert caps["is_switch"] is False
    assert caps["supports_rgb"] is True
    assert caps["has_wifi"] is True


async def test_live_entity_state_is_included(hass):
    from cync_lan.structs import EntityState

    entry, bridge = _entry(hass)
    await bridge.parse_entity_state(
        EntityState(name="x", dev_id=5, power=1, brightness=70, temperature=254)
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    state = result["devices"][0]["state"]
    assert state["power"] == 1
    assert state["brightness"] == 70


async def test_sessions_are_reported_individually(hass):
    """'Devices are offline' needs to separate nothing-connected from
    connected-but-not-ready from stuck-in-MITM."""
    entry, _ = _entry(hass, sessions={"10.0.0.5": _fake_session()})

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["server"]["connected_sessions"] == 1
    session = result["sessions"][0]
    assert session["ip_address"] == "10.0.0.5"
    assert session["device_id"] == 5
    assert session["ready_to_control"] is True
    assert session["mitm_mode"] is False


async def test_cloud_export_summary_is_reported(hass):
    entry, _ = _entry(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    export = result["cloud_export"]
    assert export["group_count"] == 1
    assert export["scene_ids"] == [3]
    assert export["schedule_ids"] == [7]


async def test_bridge_re_export_state_is_reported(hass):
    """The unknown-device counters are how the 'new device was ignored'
    class of report gets diagnosed."""
    entry, bridge = _entry(hass)
    # report_unknown_device_id returns immediately with no callback wired, so
    # the counters only move on a bridge that has one - as it always does in
    # production, where __init__.py supplies the re-export callback.
    async def _noop():
        return None

    bridge._on_unknown_device = _noop
    bridge.report_unknown_device_id(99)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["bridge"]["unknown_device_sightings"] == {99: 1}
    assert result["bridge"]["seen_threshold"] == bridge.UNKNOWN_DEVICE_SEEN_THRESHOLD
    assert result["bridge"]["has_triggered_re_export"] is False


# ---------------------------------------------------------------------------
# robustness
# ---------------------------------------------------------------------------


async def test_a_property_that_raises_does_not_break_the_dump(hass):
    """Classification properties compute from optional metadata and can raise
    on a partially-identified device - which is precisely the state most
    likely to be reported."""
    # A real object, not a MagicMock: a mock stores is_light as an instance
    # attribute, which shadows any property put on its type.
    class _HalfIdentifiedDevice:
        id = 5
        name = "Half Identified"
        type = 48
        home_id = 1234
        version_str = None
        mac = "AA:BB:CC:DD:EE:FF"
        wifi_mac = None
        bt_only = False
        metadata = None
        entities = None
        tcp_session = None
        relay_source = None

        @property
        def is_light(self):
            raise AttributeError("no metadata")

    entry, _ = _entry(hass, nodes={5: _HalfIdentifiedDevice()})

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert "unavailable" in str(result["devices"][0]["capabilities"]["is_light"])
    # ...and the rest of the dump is intact.
    assert result["devices"][0]["id"] == 5
    assert result["versions"]["home_assistant"]


async def test_dump_survives_a_server_with_no_devices_or_sessions(hass):
    entry, _ = _entry(hass, nodes={}, sessions={})

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["devices"] == []
    assert result["sessions"] == []
    assert result["server"]["known_devices"] == 0


@pytest.mark.parametrize("missing", ["groups", "scenes", "schedules"])
async def test_dump_survives_missing_cloud_export_data(hass, missing):
    entry, _ = _entry(hass)
    setattr(entry.runtime_data, missing, None)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["cloud_export"] is not None
