"""Tests for the scene platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.cync_lan.scene import CyncLanScene, async_setup_entry


async def test_setup_entry_creates_one_entity_per_scene(hass):
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.scenes = {
        3: {"name": "Movie Night"},
        7: {"name": "Good Morning"},
    }

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 2
    assert {e.name for e in added} == {"Movie Night", "Good Morning"}
    assert {e.unique_id for e in added} == {"entry1_scene_3", "entry1_scene_7"}


async def test_setup_entry_no_scenes_creates_no_entities(hass):
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.scenes = {}

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert added == []


async def test_setup_entry_missing_scenes_attr_creates_no_entities(hass):
    """runtime_data.scenes could be None (entry never finished setup, or
    parse_scenes() failed and left the best-effort default) - must not
    crash."""
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.scenes = None

    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert added == []


async def test_scene_device_info_attaches_to_bridge():
    entity = CyncLanScene("entry1", 3, "Movie Night")
    assert entity.device_info["identifiers"] == {("cync_lan", "entry1")}
    assert entity.device_info["name"] == "Cync LAN Bridge"


async def test_scene_activate_calls_execute_scene():
    entity = CyncLanScene("entry1", 3, "Movie Night")

    with patch(
        "cync_lan.devices.execute_scene", new=AsyncMock()
    ) as mock_execute_scene:
        await entity.async_activate()

    mock_execute_scene.assert_awaited_once_with(3)
