#!/usr/bin/env python3
"""Cync / GE Smart Home Virtual Mock API Server

Emulates api.gelighting.com REST API endpoints for offline Cync-LAN development,
Home Assistant integration testing, and CI automated test suites.
Extracted from decompiled Cync Android SDK specifications (Cloud.java & Environment.java).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import pathlib
import sys
from aiohttp import web

# Load extracted 138-device type registry
REGISTRY_FILE = pathlib.Path(__file__).parent / "data" / "device_registry.json"


def load_device_registry() -> dict:
    if REGISTRY_FILE.exists():
        with open(REGISTRY_FILE, "r") as f:
            return json.load(f)
    return {}


class MockCyncServer:
    """Virtual Cync Cloud Server emulating api.gelighting.com REST APIs."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port
        self.bound_port = port
        self.app = web.Application()
        self.registry = load_device_registry()
        self.runner: web.AppRunner | None = None
        self._setup_routes()

    def _setup_routes(self):
        # 1. 2FA Authentication Endpoints
        self.app.router.add_post("/v2/two_factor/email/verifycode", self.handle_request_otp)
        self.app.router.add_post("/v2/user_auth/two_factor", self.handle_user_auth)

        # 2. User & Device Inventory Endpoints
        self.app.router.add_get("/v2/user/{user_id}/subscribe/devices", self.handle_get_devices)
        self.app.router.add_post("/v2/user/{user_id}/subscribe/devices", self.handle_get_devices)
        self.app.router.add_post("/v2/user/{user_id}/unsubscribe", self.handle_unsubscribe_user)
        self.app.router.add_get("/v2/user/{user_id}", self.handle_get_user_profile)
        self.app.router.add_get("/v2/user/{user_id}/property", self.handle_get_user_property)

        # 3. Individual Device & Property Endpoints
        self.app.router.add_get("/v2/product/{product_id}/device/{device_id}", self.handle_get_device_detail)
        self.app.router.add_get("/v2/product/{product_id}/device/{device_id}/property", self.handle_get_device_property)
        self.app.router.add_post("/v2/product/{product_id}/device/{device_id}/property", self.handle_set_device_property)

        # 4. Firmware Upgrade & OTA Endpoints
        self.app.router.add_post("/v2/upgrade/firmware/check/{device_id}/geapp", self.handle_check_firmware)
        self.app.router.add_get("/firmware/sample_fw.bin", self.handle_download_firmware)

        # 5. Camera & Location Share Service Endpoints
        self.app.router.add_get("/scs/notifications/config", self.handle_camera_notification_config)
        self.app.router.add_post("/services/ge-location/share/record", self.handle_share_record)

    async def handle_request_otp(self, request: web.Request) -> web.Response:
        """Mock POST /v2/two_factor/email/verifycode."""
        data = await request.json()
        return web.json_response({"status": 200, "msg": "OTP verification code sent"})

    async def handle_user_auth(self, request: web.Request) -> web.Response:
        """Mock POST /v2/user_auth/two_factor."""
        data = await request.json()
        return web.json_response({
            "status": 200,
            "access_token": "mock_cync_access_token_999888777",
            "user_id": 99999,
            "authorize_code": "mock_auth_code_12345",
            "expires_in": 7200,
        })

    async def handle_get_devices(self, request: web.Request) -> web.Response:
        """Mock GET/POST /v2/user/{user_id}/subscribe/devices?version=0."""
        user_id = request.match_info.get("user_id")

        devices = []
        for dev_type_id, info in self.registry.items():
            class_name = info["class_name"]
            dev_id = 900000000 + int(dev_type_id)
            mac = f"8850F6{int(dev_type_id):06X}"

            devices.append({
                "id": dev_id,
                "name": f"Mock_{class_name}",
                "mac": mac,
                "product_id": info["product_id"],
                "deviceType": int(dev_type_id),
                "firmware_mod": class_name,
                "firmware_version": 10150,
                "mcu_version": 10050,
                "is_online": True,
                "is_active": True,
                "authority": "RW",
                "access_key": 888,
                "authorize_code": f"1e{mac[:12].lower()}",
            })

        return web.json_response(devices)

    async def handle_unsubscribe_user(self, request: web.Request) -> web.Response:
        """Mock POST /v2/user/{user_id}/unsubscribe."""
        return web.json_response({"status": 200, "msg": "unsubscribed"})

    async def handle_get_user_profile(self, request: web.Request) -> web.Response:
        """Mock GET /v2/user/{user_id}."""
        user_id = request.match_info.get("user_id")
        return web.json_response({
            "id": int(user_id) if user_id and user_id.isdigit() else 99999,
            "email": "user@example.com",
            "nickname": "Cync Developer",
            "create_time": 1600000000,
        })

    async def handle_get_user_property(self, request: web.Request) -> web.Response:
        """Mock GET /v2/user/{user_id}/property."""
        return web.json_response({
            "properties": {"temperature_unit": "fahrenheit", "theme": "dark"}
        })

    async def handle_get_device_detail(self, request: web.Request) -> web.Response:
        """Mock GET /v2/product/{product_id}/device/{device_id}."""
        device_id = request.match_info.get("device_id")
        product_id = request.match_info.get("product_id")
        return web.json_response({
            "id": int(device_id) if device_id and device_id.isdigit() else 900000001,
            "product_id": product_id,
            "is_online": True,
            "firmware_version": 10150,
        })

    async def handle_get_device_property(self, request: web.Request) -> web.Response:
        """Mock GET /v2/product/{product_id}/device/{device_id}/property."""
        return web.json_response({
            "power": 1,
            "brightness": 100,
            "color_temp": 50,
            "rgb": "FF0000",
        })

    async def handle_set_device_property(self, request: web.Request) -> web.Response:
        """Mock POST /v2/product/{product_id}/device/{device_id}/property."""
        payload = await request.json()
        return web.json_response({"status": 200, "updated": payload})

    async def handle_check_firmware(self, request: web.Request) -> web.Response:
        """Mock POST /v2/upgrade/firmware/check/{device_id}/geapp?useHttps=true."""
        device_id = request.match_info.get("device_id")
        payload = await request.json()

        current_version = payload.get("current_version", 100)
        if current_version < 10160:
            sample_bin = b"CYNC_FW_SAMPLE_v10160\x00\x00\x00" + (b"\xAA" * 2024)
            sample_md5 = hashlib.md5(sample_bin).hexdigest()

            return web.json_response({
                "targetVersionCode": 10160,
                "targetVersionUrl": f"http://{self.host}:{self.bound_port}/firmware/sample_fw.bin",
                "targetVersionMd5": sample_md5,
                "targetVersionSize": len(sample_bin),
            })
        else:
            return web.json_response(
                {"error": {"msg": "upgrade task not exists", "code": 4041013}},
                status=404,
            )

    async def handle_download_firmware(self, request: web.Request) -> web.Response:
        """Mock GET /firmware/sample_fw.bin."""
        sample_bin = b"CYNC_FW_SAMPLE_v10160\x00\x00\x00" + (b"\xAA" * 2024)
        return web.Response(
            body=sample_bin,
            content_type="application/octet-stream",
            headers={"Content-Disposition": "attachment; filename=sample_fw.bin"},
        )

    async def handle_camera_notification_config(self, request: web.Request) -> web.Response:
        """Mock GET /scs/notifications/config?cameraId={did}."""
        camera_id = request.query.get("cameraId", "unknown")
        return web.json_response({
            "cameraId": camera_id,
            "motion_notifications": True,
            "sound_notifications": False,
        })

    async def handle_share_record(self, request: web.Request) -> web.Response:
        """Mock POST /services/ge-location/share/record."""
        return web.json_response({"status": 200, "msg": "location shared"})

    async def start(self):
        """Start the virtual server asynchronously and resolve bound port."""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()

        if site._server and site._server.sockets:
            self.bound_port = site._server.sockets[0].getsockname()[1]
        print(f"[+] Virtual Cync Cloud Server running at http://{self.host}:{self.bound_port}")

    async def stop(self):
        """Stop the virtual server."""
        if self.runner:
            await self.runner.cleanup()
            print("[+] Virtual Cync Cloud Server stopped.")


async def main():
    server = MockCyncServer(host="127.0.0.1", port=8888)
    await server.start()
    print("[*] Press Ctrl+C to stop Mock Cync Server.")
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
