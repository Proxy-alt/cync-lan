"""Shared plumbing for Cync device-group aggregate entities.

light.py's CyncLanLightGroup and switch.py's CyncLanSwitchGroup both need
the same "wait for a just-scheduled member entity to actually register,
then hide/reveal it" logic - identical apart from which platform their
members live on, so it lives here once rather than twice.
"""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_ENTITY_REGISTRATION_POLL_INTERVAL = 0.1
_ENTITY_REGISTRATION_TIMEOUT = 5.0


async def wait_for_member_entities(
    hass: HomeAssistant,
    registry: er.EntityRegistry,
    platform: Platform,
    entry_id: str,
    dev_ids: list[int],
) -> None:
    """Poll the entity registry until every entity async_add_entities() just
    scheduled for these dev_ids on `platform` has actually registered, or a
    short timeout elapses.

    async_add_entities() only *schedules* registration as a background task
    (EntityPlatform._async_schedule_add_entities_for_entry) - it does not
    complete before returning, so group entities built right after it would
    resolve member entity_ids against a registry that hasn't caught up yet.
    Polling just for these specific entities resolves as soon as they're
    actually ready without waiting on anything unrelated (e.g.
    hass.async_block_till_done(), which waits process-wide and has caused
    its own 60+ second regression on a real HA install), and the timeout
    keeps this from hanging indefinitely if one somehow never registers -
    callers already tolerate a missing entity by skipping that group member.
    """
    if not dev_ids:
        return
    deadline = hass.loop.time() + _ENTITY_REGISTRATION_TIMEOUT
    while hass.loop.time() < deadline:
        if all(
            registry.async_get_entity_id(platform, DOMAIN, f"{entry_id}_{dev_id}")
            is not None
            for dev_id in dev_ids
        ):
            return
        await asyncio.sleep(_ENTITY_REGISTRATION_POLL_INTERVAL)


def apply_group_member_visibility(
    registry: er.EntityRegistry,
    platform: Platform,
    entry_id: str,
    groups: dict[int, dict[str, Any]],
    hide: bool,
) -> None:
    """Hide or reveal each group's member entities on `platform`, without
    touching entities the user hid themselves.

    The entity registry tracks *why* an entity is hidden via hidden_by
    (None, RegistryEntryHider.USER, or RegistryEntryHider.INTEGRATION) - only
    ever touches entities this integration hid itself (hidden_by ==
    INTEGRATION), so a user who explicitly hid a member for their own
    reasons keeps that choice regardless of this option, in either
    direction.
    """
    for group in groups.values():
        for dev_id in group.get("device_ids", []):
            unique_id = f"{entry_id}_{dev_id}"
            entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
            if entity_id is None:
                continue
            reg_entry = registry.async_get(entity_id)
            if reg_entry is None:
                continue
            if hide:
                if reg_entry.hidden_by is None:
                    registry.async_update_entity(
                        entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
                    )
            elif reg_entry.hidden_by is er.RegistryEntryHider.INTEGRATION:
                registry.async_update_entity(entity_id, hidden_by=None)
