"""Tests for the button platform - the UI entry points for experimental
actions.

The gate matters as much as the behaviour here: these buttons must not exist
at all on a default install, because every one of them sends a mesh command
whose wire shape is predicted rather than confirmed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.cync_lan.button import (
    CyncLanDeleteSceneButton,
    CyncLanDeleteScheduleButton,
    CyncLanQueryMeshCredentialsButton,
    async_setup_entry,
)
from custom_components.cync_lan.const import CONF_ENABLE_EXPERIMENTAL, DOMAIN


@pytest.fixture(autouse=True)
def _seed_hass(hass):
    _HASS[0] = hass
    yield
    _HASS[0] = None


def _entry(experimental: bool, scenes=None, schedules=None, groups=None, nodes=None):
    from custom_components.cync_lan.bridge import CyncLanBridge

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {CONF_ENABLE_EXPERIMENTAL: experimental}
    entry.runtime_data = SimpleNamespace(
        scenes=scenes or {},
        schedules=schedules or {},
        groups=groups or {},
        bridge=CyncLanBridge(_HASS[0], "entry1"),
        ncync_server=SimpleNamespace(node_devices=nodes or {}),
    )
    return entry


# the entry factory is sync but needs a hass for the bridge; stashed by _setup
_HASS: list = [None]


async def _setup(hass, entry):
    added = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    return added


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


async def test_no_buttons_without_the_experimental_opt_in(hass):
    entry = _entry(False, scenes={1: {"name": "Movie Night"}})

    assert await _setup(hass, entry) == []


async def test_buttons_appear_once_opted_in(hass):
    entry = _entry(True, scenes={1: {"name": "Movie Night"}})

    added = await _setup(hass, entry)

    assert any(isinstance(e, CyncLanQueryMeshCredentialsButton) for e in added)
    assert any(isinstance(e, CyncLanDeleteSceneButton) for e in added)


async def test_one_delete_button_per_scene_and_schedule(hass):
    entry = _entry(
        True,
        scenes={1: {"name": "Movie Night"}, 2: {"name": "Dinner"}},
        schedules={7: {"name": "Wake Up", "scene_id": 1, "enabled": True}},
    )

    added = await _setup(hass, entry)

    scene_buttons = [e for e in added if isinstance(e, CyncLanDeleteSceneButton)]
    schedule_buttons = [e for e in added if isinstance(e, CyncLanDeleteScheduleButton)]
    assert len(scene_buttons) == 2
    assert len(schedule_buttons) == 1
    assert {b.unique_id for b in scene_buttons} == {
        "entry1_delete_scene_1",
        "entry1_delete_scene_2",
    }


async def test_an_account_with_no_scenes_still_gets_the_bridge_buttons(hass):
    """The bridge-level buttons do not depend on any scene, schedule or
    device existing."""
    added = await _setup(hass, _entry(True))

    assert any(isinstance(e, CyncLanQueryMeshCredentialsButton) for e in added)
    assert not any(isinstance(e, CyncLanDeleteSceneButton) for e in added)


# ---------------------------------------------------------------------------
# safety posture
# ---------------------------------------------------------------------------


async def test_destructive_buttons_are_disabled_by_default(hass):
    """HA has no confirmation dialog for a button press, and recreating a
    deleted Cync scene means going back to the phone app."""
    entry = _entry(
        True,
        scenes={1: {"name": "Movie Night"}},
        schedules={7: {"name": "Wake Up", "scene_id": 1, "enabled": True}},
    )

    added = await _setup(hass, entry)

    for button in added:
        if isinstance(
            button, (CyncLanDeleteSceneButton, CyncLanDeleteScheduleButton)
        ):
            assert button.entity_registry_enabled_default is False


async def test_read_only_query_button_is_enabled_by_default(hass):
    added = await _setup(hass, _entry(True))

    assert added[0].entity_registry_enabled_default is True


async def test_buttons_attach_to_the_bridge_device(hass):
    """These are home-wide, not properties of any one light."""
    entry = _entry(True, scenes={1: {"name": "Movie Night"}})

    added = await _setup(hass, entry)

    for button in added:
        assert button.device_info["identifiers"] == {(DOMAIN, "entry1")}


# ---------------------------------------------------------------------------
# presses
# ---------------------------------------------------------------------------


async def test_delete_scene_button_sends_its_own_scene_id(hass):
    """The whole point: the id is baked in, so the user never types one."""
    button = CyncLanDeleteSceneButton("entry1", 42, "Movie Night")

    with patch(
        "cync_lan.devices.delete_scene", new=AsyncMock()
    ) as mock_delete:
        await button.async_press()

    mock_delete.assert_awaited_once_with(42)


async def test_delete_schedule_button_sends_its_own_schedule_id(hass):
    button = CyncLanDeleteScheduleButton("entry1", 9, "Wake Up")

    with patch(
        "cync_lan.devices.delete_schedule", new=AsyncMock()
    ) as mock_delete:
        await button.async_press()

    mock_delete.assert_awaited_once_with(9)


async def test_query_button_notifies_instead_of_logging_the_password(hass, caplog):
    """The password is the mesh's shared secret - it must not reach the log,
    which is a far broader and longer-lived audience than the notification."""
    button = CyncLanQueryMeshCredentialsButton("entry1")
    button.hass = hass

    with patch(
        "cync_lan.devices.query_hub_mesh_credentials",
        new=AsyncMock(return_value=("my_mesh", "sup3rs3cret")),
    ):
        await button.async_press()

    notifications = hass.data.get("persistent_notification", {})
    assert notifications, "expected a persistent notification"
    body = str(list(notifications.values()))
    assert "my_mesh" in body
    assert "sup3rs3cret" in body
    assert "sup3rs3cret" not in caplog.text


async def test_query_button_raises_a_clear_error_on_timeout(hass):
    from homeassistant.exceptions import HomeAssistantError

    button = CyncLanQueryMeshCredentialsButton("entry1")
    button.hass = hass

    with patch(
        "cync_lan.devices.query_hub_mesh_credentials",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(HomeAssistantError, match="did not|timeout|answer"):
            await button.async_press()


async def test_delete_automation_button_is_separate_from_delete_schedule(hass):
    """They do different things: one removes the Schedule, the other only
    unbinds what makes it fire, leaving the Schedule to be re-bound."""
    from custom_components.cync_lan.button import CyncLanDeleteAutomationButton

    entry = _entry(True, schedules={7: {"name": "Wake", "scene_id": 1, "enabled": True}})

    added = await _setup(hass, entry)

    schedule_btns = [e for e in added if isinstance(e, CyncLanDeleteScheduleButton)]
    automation_btns = [e for e in added if isinstance(e, CyncLanDeleteAutomationButton)]
    assert len(schedule_btns) == 1
    assert len(automation_btns) == 1
    assert schedule_btns[0].unique_id != automation_btns[0].unique_id


async def test_delete_automation_button_sends_its_schedule_id(hass):
    from custom_components.cync_lan.button import CyncLanDeleteAutomationButton

    button = CyncLanDeleteAutomationButton("entry1", 9, "Wake")

    with patch("cync_lan.devices.delete_automation", new=AsyncMock()) as mock:
        await button.async_press()

    mock.assert_awaited_once_with(9)


async def test_delete_group_button_sends_the_group_mesh_address(hass):
    from custom_components.cync_lan.button import CyncLanDeleteGroupButton

    button = CyncLanDeleteGroupButton("entry1", 32770, "Kitchen")

    with patch("cync_lan.devices.delete_group", new=AsyncMock()) as mock:
        await button.async_press()

    mock.assert_awaited_once_with(32770)


async def test_new_delete_buttons_are_also_disabled_by_default(hass):
    from custom_components.cync_lan.button import (
        CyncLanDeleteAutomationButton,
        CyncLanDeleteGroupButton,
    )

    entry = _entry(
        True,
        schedules={7: {"name": "Wake", "scene_id": 1, "enabled": True}},
        groups={32770: {"name": "Kitchen"}},
    )

    added = await _setup(hass, entry)

    for b in added:
        if isinstance(b, (CyncLanDeleteAutomationButton, CyncLanDeleteGroupButton)):
            assert b.entity_registry_enabled_default is False


async def test_identify_button_exists_per_device_and_uses_ha_device_class(hass):
    """Rendered as Home Assistant's standard identify affordance rather than
    another experimental button."""
    from homeassistant.components.button import ButtonDeviceClass

    from custom_components.cync_lan.button import CyncLanIdentifyButton

    node = MagicMock(id=5)
    node.name = "Hall"
    node.mac = "AA:BB:CC:DD:EE:FF"
    node.wifi_mac = "11:22:33:44:55:66"
    node.bt_only = False
    node.metadata = MagicMock(supported=True)
    node.metadata.model_string = "Model"
    node.identify = AsyncMock()

    added = await _setup(hass, _entry(True, nodes={5: node}))

    buttons = [e for e in added if isinstance(e, CyncLanIdentifyButton)]
    assert len(buttons) == 1
    assert buttons[0].device_class is ButtonDeviceClass.IDENTIFY

    await buttons[0].async_press()
    node.identify.assert_awaited_once_with(True)


async def test_sync_hub_clock_button_pushes_current_time(hass):
    from custom_components.cync_lan.button import CyncLanSyncHubClockButton

    button = CyncLanSyncHubClockButton("entry1")

    with patch("cync_lan.devices.set_time", new=AsyncMock()) as mock:
        await button.async_press()

    mock.assert_awaited_once_with()
