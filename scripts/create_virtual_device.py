#!/usr/bin/env python3
"""Cync / GE Smart Home Virtual Device Generator CLI Tool

Generates virtual mock device specifications for Cync-LAN development and Home Assistant
integration testing based on decompiled Cync Android SDK specifications.
Implements exact capability validations and exception checks matching DeviceTypeKt.java.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import yaml
from pathlib import Path

# Built-in Virtual Device Profiles mapped from Cync SDK DeviceType registry
VIRTUAL_DEVICE_TEMPLATES: dict[str, dict] = {
    "c_reach_hub": {
        "description": "C-Reach Smart Hub / Bridge (Wi-Fi to BLE Mesh Gateway)",
        "deviceType": 9,
        "product_id": "160fa2b07d45ba00160fa2b07d45ba01",
        "firmware_mod": "CReach",
        "firmware_version": 12281,
        "mcu_version": 28,
        "wifi_chipset": "MT7688AN",
        "bt_chipset": "TLSR8267",
        "capabilities": ["hub_bridge", "mesh_routing", "wifi_ota"],
        "default_name": "Living Room C-Reach Bridge",
    },
    "sol_lamp": {
        "description": "Cync / GE Sol Smart Desk Lamp (MediaTek MT7688AN + Telink TLSR8267)",
        "deviceType": 11,
        "product_id": "160fa6b2279f03e9160fa6b2279f4801",
        "firmware_mod": "SolGen1Standalone",
        "firmware_version": 10090,
        "mcu_version": 10090,
        "wifi_chipset": "MT7688AN",
        "bt_chipset": "TLSR8267",
        "capabilities": ["on_off", "brightness", "color_temp", "rgb_color", "alexa_voice"],
        "default_name": "Office Sol Smart Lamp",
    },
    "fan_controller": {
        "description": "Cync Ceiling Fan Speed Controller Switch",
        "deviceType": 81,
        "product_id": "160fa2b48e5b03e9160fa2b48e5b8a81",
        "firmware_mod": "Fan Speed Switch",
        "firmware_version": 10045,
        "mcu_version": 10045,
        "wifi_chipset": "NONE",
        "bt_chipset": "TLSR8267",
        "capabilities": ["fan_speed", "on_off"],
        "default_name": "Living Room Ceiling Fan",
    },
    "thermostat": {
        "description": "Cync Smart Thermostat (HVAC)",
        "deviceType": 137,
        "product_id": "160fa2b48e5b03e9160fa2b48e5b137a",
        "firmware_mod": "ThermostatGen1",
        "firmware_version": 10080,
        "mcu_version": 10080,
        "wifi_chipset": "MT7688AN",
        "bt_chipset": "TLSR8267",
        "capabilities": ["target_temperature", "current_temperature", "hvac_mode", "fan_mode"],
        "default_name": "Hallway Thermostat",
    },
    "dynamic_light_strip": {
        "description": "Cync Dynamic Effects Full Color Light Strip (16ft/32ft)",
        "deviceType": 72,
        "product_id": "1607d4c4098400011607d4c40984d072",
        "firmware_mod": "Dynamic Light Strip",
        "firmware_version": 10160,
        "mcu_version": 28,
        "wifi_chipset": "RTL8710C",
        "bt_chipset": "TLSR8258",
        "capabilities": ["on_off", "brightness", "color_temp", "rgb_color", "light_effects"],
        "default_name": "TV Backlight Strip",
    },
    "motion_sensor": {
        "description": "Cync Wire-Free Motion & Ambient Light Sensor",
        "deviceType": 96,
        "product_id": "160042baac4403e9160042baac448896",
        "firmware_mod": "WireFreeMotionSensor",
        "firmware_version": 15644,
        "mcu_version": 1,
        "wifi_chipset": "NONE",
        "bt_chipset": "TLSR8267",
        "capabilities": ["motion_occupancy", "ambient_light", "battery_level"],
        "default_name": "Hallway Motion Sensor",
    },
    "wireless_remote": {
        "description": "Cync Wire-Free Smart Remote Switch / Button",
        "deviceType": 112,
        "product_id": "160042baac4403e9160042baac448112",
        "firmware_mod": "WireFreeRemote",
        "firmware_version": 15644,
        "mcu_version": 1,
        "wifi_chipset": "NONE",
        "bt_chipset": "TLSR8267",
        "capabilities": ["button_press", "occupancy", "battery_level"],
        "default_name": "Bedroom Remote Switch",
    },
    "dimmer_switch": {
        "description": "Cync 4-Wire / No-Neutral Smart Dimmer Switch",
        "deviceType": 48,
        "product_id": "160fa2b48e5b03e9160fa2b48e5b8a01",
        "firmware_mod": "Dimmer Switch",
        "firmware_version": 10152,
        "mcu_version": 10064,
        "wifi_chipset": "NONE",
        "bt_chipset": "TLSR8267",
        "capabilities": ["on_off", "brightness"],
        "default_name": "Kitchen Dimmer Switch",
    },
    "outdoor_smart_plug": {
        "description": "Cync Outdoor Dual-Outlet Smart Plug",
        "deviceType": 56,
        "product_id": "1607d4bf51b800011607d4bf51b84256",
        "firmware_mod": "PlugOutdoorGen2",
        "firmware_version": 10386,
        "mcu_version": 28,
        "wifi_chipset": "RTL8710C",
        "bt_chipset": "TLSR8258",
        "capabilities": ["on_off", "dual_outlet"],
        "default_name": "Patio Lights Plug",
    },
}


def validate_device_capability(device: dict, required_capability: str) -> bool:
    """Validate that the virtual device supports a specific capability (matching DeviceTypeKt.m13630e).

    Raises:
        IllegalStateException: If the required capability is not supported by the device.
    """
    capabilities = device.get("capabilities", [])
    if required_capability in capabilities:
        return True
    raise RuntimeError(f"IllegalStateException: Device type '{device.get('firmware_mod')}' (ID: {device.get('deviceType')}) doesn't support '{required_capability}' capability")


def has_wifi_chipset(device: dict) -> bool:
    """Check if the device has a Wi-Fi chipset (matching DeviceTypeKt.m13629d)."""
    return device.get("wifi_chipset", "NONE") != "NONE"


def generate_random_mac() -> str:
    """Generate a valid MAC address string in Cync hex format (8850F6XXXXXX)."""
    suffix = "".join(random.choices("0123456789ABCDEF", k=6))
    return f"8850F6{suffix}"


def generate_virtual_device(
    template_key: str,
    custom_name: str | None = None,
    custom_id: int | None = None,
    custom_mac: str | None = None,
) -> dict:
    """Construct a full virtual device object matching Cync Cloud & LAN specifications."""
    template = VIRTUAL_DEVICE_TEMPLATES.get(template_key)
    if not template:
        raise ValueError(f"Unknown template key '{template_key}'. Available: {list(VIRTUAL_DEVICE_TEMPLATES.keys())}")

    device_id = custom_id or random.randint(100000000, 999999999)
    mac = custom_mac or generate_random_mac()
    name = custom_name or template["default_name"]

    virtual_dev = {
        "id": device_id,
        "name": name,
        "mac": mac,
        "product_id": template["product_id"],
        "deviceType": template["deviceType"],
        "firmware_mod": template["firmware_mod"],
        "firmware_version": template["firmware_version"],
        "mcu_version": template["mcu_version"],
        "wifi_chipset": template.get("wifi_chipset", "NONE"),
        "bt_chipset": template.get("bt_chipset", "TLSR8267"),
        "capabilities": template["capabilities"],
        "description": template["description"],
        "is_online": True,
        "is_active": True,
        "authority": "RW",
        "access_key": 888,
        "active_code": "virtual_active_code_" + mac.lower(),
        "authorize_code": "1e" + mac[:14].lower(),
    }
    return virtual_dev


def export_to_cync_mesh_yaml(device: dict, target_yaml_path: Path):
    """Export the virtual device entry into a cync_mesh.yaml config file."""
    mesh_entry = {
        "id": device["id"],
        "name": device["name"],
        "mac": device["mac"],
        "product_id": device["product_id"],
        "type": device["deviceType"],
        "firmware_version": device["firmware_version"],
        "mcu_version": device["mcu_version"],
        "virtual": True,
    }

    config_data = {}
    if target_yaml_path.exists():
        try:
            with open(target_yaml_path, "r") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    config_data = loaded
        except Exception:
            pass

    devices_list = config_data.get("devices", [])
    if not isinstance(devices_list, list):
        devices_list = []

    # Update existing or append new
    updated = False
    for idx, d in enumerate(devices_list):
        if isinstance(d, dict) and d.get("id") == device["id"]:
            devices_list[idx] = mesh_entry
            updated = True
            break

    if not updated:
        devices_list.append(mesh_entry)

    config_data["devices"] = devices_list

    with open(target_yaml_path, "w") as f:
        yaml.dump(config_data, f, default_flow_style=False)

    print(f"[+] Successfully exported virtual device '{device['name']}' to: {target_yaml_path}")


def main():
    parser = argparse.ArgumentParser(
        description="CLI tool to create virtual Cync / GE devices for local Cync-LAN testing."
    )
    parser.add_argument(
        "--type",
        choices=list(VIRTUAL_DEVICE_TEMPLATES.keys()),
        default="c_reach_hub",
        help="Virtual device preset template to create",
    )
    parser.add_argument(
        "--name",
        type=str,
        help="Custom friendly name for the virtual device",
    )
    parser.add_argument(
        "--device-id",
        type=int,
        help="Custom integer device ID (default: randomly generated)",
    )
    parser.add_argument(
        "--mac",
        type=str,
        help="Custom MAC address (default: randomly generated 8850F6XXXXXX)",
    )
    parser.add_argument(
        "--export-json",
        type=str,
        help="Save virtual device JSON representation to file",
    )
    parser.add_argument(
        "--export-yaml",
        type=str,
        help="Export virtual device entry to cync_mesh.yaml config file",
    )
    parser.add_argument(
        "--list-templates",
        action="store_true",
        help="List all supported virtual device templates and exit",
    )

    args = parser.parse_args()

    if args.list_templates:
        print("\n=== Supported Cync Virtual Device Templates ===")
        for key, tpl in VIRTUAL_DEVICE_TEMPLATES.items():
            print(f"  [{key}]")
            print(f"    - Description: {tpl['description']}")
            print(f"    - DeviceType ID: {tpl['deviceType']}")
            print(f"    - Capabilities: {', '.join(tpl['capabilities'])}\n")
        sys.exit(0)

    device = generate_virtual_device(
        template_key=args.type,
        custom_name=args.name,
        custom_id=args.device_id,
        custom_mac=args.mac,
    )

    print(f"\n=== Created Virtual Cync Device ===")
    print(json.dumps(device, indent=2))

    if args.export_json:
        json_path = Path(args.export_json)
        with open(json_path, "w") as f:
            json.dump(device, f, indent=2)
        print(f"\n[+] Saved virtual device JSON to: {json_path}")

    if args.export_yaml:
        export_to_cync_mesh_yaml(device, Path(args.export_yaml))


if __name__ == "__main__":
    main()
