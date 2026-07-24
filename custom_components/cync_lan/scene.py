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
"Scenes control" section). Note execute_scene() validates scene_id 0-255
(a real width limit in the already-shipped command) - a real account scene
ID above that range would silently fail to activate; logged as an error in
devices.py, not raised here.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    scenes = getattr(entry.runtime_data, "scenes", None) or {}
    entities = [
        CyncLanScene(entry.entry_id, scene_id, scene["name"])
        for scene_id, scene in scenes.items()
    ]
    async_add_entities(entities)


class CyncLanScene(Scene):
    _attr_should_poll = False

    def __init__(self, entry_id: str, scene_id: int, name: str) -> None:
        self._scene_id = scene_id
        self._attr_name = name
        self._attr_unique_id = f"{entry_id}_scene_{scene_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            manufacturer=MANUFACTURER,
            name="Cync LAN Bridge",
        )

    async def async_activate(self, **kwargs: Any) -> None:
        from cync_lan.devices import execute_scene

        await execute_scene(self._scene_id)
