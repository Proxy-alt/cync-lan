"""Fan platform for Cync LAN (fan controller switches, e.g. deviceType 81)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import CyncLanEntity

PARALLEL_UPDATES = 0

_PRESET_MODES = ["low", "medium", "high", "max"]
# Mirrors src/cync_lan/structs.py's FanSpeed.to_perc() exactly.
_PRESET_PERCENTAGES = {"low": 25, "medium": 50, "high": 75, "max": 100}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data = entry.runtime_data
    bridge = runtime_data.bridge
    entities = [
        CyncLanFan(bridge, entry.entry_id, node)
        for node in runtime_data.ncync_server.node_devices.values()
        if node.metadata is not None
        and node.metadata.supported
        and node.is_fan_controller
    ]
    async_add_entities(entities)


class CyncLanFan(CyncLanEntity, FanEntity):
    _attr_name = None
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _attr_preset_modes = _PRESET_MODES
    _attr_speed_count = 4

    @property
    def is_on(self) -> bool | None:
        state = self._entity_state()
        return bool(state.power) if state else None

    @property
    def percentage(self) -> int | None:
        state = self._entity_state()
        return state.brightness if state else None

    @property
    def preset_mode(self) -> str | None:
        """Live, reflecting the current percentage back to the preset
        dropdown - previously always None (no property existed at all),
        so the UI never showed a selection even when the fan was at
        exactly one of the 4 preset percentages."""
        percentage = self.percentage
        for preset, pct in _PRESET_PERCENTAGES.items():
            if percentage == pct:
                return preset
        return None

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        if percentage is not None:
            await self._node.set_fan_percentage(percentage)
        elif preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
        else:
            await self._node.set_power(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._node.set_power(0)

    async def async_set_percentage(self, percentage: int) -> None:
        await self._node.set_fan_percentage(percentage)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        from cync_lan.structs import FanSpeed

        await self._node.set_fan_speed(FanSpeed(preset_mode))
