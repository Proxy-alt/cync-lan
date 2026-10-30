"""Tests for the scene platform."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

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


async def test_scene_activate_out_of_wire_range_id_raises_and_sends_nothing():
    """execute_scene()'s wire format carries sceneId as one byte (0-255,
    ExecuteSceneCommand.java's x() write). A scene whose cloud-exported ID
    falls outside that range used to "activate" in HA while the library
    silently refused to send anything - the exact no-error, bulbs-do-nothing
    failure reported in #55. Activation must raise instead."""
    entity = CyncLanScene("entry1", 517, "Out of range")

    with patch(
        "cync_lan.devices.execute_scene", new=AsyncMock()
    ) as mock_execute_scene, pytest.raises(HomeAssistantError):
        await entity.async_activate()

    mock_execute_scene.assert_not_awaited()


async def test_scene_activate_no_connected_device_raises_and_sends_nothing():
    """With zero devices in the TCP pool, the library's broadcast path
    transmits to nothing and only logs at debug level - HA still recorded
    the activation as successful (issue #55). The entity must raise when
    dispatch provably cannot go out."""
    server = MagicMock()
    server.get_dev_tcp_pool_sync.return_value = []
    entity = CyncLanScene("entry1", 3, "Movie Night", ncync_server=server)

    with patch(
        "cync_lan.devices.execute_scene", new=AsyncMock()
    ) as mock_execute_scene, pytest.raises(HomeAssistantError):
        await entity.async_activate()

    mock_execute_scene.assert_not_awaited()


async def test_scene_activate_no_writable_session_raises_and_sends_nothing():
    """A pool of connected-but-not-writable sessions (none ready_to_control,
    or every one stuck in observe-only MITM capture) also transmits nothing
    - broadcast_control_command logs debug and drops the packet. Must raise
    rather than record a fake activation."""
    server = MagicMock()
    server.get_dev_tcp_pool_sync.return_value = [
        SimpleNamespace(ready_to_control=False, observe_only=False),
        SimpleNamespace(ready_to_control=True, observe_only=True),
    ]
    entity = CyncLanScene("entry1", 3, "Movie Night", ncync_server=server)

    with patch(
        "cync_lan.devices.execute_scene", new=AsyncMock()
    ) as mock_execute_scene, pytest.raises(HomeAssistantError):
        await entity.async_activate()

    mock_execute_scene.assert_not_awaited()


async def test_scene_activate_proceeds_when_a_session_can_carry_the_command():
    server = MagicMock()
    server.get_dev_tcp_pool_sync.return_value = [
        SimpleNamespace(ready_to_control=True, observe_only=False)
    ]
    entity = CyncLanScene("entry1", 3, "Movie Night", ncync_server=server)

    with patch(
        "cync_lan.devices.execute_scene", new=AsyncMock()
    ) as mock_execute_scene:
        await entity.async_activate()

    mock_execute_scene.assert_awaited_once_with(3)


async def test_setup_entry_warns_but_still_registers_out_of_wire_range_scene(
    hass, caplog
):
    """Out-of-range scenes still register (the entity list mirrors the
    account), but the registration must log that they can never execute -
    not silently present an activatable tile that always no-ops."""
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.scenes = {
        3: {"name": "Movie Night"},
        517: {"name": "Too Big"},
    }

    added = []
    with caplog.at_level(logging.WARNING, logger="custom_components.cync_lan.scene"):
        await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 2
    assert "517" in caplog.text
    assert "Too Big" in caplog.text
