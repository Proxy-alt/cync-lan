"""Shared base entity for Cync LAN platforms."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.restore_state import RestoreEntity

from .bridge import (
    CyncLanBridge,
    signal_device_online,
    signal_entity_update,
    signal_indicator_led_update,
)
from .const import DOMAIN, MANUFACTURER

if TYPE_CHECKING:
    from cync_lan.devices import CyncDevice
    from cync_lan.structs import EntityState


def build_device_info(entry_id: str, node: "CyncDevice") -> DeviceInfo:
    """devices (gold): every entity belongs to a proper HA device entry."""
    unique_id = f"{entry_id}_{node.id}"
    connections = {("bluetooth", node.mac.casefold())} if node.mac else set()
    if not node.bt_only and node.wifi_mac:
        connections.add(("mac", node.wifi_mac.casefold()))
    model = "Unknown"
    if node.metadata is not None:
        model = node.metadata.model_string
    return DeviceInfo(
        identifiers={(DOMAIN, unique_id)},
        connections=connections,
        manufacturer=MANUFACTURER,
        name=node.name,
        model=model,
        sw_version=node.version_str,
        via_device=(DOMAIN, entry_id),
    )


class CyncLanEntity(Entity):
    """Common plumbing for every Cync LAN entity.

    has-entity-name (bronze): has_entity_name = True, subclasses set
    `_attr_name` to None (device-name-only) or a short suffix like "Motion".
    entity-unique-id (bronze): unique_id always set below.
    entity-unavailable (silver): available reflects the bridge's per-device
    online tracking, updated from real fa db status packets.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        bridge: CyncLanBridge,
        entry_id: str,
        node: "CyncDevice",
        sub_id: int = 0,
        unique_id_suffix: str = "",
    ) -> None:
        self._bridge = bridge
        self._entry_id = entry_id
        self._node = node
        self._sub_id = sub_id
        self._unique_id = f"{entry_id}_{node.id}" + (
            f"_{sub_id}" if sub_id else ""
        ) + unique_id_suffix
        self._attr_unique_id = self._unique_id
        self._attr_device_info = build_device_info(entry_id, node)

    @property
    def available(self) -> bool:
        return self._bridge.is_online(self._node.id)

    async def async_added_to_hass(self) -> None:
        """entity-event-setup (bronze): subscribe during the lifecycle phase
        HA expects, not in __init__ (before the entity has a hass instance)."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_entity_update(self._unique_id),
                self._handle_update,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_device_online(self._node.id),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        """Must be @callback: async_dispatcher_connect's target is run
        through HA's HassJob classifier, which only recognizes coroutine
        functions or @callback-decorated ones as safe to run directly on
        the event loop. An undecorated plain function like this one used
        to be, HA defaults to running via the executor thread pool -
        exactly "a thread other than the event loop" - so every dispatch
        silently failed to call async_write_ha_state() on the loop,
        meaning entity state was computed correctly internally but never
        actually reached HA's frontend. Confirmed via a real user's logs:
        hundreds of "calls async_write_ha_state from a thread other than
        the event loop" errors, all originating from this exact line,
        immediately following a burst of real device state updates that
        were computed correctly (visible in DEBUG logs) but never shown.
        """
        self.async_write_ha_state()

    def _entity_state(self) -> Optional["EntityState"]:
        return self._bridge.get_state(self._node.id, self._sub_id)


class CyncLanIndicatorLedEntity(CyncLanEntity, RestoreEntity):
    """Shared plumbing for the 4 indicator-LED entities (select x2, number,
    switch) - they all read/write the same per-device IndicatorLedState
    cache (see bridge.py), so all 4 must re-render whenever any one of them
    changes, via a shared dispatcher signal distinct from the normal
    per-unique_id one CyncLanEntity itself listens for.
    """

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_indicator_led_update(self._entry_id, self._node.id),
                self._handle_update,
            )
        )

    async def _restore_led_field(
        self, field: str, parser: Callable[[str], Any]
    ) -> None:
        """Seed the shared cache from this entity's own last HA-known state
        on startup (RestoreEntity) - `parser` maps the restored state string
        back to the field's real value, returning None to skip restoring
        (e.g. an unrecognized/stale option value)."""
        last_state = await self.async_get_last_state()
        if last_state is None:
            return
        value = parser(last_state.state)
        if value is not None:
            self._bridge.seed_indicator_led_field(self._node, **{field: value})
