"""Tests for util.py, including inject-websession (platinum) verification."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.cync_lan.util import (
    _count_wifi_devices,
    build_device_group_map,
    configure_environment,
    get_cloud_api,
    group_sensor_schedules_for_device,
)


async def test_configure_environment_sets_expected_env_vars(hass, tmp_path):
    with patch.object(hass.config, "path", return_value=str(tmp_path / "cync_lan")):
        await configure_environment(hass, "user@example.com", "hunter2")
    assert os.environ["CYNC_ACCOUNT_USERNAME"] == "user@example.com"
    assert os.environ["CYNC_ACCOUNT_PASSWORD"] == "hunter2"
    assert os.path.isdir(os.environ["CYNC_CONFIG_DIR"])


def test_count_wifi_devices_missing_file_returns_zero(tmp_path):
    assert _count_wifi_devices(tmp_path / "does_not_exist.yaml") == 0


def test_count_wifi_devices_counts_only_devices_with_wifi_mac(tmp_path):
    cfg_file = tmp_path / "cync_mesh.yaml"
    cfg_file.write_text(
        """
exported_homes:
  Home:
    devices:
      1:
        name: Kitchen Light
        wifi_mac: "AA:BB:CC:DD:EE:01"
      2:
        name: Kitchen Switch
        wifi_mac: "AA:BB:CC:DD:EE:02"
      3:
        name: Bluetooth-only Bulb
"""
    )
    assert _count_wifi_devices(cfg_file) == 2


def test_count_wifi_devices_supports_account_data_key(tmp_path):
    """The upstream export format used "account data" as the top-level key
    before exported_homes was introduced - still supported for older
    exports, mirrors cync_lan.utils.parse_config's own main_key fallback.
    """
    cfg_file = tmp_path / "cync_mesh.yaml"
    cfg_file.write_text(
        """
account data:
  Home:
    devices:
      1:
        name: Plug
        wifi_mac: "AA:BB:CC:DD:EE:01"
"""
    )
    assert _count_wifi_devices(cfg_file) == 1


def test_count_wifi_devices_malformed_yaml_returns_zero(tmp_path):
    """Sizing a tuning value must never block setup - a corrupt/partial
    export file falls back to 0 (which configure_environment then clamps
    up to the original default of 8) rather than raising.
    """
    cfg_file = tmp_path / "cync_mesh.yaml"
    cfg_file.write_text(":\n  - this is not: [valid yaml")
    assert _count_wifi_devices(cfg_file) == 0


async def test_configure_environment_sizes_max_tcp_conn_from_export(
    hass, tmp_path, monkeypatch
):
    """CYNC_MAX_TCP_CONN must reflect the real exported device count (plus
    headroom) rather than staying at the package's hardcoded default of 8 -
    confirmed via a real account with 48 wifi-connected devices hitting the
    old cap ("server max (33/8) TCP connections reached") thousands of
    times in production logs.
    """
    monkeypatch.delenv("CYNC_MAX_TCP_CONN", raising=False)
    config_dir = tmp_path / "cync_lan"
    config_dir.mkdir()
    (config_dir / "cync_mesh.yaml").write_text(
        "exported_homes:\n"
        "  Home:\n"
        "    devices:\n"
        + "".join(
            f'      {i}:\n        name: Device {i}\n        wifi_mac: "AA:{i:02X}"\n'
            for i in range(1, 11)
        )
    )
    with patch.object(hass.config, "path", return_value=str(config_dir)):
        await configure_environment(hass, "user@example.com", "hunter2")
    # 10 wifi devices + 4 headroom = 14, above the default-8 floor.
    assert os.environ["CYNC_MAX_TCP_CONN"] == "14"


async def test_configure_environment_max_tcp_conn_floors_at_default(
    hass, tmp_path, monkeypatch
):
    """A brand-new setup (no export yet) or a tiny account must not shrink
    the cap below the original default of 8.
    """
    monkeypatch.delenv("CYNC_MAX_TCP_CONN", raising=False)
    config_dir = str(tmp_path / "cync_lan")
    with patch.object(hass.config, "path", return_value=config_dir):
        await configure_environment(hass, "user@example.com", "hunter2")
    assert os.environ["CYNC_MAX_TCP_CONN"] == "8"


async def test_configure_environment_points_base_dir_at_ha_config(
    hass, tmp_path, monkeypatch
):
    """CYNC_BASE_DIR must not be left at its "/root/cync-lan" default - that
    path only exists in the standalone add-on's own Docker image, never in a
    HA container. A real install crashed server.start() with
    FileNotFoundError on load_cert_chain because the cert/key paths (which
    derive from CYNC_BASE_DIR) pointed nowhere writable.

    configure_environment uses setdefault, so a prior test's value would
    otherwise leak here (env vars are process-global, not test-isolated) -
    explicitly cleared first.
    """
    monkeypatch.delenv("CYNC_BASE_DIR", raising=False)
    config_dir = str(tmp_path / "cync_lan")
    with patch.object(hass.config, "path", return_value=config_dir):
        await configure_environment(hass, "user@example.com", "hunter2")
    assert os.environ["CYNC_BASE_DIR"] == config_dir


async def test_get_cloud_api_injects_has_shared_session(hass):
    """inject-websession (platinum): the session actually passed to
    CyncCloudAPI is Home Assistant's own shared aiohttp session, not one
    the API client creates for itself."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession


    expected_session = async_get_clientsession(hass)
    api = get_cloud_api(hass)

    assert api.http_session is expected_session
    assert api._session_injected is True

    # _check_session must be a no-op for an injected session - it should
    # not be replaced with a self-created one.
    await api._check_session()
    assert api.http_session is expected_session


async def test_cloud_api_close_does_not_close_injected_session(hass):
    """close() must never close a session this instance doesn't own -
    Home Assistant (or another integration) may still be using it."""
    from cync_lan.cloud_api import CyncCloudAPI

    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.close = AsyncMock()
    api = CyncCloudAPI(session=mock_session)

    await api.close()

    mock_session.close.assert_not_called()


async def test_cloud_api_close_does_close_self_created_session():
    """Backward-compat check: when nothing was injected, close() keeps
    closing its own self-created session as before."""
    from cync_lan.cloud_api import CyncCloudAPI

    api = CyncCloudAPI()
    api._session_injected = False  # reset in case a prior test's singleton state leaked
    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.close = AsyncMock()
    api.http_session = mock_session

    await api.close()

    mock_session.close.assert_awaited_once()
    assert api.http_session is None


def test_build_device_group_map_single_and_multi_group_membership():
    groups = {
        1: {"name": "Kitchen", "device_ids": [10, 11]},
        2: {"name": "Kitchen Subgroup", "device_ids": [10]},
        3: {"name": "Garage", "device_ids": [20]},
    }
    device_map = build_device_group_map(groups)
    assert sorted(device_map[10]) == [1, 2]
    assert device_map[11] == [1]
    assert device_map[20] == [3]
    assert 99 not in device_map


def test_build_device_group_map_empty_groups():
    assert build_device_group_map({}) == {}
    assert build_device_group_map(None) == {}


def test_group_sensor_schedules_for_device_filters_empty_schedules():
    groups = {
        1: {
            "name": "Utility Room",
            "device_ids": [5],
            "sensor_schedules": {"daytime": {"slot_id": 1}},
        },
        2: {"name": "Empty Group", "device_ids": [5], "sensor_schedules": {}},
    }
    device_map = build_device_group_map(groups)
    result = group_sensor_schedules_for_device(groups, device_map, 5)
    assert result == [
        {
            "group_id": 1,
            "group_name": "Utility Room",
            "sensor_schedules": {"daytime": {"slot_id": 1}},
        }
    ]


def test_group_sensor_schedules_for_device_no_membership():
    groups = {1: {"name": "Utility Room", "device_ids": [5], "sensor_schedules": {}}}
    device_map = build_device_group_map(groups)
    assert group_sensor_schedules_for_device(groups, device_map, 999) == []


async def test_capture_unknown_packets_flag_reaches_the_environment(hass, monkeypatch):
    """The library reads CYNC_UNSUPPORTED_RAW_DEBUG at import time, so the
    option has to land in the environment before anything imports it."""
    from custom_components.cync_lan.util import configure_environment

    monkeypatch.delenv("CYNC_UNSUPPORTED_RAW_DEBUG", raising=False)
    await configure_environment(hass, "u@e.com", "pw", capture_unknown_packets=True)
    assert os.environ["CYNC_UNSUPPORTED_RAW_DEBUG"] == "1"

    await configure_environment(hass, "u@e.com", "pw", capture_unknown_packets=False)
    assert os.environ["CYNC_UNSUPPORTED_RAW_DEBUG"] == "0"


async def test_capture_unknown_packets_defaults_off(hass, monkeypatch):
    from custom_components.cync_lan.util import configure_environment

    monkeypatch.delenv("CYNC_UNSUPPORTED_RAW_DEBUG", raising=False)
    await configure_environment(hass, "u@e.com", "pw")
    assert os.environ["CYNC_UNSUPPORTED_RAW_DEBUG"] == "0"


async def test_hub_envelope_flag_reaches_the_environment(hass, monkeypatch):
    """The A/B toggle has to land in CYNC_HUB_ENVELOPE for cync_lan.devices
    to see it. Unlike the other flags here that value is re-read on every
    hub command, so setting it is all that is needed - no restart."""
    from custom_components.cync_lan.util import configure_environment

    monkeypatch.delenv("CYNC_HUB_ENVELOPE", raising=False)
    await configure_environment(hass, "u@e.com", "pw", hub_envelope_bare=True)
    assert os.environ["CYNC_HUB_ENVELOPE"] == "bare"

    await configure_environment(hass, "u@e.com", "pw", hub_envelope_bare=False)
    assert os.environ["CYNC_HUB_ENVELOPE"] == "routed"


async def test_hub_envelope_defaults_to_the_shipped_shape(hass, monkeypatch):
    """Default must stay the envelope that has shipped since 0.3.0 - the
    alternate one is an experiment, not a correction."""
    from custom_components.cync_lan.util import configure_environment

    monkeypatch.delenv("CYNC_HUB_ENVELOPE", raising=False)
    await configure_environment(hass, "u@e.com", "pw")
    assert os.environ["CYNC_HUB_ENVELOPE"] == "routed"


def test_hub_envelope_support_detection_matches_installed_library():
    """The guard must reflect reality, not a hardcoded answer - it is what
    stops a silent no-op being recorded as a failed experiment."""
    from custom_components.cync_lan.util import hub_envelope_supported

    from cync_lan import devices as core_devices

    assert hub_envelope_supported() is hasattr(core_devices, "_hub_envelope_mode")
