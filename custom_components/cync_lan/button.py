"""Button platform for Cync LAN - UI entry points for experimental actions.

The experimental_* services work, but they are a poor way to actually reach
these features: every one wants a numeric scene_id/schedule_id that the Cync
app never shows you, typed into a Developer Tools form. These buttons carry
the ID they act on, so the thing a user wants ("delete THIS scene") is one
click on the device page instead of a lookup plus a form.

Only created when the experimental option is on (hub -> Configure ->
General settings), so a default install has none of them. See services.py's
async_setup_services for the same gate on the service side.

Destructive buttons are disabled by default. Home Assistant has no
confirmation dialog for a button press, so deleting a Cync-app-created scene
would otherwise be one stray click - and recreating it means going back to
the phone app. Requiring the user to enable the entity first makes that a
deliberate two-step, the same treatment switch.py already gives MITM mode.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ENABLE_EXPERIMENTAL,
    DEFAULT_ENABLE_EXPERIMENTAL,
    DOMAIN,
    MANUFACTURER,
)

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if not entry.options.get(CONF_ENABLE_EXPERIMENTAL, DEFAULT_ENABLE_EXPERIMENTAL):
        return

    runtime_data = entry.runtime_data
    entities: list[ButtonEntity] = [
        CyncLanQueryMeshCredentialsButton(entry.entry_id),
    ]

    for scene_id, scene in (runtime_data.scenes or {}).items():
        entities.append(
            CyncLanDeleteSceneButton(entry.entry_id, scene_id, scene["name"])
        )
    for schedule_id, schedule in (runtime_data.schedules or {}).items():
        entities.append(
            CyncLanDeleteScheduleButton(
                entry.entry_id, schedule_id, schedule["name"]
            )
        )
        entities.append(
            CyncLanDeleteAutomationButton(
                entry.entry_id, schedule_id, schedule["name"]
            )
        )
    for group_id, group in (runtime_data.groups or {}).items():
        entities.append(
            CyncLanDeleteGroupButton(
                entry.entry_id, group_id, group.get("name") or f"Group {group_id}"
            )
        )

    async_add_entities(entities)


def _bridge_device_info(entry_id: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        manufacturer=MANUFACTURER,
        name="Cync LAN Bridge",
    )


class _CyncLanBridgeButton(ButtonEntity):
    """Shared plumbing for the home-wide buttons, which belong to the bridge
    device rather than any single light."""

    _attr_should_poll = False

    def __init__(self, entry_id: str, unique_id_suffix: str) -> None:
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_{unique_id_suffix}"
        self._attr_device_info = _bridge_device_info(entry_id)


class CyncLanQueryMeshCredentialsButton(_CyncLanBridgeButton):
    """Read the BTLE mesh name and password off a connected hub (op_code
    0x8A) and show them once, in a notification.

    Read-only, so unlike the delete buttons this is enabled by default.

    The result goes to a persistent notification rather than the log or an
    entity state: the password is the mesh's shared secret, and both of
    those are broader, longer-lived audiences than the person who just
    pressed the button. A notification is dismissible and is not written to
    disk in plain text the way the log is.
    """

    _attr_translation_key = "query_mesh_credentials"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry_id: str) -> None:
        super().__init__(entry_id, "query_mesh_credentials")

    async def async_press(self) -> None:
        from homeassistant.components import persistent_notification

        from cync_lan.devices import query_hub_mesh_credentials

        result = await query_hub_mesh_credentials()
        if result is None:
            raise HomeAssistantError(
                "No connected Cync device answered the mesh-credentials query "
                "within the timeout. This command's response channel is "
                "experimental and may not work on your hardware."
            )
        mesh_name, mesh_password = result
        persistent_notification.async_create(
            self.hass,
            title="Cync mesh credentials",
            message=(
                f"**Mesh name:** `{mesh_name}`\n\n"
                f"**Mesh password:** `{mesh_password}`\n\n"
                "Pass these to `cync-lan-ble-provision provision <address> "
                "<mesh_name> <mesh_password>` to add a new device to this "
                "mesh. Dismiss this notification when you are done - the "
                "password is your mesh's shared secret."
            ),
            notification_id=f"{DOMAIN}_mesh_credentials_{self._entry_id}",
        )


class _CyncLanDestructiveButton(_CyncLanBridgeButton):
    """Disabled by default - see this module's docstring."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False


class CyncLanDeleteSceneButton(_CyncLanDestructiveButton):
    """Delete one Cync Scene from the hub, replacing experimental_delete_scene
    and its numeric scene_id field."""

    _attr_translation_key = "delete_scene"

    def __init__(self, entry_id: str, scene_id: int, name: str) -> None:
        super().__init__(entry_id, f"delete_scene_{scene_id}")
        self._scene_id = scene_id
        self._attr_translation_placeholders = {"scene_name": name}

    async def async_press(self) -> None:
        from cync_lan.devices import delete_scene

        await delete_scene(self._scene_id)
        _LOGGER.info(
            "Sent delete for Cync scene_id=%s. The scene list is read from the "
            "cloud export, so it will keep showing until the next refresh.",
            self._scene_id,
        )


class CyncLanDeleteScheduleButton(_CyncLanDestructiveButton):
    """Delete one Cync Schedule from the hub, replacing
    experimental_delete_schedule and its numeric schedule_id field."""

    _attr_translation_key = "delete_schedule"

    def __init__(self, entry_id: str, schedule_id: int, name: str) -> None:
        super().__init__(entry_id, f"delete_schedule_{schedule_id}")
        self._schedule_id = schedule_id
        self._attr_translation_placeholders = {"schedule_name": name}

    async def async_press(self) -> None:
        from cync_lan.devices import delete_schedule

        await delete_schedule(self._schedule_id)
        _LOGGER.info(
            "Sent delete for Cync schedule_id=%s. The schedule list is read "
            "from the cloud export, so it will keep showing until the next "
            "refresh.",
            self._schedule_id,
        )


class CyncLanDeleteAutomationButton(_CyncLanDestructiveButton):
    """Remove a Schedule's trigger binding without deleting the Schedule.

    Distinct from "Delete schedule": that removes the Schedule itself, this
    only unbinds what makes it fire, leaving the Schedule to be re-bound.
    Until cync-lan 0.3.0 there was no way to do this at all - create, toggle
    and delete-schedule all existed, but nothing removed the binding
    add_automation creates.
    """

    _attr_translation_key = "delete_automation"

    def __init__(self, entry_id: str, schedule_id: int, name: str) -> None:
        super().__init__(entry_id, f"delete_automation_{schedule_id}")
        self._schedule_id = schedule_id
        self._attr_translation_placeholders = {"schedule_name": name}

    async def async_press(self) -> None:
        from cync_lan.devices import delete_automation

        await delete_automation(self._schedule_id)
        _LOGGER.info(
            "Sent automation-binding delete for Cync schedule_id=%s",
            self._schedule_id,
        )


class CyncLanDeleteGroupButton(_CyncLanDestructiveButton):
    """Delete a Cync device group from the mesh.

    The group list comes from the cloud export, so the entity stays until the
    next refresh even on success - same as the scene and schedule deletes.
    """

    _attr_translation_key = "delete_group"

    def __init__(self, entry_id: str, group_id: int, name: str) -> None:
        super().__init__(entry_id, f"delete_group_{group_id}")
        self._group_id = group_id
        self._attr_translation_placeholders = {"group_name": name}

    async def async_press(self) -> None:
        from cync_lan.devices import delete_group

        await delete_group(self._group_id)
        _LOGGER.info("Sent delete for Cync group_id=%s", self._group_id)
