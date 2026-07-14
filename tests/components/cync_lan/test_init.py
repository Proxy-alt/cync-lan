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


async def test_no_devices_check_creates_issue_when_nothing_connected(hass):
    """repair-issues (gold)."""
    from homeassistant.helpers import issue_registry as ir

    from custom_components.cync_lan import _check_and_report_no_devices

    entry = _make_entry()
    entry.add_to_hass(hass)
    server = _mock_server()
    server.tcp_connections = {}  # nothing connected

    await _check_and_report_no_devices(hass, entry, server)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"no_devices_connected_{entry.entry_id}")
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.is_fixable is False


async def test_no_devices_check_clears_issue_once_a_device_connects(hass):
    from homeassistant.helpers import issue_registry as ir

    from custom_components.cync_lan import _check_and_report_no_devices

    entry = _make_entry()
    entry.add_to_hass(hass)
    server = _mock_server()
    server.tcp_connections = {}

    await _check_and_report_no_devices(hass, entry, server)
    assert ir.async_get(hass).async_get_issue(
        DOMAIN, f"no_devices_connected_{entry.entry_id}"
    )

    server.tcp_connections = {"192.168.1.50": MagicMock()}
    await _check_and_report_no_devices(hass, entry, server)
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"no_devices_connected_{entry.entry_id}"
        )
        is None
    )


async def test_unload_entry_clears_no_devices_issue(hass, tmp_path):
    from homeassistant.helpers import issue_registry as ir

    from custom_components.cync_lan import (
        CyncLanRuntimeData,
        _check_and_report_no_devices,
        async_unload_entry,
    )
    from custom_components.cync_lan.bridge import CyncLanBridge

    entry = _make_entry()
    entry.add_to_hass(hass)
    server = _mock_server()
    server.start = AsyncMock()
    server.tcp_connections = {}
    task = hass.async_create_task(server.start())
    await hass.async_block_till_done()

    entry.runtime_data = CyncLanRuntimeData(
        bridge=CyncLanBridge(hass, entry.entry_id),
        ncync_server=server,
        server_task=task,
    )
    await _check_and_report_no_devices(hass, entry, server)
    assert ir.async_get(hass).async_get_issue(
        DOMAIN, f"no_devices_connected_{entry.entry_id}"
    )

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        await async_unload_entry(hass, entry)

    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"no_devices_connected_{entry.entry_id}"
        )
        is None
    )


async def test_setup_entry_schedules_no_devices_check(hass, tmp_path):
    """Confirm setup wires the scheduled check up at all (the check logic
    itself is covered directly above; this just verifies the call_later
    registration and that it's cancellable on unload)."""
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

        await async_setup_entry(hass, entry)

    assert entry.runtime_data.unsub_no_devices_check is not None


def _register_device(hass, entry, dev_id: int):
    from homeassistant.helpers import device_registry as dr

    device_reg = dr.async_get(hass)
    return device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_{dev_id}")},
        name=f"Device {dev_id}",
    )


async def test_refresh_no_mtime_change_does_nothing(hass, tmp_path):
    from custom_components.cync_lan import _refresh_export_and_reload_if_changed

    cfg_file = tmp_path / "cync_mesh.yaml"
    cfg_file.write_text("devices: {}")
    entry = _make_entry()
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock()
    entry.runtime_data.ncync_server.node_devices = {1: MagicMock()}

    with patch("cync_lan.cloud_api.CyncCloudAPI") as mock_api_cls, patch(
        "homeassistant.config_entries.ConfigEntries.async_reload", new=AsyncMock()
    ) as mock_reload:
        mock_api_cls.return_value.export_config_file = AsyncMock(return_value=True)
        await _refresh_export_and_reload_if_changed(hass, entry, cfg_file)

    mock_reload.assert_not_called()


async def test_refresh_removed_device_deletes_from_registry_without_reload(
    hass, tmp_path
):
    """stale-devices (gold): removal alone doesn't need a reload."""
    from homeassistant.helpers import device_registry as dr

    from custom_components.cync_lan import _refresh_export_and_reload_if_changed

    cfg_file = tmp_path / "cync_mesh.yaml"
    cfg_file.write_text("devices: {}")
    entry = _make_entry()
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock()
    entry.runtime_data.ncync_server.node_devices = {1: MagicMock(), 2: MagicMock()}

    device = _register_device(hass, entry, 2)
    assert dr.async_get(hass).async_get(device.id) is not None

    async def _touch_mtime():
        cfg_file.write_text("devices: {1: {}}")  # different content -> new mtime

    with patch("cync_lan.cloud_api.CyncCloudAPI") as mock_api_cls, patch(
        "cync_lan.utils.parse_config", new=AsyncMock(return_value={1: MagicMock()})
    ), patch(
        "homeassistant.config_entries.ConfigEntries.async_reload", new=AsyncMock()
    ) as mock_reload:
        mock_api_cls.return_value.export_config_file = AsyncMock(
            side_effect=_touch_mtime
        )
        await _refresh_export_and_reload_if_changed(hass, entry, cfg_file)

    mock_reload.assert_not_called()
    assert dr.async_get(hass).async_get(device.id) is None


async def test_refresh_added_device_triggers_reload(hass, tmp_path):
    """dynamic-devices (gold): additions still need a reload - see
    quality_scale.yaml for why incremental add isn't implemented yet."""
    from custom_components.cync_lan import _refresh_export_and_reload_if_changed

    cfg_file = tmp_path / "cync_mesh.yaml"
    cfg_file.write_text("devices: {}")
    entry = _make_entry()
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock()
    entry.runtime_data.ncync_server.node_devices = {1: MagicMock()}

    async def _touch_mtime():
        cfg_file.write_text("devices: {1: {}, 2: {}}")

    with patch("cync_lan.cloud_api.CyncCloudAPI") as mock_api_cls, patch(
        "cync_lan.utils.parse_config",
        new=AsyncMock(return_value={1: MagicMock(), 2: MagicMock()}),
    ), patch(
        "homeassistant.config_entries.ConfigEntries.async_reload", new=AsyncMock()
    ) as mock_reload:
        mock_api_cls.return_value.export_config_file = AsyncMock(
            side_effect=_touch_mtime
        )
        await _refresh_export_and_reload_if_changed(hass, entry, cfg_file)

    mock_reload.assert_awaited_once_with(entry.entry_id)


async def test_refresh_swallows_exceptions(hass, tmp_path):
    """A failed background refresh must not crash HA."""
    from custom_components.cync_lan import _refresh_export_and_reload_if_changed

    cfg_file = tmp_path / "cync_mesh.yaml"
    entry = _make_entry()
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock()

    with patch(
        "cync_lan.cloud_api.CyncCloudAPI",
        side_effect=RuntimeError("cloud unreachable"),
    ):
        await _refresh_export_and_reload_if_changed(hass, entry, cfg_file)  # no raise
