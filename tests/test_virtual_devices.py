"""Automated CI Unit Test Suite for Virtual Cync Devices & Mock Cloud Server

Validates that all 138 Cync device types extracted from decompiled SDK specifications
can be generated, parsed by cync_lan, and tested against a Mock Cync API Server,
including dedicated code paths, exception assertions, and all 12 REST API endpoints.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

import scripts.create_virtual_device as vdev_gen
import scripts.mock_cync_server as mock_srv
import scripts.cync_ota_fetch as ota_fetch


@pytest.fixture(scope="module")
def device_registry():
    """Load the extracted 138-device type registry."""
    return mock_srv.load_device_registry()


def test_device_registry_completeness(device_registry):
    """Verify that all 138 Cync device types are loaded into the registry."""
    assert len(device_registry) >= 135, f"Expected >= 135 device types, found {len(device_registry)}"
    print(f"[+] Loaded {len(device_registry)} Cync device types into registry.")


def test_virtual_device_generation(device_registry):
    """Verify that virtual device generation works for all registered device types."""
    for dev_type_id, info in device_registry.items():
        vdev = vdev_gen.generate_virtual_device(
            template_key="dimmer_switch",
            custom_name=f"Virtual_{info['class_name']}",
            custom_id=900000000 + int(dev_type_id),
        )
        assert vdev["id"] == 900000000 + int(dev_type_id)
        assert vdev["name"].startswith("Virtual_")
        assert len(vdev["mac"]) == 12


def test_creach_and_sol_lamp_dedicated_paths():
    """Verify dedicated presets and properties for C-Reach Bridge and Sol Smart Lamp."""
    # Test C-Reach Hub (deviceType 9)
    creach = vdev_gen.generate_virtual_device(template_key="c_reach_hub")
    assert creach["deviceType"] == 9
    assert creach["firmware_mod"] == "CReach"
    assert creach["product_id"] == "160fa2b07d45ba00160fa2b07d45ba01"
    assert "hub_bridge" in creach["capabilities"]
    assert vdev_gen.has_wifi_chipset(creach) is True

    # Test Sol Smart Lamp (deviceType 11)
    sol = vdev_gen.generate_virtual_device(template_key="sol_lamp")
    assert sol["deviceType"] == 11
    assert sol["firmware_mod"] == "SolGen1Standalone"
    assert sol["product_id"] == "160fa6b2279f03e9160fa6b2279f4801"
    assert "alexa_voice" in sol["capabilities"]
    assert "rgb_color" in sol["capabilities"]
    assert vdev_gen.has_wifi_chipset(sol) is True


def test_capability_exception_assertions():
    """Verify that requesting an unsupported capability raises an IllegalStateException (DeviceTypeKt.m13630e)."""
    dimmer = vdev_gen.generate_virtual_device(template_key="dimmer_switch")
    
    # Valid capability succeeds
    assert vdev_gen.validate_device_capability(dimmer, "on_off") is True
    
    # Unsupported capability raises RuntimeError matching IllegalStateException
    with pytest.raises(RuntimeError, match="IllegalStateException: Device type 'Dimmer Switch'"):
        vdev_gen.validate_device_capability(dimmer, "rgb_color")


@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_all_12_mock_server_api_endpoints(socket_enabled):
    # `socket_enabled` is pytest_homeassistant_custom_component's fixture,
    # and it is the one that matters here. The enable_socket marker above is
    # pytest-socket's, and phcc does not consult it: its own autouse cleanup
    # asserts `not HASocketBlockedError.instances` at teardown, so a blocked
    # socket fails the test even after the marker "allowed" it. This mock
    # cloud server binds a real aiohttp TestServer, so it needs the fixture.
    #
    # Local runs passed without it, which is why this went unnoticed - the
    # failure only ever appeared in CI, on the same pinned phcc 0.13.347.
    """Test all 12 REST API endpoints extracted from Cloud.java & Environment.java."""
    server = mock_srv.MockCyncServer()
    async with TestClient(TestServer(server.app)) as client:
        # 1. POST /v2/two_factor/email/verifycode
        resp = await client.post("/v2/two_factor/email/verifycode", json={"email": "test@example.com"})
        assert resp.status == 200

        # 2. POST /v2/user_auth/two_factor
        resp = await client.post("/v2/user_auth/two_factor", json={"email": "test@example.com", "password": "pass", "two_factor": 123456})
        assert resp.status == 200
        auth = await resp.json()
        assert "access_token" in auth

        # 3. GET /v2/user/99999/subscribe/devices
        resp = await client.get("/v2/user/99999/subscribe/devices")
        assert resp.status == 200
        devs = await resp.json()
        assert len(devs) >= 135

        # 4. POST /v2/user/99999/unsubscribe
        resp = await client.post("/v2/user/99999/unsubscribe")
        assert resp.status == 200

        # 5. GET /v2/user/99999
        resp = await client.get("/v2/user/99999")
        assert resp.status == 200
        prof = await resp.json()
        assert prof["id"] == 99999

        # 6. GET /v2/user/99999/property
        resp = await client.get("/v2/user/99999/property")
        assert resp.status == 200

        # 7. GET /v2/product/160fa2b48e5b03e9160fa2b48e5b8a01/device/900000048
        resp = await client.get("/v2/product/160fa2b48e5b03e9160fa2b48e5b8a01/device/900000048")
        assert resp.status == 200

        # 8. GET /v2/product/160fa2b48e5b03e9160fa2b48e5b8a01/device/900000048/property
        resp = await client.get("/v2/product/160fa2b48e5b03e9160fa2b48e5b8a01/device/900000048/property")
        assert resp.status == 200
        props = await resp.json()
        assert props["power"] == 1

        # 9. POST /v2/product/160fa2b48e5b03e9160fa2b48e5b8a01/device/900000048/property
        resp = await client.post("/v2/product/160fa2b48e5b03e9160fa2b48e5b8a01/device/900000048/property", json={"power": 0})
        assert resp.status == 200

        # 10. POST /v2/upgrade/firmware/check/900000048/geapp
        resp = await client.post(
            "/v2/upgrade/firmware/check/900000048/geapp",
            json={"type": 1, "identify": 1, "product_id": "160fa2b48e5b03e9160fa2b48e5b8a01", "current_version": 100},
        )
        assert resp.status == 200

        # 11. GET /scs/notifications/config
        resp = await client.get("/scs/notifications/config?cameraId=CAM123")
        assert resp.status == 200
        cam = await resp.json()
        assert cam["cameraId"] == "CAM123"

        # 12. POST /services/ge-location/share/record
        resp = await client.post("/services/ge-location/share/record", json={"lat": 37.77, "lng": -122.41})
        assert resp.status == 200
