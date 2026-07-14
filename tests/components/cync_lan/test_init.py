"""Tests for custom_components/cync_lan/__init__.py setup/unload flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.cync_lan.const import (
    CONF_ACCOUNT_PASSWORD,
    CONF_ACCOUNT_USERNAME,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "cync_lan"


def _make_entry(**options):
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={CONF_ACCOUNT_USERNAME: "user@example.com", CONF_ACCOUNT_PASSWORD: "x"},
        options=options or {"local_port": 23779, "export_refresh_interval": 0},
    )


def _mock_server(running_after_start=True):
    server = MagicMock()
    server.running = False
    server.node_devices = {}
    server.host = "0.0.0.0"
    server.port = 23779

    async def _start():
        server.running = running_after_start

    server.start = AsyncMock(side_effect=_start)
    server.stop = AsyncMock()
    return server


@pytest.fixture(autouse=True)
def _fast_bind_poll():
    """Shrink the bind-wait loop so tests don't burn the real timeout."""
    with patch("custom_components.cync_lan._BIND_POLL_INTERVAL", 0.001), patch(
        "custom_components.cync_lan._BIND_TIMEOUT", 0.05
    ):
        yield


async def test_setup_entry_no_config_file_raises_not_ready(hass, tmp_path):
    entry = _make_entry()
    entry.add_to_hass(hass)
    with patch(
        "cync_lan.const.CYNC_CONFIG_FILE_PATH", str(tmp_path / "does_not_exist.yaml")
    ):
        with pytest.raises(ConfigEntryNotReady):
            from custom_components.cync_lan import async_setup_entry

            await async_setup_entry(hass, entry)


async def test_setup_entry_success(hass, tmp_path):
    cfg_file = tmp_path / "cync_mesh.yaml"
    cfg_file.write_text("devices: {}")
    entry = _make_entry()
    entry.add_to_hass(hass)

    server = _mock_server(running_after_start=True)
    with patch("cync_lan.const.CYNC_CONFIG_FILE_PATH", str(cfg_file)), patch(
        "cync_lan.server.nCyncServer", return_value=server
    ), patch("cync_lan.utils.parse_config", new=AsyncMock(return_value={})), patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        from custom_components.cync_lan import async_setup_entry

        result = await async_setup_entry(hass, entry)

    assert result is True
    assert entry.runtime_data.ncync_server is server
    server.start.assert_awaited_once()


async def test_setup_entry_bind_timeout_raises_not_ready(hass, tmp_path):
    cfg_file = tmp_path / "cync_mesh.yaml"
    cfg_file.write_text("devices: {}")
    entry = _make_entry()
    entry.add_to_hass(hass)

    server = _mock_server(running_after_start=False)  # never binds
    with patch("cync_lan.const.CYNC_CONFIG_FILE_PATH", str(cfg_file)), patch(
        "cync_lan.server.nCyncServer", return_value=server
    ), patch("cync_lan.utils.parse_config", new=AsyncMock(return_value={})):
        from custom_components.cync_lan import async_setup_entry

        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)


async def test_unload_entry_stops_server_and_platforms(hass, tmp_path):
    from custom_components.cync_lan import CyncLanRuntimeData
    from custom_components.cync_lan.bridge import CyncLanBridge

    entry = _make_entry()
    entry.add_to_hass(hass)
    server = _mock_server()
    server.start = AsyncMock()  # already "started" - task below just resolves
    task = hass.async_create_task(server.start())
    await hass.async_block_till_done()

    entry.runtime_data = CyncLanRuntimeData(
        bridge=CyncLanBridge(hass, entry.entry_id),
        ncync_server=server,
        server_task=task,
    )

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        from custom_components.cync_lan import async_unload_entry

        result = await async_unload_entry(hass, entry)

    assert result is True
    server.stop.assert_awaited_once()
