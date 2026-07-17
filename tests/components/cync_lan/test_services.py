"""Tests for services.py's 4 experimental_* services."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.cync_lan.bridge import CyncLanBridge
from custom_components.cync_lan.const import DOMAIN
from custom_components.cync_lan.services import (
    SERVICE_EXECUTE_SCENE,
    SERVICE_SET_GROUP_POWER,
    SERVICE_SET_INDICATOR_LED,
    SERVICE_SET_MOTION_SENSOR_SETTINGS,
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


def _make_entry(hass, dev_ids: list[int] = ()):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, unique_id="user@example.com")
    entry.add_to_hass(hass)
    nodes = {dev_id: MagicMock(id=dev_id) for dev_id in dev_ids}
    for node in nodes.values():
        node.set_indicator_led = AsyncMock()
        node.set_motion_sensor_settings = AsyncMock()
    entry.runtime_data = SimpleNamespace(
        ncync_server=SimpleNamespace(node_devices=nodes),
        bridge=CyncLanBridge(hass, entry.entry_id),
    )
    return entry


async def test_setup_registers_all_four_services(hass):
    async_setup_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_INDICATOR_LED)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_MOTION_SENSOR_SETTINGS)
    assert hass.services.has_service(DOMAIN, SERVICE_EXECUTE_SCENE)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_GROUP_POWER)
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
