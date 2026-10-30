"""Scene platform for Cync LAN: one activatable scene entity per Cync Scene
(the "Routines -> Scenes" tab), replacing the raw experimental_execute_scene
service (which requires the user to already know/look up a numeric scene_id)
with HA's own native scene picker/dashboard tile.

Home-wide, not tied to any device - attached to the "Cync LAN Bridge" device
for grouping on its device page, same pattern as binary_sensor.py's
diagnostic sensors. Deliberately does NOT set has_entity_name - a scene's
name is its own complete identity (e.g. "Movie Night"), not a facet of the
bridge the way "App Mesh Active" is; prefixing every scene with "Cync LAN
Bridge" would read oddly in the scene picker and doesn't match how HA's own
built-in scene platforms from other integrations behave.

UNVALIDATED against a real populated export - see cloud_api.py's
parse_scenes() docstring and docs/cync_automations.md: the one real account
export available for this codebase's research had zero scenes configured.
Activating calls the already-wired, already-EXPERIMENTAL execute_scene() -
same caveats apply here (predicted cmd_code, see docs/mesh_opcodes.md's
"Scenes control" section). execute_scene() validates scene_id 0-255 (a real
width limit in the already-shipped command) - a real account scene ID above
that range can never be dispatched, so registration logs a warning and
activation raises instead of silently succeeding (the no-error,
bulbs-do-nothing failure reported in issue #55).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER
from .util import assert_broadcast_target_available

if TYPE_CHECKING:
    from cync_lan.server import nCyncServer

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)

# execute_scene()'s wire format carries sceneId as a single byte
# (ExecuteSceneCommand.java's x() write - docs/mesh_opcodes.md's "Scenes
# control" section). Nothing outside 0-255 can ever be transmitted: the
# library logs and returns without sending, which HA-side looks identical
# to a successful activation.
_SCENE_ID_MAX = 255


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    scenes = entry.runtime_data.scenes or {}
    for scene_id, scene in scenes.items():
        if not 0 <= scene_id <= _SCENE_ID_MAX:
            _LOGGER.warning(
                "Cync scene '%s' (id %s) is outside the 0-%d scene_id range the "
                "execute_scene wire command can carry; it will register but "
                "can never activate - pressing it will raise instead of "
                "silently doing nothing",
                scene["name"],
                scene_id,
                _SCENE_ID_MAX,
            )
    entities = [
        CyncLanScene(
            entry.entry_id,
            scene_id,
            scene["name"],
            ncync_server=entry.runtime_data.ncync_server,
        )
        for scene_id, scene in scenes.items()
    ]
    async_add_entities(entities)


class CyncLanScene(Scene):
    _attr_should_poll = False

    def __init__(
        self,
        entry_id: str,
        scene_id: int,
        name: str,
        ncync_server: "nCyncServer | None" = None,
    ) -> None:
        self._scene_id = scene_id
        # Held only for async_activate's dispatch precondition check -
        # execute_scene() itself uses the library's GlobalObject singleton.
        self._ncync_server = ncync_server
        self._attr_name = name
        self._attr_unique_id = f"{entry_id}_scene_{scene_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            manufacturer=MANUFACTURER,
            name="Cync LAN Bridge",
        )

    async def async_activate(self, **kwargs: Any) -> None:
        # Check the preconditions execute_scene() would otherwise silently
        # fail under (out-of-range id: logged and dropped; no writable
        # device connection: broadcast returns False, debug-logged) BEFORE
        # calling it, so a broken activation surfaces as an HA error instead
        # of a recorded success with nothing sent - issue #55's report.
        if not 0 <= self._scene_id <= _SCENE_ID_MAX:
            raise HomeAssistantError(
                f"Scene id {self._scene_id} is outside the 0-{_SCENE_ID_MAX} "
                "range the execute_scene wire command can carry; it cannot "
                "be dispatched to the mesh."
            )
        if self._ncync_server is not None:
            assert_broadcast_target_available(self._ncync_server)
        from cync_lan.devices import execute_scene

        await execute_scene(self._scene_id)
