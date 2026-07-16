"""Config flow for Cync LAN.

Known limitation (documented, not hidden): the upstream `cync_lan` package
this integration depends on reads account credentials, secret key, and the
exported-device-config path from process-wide environment variables /
module-level constants (cync_lan.const), not per-call arguments - it was
built for a one-account-per-container add-on. This flow sets those
environment variables before touching the cloud API, which means Home
Assistant can only run a single Cync LAN account/config entry at a time
(unique-config-entry is enforced below for the same reason). Making the
upstream package multi-instance-safe is out of scope for this integration
and would be a breaking change to cync_lan itself.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_ACCOUNT_PASSWORD,
    CONF_ACCOUNT_USERNAME,
    CONF_ENABLE_LIGHT_GROUPS,
    CONF_EXPORT_REFRESH_INTERVAL,
    CONF_LOCAL_PORT,
    DEFAULT_ENABLE_LIGHT_GROUPS,
    DEFAULT_EXPORT_REFRESH_INTERVAL_HOURS,
    DEFAULT_LOCAL_PORT,
    DOMAIN,
)
from .util import configure_environment, get_cloud_api, refresh_cloud_export

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCOUNT_USERNAME): str,
        vol.Required(CONF_ACCOUNT_PASSWORD): str,
    }
)
STEP_OTP_SCHEMA = vol.Schema({vol.Required("otp_code"): str})


class InvalidAuth(HomeAssistantError):
    """Username/password rejected."""


class InvalidOtp(HomeAssistantError):
    """OTP code rejected."""


class CyncLanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Cync LAN."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._password: str | None = None
        self._device_count: int = 0

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_ACCOUNT_USERNAME]
            self._password = user_input[CONF_ACCOUNT_PASSWORD]

            # unique-config-entry: one HA instance, one Cync account (see
            # module docstring for why this integration can't support more).
            await self.async_set_unique_id(self._username.casefold())
            self._abort_if_unique_id_configured()

            await configure_environment(self.hass, self._username, self._password)
            try:
                api = get_cloud_api(self.hass)
                await api._check_session()
                have_token = await api.check_token()
                if have_token:
                    return await self._finish_export()
                requested = await api.request_otp()
                if not requested:
                    raise InvalidAuth
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001 - surfaced to the user as a form error
                _LOGGER.exception("Unexpected error talking to the Cync cloud API")
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_otp()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                api = get_cloud_api(self.hass)
                ok = await api.send_otp(int(user_input["otp_code"]))
                if not ok:
                    raise InvalidOtp
            except (InvalidOtp, ValueError):
                errors["base"] = "invalid_otp"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error submitting OTP to the Cync cloud API")
                errors["base"] = "cannot_connect"
            else:
                return await self._finish_export()

        return self.async_show_form(
            step_id="otp", data_schema=STEP_OTP_SCHEMA, errors=errors
        )

    async def _finish_export(self) -> config_entries.ConfigFlowResult:
        """test-before-configure: actually pull the device list before
        letting the user finish setup, so a bad account/empty home fails
        here instead of silently producing zero entities after setup."""
        from pathlib import Path

        from cync_lan.const import CYNC_CONFIG_FILE_PATH
        from cync_lan.utils import parse_config

        api = get_cloud_api(self.hass)
        exported = await api.export_config_file()
        if not exported:
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_SCHEMA,
                errors={"base": "no_devices"},
            )
        node_map = await parse_config(Path(CYNC_CONFIG_FILE_PATH))
        self._device_count = len(node_map)
        if self._device_count == 0:
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_SCHEMA,
                errors={"base": "no_devices"},
            )
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=self._username,
                data={
                    CONF_ACCOUNT_USERNAME: self._username,
                    CONF_ACCOUNT_PASSWORD: self._password,
                },
                options={
                    CONF_LOCAL_PORT: DEFAULT_LOCAL_PORT,
                    CONF_EXPORT_REFRESH_INTERVAL: DEFAULT_EXPORT_REFRESH_INTERVAL_HOURS,
                },
            )
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"device_count": str(self._device_count)},
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Silver: reauthentication-flow - triggered when the cached cloud
        token can't be refreshed (expired refresh token, password changed)."""
        self._username = entry_data[CONF_ACCOUNT_USERNAME]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._password = user_input[CONF_ACCOUNT_PASSWORD]
            await configure_environment(self.hass, self._username, self._password)
            try:
                api = get_cloud_api(self.hass)
                requested = await api.request_otp()
                if not requested:
                    raise InvalidAuth
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_otp()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_ACCOUNT_PASSWORD): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "CyncLanOptionsFlow":
        return CyncLanOptionsFlow(config_entry)


class CyncLanOptionsFlow(config_entries.OptionsFlow):
    """Gold: reconfiguration-flow - lets the user change the local port and
    export refresh interval without deleting and re-adding the integration."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def _refresh_and_apply_light_groups(self) -> None:
        """Best-effort: refresh the cloud export, reparse groups from it,
        and add any newly-available light-group entities to the
        already-running light platform - all without forcing a full entry
        reload, which would drop every device's TCP connection just to
        add a handful of group entities. Confirmed via a real user
        enabling light groups against a stale export and getting none
        until a full restart; this makes it apply immediately instead.

        A failure at any step here logs and falls back to whatever group
        data/entities already exist, rather than blocking the rest of the
        options from saving.
        """
        entry = self._config_entry
        try:
            exported = await refresh_cloud_export(self.hass)
        except Exception:  # noqa: BLE001 - best-effort refresh, not fatal
            _LOGGER.exception(
                "Failed to refresh Cync cloud export while saving light-group "
                "settings; continuing with the existing local config"
            )
            exported = False

        # None until the entry finishes its own initial async_setup_entry -
        # e.g. options can be opened for an entry that failed setup. Nothing
        # running yet to add group entities to; the next setup will parse
        # whatever the export above just wrote.
        runtime_data = getattr(entry, "runtime_data", None)
        if runtime_data is None:
            return

        if exported:
            from pathlib import Path

            from cync_lan.const import CYNC_CONFIG_FILE_PATH
            from cync_lan.utils import parse_groups

            try:
                runtime_data.groups = await parse_groups(Path(CYNC_CONFIG_FILE_PATH))
            except Exception:  # noqa: BLE001 - groups are optional, must not block setup
                _LOGGER.exception("Failed to parse refreshed Cync device groups")

        from .light import async_add_light_groups

        await async_add_light_groups(self.hass, entry)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            if user_input.get(CONF_ENABLE_LIGHT_GROUPS):
                await self._refresh_and_apply_light_groups()
            return self.async_create_entry(data=user_input)

        current = self._config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LOCAL_PORT,
                        default=current.get(CONF_LOCAL_PORT, DEFAULT_LOCAL_PORT),
                    ): int,
                    vol.Required(
                        CONF_EXPORT_REFRESH_INTERVAL,
                        default=current.get(
                            CONF_EXPORT_REFRESH_INTERVAL,
                            DEFAULT_EXPORT_REFRESH_INTERVAL_HOURS,
                        ),
                    ): int,
                    vol.Required(
                        CONF_ENABLE_LIGHT_GROUPS,
                        default=current.get(
                            CONF_ENABLE_LIGHT_GROUPS, DEFAULT_ENABLE_LIGHT_GROUPS
                        ),
                    ): bool,
                }
            ),
        )
