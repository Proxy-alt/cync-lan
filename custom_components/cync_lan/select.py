"""Select platform for Cync LAN: indicator-LED mode and color.

Both are config entities backed by CyncDevice.set_indicator_led() -
confirmed working on real hardware this session (see docs/mesh_opcodes.md's
"Indicator LED ring" section). There's no way to read the indicator LED's
real state back from the device, so these are assumed-state entities
(HA's documented pattern for "can command it, can't read it back") backed
by the shared per-device cache in bridge.py and restored across HA
restarts via RestoreEntity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .bridge import LED_COLOR_TO_INT, LED_MODE_TO_INT, CyncLanBridge
from .const import CONF_ENABLE_EXPERIMENTAL, DEFAULT_ENABLE_EXPERIMENTAL
from .entity import CyncLanEntity, CyncLanIndicatorLedEntity

if TYPE_CHECKING:
    from cync_lan.devices import CyncDevice

PARALLEL_UPDATES = 0


# Indicator-LED presentation: one form or the other, never both. All of these
# entities write the same single atomic mesh command, so offering both a light
# and the select/number/switch trio would put two UIs in a race over one piece
# of hardware and let them disagree about its state.
def _indicator_led_is_a_light(entry: ConfigEntry) -> bool:
    from .const import CONF_INDICATOR_LED_AS_LIGHT, DEFAULT_INDICATOR_LED_AS_LIGHT

    return bool(
        entry.options.get(CONF_INDICATOR_LED_AS_LIGHT, DEFAULT_INDICATOR_LED_AS_LIGHT)
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data = entry.runtime_data
    bridge = runtime_data.bridge
    entities: list[SelectEntity] = []
    for node in runtime_data.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
        if not _indicator_led_is_a_light(entry):
            entities.append(CyncLanIndicatorLedModeSelect(bridge, entry.entry_id, node))
        if not _indicator_led_is_a_light(entry):
            entities.append(
                CyncLanIndicatorLedColorSelect(bridge, entry.entry_id, node)
            )

    if entry.options.get(CONF_ENABLE_EXPERIMENTAL, DEFAULT_ENABLE_EXPERIMENTAL):
        for node in runtime_data.ncync_server.node_devices.values():
            if node.metadata is None or not node.metadata.supported:
                continue
            # The level bar exists on dimmers, not on binary switches.
            # Same unsatisfiable condition as number.py's - never created.
            if node.is_dimmer_switch:
                entities.append(
                    CyncLanDimmerLedModeSelect(bridge, entry.entry_id, node)
                )

    async_add_entities(entities)


class CyncLanIndicatorLedModeSelect(CyncLanIndicatorLedEntity, SelectEntity):
    _attr_translation_key = "indicator_led_mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True
    _attr_options = list(LED_MODE_TO_INT)

    def __init__(
        self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice"
    ) -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_indicator_led_mode")

    @property
    def current_option(self) -> str:
        return self._bridge.get_indicator_led(self._node.id).mode

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_led_field(
            "mode", lambda s: s if s in LED_MODE_TO_INT else None
        )

    async def async_select_option(self, option: str) -> None:
        await self._bridge.set_indicator_led_field(self._node, mode=option)


class CyncLanIndicatorLedColorSelect(CyncLanIndicatorLedEntity, SelectEntity):
    _attr_translation_key = "indicator_led_color"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True
    _attr_options = list(LED_COLOR_TO_INT)

    def __init__(
        self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice"
    ) -> None:
        super().__init__(
            bridge, entry_id, node, unique_id_suffix="_indicator_led_color"
        )

    @property
    def current_option(self) -> str:
        return self._bridge.get_indicator_led(self._node.id).color

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_led_field(
            "color", lambda s: s if s in LED_COLOR_TO_INT else None
        )

    async def async_select_option(self, option: str) -> None:
        await self._bridge.set_indicator_led_field(self._node, color=option)


# DimmingLedsIndicatorMode.java's only two values - there is deliberately no
# "off" option, because the enum does not have one.
DIMMER_LED_MODES = {"briefly_display": 1, "always_on": 2}


class CyncLanDimmerLedModeSelect(CyncLanEntity, RestoreEntity, SelectEntity):
    """How a dimmer's row of level LEDs behaves.

    Distinct from the indicator-LED entities above, which control the small
    status light. Assumed state for the same reason: the device never reports
    this back, so the last value set is restored across restarts rather than
    read from hardware.
    """

    _attr_translation_key = "dimmer_led_mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True
    _attr_options = list(DIMMER_LED_MODES)

    def __init__(
        self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice"
    ) -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_dimmer_led_mode")
        self._attr_current_option = "always_on"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in DIMMER_LED_MODES:
            self._attr_current_option = last.state

    async def async_select_option(self, option: str) -> None:
        await self._node.set_dimmer_led_mode(DIMMER_LED_MODES[option])
        self._attr_current_option = option
        self.async_write_ha_state()
