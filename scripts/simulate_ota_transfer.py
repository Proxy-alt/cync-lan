#!/usr/bin/env python3
"""Local OTA Firmware Streaming & Virtual Device Flashing Simulator

Integrates with cync_ota_fetch to query Cync's cloud OTA endpoints, download any available
firmware binary (or generate a structured sample image), verify MD5 checksums, and stream
the binary packets to an in-memory Virtual Cync Device.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import pathlib
import struct
import sys
import time
import urllib.request

# Import local OTA query module
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import scripts.cync_ota_fetch as ota_fetch

# Opcodes identified from decompiled Cync Android SDK Java classes
OPCODE_SET_OTA_MODE = bytes([0xF7, 0x11, 0x02, 0x0C])     # SetWifiOtaUpdateModeCommand
OPCODE_START_OTA    = bytes([0xF7, 0x11, 0x02, 0x0D, 0x01])  # StartWifiOtaUpdateCommand
OPCODE_QUERY_STATUS = bytes([0xEA, 0x11, 0x02, 0x10, 0x06])  # QueryWifiOtaUpdateStatusCommand


class VirtualCyncDevice:
    """Simulates an embedded Cync device's internal OTA bootloader & dual-bank flash memory."""

    def __init__(self, name: str = "Virtual_Cync_Bulb_01"):
        self.name = name
        self.state = "IDLE"
        self.active_bank = "Bank_A (v10152)"
        self.bank_b_buffer = bytearray()
        self.received_chunks = 0
        self.last_sequence = -1

    def handle_command(self, packet: bytes) -> dict:
        """Parse incoming raw byte commands and update state machine."""
        if packet.startswith(OPCODE_SET_OTA_MODE):
            mode = packet[len(OPCODE_SET_OTA_MODE)]
            self.state = f"OTA_MODE_SET (mode={mode})"
            self.bank_b_buffer.clear()
            self.received_chunks = 0
            return {"status": "ACK", "state": self.state, "opcode": "SET_OTA_MODE"}

        elif packet == OPCODE_START_OTA:
            self.state = "RECEIVING_BINARY"
            return {"status": "ACK", "state": self.state, "opcode": "START_OTA"}

        elif packet.startswith(b"\xAA\x55"):  # Data Chunk Packet Header
            if len(packet) < 6:
                return {"status": "ERROR", "msg": "Malformed data packet"}
            seq_num, chunk_size = struct.unpack(">HH", packet[2:6])
            chunk_data = packet[6:6 + chunk_size]
            
            self.bank_b_buffer.extend(chunk_data)
            self.received_chunks += 1
            self.last_sequence = seq_num
            return {
                "status": "ACK",
                "seq": seq_num,
                "bytes_written": len(self.bank_b_buffer),
                "chunks": self.received_chunks,
            }

        elif packet == OPCODE_QUERY_STATUS:
            if len(self.bank_b_buffer) > 0 and self.state == "RECEIVING_BINARY":
                self.state = "VERIFYING_FLASH"
                flash_sha = hashlib.sha256(self.bank_b_buffer).hexdigest()
                flash_md5 = hashlib.md5(self.bank_b_buffer).hexdigest()
                self.active_bank = "Bank_B (New_Version_Flashed)"
                self.state = "UPGRADE_SUCCESS"
                return {
                    "status": "SUCCESS",
                    "state": self.state,
                    "progress": 100,
                    "active_bank": self.active_bank,
                    "total_bytes_flashed": len(self.bank_b_buffer),
                    "flash_md5": flash_md5,
                    "flash_sha256": flash_sha,
                }
            else:
                return {"status": "IDLE", "state": self.state, "progress": 0}

        return {"status": "UNKNOWN_OPCODE", "raw_hex": packet.hex()}


def download_firmware_binary(url: str, expected_md5: str | None = None) -> bytes | None:
    """Download firmware binary from URL and verify MD5 checksum if provided."""
    print(f"\n[*] Fetching Firmware Binary from URL:")
    print(f"    URL: {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Cync/2.6 (Android)"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            print(f"[+] Downloaded {len(data)} bytes successfully.")
            computed_md5 = hashlib.md5(data).hexdigest()
            print(f"    Downloaded MD5: {computed_md5}")
            if expected_md5:
                print(f"    Expected MD5:   {expected_md5}")
                if computed_md5.lower() == expected_md5.lower():
                    print("[+] MD5 Checksum Verification Passed!")
                else:
                    print("[-] WARNING: MD5 Checksum Mismatch!")
            return data
    except Exception as err:
        print(f"[-] Failed to download firmware binary: {err}")
        return None


async def stream_binary_to_virtual_device(bin_data: bytes, expected_md5: str | None = None):
    """Stream binary payload to Virtual Device via protocol opcodes."""
    bin_sha256 = hashlib.sha256(bin_data).hexdigest()
    bin_md5 = hashlib.md5(bin_data).hexdigest()

    print(f"\n=== Streaming Firmware Payload to Virtual Device ===")
    print(f"    Binary Size: {len(bin_data)} bytes")
    print(f"    MD5: {bin_md5}")
    print(f"    SHA256: {bin_sha256}")

    device = VirtualCyncDevice("Virtual_Cync_Smart_Bulb")
    print(f"\n[*] Initialized Virtual Device: '{device.name}'")
    print(f"    Initial State: {device.state}")
    print(f"    Current Active Partition: {device.active_bank}")

    # Step 1: Set OTA Mode Command (0xF7 0x11 0x02 0x0C 0x01)
    print("\n--- Step 1: Set OTA Mode Command ---")
    cmd_set_mode = OPCODE_SET_OTA_MODE + b"\x01"
    print(f"-> Sending Packet: {cmd_set_mode.hex().upper()}")
    res = device.handle_command(cmd_set_mode)
    print(f"<- Device Response: {json.dumps(res)}")

    # Step 2: Start OTA Update Command (0xF7 0x11 0x02 0x0D 0x01)
    print("\n--- Step 2: Start OTA Update Command ---")
    print(f"-> Sending Packet: {OPCODE_START_OTA.hex().upper()}")
    res = device.handle_command(OPCODE_START_OTA)
    print(f"<- Device Response: {json.dumps(res)}")

    # Step 3: Stream Binary Payload in 64-byte Chunks
    chunk_size = 64
    total_bytes = len(bin_data)
    total_chunks = (total_bytes + chunk_size - 1) // chunk_size

    print(f"\n--- Step 3: Streaming Payload in {total_chunks} Chunks ({chunk_size} bytes/chunk) ---")
    start_time = time.time()

    for seq in range(total_chunks):
        offset = seq * chunk_size
        chunk = bin_data[offset:offset + chunk_size]
        
        packet_header = b"\xAA\x55" + struct.pack(">HH", seq, len(chunk))
        packet = packet_header + chunk
        
        res = device.handle_command(packet)
        progress_pct = (res["bytes_written"] / total_bytes) * 100

        # Print progress update
        if seq % max(1, total_chunks // 10) == 0 or seq == total_chunks - 1:
            print(f"   Chunk {seq+1:04d}/{total_chunks:04d} [Seq {seq}] -> Flashed {res['bytes_written']:6d}/{total_bytes} bytes ({progress_pct:5.1f}%)")
        await asyncio.sleep(0.001)

    elapsed = time.time() - start_time
    print(f"[+] Transfer completed in {elapsed:.3f} seconds ({total_bytes / elapsed / 1024:.2f} KB/s)")

    # Step 4: Send Query Status Command (0xEA 0x11 0x02 0x10 0x06)
    print("\n--- Step 4: Query OTA Status & Post-Stream Flash Verification ---")
    print(f"-> Sending Packet: {OPCODE_QUERY_STATUS.hex().upper()}")
    res = device.handle_command(OPCODE_QUERY_STATUS)
    print(f"<- Device Response: {json.dumps(res, indent=2)}")

    print(f"\n=== Virtual Device Flash Verification Result ===")
    print(f"    Final Device State: {res.get('state')}")
    print(f"    Active Partition After Bootloader Swap: {res.get('active_bank')}")
    print(f"    Total Bytes Written to Flash: {res.get('total_bytes_flashed')}")
    print(f"    Flashed MD5 Match: {res.get('flash_md5') == bin_md5}")
    print(f"    Flashed SHA256 Match: {res.get('flash_sha256') == bin_sha256}")


async def async_main():
    parser = argparse.ArgumentParser(
        description="Fetch OTA firmware update from Cync Cloud and pipe into Virtual Device simulator."
    )
    parser.add_argument(
        "--url",
        type=str,
        help="Direct firmware binary URL to fetch and pipe into simulator",
    )
    parser.add_argument(
        "--md5",
        type=str,
        help="Expected MD5 checksum for binary verification",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=2048,
        help="Sample binary size in bytes if no live cloud update is pending (default: 2048)",
    )
    args = parser.parse_args()

    bin_data = None

    if args.url:
        bin_data = download_firmware_binary(args.url, args.md5)

    if not bin_data:
        # Check cloud account for any active updates
        token = await ota_fetch.get_cached_token()
        if token:
            print("[*] Checking account devices for live cloud OTA updates...")
            devices = await ota_fetch.get_account_devices()
            for dev in devices:
                dev_id = dev.get("id")
                prod_id = dev.get("product_id")
                fw_ver = dev.get("firmware_version", 100)
                if not dev_id or not prod_id:
                    continue
                res = ota_fetch.check_firmware_update(
                    device_id=dev_id,
                    product_id=prod_id,
                    ota_type=1,
                    identify=1,
                    current_version=fw_ver,
                    access_token=token,
                    silent=True,
                )
                if res and res.get("targetVersionUrl"):
                    print(f"[+] Found live cloud OTA update for device {dev_id}!")
                    bin_data = download_firmware_binary(res["targetVersionUrl"], res.get("targetVersionMd5"))
                    break

    if not bin_data:
        print(f"\n[*] No pending cloud firmware updates found on account.")
        print(f"[*] Generating a structured {args.sample_size}-byte sample binary payload to test pipeline...")
        header = b"CYNC_FW_SAMPLE_v10160\x00\x00\x00"
        pattern = bytes([i % 256 for i in range(args.sample_size - len(header))])
        bin_data = header + pattern

    await stream_binary_to_virtual_device(bin_data, args.md5)


if __name__ == "__main__":
    asyncio.run(async_main())
