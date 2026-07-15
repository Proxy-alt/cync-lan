"""Tests for the Cync LAN config flow.

config-flow-test-coverage (bronze): exercises every step and outcome the
flow can reach - immediate success (cached token), OTP-required success,
invalid credentials, invalid OTP, and the empty-account/no-devices abort.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cync_lan.const import DOMAIN


async def _start_user_step(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_immediate_success_cached_token(hass, mock_cloud_api, mock_parse_config):
    """check_token() True (a cached, still-valid session) skips OTP entirely."""
    mock_cloud_api.check_token = AsyncMock(return_value=True)

    result = await _start_user_step(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"account_username": "user@example.com", "account_password": "hunter2"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["device_count"] == "1"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "user@example.com"


async def test_otp_required_success(hass, mock_cloud_api, mock_parse_config):
    result = await _start_user_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"account_username": "user@example.com", "account_password": "hunter2"},
    )
    assert result["step_id"] == "otp"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"otp_code": "123456"}
    )
    assert result["step_id"] == "confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_invalid_auth(hass, mock_cloud_api):
    mock_cloud_api.request_otp = AsyncMock(return_value=False)

    result = await _start_user_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"account_username": "user@example.com", "account_password": "wrong"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_invalid_otp(hass, mock_cloud_api):
    mock_cloud_api.send_otp = AsyncMock(return_value=False)

    result = await _start_user_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"account_username": "user@example.com", "account_password": "hunter2"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"otp_code": "000000"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_otp"}


async def test_no_devices_found(hass, mock_cloud_api):
    with patch("cync_lan.utils.parse_config", new=AsyncMock(return_value={})):
        mock_cloud_api.check_token = AsyncMock(return_value=True)
        result = await _start_user_step(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"account_username": "user@example.com", "account_password": "hunter2"},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_devices"}


async def test_duplicate_account_aborts(hass, mock_cloud_api, mock_parse_config):
    """unique-config-entry: a second attempt to add the same account aborts."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "x"},
    ).add_to_hass(hass)

    mock_cloud_api.check_token = AsyncMock(return_value=True)
    result = await _start_user_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"account_username": "user@example.com", "account_password": "hunter2"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_flow_success(hass, mock_cloud_api, mock_parse_config):
    """reauthentication-flow (silver): triggered flow re-collects the
    password, re-authenticates, and completes via the normal OTP step."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "old"},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"account_password": "new-password"}
    )
    assert result["step_id"] == "otp"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"otp_code": "123456"}
    )
    assert result["step_id"] == "confirm"


async def test_reauth_flow_invalid_auth(hass, mock_cloud_api):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    mock_cloud_api.request_otp = AsyncMock(return_value=False)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "old"},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"account_password": "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reauth_flow_cannot_connect(hass, mock_cloud_api):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    mock_cloud_api.request_otp = AsyncMock(side_effect=RuntimeError("boom"))
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "old"},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"account_password": "new"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_step_cannot_connect(hass, mock_cloud_api):
    mock_cloud_api._check_session = AsyncMock(side_effect=RuntimeError("boom"))

    result = await _start_user_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"account_username": "user@example.com", "account_password": "hunter2"},
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_otp_step_cannot_connect(hass, mock_cloud_api):
    result = await _start_user_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"account_username": "user@example.com", "account_password": "hunter2"},
    )
    mock_cloud_api.send_otp = AsyncMock(side_effect=RuntimeError("boom"))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"otp_code": "not-a-number"}
    )
    assert result["errors"] == {"base": "invalid_otp"}


@pytest.mark.parametrize("port", [23779, 8080])
async def test_options_flow(hass, port):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "x"},
        options={"local_port": 23779, "export_refresh_interval": 24},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"local_port": port, "export_refresh_interval": 24},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_flow_enabling_light_groups_refreshes_export(
    hass, mock_cloud_api
):
    """Regression test: group membership only exists in a fresh cloud
    export, and nothing else re-pulls it on demand - a real user enabled
    light groups against a stale export (written before groups support
    existed) and got none, because async_setup_entry's reload just
    reparses whatever's already on disk. Saving the options form with
    light groups enabled must trigger export_config_file() itself.
    """
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "x"},
        options={"local_port": 23779, "export_refresh_interval": 24},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "local_port": 23779,
            "export_refresh_interval": 24,
            "enable_light_groups": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    mock_cloud_api.export_config_file.assert_awaited_once()


async def test_options_flow_disabled_light_groups_skips_export(hass, mock_cloud_api):
    """No point paying the cloud round-trip when the feature being saved
    is off - only enabling light groups needs fresh group data."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "x"},
        options={"local_port": 23779, "export_refresh_interval": 24},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "local_port": 23779,
            "export_refresh_interval": 24,
            "enable_light_groups": False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    mock_cloud_api.export_config_file.assert_not_awaited()


async def test_options_flow_export_failure_does_not_block_save(hass, mock_cloud_api):
    """A cloud hiccup while refreshing groups must not prevent the rest of
    the options (port, refresh interval) from being saved."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    mock_cloud_api.export_config_file = AsyncMock(side_effect=RuntimeError("boom"))
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "x"},
        options={"local_port": 23779, "export_refresh_interval": 24},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "local_port": 23779,
            "export_refresh_interval": 24,
            "enable_light_groups": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
