"""Tests for services.py's 9 experimental_* services."""

from __future__ import annotations

import contextlib

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.const import DOMAIN
from custom_components.cync_lan.services import (
    SERVICE_ADD_DEVICE_TO_SCENE,
    SERVICE_DELETE_SCENE,
    SERVICE_DELETE_SCHEDULE,
    SERVICE_EXECUTE_SCENE,
    SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
    SERVICE_QUERY_MESH_CREDENTIALS,
    SERVICE_REMOVE_DEVICE_FROM_SCENE,
    SERVICE_SET_GROUP_MEMBERSHIP,
    SERVICE_SET_GROUP_POWER,
    SERVICE_SET_INDICATOR_LED,
    SERVICE_SET_MOTION_SENSOR_SCHEDULE,
    SERVICE_SET_MOTION_SENSOR_SETTINGS,
    SERVICE_SET_MULTICOLOR_GRADIENT_MODE,
    SERVICE_SET_MULTICOLOR_SEGMENTS,
    SERVICE_SET_MULTICOLOR_SEGMENT_COUNT,
    SERVICE_TOGGLE_AUTOMATION,
    _async_remove_services,
    async_setup_services,
    async_unload_services,
)


def _register_device(hass, entry, dev_id: int):
    from homeassistant.helpers import device_registry as dr

    device_reg = dr.async_get(hass)
    return device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_{dev_id}")},
        name=f"Device {dev_id}",
    )


def _register_bridge_device(hass, entry):
    from homeassistant.helpers import device_registry as dr

    device_reg = dr.async_get(hass)
    return device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Cync LAN Bridge",
    )


def _register_light_entity(hass, entry, dev_id: int, *, name: str = "Test Light") -> str:
    """Registers a light entity_id in the entity registry the way
    light.py's CyncLanLight would - domain "light", platform=DOMAIN,
    unique_id f"{entry_id}_{dev_id}" - so _resolve_cync_light_entity()
    can resolve it back to (entry, node) without needing the real
    light.py entity classes loaded."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    reg_entry = registry.async_get_or_create(
        "light",
        DOMAIN,
        f"{entry.entry_id}_{dev_id}",
        config_entry=entry,
        suggested_object_id=name.lower().replace(" ", "_"),
    )
    return reg_entry.entity_id


def _register_light_group_entity(hass, entry, group_id: int) -> str:
    """Same as _register_light_entity, but with a group's own unique_id
    shape (f"{entry_id}_group_{group_id}", see light.py's
    CyncLanLightGroup) - used to confirm groups are rejected as scene
    action targets."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    reg_entry = registry.async_get_or_create(
        "light",
        DOMAIN,
        f"{entry.entry_id}_group_{group_id}",
        config_entry=entry,
        suggested_object_id=f"group_{group_id}",
    )
    return reg_entry.entity_id


def _register_automation_entity(hass, entity_id: str, raw_config: dict, name: str = "Test Automation"):
    """Installs a fake AutomationEntity-like object (just the
    .raw_config/.name attributes _handle_push_automation_to_hardware
    actually reads) into hass.data[automation.DATA_COMPONENT], standing
    in for the real automation integration's EntityComponent so these
    tests don't need to load/validate a real automation config - only
    the RAW, as-authored config dict (pre schema-normalization) matters
    here, which is exactly what AutomationEntity.raw_config holds."""
    from homeassistant.components import automation as automation_component

    fake_entity = SimpleNamespace(raw_config=raw_config, name=name)
    component = hass.data.get(automation_component.DATA_COMPONENT)
    entities = getattr(component, "_entities", None) if component is not None else None
    if entities is None:
        entities = {}
        hass.data[automation_component.DATA_COMPONENT] = SimpleNamespace(
            get_entity=lambda eid, _entities=entities: _entities.get(eid),
            _entities=entities,
        )
    entities[entity_id] = fake_entity
    return fake_entity



@pytest.fixture(autouse=True)
def opted_in_to_experimental(request):
    """The experimental_* services only register once the user opts in from
    the hub's Configure screen. These tests are about what the services DO,
    so they assume the opt-in; the gate itself is covered by the
    test_experimental_gate_* tests, which opt out of this fixture."""
    if "no_experimental_optin" in request.keywords:
        yield
        return
    with patch(
        "custom_components.cync_lan.services.experimental_enabled", return_value=True
    ):
        yield


def _make_entry(hass, dev_ids: list[int] = ()):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, unique_id="user@example.com")
    entry.add_to_hass(hass)
    nodes = {dev_id: MagicMock(id=dev_id) for dev_id in dev_ids}
    for node in nodes.values():
        node.set_indicator_led = AsyncMock()
        node.set_motion_sensor_settings = AsyncMock()
        node.set_motion_sensor_schedule = AsyncMock()
        node.set_group_membership = AsyncMock()
        node.add_to_scene = AsyncMock()
        node.remove_from_scene = AsyncMock()
        node.set_multicolor_gradient_mode = AsyncMock()
        node.set_multicolor_segment_count = AsyncMock()
        node.set_multicolor_segments = AsyncMock()
    entry.runtime_data = SimpleNamespace(
        ncync_server=SimpleNamespace(node_devices=nodes),
        bridge=CyncLanBridge(hass, entry.entry_id),
    )
    return entry


async def test_setup_registers_all_fifteen_services(hass):
    async_setup_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_INDICATOR_LED)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_MOTION_SENSOR_SETTINGS)
    assert hass.services.has_service(DOMAIN, SERVICE_EXECUTE_SCENE)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_GROUP_POWER)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_MOTION_SENSOR_SCHEDULE)
    assert hass.services.has_service(DOMAIN, SERVICE_DELETE_SCENE)
    assert hass.services.has_service(DOMAIN, SERVICE_DELETE_SCHEDULE)
    assert hass.services.has_service(DOMAIN, SERVICE_TOGGLE_AUTOMATION)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_GROUP_MEMBERSHIP)
    assert hass.services.has_service(DOMAIN, SERVICE_PUSH_AUTOMATION_TO_HARDWARE)
    assert hass.services.has_service(DOMAIN, SERVICE_ADD_DEVICE_TO_SCENE)
    assert hass.services.has_service(DOMAIN, SERVICE_REMOVE_DEVICE_FROM_SCENE)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_MULTICOLOR_GRADIENT_MODE)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_MULTICOLOR_SEGMENT_COUNT)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_MULTICOLOR_SEGMENTS)
    async_unload_services(hass)


async def test_setup_services_is_idempotent(hass):
    async_setup_services(hass)
    async_setup_services(hass)  # must not raise or double-register
    assert hass.services.has_service(DOMAIN, SERVICE_SET_INDICATOR_LED)
    async_unload_services(hass)


async def test_unload_removes_services_when_no_entries_loaded(hass):
    async_setup_services(hass)
    with patch.object(hass.config_entries, "async_loaded_entries", return_value=[]):
        async_unload_services(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_INDICATOR_LED)
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_MOTION_SENSOR_SETTINGS)
    assert not hass.services.has_service(DOMAIN, SERVICE_EXECUTE_SCENE)
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_GROUP_POWER)
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_MOTION_SENSOR_SCHEDULE)
    assert not hass.services.has_service(DOMAIN, SERVICE_DELETE_SCENE)
    assert not hass.services.has_service(DOMAIN, SERVICE_DELETE_SCHEDULE)
    assert not hass.services.has_service(DOMAIN, SERVICE_TOGGLE_AUTOMATION)
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_GROUP_MEMBERSHIP)


async def test_unload_keeps_services_when_other_entries_still_loaded(hass):
    async_setup_services(hass)
    with patch.object(
        hass.config_entries, "async_loaded_entries", return_value=[MagicMock(), MagicMock()]
    ):
        async_unload_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_INDICATOR_LED)
    async_unload_services(hass)  # actually clean up now


async def test_set_indicator_led_calls_node_method(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_INDICATOR_LED,
        {
            "device_id": device.id,
            "mode": "normal",
            "color": "red",
            "brightness": 80,
            "wifi_disconnect_blink": True,
        },
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.set_indicator_led.assert_awaited_once_with(
        mode=2, color=1, brightness=80, wifi_disconnect_blink=True
    )
    async_unload_services(hass)


async def test_set_indicator_led_updates_shared_bridge_cache(hass):
    """A service call and the 4 indicator-LED entities (select.py/number.py/
    switch.py) must converge on the same cached state, not diverge - proves
    _handle_set_indicator_led routes through bridge.set_indicator_led_field
    rather than calling node.set_indicator_led() directly."""
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_INDICATOR_LED,
        {
            "device_id": device.id,
            "mode": "always_off",
            "color": "green",
            "brightness": 33,
            "wifi_disconnect_blink": True,
        },
        blocking=True,
    )

    cached = entry.runtime_data.bridge.get_indicator_led(5)
    assert cached.mode == "always_off"
    assert cached.color == "green"
    assert cached.brightness == 33
    assert cached.wifi_disconnect_blink is True
    async_unload_services(hass)


async def test_set_motion_sensor_settings_calls_node_method(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_MOTION_SENSOR_SETTINGS,
        {
            "device_id": device.id,
            "sensor_type": "motion",
            "enabled": True,
        },
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.set_motion_sensor_settings.assert_awaited_once_with(
        setting_type=1, enabled=True, sensitivity=None, delay_seconds=0,
        deactivation_seconds=0,
    )
    async_unload_services(hass)


async def test_set_motion_sensor_settings_maps_sensitivity(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_MOTION_SENSOR_SETTINGS,
        {
            "device_id": device.id,
            "sensor_type": "ambient_light",
            "sensitivity": "low",
            "delay_seconds": 30,
            "deactivation_seconds": 60,
        },
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.set_motion_sensor_settings.assert_awaited_once_with(
        setting_type=2, enabled=None, sensitivity=2, delay_seconds=30,
        deactivation_seconds=60,
    )
    async_unload_services(hass)


async def test_resolve_device_raises_for_unknown_device_id(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_INDICATOR_LED,
            {
                "device_id": "does-not-exist",
                "mode": "normal",
                "color": "white",
                "brightness": 50,
            },
            blocking=True,
        )
    async_unload_services(hass)


async def test_resolve_device_raises_for_device_from_other_integration(hass):
    from homeassistant.helpers import device_registry as dr
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    other_entry = MockConfigEntry(domain="other_integration")
    other_entry.add_to_hass(hass)
    device_reg = dr.async_get(hass)
    other_device = device_reg.async_get_or_create(
        config_entry_id=other_entry.entry_id,
        identifiers={("other_integration", "some-device")},
        name="Other Device",
    )

    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_INDICATOR_LED,
            {
                "device_id": other_device.id,
                "mode": "normal",
                "color": "white",
                "brightness": 50,
            },
            blocking=True,
        )
    async_unload_services(hass)


async def test_execute_scene_requires_bridge_device(hass):
    """Passing an individual device (not the bridge) must raise, not
    silently try to activate a scene through the wrong target."""
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXECUTE_SCENE,
            {"device_id": device.id, "scene_id": 3},
            blocking=True,
        )
    async_unload_services(hass)


async def test_execute_scene_calls_execute_scene_with_bridge_device(hass):
    entry = _make_entry(hass)
    bridge_device = _register_bridge_device(hass, entry)
    async_setup_services(hass)

    with patch(
        "cync_lan.devices.execute_scene", new=AsyncMock()
    ) as mock_execute_scene:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXECUTE_SCENE,
            {"device_id": bridge_device.id, "scene_id": 3},
            blocking=True,
        )
    mock_execute_scene.assert_awaited_once_with(3)
    async_unload_services(hass)


async def test_execute_scene_raises_for_unknown_device_id(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXECUTE_SCENE,
            {"device_id": "does-not-exist", "scene_id": 3},
            blocking=True,
        )
    async_unload_services(hass)


async def test_set_group_power_requires_bridge_device(hass):
    """Same shape as execute_scene: groups are addressed home-wide via the
    group's own mesh address, not through an individual CyncDevice."""
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_GROUP_POWER,
            {"device_id": device.id, "group_id": 32770, "state": True},
            blocking=True,
        )
    async_unload_services(hass)


async def test_set_group_power_calls_set_group_power_with_bridge_device(hass):
    entry = _make_entry(hass)
    bridge_device = _register_bridge_device(hass, entry)
    async_setup_services(hass)

    with patch(
        "cync_lan.devices.set_group_power", new=AsyncMock()
    ) as mock_set_group_power:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_GROUP_POWER,
            {"device_id": bridge_device.id, "group_id": 32770, "state": True},
            blocking=True,
        )
    mock_set_group_power.assert_awaited_once_with(32770, 1)
    async_unload_services(hass)


async def test_set_group_power_maps_state_off(hass):
    entry = _make_entry(hass)
    bridge_device = _register_bridge_device(hass, entry)
    async_setup_services(hass)

    with patch(
        "cync_lan.devices.set_group_power", new=AsyncMock()
    ) as mock_set_group_power:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_GROUP_POWER,
            {"device_id": bridge_device.id, "group_id": 32770, "state": False},
            blocking=True,
        )
    mock_set_group_power.assert_awaited_once_with(32770, 0)
    async_unload_services(hass)


async def test_set_group_power_raises_for_unknown_device_id(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_GROUP_POWER,
            {"device_id": "does-not-exist", "group_id": 32770, "state": True},
            blocking=True,
        )
    async_unload_services(hass)


async def test_set_motion_sensor_schedule_calls_node_method_with_cct(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_MOTION_SENSOR_SCHEDULE,
        {
            "device_id": device.id,
            "slot": "daytime",
            "mode": "simple",
            "start_hour": 6,
            "start_minute": 30,
            "end_hour": 18,
            "end_minute": 0,
            "brightness": 80,
            "cct": 50,
        },
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.set_motion_sensor_schedule.assert_awaited_once_with(
        slot_id=1, mode=3, start_hour=6, start_minute=30, end_hour=18,
        end_minute=0, brightness=80, cct=50, rgb=None,
    )
    async_unload_services(hass)


async def test_set_motion_sensor_schedule_calls_node_method_with_rgb(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_MOTION_SENSOR_SCHEDULE,
        {
            "device_id": device.id,
            "slot": "sleep",
            "mode": "disabled",
            "start_hour": 22,
            "start_minute": 0,
            "end_hour": 5,
            "end_minute": 59,
            "brightness": 10,
            "rgb": [255, 128, 0],
        },
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.set_motion_sensor_schedule.assert_awaited_once_with(
        slot_id=3, mode=0, start_hour=22, start_minute=0, end_hour=5,
        end_minute=59, brightness=10, cct=None, rgb=(255, 128, 0),
    )
    async_unload_services(hass)


async def test_set_motion_sensor_schedule_raises_for_unknown_device_id(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_MOTION_SENSOR_SCHEDULE,
            {
                "device_id": "does-not-exist",
                "slot": "morning",
                "mode": "simple",
                "start_hour": 0,
                "start_minute": 0,
                "end_hour": 0,
                "end_minute": 0,
                "brightness": 50,
                "cct": 50,
            },
            blocking=True,
        )
    async_unload_services(hass)


async def test_delete_scene_requires_bridge_device(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_SCENE,
            {"device_id": device.id, "scene_id": 300},
            blocking=True,
        )
    async_unload_services(hass)


async def test_delete_scene_calls_delete_scene_with_bridge_device(hass):
    entry = _make_entry(hass)
    bridge_device = _register_bridge_device(hass, entry)
    async_setup_services(hass)

    with patch(
        "cync_lan.devices.delete_scene", new=AsyncMock()
    ) as mock_delete_scene:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_SCENE,
            {"device_id": bridge_device.id, "scene_id": 300},
            blocking=True,
        )
    mock_delete_scene.assert_awaited_once_with(300)
    async_unload_services(hass)


async def test_delete_scene_raises_for_unknown_device_id(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_SCENE,
            {"device_id": "does-not-exist", "scene_id": 300},
            blocking=True,
        )
    async_unload_services(hass)


async def test_delete_schedule_requires_bridge_device(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_SCHEDULE,
            {"device_id": device.id, "schedule_id": 12},
            blocking=True,
        )
    async_unload_services(hass)


async def test_delete_schedule_calls_delete_schedule_with_bridge_device(hass):
    entry = _make_entry(hass)
    bridge_device = _register_bridge_device(hass, entry)
    async_setup_services(hass)

    with patch(
        "cync_lan.devices.delete_schedule", new=AsyncMock()
    ) as mock_delete_schedule:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_SCHEDULE,
            {"device_id": bridge_device.id, "schedule_id": 12},
            blocking=True,
        )
    mock_delete_schedule.assert_awaited_once_with(12)
    async_unload_services(hass)


async def test_delete_schedule_raises_for_unknown_device_id(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_SCHEDULE,
            {"device_id": "does-not-exist", "schedule_id": 12},
            blocking=True,
        )
    async_unload_services(hass)


async def test_toggle_automation_requires_bridge_device(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_TOGGLE_AUTOMATION,
            {
                "device_id": device.id,
                "schedule_id": 12,
                "scene_id": 300,
                "enabled": True,
            },
            blocking=True,
        )
    async_unload_services(hass)


async def test_toggle_automation_calls_toggle_automation_with_bridge_device(hass):
    entry = _make_entry(hass)
    bridge_device = _register_bridge_device(hass, entry)
    async_setup_services(hass)

    with patch(
        "cync_lan.devices.toggle_automation", new=AsyncMock()
    ) as mock_toggle_automation:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_TOGGLE_AUTOMATION,
            {
                "device_id": bridge_device.id,
                "schedule_id": 12,
                "scene_id": 300,
                "enabled": False,
            },
            blocking=True,
        )
    mock_toggle_automation.assert_awaited_once_with(12, 300, False)
    async_unload_services(hass)


async def test_toggle_automation_raises_for_unknown_device_id(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_TOGGLE_AUTOMATION,
            {
                "device_id": "does-not-exist",
                "schedule_id": 12,
                "scene_id": 300,
                "enabled": True,
            },
            blocking=True,
        )
    async_unload_services(hass)


async def test_set_group_membership_targets_individual_device(hass):
    """Unlike execute_scene/set_group_power/delete_*/toggle_automation,
    this command targets one device (the one joining/leaving), not the
    bridge - must be reachable via a normal device_id, and must raise if
    the bridge device is passed instead."""
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_GROUP_MEMBERSHIP,
        {"device_id": device.id, "group_id": 32770, "member": True},
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.set_group_membership.assert_awaited_once_with(
        32770, member=True, reach_flag=0x00
    )
    async_unload_services(hass)


async def test_set_group_membership_maps_reach_flag(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_GROUP_MEMBERSHIP,
        {
            "device_id": device.id,
            "group_id": 32770,
            "member": False,
            "reach_flag": "receive_only",
        },
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.set_group_membership.assert_awaited_once_with(
        32770, member=False, reach_flag=0x87
    )
    async_unload_services(hass)


async def test_set_group_membership_raises_for_bridge_device(hass):
    entry = _make_entry(hass)
    bridge_device = _register_bridge_device(hass, entry)
    async_setup_services(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_GROUP_MEMBERSHIP,
            {"device_id": bridge_device.id, "group_id": 32770, "member": True},
            blocking=True,
        )
    async_unload_services(hass)


async def test_set_group_membership_raises_for_unknown_device_id(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_GROUP_MEMBERSHIP,
            {"device_id": "does-not-exist", "group_id": 32770, "member": True},
            blocking=True,
        )
    async_unload_services(hass)


def _automation_config(
    *,
    at="07:30:00",
    trigger_extra=None,
    weekday=None,
    condition_extra=None,
    no_condition_key=False,
    actions=None,
    trigger_platform="time",
):
    """Builds a raw automation config dict shaped like AutomationEntity.raw_config
    (pre schema-normalization - "trigger"/"condition"/"action", singular,
    exactly as a user would author it) for
    _extract_time_trigger/_extract_day_mask/_extract_scene_actions to validate."""
    trigger = {"trigger": trigger_platform, "at": at}
    if trigger_extra:
        trigger.update(trigger_extra)
    config = {
        "alias": "Test Automation",
        "trigger": [trigger],
        "action": actions if actions is not None else [],
    }
    if not no_condition_key and weekday is not None:
        condition = {"condition": "time", "weekday": weekday}
        if condition_extra:
            condition.update(condition_extra)
        config["condition"] = [condition]
    return config


async def test_push_automation_creates_scene_schedule_and_automation_rgb(hass):
    entry = _make_entry(hass, dev_ids=[5])
    entity_id = _register_light_entity(hass, entry, 5)
    async_setup_services(hass)

    config = _automation_config(
        at="07:30:15",
        weekday=["mon", "wed"],
        actions=[
            {
                "action": "light.turn_on",
                "target": {"entity_id": entity_id},
                "data": {"rgb_color": [255, 0, 0]},
            }
        ],
    )
    _register_automation_entity(hass, "automation.test", config)

    with (
        patch("cync_lan.devices.create_scene", new=AsyncMock(return_value=42)) as mock_create_scene,
        patch("cync_lan.devices.create_schedule", new=AsyncMock(return_value=99)) as mock_create_schedule,
        patch("cync_lan.devices.add_automation", new=AsyncMock()) as mock_add_automation,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )

    mock_create_scene.assert_awaited_once_with("Test Automation")
    node = entry.runtime_data.ncync_server.node_devices[5]
    node.add_to_scene.assert_awaited_once_with(42, cct=None, rgb=(255, 0, 0))
    mock_create_schedule.assert_awaited_once_with(42)
    mock_add_automation.assert_awaited_once_with(99, 42, 0x02 | 0x08, 7, 30, 15)
    async_unload_services(hass)


async def test_push_automation_supports_color_temp_kelvin(hass):
    entry = _make_entry(hass, dev_ids=[5])
    entity_id = _register_light_entity(hass, entry, 5)
    async_setup_services(hass)

    config = _automation_config(
        actions=[
            {
                "action": "light.turn_on",
                "target": {"entity_id": entity_id},
                "data": {"color_temp_kelvin": 50},
            }
        ],
    )
    _register_automation_entity(hass, "automation.test", config)

    with (
        patch("cync_lan.devices.create_scene", new=AsyncMock(return_value=1)),
        patch("cync_lan.devices.create_schedule", new=AsyncMock(return_value=2)),
        patch("cync_lan.devices.add_automation", new=AsyncMock()),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.add_to_scene.assert_awaited_once_with(1, cct=50, rgb=None)
    async_unload_services(hass)


async def test_push_automation_defaults_to_all_days_when_no_condition(hass):
    entry = _make_entry(hass, dev_ids=[5])
    entity_id = _register_light_entity(hass, entry, 5)
    async_setup_services(hass)

    config = _automation_config(
        actions=[
            {
                "action": "light.turn_on",
                "target": {"entity_id": entity_id},
                "data": {"rgb_color": [1, 2, 3]},
            }
        ],
    )
    _register_automation_entity(hass, "automation.test", config)

    with (
        patch("cync_lan.devices.create_scene", new=AsyncMock(return_value=1)),
        patch("cync_lan.devices.create_schedule", new=AsyncMock(return_value=2)),
        patch("cync_lan.devices.add_automation", new=AsyncMock()) as mock_add_automation,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )

    mock_add_automation.assert_awaited_once_with(2, 1, 0x7F, 7, 30, 0)
    async_unload_services(hass)


async def test_push_automation_expands_multiple_entity_ids_in_one_action(hass):
    entry = _make_entry(hass, dev_ids=[5, 6])
    entity_id_5 = _register_light_entity(hass, entry, 5, name="Light Five")
    entity_id_6 = _register_light_entity(hass, entry, 6, name="Light Six")
    async_setup_services(hass)

    config = _automation_config(
        actions=[
            {
                "action": "light.turn_on",
                "target": {"entity_id": [entity_id_5, entity_id_6]},
                "data": {"rgb_color": [10, 20, 30]},
            }
        ],
    )
    _register_automation_entity(hass, "automation.test", config)

    with (
        patch("cync_lan.devices.create_scene", new=AsyncMock(return_value=1)),
        patch("cync_lan.devices.create_schedule", new=AsyncMock(return_value=2)),
        patch("cync_lan.devices.add_automation", new=AsyncMock()),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )

    entry.runtime_data.ncync_server.node_devices[5].add_to_scene.assert_awaited_once_with(
        1, cct=None, rgb=(10, 20, 30)
    )
    entry.runtime_data.ncync_server.node_devices[6].add_to_scene.assert_awaited_once_with(
        1, cct=None, rgb=(10, 20, 30)
    )
    async_unload_services(hass)


async def test_push_automation_raises_for_unknown_automation_entity(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.does_not_exist"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_raises_when_raw_config_empty(hass):
    async_setup_services(hass)
    _register_automation_entity(hass, "automation.test", {})

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_multiple_triggers(hass):
    async_setup_services(hass)
    config = _automation_config()
    config["trigger"].append({"trigger": "time", "at": "08:00:00"})
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="exactly one trigger"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_non_time_trigger(hass):
    async_setup_services(hass)
    config = _automation_config(trigger_platform="state")
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="not 'time'"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_entity_referenced_at_value(hass):
    async_setup_services(hass)
    config = _automation_config()
    config["trigger"][0]["at"] = "input_datetime.wake_up"
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="references an entity"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_multiple_conditions(hass):
    async_setup_services(hass)
    config = _automation_config(weekday=["mon"])
    config["condition"].append({"condition": "time", "weekday": ["tue"]})
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="zero or one condition"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_non_time_condition(hass):
    async_setup_services(hass)
    config = _automation_config()
    config["condition"] = [{"condition": "state", "entity_id": "sun.sun", "state": "above_horizon"}]
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="not 'time'"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_time_range_condition(hass):
    async_setup_services(hass)
    config = _automation_config(weekday=["mon"], condition_extra={"after": "06:00:00"})
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="unsupported option"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_empty_weekday(hass):
    async_setup_services(hass)
    config = _automation_config(weekday=[])
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="no days selected"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_non_light_turn_on_action(hass):
    async_setup_services(hass)
    config = _automation_config(actions=[{"action": "light.turn_off", "target": {"entity_id": "light.x"}}])
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="light.turn_on"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_action_with_no_color(hass):
    entry = _make_entry(hass, dev_ids=[5])
    entity_id = _register_light_entity(hass, entry, 5)
    async_setup_services(hass)

    config = _automation_config(
        actions=[{"action": "light.turn_on", "target": {"entity_id": entity_id}, "data": {}}]
    )
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="no rgb_color or color_temp_kelvin"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_action_with_both_colors(hass):
    entry = _make_entry(hass, dev_ids=[5])
    entity_id = _register_light_entity(hass, entry, 5)
    async_setup_services(hass)

    config = _automation_config(
        actions=[
            {
                "action": "light.turn_on",
                "target": {"entity_id": entity_id},
                "data": {"rgb_color": [1, 2, 3], "color_temp_kelvin": 50},
            }
        ]
    )
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="only be one or the other"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_action_with_brightness(hass):
    entry = _make_entry(hass, dev_ids=[5])
    entity_id = _register_light_entity(hass, entry, 5)
    async_setup_services(hass)

    config = _automation_config(
        actions=[
            {
                "action": "light.turn_on",
                "target": {"entity_id": entity_id},
                "data": {"rgb_color": [1, 2, 3], "brightness": 128},
            }
        ]
    )
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="brightness"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_light_group_target(hass):
    entry = _make_entry(hass, dev_ids=[5])
    entity_id = _register_light_group_entity(hass, entry, 32770)
    async_setup_services(hass)

    config = _automation_config(
        actions=[
            {
                "action": "light.turn_on",
                "target": {"entity_id": entity_id},
                "data": {"rgb_color": [1, 2, 3]},
            }
        ]
    )
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="light group"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_non_cync_entity_target(hass):
    async_setup_services(hass)
    config = _automation_config(
        actions=[
            {
                "action": "light.turn_on",
                "target": {"entity_id": "light.some_other_integration"},
                "data": {"rgb_color": [1, 2, 3]},
            }
        ]
    )
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="not a Cync LAN entity"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_raises_home_assistant_error_when_scene_creation_times_out(hass):
    entry = _make_entry(hass, dev_ids=[5])
    entity_id = _register_light_entity(hass, entry, 5)
    async_setup_services(hass)

    config = _automation_config(
        actions=[
            {
                "action": "light.turn_on",
                "target": {"entity_id": entity_id},
                "data": {"rgb_color": [1, 2, 3]},
            }
        ]
    )
    _register_automation_entity(hass, "automation.test", config)

    with patch("cync_lan.devices.create_scene", new=AsyncMock(return_value=None)):
        with pytest.raises(HomeAssistantError, match="did not respond"):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
                {"automation_entity_id": "automation.test"},
                blocking=True,
            )
    async_unload_services(hass)


async def test_push_automation_raises_home_assistant_error_when_schedule_creation_times_out(hass):
    entry = _make_entry(hass, dev_ids=[5])
    entity_id = _register_light_entity(hass, entry, 5)
    async_setup_services(hass)

    config = _automation_config(
        actions=[
            {
                "action": "light.turn_on",
                "target": {"entity_id": entity_id},
                "data": {"rgb_color": [1, 2, 3]},
            }
        ]
    )
    _register_automation_entity(hass, "automation.test", config)

    with (
        patch("cync_lan.devices.create_scene", new=AsyncMock(return_value=7)),
        patch("cync_lan.devices.create_schedule", new=AsyncMock(return_value=None)),
    ):
        with pytest.raises(HomeAssistantError, match="scene_id=7"):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
                {"automation_entity_id": "automation.test"},
                blocking=True,
            )
    async_unload_services(hass)


async def test_add_device_to_scene_calls_node_method_with_cct(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_DEVICE_TO_SCENE,
        {"device_id": device.id, "scene_id": 3, "cct": 80},
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.add_to_scene.assert_awaited_once_with(3, cct=80, rgb=None, fade=0xFF)
    async_unload_services(hass)


async def test_add_device_to_scene_calls_node_method_with_rgb_and_fade(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_DEVICE_TO_SCENE,
        {
            "device_id": device.id,
            "scene_id": 3,
            "rgb": [255, 128, 0],
            "fade": "10_seconds",
        },
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.add_to_scene.assert_awaited_once_with(3, cct=None, rgb=(255, 128, 0), fade=1)
    async_unload_services(hass)


async def test_add_device_to_scene_raises_for_unknown_device_id(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_DEVICE_TO_SCENE,
            {"device_id": "does-not-exist", "scene_id": 3, "cct": 80},
            blocking=True,
        )
    async_unload_services(hass)


async def test_remove_device_from_scene_calls_node_method(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_DEVICE_FROM_SCENE,
        {"device_id": device.id, "scene_id": 3},
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.remove_from_scene.assert_awaited_once_with(3)
    async_unload_services(hass)


async def test_remove_device_from_scene_raises_for_unknown_device_id(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REMOVE_DEVICE_FROM_SCENE,
            {"device_id": "does-not-exist", "scene_id": 3},
            blocking=True,
        )
    async_unload_services(hass)


async def test_set_multicolor_gradient_mode_calls_node_method(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_MULTICOLOR_GRADIENT_MODE,
        {"device_id": device.id, "enabled": True},
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.set_multicolor_gradient_mode.assert_awaited_once_with(True)
    async_unload_services(hass)


async def test_set_multicolor_gradient_mode_raises_for_unknown_device_id(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_MULTICOLOR_GRADIENT_MODE,
            {"device_id": "does-not-exist", "enabled": True},
            blocking=True,
        )
    async_unload_services(hass)


async def test_set_multicolor_segment_count_calls_node_method(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_MULTICOLOR_SEGMENT_COUNT,
        {"device_id": device.id, "count": 12},
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.set_multicolor_segment_count.assert_awaited_once_with(12)
    async_unload_services(hass)


async def test_set_multicolor_segment_count_raises_for_unknown_device_id(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_MULTICOLOR_SEGMENT_COUNT,
            {"device_id": "does-not-exist", "count": 12},
            blocking=True,
        )
    async_unload_services(hass)


async def test_set_multicolor_segments_calls_node_method_with_both_slots(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_MULTICOLOR_SEGMENTS,
        {
            "device_id": device.id,
            "segment_1_position": 1,
            "segment_1_rgb": [255, 0, 0],
            "segment_2_position": 2,
            "segment_2_rgb": [0, 255, 0],
        },
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.set_multicolor_segments.assert_awaited_once_with(
        [(1, (255, 0, 0)), (2, (0, 255, 0))]
    )
    async_unload_services(hass)


async def test_set_multicolor_segments_calls_node_method_with_one_slot(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_MULTICOLOR_SEGMENTS,
        {"device_id": device.id, "segment_1_position": 1, "segment_1_rgb": [255, 0, 0]},
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.set_multicolor_segments.assert_awaited_once_with([(1, (255, 0, 0))])
    async_unload_services(hass)


async def test_set_multicolor_segments_position_without_rgb_is_valid(hass):
    """Position and color are independently optional on the wire (see
    CyncDevice.set_multicolor_segments()'s docstring) - a position-only
    slot must be passed through, not rejected."""
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_MULTICOLOR_SEGMENTS,
        {"device_id": device.id, "segment_1_position": 7},
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.set_multicolor_segments.assert_awaited_once_with([(7, None)])
    async_unload_services(hass)


async def test_set_multicolor_segments_raises_when_no_segment_given(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_MULTICOLOR_SEGMENTS,
            {"device_id": device.id},
            blocking=True,
        )
    async_unload_services(hass)


async def test_set_multicolor_segments_raises_for_unknown_device_id(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_MULTICOLOR_SEGMENTS,
            {"device_id": "does-not-exist", "segment_1_position": 1, "segment_1_rgb": [0, 0, 0]},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_trigger_with_unsupported_option(hass):
    async_setup_services(hass)
    config = _automation_config(trigger_extra={"for": {"minutes": 5}})
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="unsupported option"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_multiple_at_times(hass):
    async_setup_services(hass)
    config = _automation_config()
    config["trigger"][0]["at"] = ["07:00:00", "19:00:00"]
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="multiple times"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_unrecognized_weekday(hass):
    async_setup_services(hass)
    config = _automation_config(weekday=["someday"])
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="Unrecognized weekday"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_no_actions(hass):
    async_setup_services(hass)
    config = _automation_config(actions=[])
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="no actions"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


async def test_push_automation_rejects_action_missing_entity_id(hass):
    async_setup_services(hass)
    config = _automation_config(
        actions=[{"action": "light.turn_on", "data": {"rgb_color": [1, 2, 3]}}]
    )
    _register_automation_entity(hass, "automation.test", config)

    with pytest.raises(ServiceValidationError, match="no target entity_id"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PUSH_AUTOMATION_TO_HARDWARE,
            {"automation_entity_id": "automation.test"},
            blocking=True,
        )
    async_unload_services(hass)


# ---------------------------------------------------------------------------
# The experimental opt-in gate
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _loaded_entry(hass, *, experimental: bool):
    """Yield an entry that experimental_enabled() will see as loaded.

    async_loaded_entries is patched rather than the entry being marked
    LOADED for real: a real LOADED MockConfigEntry makes HA run the actual
    async_unload_entry at teardown, which expects a full CyncLanRuntimeData
    rather than the stub these tests use.
    """
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.cync_lan.const import CONF_ENABLE_EXPERIMENTAL

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        options={CONF_ENABLE_EXPERIMENTAL: experimental},
    )
    entry.add_to_hass(hass)
    with patch.object(
        hass.config_entries, "async_loaded_entries", return_value=[entry]
    ):
        yield entry


@pytest.mark.no_experimental_optin
async def test_experimental_gate_registers_nothing_unproven_by_default(hass):
    """Off by default: these send mesh commands whose cmd_code is predicted
    rather than confirmed, so they must not appear in the action picker until
    the user asks for them.

    set_indicator_led is deliberately exempt - it is confirmed working on real
    hardware, so the gate has nothing to protect anyone from."""
    with _loaded_entry(hass, experimental=False):
        async_setup_services(hass)

        assert not hass.services.has_service(DOMAIN, SERVICE_EXECUTE_SCENE)
        assert not hass.services.has_service(
            DOMAIN, SERVICE_PUSH_AUTOMATION_TO_HARDWARE
        )
        # ...but the confirmed one is available without opting in.
        assert hass.services.has_service(DOMAIN, SERVICE_SET_INDICATOR_LED)


@pytest.mark.no_experimental_optin
async def test_indicator_led_legacy_name_still_works(hass):
    """The service was called experimental_set_indicator_led for several
    releases, so it is in people's automations. Renaming without an alias
    would break them silently - the failure being a light that does not come
    on, which nobody traces back to a service rename."""
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    async_setup_services(hass)

    assert hass.services.has_service(DOMAIN, "experimental_set_indicator_led")

    await hass.services.async_call(
        DOMAIN,
        "experimental_set_indicator_led",
        {"device_id": device.id, "mode": "always_on", "color": "green",
         "brightness": 50},
        blocking=True,
    )
    cached = entry.runtime_data.bridge.get_indicator_led(5)
    assert cached.color == "green"
    async_unload_services(hass)


@pytest.mark.no_experimental_optin
async def test_experimental_gate_registers_when_opted_in(hass):
    with _loaded_entry(hass, experimental=True):
        async_setup_services(hass)

        assert hass.services.has_service(DOMAIN, SERVICE_SET_INDICATOR_LED)
        assert hass.services.has_service(DOMAIN, SERVICE_EXECUTE_SCENE)
        assert hass.services.has_service(DOMAIN, SERVICE_QUERY_MESH_CREDENTIALS)
    _async_remove_services(hass)


@pytest.mark.no_experimental_optin
async def test_experimental_gate_removes_services_when_turned_back_off(hass):
    """Toggling the option off must take effect without a reload - the
    options flow calls async_setup_services again after saving."""
    from custom_components.cync_lan.const import CONF_ENABLE_EXPERIMENTAL

    with _loaded_entry(hass, experimental=True) as entry:
        async_setup_services(hass)
        assert hass.services.has_service(DOMAIN, SERVICE_EXECUTE_SCENE)

        hass.config_entries.async_update_entry(
            entry, options={CONF_ENABLE_EXPERIMENTAL: False}
        )
        async_setup_services(hass)

        assert not hass.services.has_service(DOMAIN, SERVICE_EXECUTE_SCENE)


@pytest.mark.no_experimental_optin
async def test_query_mesh_credentials_returns_response_data(hass):
    """The password is the mesh's shared secret, so it comes back as action
    response data rather than being logged."""
    ctx = _loaded_entry(hass, experimental=True)
    entry = ctx.__enter__()
    entry.runtime_data = SimpleNamespace(
        ncync_server=SimpleNamespace(node_devices={}),
        bridge=CyncLanBridge(hass, entry.entry_id),
    )
    bridge_device = _register_bridge_device(hass, entry)
    async_setup_services(hass)

    with patch(
        "cync_lan.devices.query_hub_mesh_credentials",
        new=AsyncMock(return_value=("my_mesh", "s3cret")),
    ):
        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_QUERY_MESH_CREDENTIALS,
            {"device_id": bridge_device.id},
            blocking=True,
            return_response=True,
        )

    assert result == {"mesh_name": "my_mesh", "mesh_password": "s3cret"}
    ctx.__exit__(None, None, None)
    _async_remove_services(hass)


@pytest.mark.no_experimental_optin
async def test_query_mesh_credentials_raises_on_timeout(hass):
    ctx = _loaded_entry(hass, experimental=True)
    entry = ctx.__enter__()
    entry.runtime_data = SimpleNamespace(
        ncync_server=SimpleNamespace(node_devices={}),
        bridge=CyncLanBridge(hass, entry.entry_id),
    )
    bridge_device = _register_bridge_device(hass, entry)
    async_setup_services(hass)

    with patch(
        "cync_lan.devices.query_hub_mesh_credentials",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_QUERY_MESH_CREDENTIALS,
                {"device_id": bridge_device.id},
                blocking=True,
                return_response=True,
            )
    ctx.__exit__(None, None, None)
    _async_remove_services(hass)


async def test_motion_sensor_settings_refused_while_device_is_asleep(hass):
    """Battery sensors only join the mesh while awake, and the real Cync app's
    own writeSettings returns a fake success without transmitting when the
    target is offline. Reproducing that silent no-op is the most confusing
    failure this integration can produce, so refuse instead."""
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    entry.runtime_data.bridge._set_online(5, False)
    async_setup_services(hass)

    with pytest.raises(ServiceValidationError, match="asleep"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_MOTION_SENSOR_SETTINGS,
            {"device_id": device.id, "sensor_type": "motion", "enabled": True},
            blocking=True,
        )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.set_motion_sensor_settings.assert_not_awaited()


async def test_motion_sensor_schedule_refused_while_device_is_asleep(hass):
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    entry.runtime_data.bridge._set_online(5, False)
    async_setup_services(hass)

    with pytest.raises(ServiceValidationError, match="asleep"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_MOTION_SENSOR_SCHEDULE,
            {
                "device_id": device.id,
                "slot": "morning",
                "mode": "occupancy",
                "start_hour": 8,
                "start_minute": 0,
                "end_hour": 9,
                "end_minute": 0,
                "brightness": 50,
            },
            blocking=True,
        )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.set_motion_sensor_schedule.assert_not_awaited()


async def test_motion_sensor_write_proceeds_once_the_device_is_awake(hass):
    """The gate must not be a one-way door - waking the device has to make the
    same call go through, which is what the LED-turns-green step achieves."""
    entry = _make_entry(hass, dev_ids=[5])
    device = _register_device(hass, entry, 5)
    entry.runtime_data.bridge._set_online(5, False)
    async_setup_services(hass)

    entry.runtime_data.bridge._set_online(5, True)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_MOTION_SENSOR_SETTINGS,
        {"device_id": device.id, "sensor_type": "motion", "enabled": True},
        blocking=True,
    )

    node = entry.runtime_data.ncync_server.node_devices[5]
    node.set_motion_sensor_settings.assert_awaited_once()
    async_unload_services(hass)
