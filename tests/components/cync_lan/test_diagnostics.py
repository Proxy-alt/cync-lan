"""Tests for diagnostics.py."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.diagnostics import async_get_config_entry_diagnostics


def _fake_node(**overrides):
    node = MagicMock()
    node.id = 5
    node.name = "Test Light"
    node.type = 48
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.metadata = MagicMock(supported=True)
    node.metadata.type = "light"
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


async def test_diagnostics_redacts_credentials_and_macs(hass):
    bridge = CyncLanBridge(hass, "entry1")
    await bridge.pub_online(5, True)
    node = _fake_node()

    server = MagicMock()
    server.node_devices = {5: node}
    server.running = True
    server.host = "0.0.0.0"
    server.port = 23779
    server.tcp_connections = [MagicMock()]

    entry = MagicMock()
    entry.data = {"account_username": "user@example.com", "account_password": "secret"}
    entry.options = {"local_port": 23779}
    entry.runtime_data.bridge = bridge
    entry.runtime_data.ncync_server = server

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"]["data"]["account_password"] == "**REDACTED**"
    assert result["devices"][0]["mac"] == "**REDACTED**"
    assert result["devices"][0]["wifi_mac"] == "**REDACTED**"
    assert result["devices"][0]["id"] == 5
    assert result["devices"][0]["online"] is True
    assert result["server"]["running"] is True
    assert result["server"]["connected_tcp_devices"] == 1


async def test_diagnostics_handles_missing_tcp_connections_attr(hass):
    bridge = CyncLanBridge(hass, "entry1")
    node = _fake_node()

    server = MagicMock(spec=["node_devices", "running", "host", "port"])
    server.node_devices = {5: node}
    server.running = False
    server.host = "0.0.0.0"
    server.port = 23779

    entry = MagicMock()
    entry.data = {"account_username": "user@example.com", "account_password": "secret"}
    entry.options = {}
    entry.runtime_data.bridge = bridge
    entry.runtime_data.ncync_server = server

    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["server"]["connected_tcp_devices"] is None
