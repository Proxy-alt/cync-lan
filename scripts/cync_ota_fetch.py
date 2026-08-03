#!/usr/bin/env python3
"""Cync / GE Smart Home OTA Firmware Query & Fetch CLI Tool

Uses cync_lan.cloud_api for authentication to retrieve a valid Cync Access-Token,
auto-discovers real devices on the account, queries Cync's cloud OTA update endpoints,
and supports simulated version sweeps to discover available OTA packages.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

# Ensure local runtime directory exists for cync_lan token storage
DEFAULT_CONFIG_DIR = pathlib.Path(__file__).parent / ".cync_lan_cache"
DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
os.environ["CYNC_CONFIG_DIR"] = str(DEFAULT_CONFIG_DIR)

try:
    import cync_lan.cloud_api
    HAS_CYNC_LAN_LIB = True
except ImportError:
    HAS_CYNC_LAN_LIB = False

API_BASE_URL = "https://api.gelighting.com/v2"

# Known Cync device product profiles for quick testing
DEVICE_PROFILES: dict[str, dict] = {
    "direct_connect_bulb": {
        "description": "Cync Direct Connect Wi-Fi Smart Bulb",
        "productId": "0001",
        "type": 1,        # Wi-Fi OTA
        "identify": 1,
        "currentVersion": 100,
    },
    "c_reach_hub": {
        "description": "C-Reach Smart Bridge / Hub",
        "productId": "0002",
        "type": 1,
        "identify": 2,
        "currentVersion": 100,
    },
    "smart_switch": {
        "description": "Cync Direct Connect Wi-Fi Smart Switch",
        "productId": "0003",
        "type": 1,
        "identify": 3,
        "currentVersion": 100,
    },
}


async def cync_request_otp(email: str) -> bool:
    """Request a 2FA OTP code using cync_lan.cloud_api."""
    if not HAS_CYNC_LAN_LIB:
        print("[-] Error: cync_lan library not available.")
        return False

    cync_lan.cloud_api.CYNC_ACCOUNT_USERNAME = email
    cync_lan.cloud_api.CYNC_ACCOUNT_PASSWORD = "placeholder_password"
    cync_lan.cloud_api.CYNC_SECRET_KEY = "cync_lan_default_secret_key"

    api = cync_lan.cloud_api.CyncCloudAPI()
    try:
        print(f"[*] Requesting 2FA OTP code for {email} via cync_lan.cloud_api...")
        success = await api.request_otp()
        if success:
            print(f"[+] OTP verification code successfully sent to {email}!")
            return True
        else:
            print(f"[-] Failed to request OTP code.")
            return False
    finally:
        await api.close()


async def cync_authenticate(email: str, password: str, otp_code: str | int, secret_key: str = "cync_lan_default_secret_key") -> str | None:
    """Authenticate using cync_lan.cloud_api and return the Access-Token."""
    if not HAS_CYNC_LAN_LIB:
        print("[-] Error: cync_lan library not available.")
        return None

    cync_lan.cloud_api.CYNC_ACCOUNT_USERNAME = email
    cync_lan.cloud_api.CYNC_ACCOUNT_PASSWORD = password
    cync_lan.cloud_api.CYNC_SECRET_KEY = secret_key

    api = cync_lan.cloud_api.CyncCloudAPI()
    try:
        print(f"[*] Authenticating {email} via cync_lan.cloud_api...")
        otp_int = int(otp_code) if str(otp_code).isdigit() else otp_code
        success = await api.send_otp(otp_int)
        if not success:
            print("[-] Authentication failed (invalid credentials or OTP).")
            return None

        print("[+] Authentication successful!")
        token_struct = await api.read_token_cache()
        if token_struct and hasattr(token_struct, "access_token"):
            token = token_struct.access_token
            print(f"[+] Access Token obtained: {token[:10]}...{token[-5:]}")
            return token
        elif isinstance(token_struct, dict) and "access_token" in token_struct:
            token = token_struct["access_token"]
            print(f"[+] Access Token obtained: {token[:10]}...{token[-5:]}")
            return token
        else:
            print("[-] Authenticated, but could not extract access_token from token cache.")
            return None
    finally:
        await api.close()


async def get_cached_token(secret_key: str = "cync_lan_default_secret_key") -> str | None:
    """Retrieve cached Access-Token if available."""
    if not HAS_CYNC_LAN_LIB:
        return None
    cync_lan.cloud_api.CYNC_SECRET_KEY = secret_key
    api = cync_lan.cloud_api.CyncCloudAPI()
    try:
        tkn = await api.read_token_cache()
        if tkn and hasattr(tkn, "access_token"):
            return tkn.access_token
        return None
    except Exception:
        return None
    finally:
        await api.close()


async def get_account_devices(secret_key: str = "cync_lan_default_secret_key") -> list[dict]:
    """Retrieve subscribed devices registered on the authenticated Cync account."""
    if not HAS_CYNC_LAN_LIB:
        return []

    cync_lan.cloud_api.CYNC_SECRET_KEY = secret_key
    api = cync_lan.cloud_api.CyncCloudAPI()
    try:
        tkn = await api.read_token_cache()
        if not tkn:
            return []
        api.token_cache = tkn
        devices = await api.request_device_data()
        return devices if isinstance(devices, list) else []
    except Exception as err:
        print(f"[-] Error fetching account devices: {err}")
        return []
    finally:
        await api.close()


def check_firmware_update(
    device_id: int,
    product_id: str,
    ota_type: int,
    identify: int,
    current_version: int,
    access_token: str | None = None,
    silent: bool = False,
) -> dict | None:
    """Send a POST request to Cync's firmware update check endpoint for a specific device ID."""
    url = f"{API_BASE_URL}/upgrade/firmware/check/{device_id}/geapp?useHttps=true"

    payload = {
        "type": ota_type,
        "identify": identify,
        "product_id": product_id,
        "current_version": current_version,
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Cync/2.6 (Android)",
    }
    if access_token:
        headers["Access-Token"] = access_token

    json_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=json_bytes, headers=headers, method="POST")

    if not silent:
        print(f"\n[*] Querying Cync Cloud OTA Endpoint:")
        print(f"    URL: {url}")
        print(f"    Payload: {json.dumps(payload)}")

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            res_body = response.read().decode("utf-8")
            data = json.loads(res_body)
            return data
    except urllib.error.HTTPError as err:
        try:
            error_body = json.loads(err.read().decode("utf-8"))
            err_code = error_body.get("error", {}).get("code")
            err_msg = error_body.get("error", {}).get("msg", "")

            if err_code == 4041013:  # "upgrade task not exists"
                if not silent:
                    print(f"[+] Device ID {device_id} is running latest firmware (Server response: {err_msg})")
                return {"upToDate": True, "code": err_code, "msg": err_msg}

            if not silent:
                print(f"[-] HTTP Error {err.code}: {err.reason} ({err_msg})")
        except Exception:
            if not silent:
                print(f"[-] HTTP Error {err.code}: {err.reason}")
        return None
    except urllib.error.URLError as err:
        if not silent:
            print(f"[-] URL/Network Error: {err.reason}")
        return None


def verify_firmware_url(firmware_url: str) -> bool:
    """Verify that the target firmware binary URL is accessible via a HEAD request."""
    print(f"\n[*] Verifying Firmware Binary URL Access:")
    print(f"    URL: {firmware_url}")
    req = urllib.request.Request(firmware_url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            content_length = resp.headers.get("Content-Length", "unknown")
            content_type = resp.headers.get("Content-Type", "unknown")
            print(f"[+] Direct download link is accessible!")
            print(f"    HTTP Status: {resp.status}")
            print(f"    Content-Length: {content_length} bytes")
            print(f"    Content-Type: {content_type}")
            return True
    except urllib.error.HTTPError as err:
        print(f"[-] HTTP Error {err.code} accessing binary: {err.reason}")
        return False
    except urllib.error.URLError as err:
        print(f"[-] Network Error accessing binary: {err.reason}")
        return False


async def simulate_version_sweep(token: str, secret_key: str):
    """Sweep simulated version numbers across unique product types on the account."""
    devices = await get_account_devices(secret_key)
    if not devices:
        print("[-] No devices found on account for simulation.")
        return

    # Deduplicate by product_id
    unique_products: dict[str, dict] = {}
    for dev in devices:
        prod_id = dev.get("product_id")
        if prod_id and prod_id not in unique_products:
            unique_products[prod_id] = dev

    print(f"\n=== Simulating Version Queries across {len(unique_products)} Unique Product Types ===")
    
    test_versions = [1, 10, 100, 1000, 10000, 10100, 10120, 10140, 10150]

    found_packages = 0
    for prod_id, dev in unique_products.items():
        dev_id = dev.get("id")
        name = dev.get("name") or dev.get("firmware_mod") or "Device"
        real_fw = dev.get("firmware_version", 0)
        print(f"\n[*] Product: '{name}' (ID: {dev_id}, Product: {prod_id}, Real FW: {real_fw})")

        for ver in test_versions:
            sys.stdout.write(f"    Testing simulated current_version={ver:5d}... ")
            sys.stdout.flush()
            res = check_firmware_update(
                device_id=dev_id,
                product_id=prod_id,
                ota_type=1,
                identify=1,
                current_version=ver,
                access_token=token,
                silent=True,
            )
            if res and not res.get("upToDate"):
                print(" -> [!!! OTA PACKAGE FOUND !!!]")
                print(json.dumps(res, indent=4))
                found_packages += 1
                target_url = res.get("targetVersionUrl")
                if target_url:
                    verify_firmware_url(target_url)
            elif res and res.get("upToDate"):
                print(" -> [No Task / Up To Date]")
            else:
                print(" -> [No Task / Error]")

    if found_packages == 0:
        print("\n[*] Simulation finished: Server returned 'no task' across all tested simulated versions.")


async def async_main():
    parser = argparse.ArgumentParser(
        description="CLI tool to test Cync / GE device OTA firmware update check & download flow."
    )
    parser.add_argument(
        "--profile",
        choices=list(DEVICE_PROFILES.keys()),
        default="direct_connect_bulb",
        help="Preset device profile to test",
    )
    parser.add_argument(
        "--device-id",
        type=int,
        help="Target device ID on account",
    )
    parser.add_argument(
        "--product-id",
        type=str,
        help="Override device Product ID",
    )
    parser.add_argument(
        "--token",
        type=str,
        help="Directly specify an existing Cync Access-Token",
    )
    parser.add_argument(
        "--email",
        type=str,
        help="Cync account email address",
    )
    parser.add_argument(
        "--password",
        type=str,
        help="Cync account password",
    )
    parser.add_argument(
        "--otp",
        type=str,
        help="2FA OTP verification code from email",
    )
    parser.add_argument(
        "--secret-key",
        type=str,
        default="cync_lan_default_secret_key",
        help="Encryption secret key for token cache (default: cync_lan_default_secret_key)",
    )
    parser.add_argument(
        "--auto-discover",
        action="store_true",
        help="Auto-discover all real devices on the account and query updates for each",
    )
    parser.add_argument(
        "--simulate-versions",
        action="store_true",
        help="Simulate a sweep of older version codes across account product types",
    )
    parser.add_argument(
        "--request-otp-only",
        action="store_true",
        help="Only request an OTP code sent to email",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check update info, do not test downloading binary URL",
    )

    args = parser.parse_args()

    token = args.token

    if args.request_otp_only:
        if not args.email:
            print("[-] Error: --email is required to request OTP code.")
            sys.exit(1)
        res = await cync_request_otp(args.email)
        sys.exit(0 if res else 1)

    if not token and args.email and args.password:
        if not args.otp:
            print("[!] Note: --otp was not provided. If your account requires 2FA, pass --otp <CODE>.")
            print("[*] Attempting OTP request first...")
            await cync_request_otp(args.email)
            print("[!] Check your email for the OTP code, then re-run with --otp <CODE>.")
            sys.exit(0)
        token = await cync_authenticate(args.email, args.password, args.otp, args.secret_key)
        if not token:
            print("[-] Unable to obtain access token. Exiting.")
            sys.exit(1)

    if not token:
        token = await get_cached_token(args.secret_key)

    if args.simulate_versions:
        if not token:
            print("[-] Error: Valid authentication token required for version simulation.")
            sys.exit(1)
        await simulate_version_sweep(token, args.secret_key)
        return

    print(f"\n=== Cync OTA Firmware Query Tool ===")

    # If --auto-discover is set (or no specific device ID / product ID override given), fetch account devices
    if args.auto_discover or (token and not args.device_id and not args.product_id):
        print("[*] Fetching subscribed devices registered on account...")
        devices = await get_account_devices(args.secret_key)
        if devices:
            print(f"[+] Found {len(devices)} device(s) on account:")
            for dev in devices:
                dev_id = dev.get("id")
                name = dev.get("name") or dev.get("firmware_mod") or "Device"
                mac = dev.get("mac", "unknown")
                prod_id = dev.get("product_id")
                fw_ver = dev.get("firmware_version", 0)
                print(f"    - ID: {dev_id} | Name: '{name}' | MAC: {mac} | Product: {prod_id} | FW Version: {fw_ver}")

            print("\n[*] Checking OTA updates across account devices...")
            updates_found = 0
            for dev in devices:
                dev_id = dev.get("id")
                prod_id = dev.get("product_id")
                fw_ver = dev.get("firmware_version", 100)
                if not dev_id or not prod_id:
                    continue

                res = check_firmware_update(
                    device_id=dev_id,
                    product_id=prod_id,
                    ota_type=1,
                    identify=1,
                    current_version=fw_ver,
                    access_token=token,
                )
                if res and not res.get("upToDate"):
                    updates_found += 1
                    print(f"\n[+] Firmware update available for Device ID {dev_id}:")
                    print(json.dumps(res, indent=2))
                    target_url = res.get("targetVersionUrl")
                    if target_url and not args.check_only:
                        verify_firmware_url(target_url)

            if updates_found == 0:
                print("\n[*] All account devices are currently running the latest firmware version (no pending updates).")
            return

    # Single device query fallback
    profile = DEVICE_PROFILES[args.profile]
    device_id = args.device_id or 1
    product_id = args.product_id or profile["productId"]
    ota_type = profile["type"]
    identify = profile["identify"]
    current_version = profile["currentVersion"]

    result = check_firmware_update(
        device_id=device_id,
        product_id=product_id,
        ota_type=ota_type,
        identify=identify,
        current_version=current_version,
        access_token=token,
    )

    if result:
        if result.get("upToDate"):
            print(f"\n[+] Device {device_id} is up to date.")
        else:
            print(f"\n[+] Successfully received firmware update response:")
            print(json.dumps(result, indent=2))
            target_url = result.get("targetVersionUrl")
            if target_url and not args.check_only:
                verify_firmware_url(target_url)
    else:
        print(f"\n[-] Firmware update check yielded no results or failed.")


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
