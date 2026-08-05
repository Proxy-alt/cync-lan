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

import os

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.helpers.service_info.bluetooth import BluetoothServiceInfo
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .const import (
    CONF_ACCOUNT_PASSWORD,
    CONF_ACCOUNT_USERNAME,
    CONF_CAPTURE_FIRMWARE,
    CONF_CAPTURE_UNKNOWN_PACKETS,
    CONF_HUB_ENVELOPE_BARE,
    CONF_ENABLE_EXPERIMENTAL,
    CONF_ENABLE_LIGHT_GROUPS,
    CONF_EXPORT_REFRESH_INTERVAL,
    CONF_HIDE_GROUP_MEMBERS,
    CONF_INDICATOR_LED_AS_LIGHT,
    CONF_LOCAL_PORT,
    DEFAULT_CAPTURE_FIRMWARE,
    DEFAULT_CAPTURE_UNKNOWN_PACKETS,
    DEFAULT_HUB_ENVELOPE_BARE,
    DEFAULT_ENABLE_EXPERIMENTAL,
    DEFAULT_ENABLE_LIGHT_GROUPS,
    DEFAULT_EXPORT_REFRESH_INTERVAL_HOURS,
    DEFAULT_HIDE_GROUP_MEMBERS,
    DEFAULT_INDICATOR_LED_AS_LIGHT,
    DEFAULT_LOCAL_PORT,
    DOMAIN,
    FADE_OPTIONS,
    MOTION_SENSOR_SENSITIVITY,
    MOTION_SENSOR_TYPE,
    REACH_FLAG_OPTIONS,
    SCHEDULE_MODE_OPTIONS,
    SCHEDULE_SLOT_OPTIONS,
)
from .services import async_setup_services, push_automation_to_hardware
from .util import (
    configure_environment,
    get_cloud_api,
    hub_envelope_supported,
    refresh_cloud_export,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCOUNT_USERNAME): str,
        vol.Required(CONF_ACCOUNT_PASSWORD): str,
    }
)
STEP_OTP_SCHEMA = vol.Schema({vol.Required("otp_code"): str})
# Reauth re-collects only the password - the account it belongs to is fixed
# by the entry being reauthenticated and must not be changeable here.
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_ACCOUNT_PASSWORD): str})


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

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> config_entries.ConfigFlowResult:
        """discovery (gold): a device with a Cync-pattern DHCP hostname
        (see manifest.json's "dhcp" matcher) was seen on the network -
        nudge the user into the normal cloud-account setup flow instead of
        requiring them to find this integration manually. Doesn't skip
        account credentials (see module docstring: setup is
        account-based, not per-device), just triggers the same flow
        proactively.

        A fixed sentinel unique_id (not the eventual account username,
        which isn't known yet) makes Home Assistant collapse multiple
        DHCP matches - e.g. every Cync device on the network sending a
        matching hostname - into a single discovery flow instead of one
        per device.
        """
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return await self.async_step_user()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfo
    ) -> config_entries.ConfigFlowResult:
        """discovery (gold): manifest.json's "bluetooth" matcher only ever
        fires for a factory-default, never-provisioned Telink device (its
        advertised local name reverts to something device-specific the
        instant it's actually provisioned into a mesh - see
        cync_lan.ble_provision's FACTORY_ADVERTISED_NAME/scan_for_unprovisioned_devices).
        That's a fundamentally different situation than DHCP discovery: a
        factory-fresh device isn't part of any Cync account yet, so
        nudging straight into the account-setup flow (like async_step_dhcp
        does) would be premature - the user still needs to add it to
        their account via the Cync app, or provision it directly onto
        their WiFi via the cync-lan-ble-provision CLI tool, before this
        integration has anything to do with it. Surfaced as an
        informational nudge toward that tool instead via
        async_step_bluetooth_confirm, not as account setup.
        """
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_abort(reason="unprovisioned_device_found")
        return self.async_show_form(step_id="bluetooth_confirm")

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

    def _export_failed(self) -> config_entries.ConfigFlowResult:
        """Re-show whichever credential step this flow actually started
        from. A reauth flow has no "user" step in its own history - showing
        one there would ask for a username the reauth flow already knows and
        deliberately doesn't let the user change."""
        if self.source == config_entries.SOURCE_REAUTH:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=STEP_REAUTH_SCHEMA,
                errors={"base": "no_devices"},
            )
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors={"base": "no_devices"},
        )

    async def _finish_export(self) -> config_entries.ConfigFlowResult:
        """test-before-configure: actually pull the device list before
        letting the user finish setup, so a bad account/empty home fails
        here instead of silently producing zero entities after setup.

        Terminates differently depending on how the flow started: a fresh
        setup goes on to the confirm step and creates an entry, while a
        reauth updates the EXISTING entry's stored password and aborts.
        Home Assistant hard-raises on async_create_entry from a reauth flow
        ("Creates a new entry in a 'reauth' flow, when it is expected to
        update an existing entry and abort"), so this split isn't optional -
        without it reauth crashes at its final step and the user can never
        recover from an expired token.
        """
        # Deferred, not module-level: cync_lan.const reads its env-var-backed
        # constants at import time, so configure_environment() must have run
        # first (see util.configure_environment's docstring).
        from cync_lan.const import CYNC_CONFIG_FILE_PATH
        from cync_lan.utils import parse_config

        api = get_cloud_api(self.hass)
        exported = await api.export_config_file()
        if not exported:
            return self._export_failed()
        node_map = await parse_config(Path(CYNC_CONFIG_FILE_PATH))
        self._device_count = len(node_map)
        if self._device_count == 0:
            return self._export_failed()

        if self.source == config_entries.SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data_updates={CONF_ACCOUNT_PASSWORD: self._password},
            )
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            assert self._username is not None
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
        self, entry_data: Mapping[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Silver: reauthentication-flow - triggered when the cached cloud
        token can't be refreshed (expired refresh token, password changed).
        Ends by updating the existing entry in place (see _finish_export),
        never by creating a second one."""
        self._username = entry_data[CONF_ACCOUNT_USERNAME]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._password = user_input[CONF_ACCOUNT_PASSWORD]
            assert self._username is not None
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
            data_schema=STEP_REAUTH_SCHEMA,
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
    export refresh interval without deleting and re-adding the integration,
    and also hosts the "Edit Motion Sensor Settings" guided wizard (device
    picker -> physical-wake gate -> settings form) reachable from the same
    "Configure" entry point, mirroring the real Cync app's own guided flow
    for the same operation (see docs/mesh_opcodes.md's "Operational
    prerequisite" section for the research this wizard is built on)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._motion_sensor_dev_id: int | None = None

    async def _refresh_and_apply_light_groups(self, hide_members: bool) -> None:
        """Best-effort: refresh the cloud export, reparse groups from it,
        and add any newly-available light-group entities - plus apply the
        current hide_members setting to every known group's members - to
        the already-running light platform. All without forcing a full
        entry reload, which would drop every device's TCP connection just
        to add or hide a handful of entities. Confirmed via a real user
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

        await async_add_light_groups(self.hass, entry, hide_members=hide_members)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        menu = ["general_settings", "motion_sensor_select"]
        if self._config_entry.options.get(
            CONF_ENABLE_EXPERIMENTAL, DEFAULT_ENABLE_EXPERIMENTAL
        ):
            # The remaining experimental commands take several parameters and
            # have no persistent state to model, so an entity would be the
            # wrong shape for them - a guided form is what Home Assistant
            # offers for a parameterised one-shot. Hidden entirely until the
            # user opts in, same as the services and buttons.
            menu += [
                "experimental_menu",
            ]
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_experimental_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="experimental_menu",
            menu_options=[
                "exp_push_automation",
                "exp_scene_membership",
                "exp_group_membership",
                "exp_motion_schedule",
                "exp_multicolor_segments",
            ],
        )

    async def async_step_general_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            if user_input.get(CONF_ENABLE_LIGHT_GROUPS):
                await self._refresh_and_apply_light_groups(
                    hide_members=user_input.get(
                        CONF_HIDE_GROUP_MEMBERS, DEFAULT_HIDE_GROUP_MEMBERS
                    )
                )
            experimental_changed = user_input.get(
                CONF_ENABLE_EXPERIMENTAL, DEFAULT_ENABLE_EXPERIMENTAL
            ) != self._config_entry.options.get(
                CONF_ENABLE_EXPERIMENTAL, DEFAULT_ENABLE_EXPERIMENTAL
            )
            result = self.async_create_entry(data=user_input)
            # Apply the hub-envelope choice straight away. cync_lan.devices
            # re-reads this variable on every hub command instead of caching
            # it at import, so unlike the other advanced options here this
            # one needs neither a reload nor a restart - which is the point,
            # since running both arms of the A/B has to be cheap or nobody
            # finishes it. See docs/hub_envelope_ab_test.md.
            os.environ["CYNC_HUB_ENVELOPE"] = (
                "bare"
                if user_input.get(
                    CONF_HUB_ENVELOPE_BARE,
                    self._config_entry.options.get(
                        CONF_HUB_ENVELOPE_BARE, DEFAULT_HUB_ENVELOPE_BARE
                    ),
                )
                else "routed"
            )
            if (
                os.environ["CYNC_HUB_ENVELOPE"] == "bare"
                and not hub_envelope_supported()
            ):
                # Do not let a silent no-op be mistaken for a negative
                # result - see hub_envelope_supported()'s docstring.
                _LOGGER.warning(
                    "The alternate hub envelope was enabled, but the installed "
                    "cync-lan library does not support it and will keep sending "
                    "the original envelope. Upgrade cync-lan before recording "
                    "any result from this experiment"
                )
            # Register or remove the experimental_* services to match the
            # toggle immediately. async_create_entry has already written
            # the new options by this point, so experimental_enabled()
            # reads the just-saved value rather than the stale one.
            async_setup_services(self.hass)
            if experimental_changed:
                # The button platform only builds its entities when the
                # option is on, and entities - unlike services - cannot be
                # added or dropped without re-running platform setup. Only
                # reload when the flag actually flipped: a reload drops
                # every device's TCP connection, which is far too heavy a
                # price for someone who just changed the refresh interval.
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(
                        self._config_entry.entry_id
                    )
                )
            return result

        current = self._config_entry.options
        # Only meaningful once experimental commands are on - it changes the
        # wire shape of the hub family, all of which is experimental. Shown
        # after the user opts in rather than alongside the opt-in, same as
        # the experimental wizard menu.
        experimental_only: dict[Any, Any] = {}
        if current.get(CONF_ENABLE_EXPERIMENTAL, DEFAULT_ENABLE_EXPERIMENTAL):
            experimental_only[
                vol.Required(
                    CONF_HUB_ENVELOPE_BARE,
                    default=current.get(
                        CONF_HUB_ENVELOPE_BARE, DEFAULT_HUB_ENVELOPE_BARE
                    ),
                )
            ] = bool
        return self.async_show_form(
            step_id="general_settings",
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
                    vol.Required(
                        CONF_HIDE_GROUP_MEMBERS,
                        default=current.get(
                            CONF_HIDE_GROUP_MEMBERS, DEFAULT_HIDE_GROUP_MEMBERS
                        ),
                    ): bool,
                    vol.Required(
                        CONF_CAPTURE_UNKNOWN_PACKETS,
                        default=current.get(
                            CONF_CAPTURE_UNKNOWN_PACKETS,
                            DEFAULT_CAPTURE_UNKNOWN_PACKETS,
                        ),
                    ): bool,
                    vol.Required(
                        CONF_INDICATOR_LED_AS_LIGHT,
                        default=current.get(
                            CONF_INDICATOR_LED_AS_LIGHT,
                            DEFAULT_INDICATOR_LED_AS_LIGHT,
                        ),
                    ): bool,
                    vol.Required(
                        CONF_CAPTURE_FIRMWARE,
                        default=current.get(
                            CONF_CAPTURE_FIRMWARE, DEFAULT_CAPTURE_FIRMWARE
                        ),
                    ): bool,
                    vol.Required(
                        CONF_ENABLE_EXPERIMENTAL,
                        default=current.get(
                            CONF_ENABLE_EXPERIMENTAL, DEFAULT_ENABLE_EXPERIMENTAL
                        ),
                    ): bool,
                    **experimental_only,
                }
            ),
        )

    # ------------------------------------------------------------------
    # Experimental wizards
    #
    # Each of these replaces a service whose arguments a user cannot
    # reasonably supply from the action picker: they need a numeric
    # scene_id/group_id the Cync app never shows, or a device picker, or
    # both. Every one is a parameterised one-shot with no state to read
    # back, so a form is the right shape rather than an entity.
    # ------------------------------------------------------------------

    def _supported_nodes(self) -> dict[int, Any]:
        runtime_data = getattr(self._config_entry, "runtime_data", None)
        if runtime_data is None:
            return {}
        return {
            node.id: node
            for node in runtime_data.ncync_server.node_devices.values()
            if node.metadata is not None and node.metadata.supported
        }

    def _device_selector(self, nodes: dict[int, Any]) -> vol.In:
        return vol.In(
            {
                str(dev_id): f"{node.name} (id {dev_id})"
                for dev_id, node in sorted(nodes.items(), key=lambda kv: kv[1].name or "")
            }
        )

    def _scene_choices(self) -> dict[str, str]:
        runtime_data = getattr(self._config_entry, "runtime_data", None)
        scenes = getattr(runtime_data, "scenes", None) or {}
        return {str(sid): f"{s['name']} (id {sid})" for sid, s in scenes.items()}

    def _group_choices(self) -> dict[str, str]:
        runtime_data = getattr(self._config_entry, "runtime_data", None)
        groups = getattr(runtime_data, "groups", None) or {}
        return {
            str(gid): f"{g.get('name') or f'Group {gid}'} (id {gid})"
            for gid, g in groups.items()
        }

    async def _run(
        self, coro: Any, reason: str, **placeholders: str
    ) -> config_entries.ConfigFlowResult:
        """Send an experimental command and end the flow with a result.

        HomeAssistantError is surfaced as an abort reason rather than
        propagating: these commands routinely time out waiting on a
        notification channel that may not exist on the user's hardware,
        and a traceback in the UI is not a useful answer to that.
        """
        try:
            await coro
        except HomeAssistantError as err:
            return self.async_abort(
                reason="experimental_failed",
                description_placeholders={"error": str(err)},
            )
        return self.async_abort(reason=reason, description_placeholders=placeholders)

    async def async_step_exp_push_automation(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Push an existing HA automation to the hub as a native Schedule."""
        if user_input is not None:
            entity_id = user_input["automation_entity_id"]
            return await self._run(
                push_automation_to_hardware(self.hass, entity_id),
                "experimental_pushed",
                automation=entity_id,
            )

        return self.async_show_form(
            step_id="exp_push_automation",
            data_schema=vol.Schema(
                {
                    vol.Required("automation_entity_id"): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="automation")
                    ),
                }
            ),
        )

    async def async_step_exp_scene_membership(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Add or remove one device's captured colour within a scene."""
        nodes = self._supported_nodes()
        scenes = self._scene_choices()
        if not nodes or not scenes:
            return self.async_abort(reason="no_scenes_or_devices")

        if user_input is not None:
            node = nodes[int(user_input["device"])]
            scene_id = int(user_input["scene"])
            if user_input["action"] == "remove":
                return await self._run(
                    node.remove_from_scene(scene_id),
                    "experimental_scene_updated",
                    device_name=node.name,
                )
            return await self._run(
                node.add_to_scene(
                    scene_id,
                    cct=user_input.get("cct"),
                    rgb=None,
                    fade=FADE_OPTIONS[user_input.get("fade", "no_fade")],
                ),
                "experimental_scene_updated",
                device_name=node.name,
            )

        return self.async_show_form(
            step_id="exp_scene_membership",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): self._device_selector(nodes),
                    vol.Required("scene"): vol.In(scenes),
                    vol.Required("action", default="add"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["add", "remove"],
                            translation_key="scene_membership_action",
                        )
                    ),
                    vol.Optional("cct"): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=100)
                    ),
                    vol.Optional("fade", default="no_fade"): vol.In(
                        list(FADE_OPTIONS)
                    ),
                }
            ),
        )

    async def async_step_exp_group_membership(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Subscribe or unsubscribe a device to a group's mesh address."""
        nodes = self._supported_nodes()
        groups = self._group_choices()
        if not nodes or not groups:
            return self.async_abort(reason="no_groups_or_devices")

        if user_input is not None:
            node = nodes[int(user_input["device"])]
            return await self._run(
                node.set_group_membership(
                    int(user_input["group"]),
                    member=user_input["member"],
                    reach_flag=REACH_FLAG_OPTIONS[user_input["reach_flag"]],
                ),
                "experimental_group_updated",
                device_name=node.name,
            )

        return self.async_show_form(
            step_id="exp_group_membership",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): self._device_selector(nodes),
                    vol.Required("group"): vol.In(groups),
                    vol.Required("member", default=True): bool,
                    vol.Required("reach_flag", default="normal"): vol.In(
                        list(REACH_FLAG_OPTIONS)
                    ),
                }
            ),
        )

    async def async_step_exp_motion_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Write one slot of a device's native motion-sensor schedule."""
        nodes = {
            dev_id: node
            for dev_id, node in self._supported_nodes().items()
            if node.has_motion_sensor
        }
        if not nodes:
            return self.async_abort(reason="no_motion_sensors")

        errors: dict[str, str] = {}
        if user_input is not None:
            node = nodes[int(user_input["device"])]
            # Same gate the dedicated motion-sensor wizard applies. Without it
            # this form silently no-ops against a sleeping sensor, because the
            # device never receives the write - see util.sleeping_battery_device.
            if not self._bridge_is_online(node.id):
                errors["base"] = "still_offline"
                user_input = None
        if user_input is not None:
            return await self._run(
                node.set_motion_sensor_schedule(
                    slot_id=SCHEDULE_SLOT_OPTIONS[user_input["slot"]],
                    mode=SCHEDULE_MODE_OPTIONS[user_input["mode"]],
                    start_hour=user_input["start_hour"],
                    start_minute=user_input["start_minute"],
                    end_hour=user_input["end_hour"],
                    end_minute=user_input["end_minute"],
                    brightness=user_input["brightness"],
                    cct=user_input.get("cct"),
                    rgb=None,
                ),
                "experimental_schedule_written",
                device_name=node.name,
            )

        return self.async_show_form(
            step_id="exp_motion_schedule",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("device"): self._device_selector(nodes),
                    vol.Required("slot"): vol.In(list(SCHEDULE_SLOT_OPTIONS)),
                    vol.Required("mode"): vol.In(list(SCHEDULE_MODE_OPTIONS)),
                    vol.Required("start_hour", default=8): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=23)
                    ),
                    vol.Required("start_minute", default=0): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=59)
                    ),
                    vol.Required("end_hour", default=22): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=23)
                    ),
                    vol.Required("end_minute", default=0): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=59)
                    ),
                    vol.Required("brightness", default=50): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=100)
                    ),
                    vol.Optional("cct"): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=100)
                    ),
                }
            ),
        )

    async def async_step_exp_multicolor_segments(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Program up to two MultiColor segments' position and colour."""
        nodes = {
            dev_id: node
            for dev_id, node in self._supported_nodes().items()
            if node.supports_rgb
        }
        if not nodes:
            return self.async_abort(reason="no_rgb_devices")

        if user_input is not None:
            node = nodes[int(user_input["device"])]
            segments: list[tuple[Optional[int], Optional[tuple[int, int, int]]]] = []
            for pos_key, rgb_key in (
                ("segment_1_position", "segment_1_rgb"),
                ("segment_2_position", "segment_2_rgb"),
            ):
                position = user_input.get(pos_key)
                rgb = user_input.get(rgb_key)
                if position is None and rgb is None:
                    continue
                segments.append(
                    (position, tuple(rgb) if rgb else None)
                )
            if not segments:
                return self.async_show_form(
                    step_id="exp_multicolor_segments",
                    data_schema=self._multicolor_schema(nodes),
                    errors={"base": "no_segments"},
                )
            return await self._run(
                node.set_multicolor_segments(segments),
                "experimental_segments_written",
                device_name=node.name,
            )

        return self.async_show_form(
            step_id="exp_multicolor_segments",
            data_schema=self._multicolor_schema(nodes),
        )

    def _multicolor_schema(self, nodes: dict[int, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required("device"): self._device_selector(nodes),
                vol.Optional("segment_1_position"): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=120)
                ),
                vol.Optional("segment_1_rgb"): selector.ColorRGBSelector(),
                vol.Optional("segment_2_position"): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=120)
                ),
                vol.Optional("segment_2_rgb"): selector.ColorRGBSelector(),
            }
        )

    def _motion_sensor_nodes(self) -> dict[int, Any]:
        """Every currently-known device capable of motion/ambient-light
        sensor settings - same filter binary_sensor.py's motion entities
        use (metadata.supported + has_motion_sensor)."""
        runtime_data = getattr(self._config_entry, "runtime_data", None)
        if runtime_data is None:
            return {}
        return {
            node.id: node
            for node in runtime_data.ncync_server.node_devices.values()
            if node.metadata is not None
            and node.metadata.supported
            and node.has_motion_sensor
        }

    async def async_step_motion_sensor_select(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        nodes = self._motion_sensor_nodes()
        if not nodes:
            return self.async_abort(reason="no_motion_sensors")

        if user_input is not None:
            self._motion_sensor_dev_id = int(user_input["device"])
            return await self.async_step_motion_sensor_wake()

        return self.async_show_form(
            step_id="motion_sensor_select",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): vol.In(
                        {
                            str(dev_id): f"{node.name} (id {dev_id})"
                            for dev_id, node in sorted(
                                nodes.items(), key=lambda kv: kv[1].name or ""
                            )
                        }
                    ),
                }
            ),
        )

    async def async_step_motion_sensor_wake(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Gate on the device's ordinary online status - confirmed via
        decompiled-source research to be exactly what the real Cync app
        itself checks before allowing a settings edit (there is no
        separate BLE/GATT "discoverable" state - see
        docs/mesh_opcodes.md's "Operational prerequisite" section). If
        submitted while still offline, redisplay the same instructions
        with an error rather than silently sending a command the real
        app's own equivalent code path would itself have faked success
        on without transmitting."""
        nodes = self._motion_sensor_nodes()
        assert self._motion_sensor_dev_id is not None
        node = nodes.get(self._motion_sensor_dev_id)
        if node is None:
            return self.async_abort(reason="no_motion_sensors")

        if self._bridge_is_online(node.id):
            return await self.async_step_motion_sensor_settings()

        errors: dict[str, str] = {}
        if user_input is not None:
            errors["base"] = "still_offline"

        return self.async_show_form(
            step_id="motion_sensor_wake",
            data_schema=vol.Schema({}),
            description_placeholders={"device_name": node.name},
            errors=errors,
        )

    def _bridge_is_online(self, dev_id: int) -> bool:
        runtime_data = getattr(self._config_entry, "runtime_data", None)
        if runtime_data is None:
            return False
        return bool(runtime_data.bridge.is_online(dev_id))

    async def async_step_motion_sensor_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        nodes = self._motion_sensor_nodes()
        assert self._motion_sensor_dev_id is not None
        node = nodes.get(self._motion_sensor_dev_id)
        if node is None:
            return self.async_abort(reason="no_motion_sensors")

        if user_input is not None:
            sensitivity = user_input.get("sensitivity")
            await node.set_motion_sensor_settings(
                setting_type=MOTION_SENSOR_TYPE[user_input["sensor_type"]],
                enabled=user_input.get("enabled"),
                sensitivity=(
                    MOTION_SENSOR_SENSITIVITY[sensitivity] if sensitivity else None
                ),
                delay_seconds=user_input.get("delay_seconds", 0),
                deactivation_seconds=user_input.get("deactivation_seconds", 0),
            )
            return self.async_abort(
                reason="motion_sensor_settings_saved",
                description_placeholders={"device_name": node.name},
            )

        return self.async_show_form(
            step_id="motion_sensor_settings",
            data_schema=vol.Schema(
                {
                    vol.Required("sensor_type"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(MOTION_SENSOR_TYPE),
                            translation_key="motion_sensor_type",
                        )
                    ),
                    vol.Optional("enabled"): bool,
                    vol.Optional("sensitivity"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(MOTION_SENSOR_SENSITIVITY),
                            translation_key="motion_sensor_sensitivity",
                        )
                    ),
                    vol.Optional("delay_seconds", default=0): vol.All(
                        vol.Coerce(int), vol.Range(min=0)
                    ),
                    vol.Optional("deactivation_seconds", default=0): vol.All(
                        vol.Coerce(int), vol.Range(min=0)
                    ),
                }
            ),
            description_placeholders={"device_name": node.name},
        )
