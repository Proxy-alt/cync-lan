"""Tests for the Cync LAN config flow.

config-flow-test-coverage (bronze): exercises every step and outcome the
flow can reach - immediate success (cached token), OTP-required success,
invalid credentials, invalid OTP, and the empty-account/no-devices abort.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cync_lan.const import DOMAIN


async def _start_user_step(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _start_general_settings(hass, entry_id: str):
    """Options flow now opens on a menu (general settings vs. the motion
    sensor wizard) - select "general_settings" to reach the form every
    pre-existing options test exercises."""
    result = await hass.config_entries.options.async_init(entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "general_settings"}
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

    result = await _start_general_settings(hass, entry.entry_id)
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

    mock_cloud_api.check_token = AsyncMock(return_value=True)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "x"},
        options={"local_port": 23779, "export_refresh_interval": 24},
    )
    entry.add_to_hass(hass)

    result = await _start_general_settings(hass, entry.entry_id)
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


async def test_options_flow_no_valid_token_skips_export(hass, mock_cloud_api):
    """refresh_cloud_export() must not attempt export_config_file() (which
    assumes token_cache is already populated) when there's no valid cached
    token to populate it with - mock_cloud_api's check_token defaults to
    False, matching a real account that's never completed the interactive
    OTP flow in this process."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "x"},
        options={"local_port": 23779, "export_refresh_interval": 24},
    )
    entry.add_to_hass(hass)

    result = await _start_general_settings(hass, entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "local_port": 23779,
            "export_refresh_interval": 24,
            "enable_light_groups": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    mock_cloud_api.export_config_file.assert_not_awaited()


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

    result = await _start_general_settings(hass, entry.entry_id)
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

    mock_cloud_api.check_token = AsyncMock(return_value=True)
    mock_cloud_api.export_config_file = AsyncMock(side_effect=RuntimeError("boom"))
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "x"},
        options={"local_port": 23779, "export_refresh_interval": 24},
    )
    entry.add_to_hass(hass)

    result = await _start_general_settings(hass, entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "local_port": 23779,
            "export_refresh_interval": 24,
            "enable_light_groups": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_flow_applies_groups_without_reload(hass, mock_cloud_api):
    """Enabling light groups must apply immediately - reparsing the freshly
    exported groups and adding any new group entities directly to the
    already-running light platform - rather than requiring the user to
    reload or restart before they show up."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    mock_cloud_api.check_token = AsyncMock(return_value=True)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "x"},
        options={"local_port": 23779, "export_refresh_interval": 24},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(groups=None)

    fresh_groups = {1: {"name": "Kitchen", "device_ids": [1], "is_subgroup": False}}
    with patch(
        "cync_lan.utils.parse_groups", new=AsyncMock(return_value=fresh_groups)
    ), patch(
        "custom_components.cync_lan.light.async_add_light_groups",
        new=AsyncMock(),
    ) as mock_add_groups:
        result = await _start_general_settings(hass, entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "local_port": 23779,
                "export_refresh_interval": 24,
                "enable_light_groups": True,
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.runtime_data.groups == fresh_groups
    mock_add_groups.assert_awaited_once_with(hass, entry, hide_members=False)


async def test_options_flow_light_groups_noop_before_initial_setup(
    hass, mock_cloud_api
):
    """Opening options for an entry that hasn't finished its own initial
    setup yet (e.g. it failed setup) has no runtime_data to apply groups
    to - must not crash."""
    from unittest.mock import patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    mock_cloud_api.check_token = AsyncMock(return_value=True)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "x"},
        options={"local_port": 23779, "export_refresh_interval": 24},
    )
    entry.add_to_hass(hass)
    # No entry.runtime_data assignment - matches an entry that never
    # finished async_setup_entry.

    with patch(
        "custom_components.cync_lan.light.async_add_light_groups",
        new=AsyncMock(),
    ) as mock_add_groups:
        result = await _start_general_settings(hass, entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "local_port": 23779,
                "export_refresh_interval": 24,
                "enable_light_groups": True,
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    mock_add_groups.assert_not_awaited()


async def test_options_flow_applies_stale_groups_when_export_fails(hass, mock_cloud_api):
    """If the cloud refresh fails (e.g. no valid token), groups must not be
    reparsed from a possibly-stale file, but light groups should still be
    (re)applied from whatever group data is already cached on
    runtime_data - covers the case where a user just wants to turn the
    feature on using data that's already there."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    mock_cloud_api.check_token = AsyncMock(return_value=False)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "x"},
        options={"local_port": 23779, "export_refresh_interval": 24},
    )
    entry.add_to_hass(hass)
    cached_groups = {2: {"name": "Old", "device_ids": [2], "is_subgroup": False}}
    entry.runtime_data = SimpleNamespace(groups=cached_groups)

    with patch(
        "cync_lan.utils.parse_groups", new=AsyncMock()
    ) as mock_parse_groups, patch(
        "custom_components.cync_lan.light.async_add_light_groups",
        new=AsyncMock(),
    ) as mock_add_groups:
        result = await _start_general_settings(hass, entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "local_port": 23779,
                "export_refresh_interval": 24,
                "enable_light_groups": True,
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    mock_parse_groups.assert_not_awaited()
    assert entry.runtime_data.groups == cached_groups
    mock_add_groups.assert_awaited_once_with(hass, entry, hide_members=False)


async def test_options_flow_parse_groups_failure_does_not_block_save(
    hass, mock_cloud_api
):
    """A corrupt/unreadable freshly-exported file must not prevent the
    rest of the options from saving - falls back to whatever group data
    was already cached rather than crashing the flow."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    mock_cloud_api.check_token = AsyncMock(return_value=True)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "x"},
        options={"local_port": 23779, "export_refresh_interval": 24},
    )
    entry.add_to_hass(hass)
    cached_groups = {3: {"name": "Cached", "device_ids": [3], "is_subgroup": False}}
    entry.runtime_data = SimpleNamespace(groups=cached_groups)

    with patch(
        "cync_lan.utils.parse_groups",
        new=AsyncMock(side_effect=RuntimeError("bad yaml")),
    ), patch(
        "custom_components.cync_lan.light.async_add_light_groups",
        new=AsyncMock(),
    ) as mock_add_groups:
        result = await _start_general_settings(hass, entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "local_port": 23779,
                "export_refresh_interval": 24,
                "enable_light_groups": True,
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    # groups left untouched at the cached value since the reparse failed
    assert entry.runtime_data.groups == cached_groups
    mock_add_groups.assert_awaited_once_with(hass, entry, hide_members=False)


def _make_motion_sensor_node(dev_id: int, name: str, has_motion_sensor: bool = True):
    # MagicMock(name=...) is special-cased by unittest.mock (sets the
    # mock's repr, not a `.name` attribute) - must be assigned after
    # construction instead.
    node = MagicMock(id=dev_id, has_motion_sensor=has_motion_sensor)
    node.name = name
    node.metadata = MagicMock(supported=True)
    node.set_motion_sensor_settings = AsyncMock()
    return node


def _entry_with_nodes(hass, nodes: dict, online_dev_id: int | None = None):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.cync_lan.bridge import CyncLanBridge

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "x"},
        options={"local_port": 23779, "export_refresh_interval": 24},
    )
    entry.add_to_hass(hass)
    bridge = CyncLanBridge(hass, entry.entry_id)
    entry.runtime_data = SimpleNamespace(
        ncync_server=SimpleNamespace(node_devices=nodes), bridge=bridge
    )
    # BridgeEntityState.online defaults to True (avoids a flash of
    # "unavailable" before a device's first real status packet) - tests
    # need deterministic online/offline state, so explicitly set every
    # node rather than relying on that default.
    for dev_id in nodes:
        bridge._set_online(dev_id, dev_id == online_dev_id)
    return entry


async def _open_motion_sensor_menu(hass, entry_id: str):
    result = await hass.config_entries.options.async_init(entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "motion_sensor_select"}
    )


async def test_motion_sensor_wizard_no_devices_aborts(hass):
    entry = _entry_with_nodes(hass, {})
    result = await _open_motion_sensor_menu(hass, entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_motion_sensors"


async def test_motion_sensor_wizard_filters_non_motion_devices(hass):
    """A device without has_motion_sensor must not appear as pickable."""
    plain_light = MagicMock(id=1, has_motion_sensor=False)
    plain_light.name = "Kitchen Light"
    plain_light.metadata = MagicMock(supported=True)
    entry = _entry_with_nodes(hass, {1: plain_light})

    result = await _open_motion_sensor_menu(hass, entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_motion_sensors"


async def test_motion_sensor_wizard_offline_device_shows_wake_instructions(hass):
    node = _make_motion_sensor_node(5, "Hallway Sensor")
    entry = _entry_with_nodes(hass, {5: node})

    result = await _open_motion_sensor_menu(hass, entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "motion_sensor_select"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device": "5"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "motion_sensor_wake"
    assert result["description_placeholders"]["device_name"] == "Hallway Sensor"
    assert not result.get("errors")


async def test_motion_sensor_wizard_wake_retry_still_offline_shows_error(hass):
    node = _make_motion_sensor_node(5, "Hallway Sensor")
    entry = _entry_with_nodes(hass, {5: node})

    result = await _open_motion_sensor_menu(hass, entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device": "5"}
    )
    assert result["step_id"] == "motion_sensor_wake"

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "motion_sensor_wake"
    assert result["errors"] == {"base": "still_offline"}
    node.set_motion_sensor_settings.assert_not_awaited()


async def test_motion_sensor_wizard_online_device_skips_wake_screen(hass):
    node = _make_motion_sensor_node(5, "Hallway Sensor")
    entry = _entry_with_nodes(hass, {5: node}, online_dev_id=5)

    result = await _open_motion_sensor_menu(hass, entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device": "5"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "motion_sensor_settings"
    assert result["description_placeholders"]["device_name"] == "Hallway Sensor"


async def test_motion_sensor_wizard_wake_then_online_proceeds_to_settings(hass):
    """The most important regression this wizard exists for: a device that
    was offline when first selected, then woken by the user physically,
    must be re-checked and let through on the next submit - not stuck
    behind a stale offline snapshot."""
    node = _make_motion_sensor_node(5, "Hallway Sensor")
    entry = _entry_with_nodes(hass, {5: node})

    result = await _open_motion_sensor_menu(hass, entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device": "5"}
    )
    assert result["step_id"] == "motion_sensor_wake"

    await entry.runtime_data.bridge.pub_online(5, True)

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "motion_sensor_settings"


async def test_motion_sensor_wizard_submits_settings(hass):
    node = _make_motion_sensor_node(5, "Hallway Sensor")
    entry = _entry_with_nodes(hass, {5: node}, online_dev_id=5)

    result = await _open_motion_sensor_menu(hass, entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device": "5"}
    )
    assert result["step_id"] == "motion_sensor_settings"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "sensor_type": "ambient_light",
            "sensitivity": "low",
            "delay_seconds": 30,
            "deactivation_seconds": 60,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "motion_sensor_settings_saved"
    assert result["description_placeholders"]["device_name"] == "Hallway Sensor"
    node.set_motion_sensor_settings.assert_awaited_once_with(
        setting_type=2, enabled=None, sensitivity=2, delay_seconds=30,
        deactivation_seconds=60,
    )


async def test_motion_sensor_wizard_submits_settings_with_enabled_flag(hass):
    node = _make_motion_sensor_node(5, "Hallway Sensor")
    entry = _entry_with_nodes(hass, {5: node}, online_dev_id=5)

    result = await _open_motion_sensor_menu(hass, entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device": "5"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"sensor_type": "motion", "enabled": True},
    )
    assert result["type"] is FlowResultType.ABORT
    node.set_motion_sensor_settings.assert_awaited_once_with(
        setting_type=1, enabled=True, sensitivity=None, delay_seconds=0,
        deactivation_seconds=0,
    )


async def test_motion_sensor_wizard_aborts_before_initial_setup(hass):
    """Opening options for an entry that hasn't finished async_setup_entry
    yet (e.g. it failed setup) has no runtime_data to list devices from -
    must abort cleanly rather than raise."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"account_username": "user@example.com", "account_password": "x"},
        options={"local_port": 23779, "export_refresh_interval": 24},
    )
    entry.add_to_hass(hass)
    # No entry.runtime_data assignment.

    result = await _open_motion_sensor_menu(hass, entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_motion_sensors"


async def test_motion_sensor_wizard_device_removed_mid_flow_at_wake_step(hass):
    """A device that disappears (e.g. removed from the mesh, or a fresh
    export dropped it) between being picked and the next step must abort
    instead of KeyError-ing."""
    node = _make_motion_sensor_node(5, "Hallway Sensor")
    entry = _entry_with_nodes(hass, {5: node})

    result = await _open_motion_sensor_menu(hass, entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device": "5"}
    )
    assert result["step_id"] == "motion_sensor_wake"

    del entry.runtime_data.ncync_server.node_devices[5]

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_motion_sensors"


async def test_motion_sensor_wizard_device_removed_mid_flow_at_settings_step(hass):
    node = _make_motion_sensor_node(5, "Hallway Sensor")
    entry = _entry_with_nodes(hass, {5: node}, online_dev_id=5)

    result = await _open_motion_sensor_menu(hass, entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device": "5"}
    )
    assert result["step_id"] == "motion_sensor_settings"

    del entry.runtime_data.ncync_server.node_devices[5]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"sensor_type": "motion"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_motion_sensors"
    node.set_motion_sensor_settings.assert_not_awaited()
