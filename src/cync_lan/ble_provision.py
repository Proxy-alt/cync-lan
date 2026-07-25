"""EXPERIMENTAL: BLE GATT provisioning for brand-new (factory-default,
never-provisioned) Telink-mesh Cync/C-by-GE devices - discovery, the
pairing/session-key handshake, and the mesh-credential handoff that gives
a device its permanent mesh name/password/LTK. See
docs/ble_provisioning_protocol.md for the full protocol research this
implements (service/characteristic UUIDs, the encryption algorithm, the
WiFi-credential handoff format for WiFi-capable devices).

UNTESTED AGAINST REAL HARDWARE as of this writing. Every byte here is
confirmed from decompiled Cync Android app source, and the pairing/
session-key/command-encryption primitives are independently
cross-validated against `python-dimond` (a real, working, unrelated
Telink-mesh BLE client) - but no live pairing attempt against real
hardware has been made from this module yet. Run this against a spare,
factory-reset device and report back what happens (success, or the exact
error/traceback) - see this module's own CLI --help.

This is a completely separate transport (BLE GATT, via `bleak`) from the
rest of cync-lan, which only ever intercepts the TCP relay of already-
provisioned devices - nothing here is used by, or shared with, the normal
TCP server. `bleak` is an optional dependency (`pip install cync_lan[ble]`)
so it isn't required just to run the everyday TCP relay server.

Only the CLI/BLE-I/O layer needs `bleak` - the crypto/framing functions
below are pure and import-safe without it, so they can be unit tested
without any BLE hardware or the `bleak` package installed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

__all__ = [
    "PAIRING_SERVICE_UUID",
    "PAIRING_CHAR_UUID",
    "STATUS_CHAR_UUID",
    "COMMAND_CHAR_UUID",
    "FACTORY_MESH_NAME",
    "FACTORY_MESH_PASSWORD",
    "FACTORY_ADVERTISED_NAME",
    "R_APP",
    "FACTORY_DEFAULT_PAIRING_WRITE",
    "DEFAULT_LTK",
    "PAIR_CONFIRM_BYTE",
    "TELINK_COMPANY_ID",
    "key_encrypt",
    "generate_sk",
    "build_pairing_write",
    "derive_session_key",
    "verify_pairing_response",
    "build_mesh_credential_write",
    "PairingError",
    "scan_for_unprovisioned_devices",
    "provision_device",
]

logger = logging.getLogger("cync_lan.ble_provision")

# Confirmed via com/gelighting/cbygekit/foundation/commands/Telink.java
# (Telink.f28872f/f28873g/f28874h/f28876j) - byte-for-byte identical to
# python-dimond's hardcoded UUIDs (dimond/__init__.py:114-116), independent
# confirmation this is standard Telink Mesh SDK behavior, not Cync-specific.
PAIRING_SERVICE_UUID = "00010203-0405-0607-0809-0a0b0c0d1910"
PAIRING_CHAR_UUID = "00010203-0405-0607-0809-0a0b0c0d1914"
STATUS_CHAR_UUID = "00010203-0405-0607-0809-0a0b0c0d1911"
COMMAND_CHAR_UUID = "00010203-0405-0607-0809-0a0b0c0d1912"

# Telink Semiconductor's registered Bluetooth SIG company ID - confirmed as
# both the BLE-advertisement manufacturer-data key used for new-device
# discovery (BleDeviceScanner.java) AND the vendor-ID bytes (0x11,0x02
# little-endian) already present in every cync-lan mesh command payload.
TELINK_COMPANY_ID = 0x0211

# Factory-default mesh identity + BLE advertised name for a brand-new,
# never-provisioned Telink device - confirmed via
# TelinkDeviceBleManager.m14334v ("authenticate")'s special-cased branch
# and BleDeviceScanner.java's "telink_mesh1" advertised-name check.
FACTORY_MESH_NAME = "telink_mesh1"
FACTORY_MESH_PASSWORD = "123"
FACTORY_ADVERTISED_NAME = "telink_mesh1"

# These factory constants only get a device as far as the initial pairing
# handshake. Provisioning it onto an EXISTING mesh needs that mesh's own
# name/password, which this module has no way to discover on its own - it
# speaks BLE, and the credentials live on the hub.
#
# `cync_lan.devices.query_hub_mesh_credentials()` reads them over the LAN
# (op_code 0x8A, confirmed via QueryHubMeshNameAndPasswordCommand.java). It
# needs a live nCyncServer with at least one connected device, so it is
# usable from the Home Assistant integration or the MQTT add-on - both of
# which run a server in-process - but not from this file's standalone CLI,
# which has no server. Pass the values it returns as `provision`'s
# mesh_name/mesh_password arguments.

# Confirmed via Telink.java's static initializer: f28877k = {0xA0..0xA7,
# zero-padded to 16}. NOT SecureRandom output despite superficially
# resembling one - it's a `final` field written once, used AS-IS at two
# independent real call sites (the initial pairing write, and the
# read-response callback that reconstructs the same value to derive the
# session key). The real Cync/GE app hardcodes this "R_app" contribution
# to a fixed constant on every single pairing attempt, unlike
# python-dimond (which generates 8 fresh random bytes per session, still
# protocol-compatible since the algorithm doesn't require freshness).
R_APP = bytes([0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7])

# Confirmed via Telink.java's static initializer: f28878l, a fully
# pre-baked 17-byte pairing-characteristic write used verbatim by
# TelinkDeviceBleManager.m14334v whenever the target mesh name/password
# are exactly the factory defaults above - i.e. this exact byte sequence
# is what the real app writes to bootstrap a brand-new device.
# build_pairing_write(FACTORY_MESH_NAME, FACTORY_MESH_PASSWORD) reproduces
# this exact value from the general formula (see its own docstring) - a
# genuine internal cross-check, not just an assumption the formula
# generalizes.
FACTORY_DEFAULT_PAIRING_WRITE = bytes(
    [
        0x0C,
        0xA0,
        0xA1,
        0xA2,
        0xA3,
        0xA4,
        0xA5,
        0xA6,
        0xA7,
        0x8D,
        0xB6,
        0x74,
        0x71,
        0x1B,
        0x85,
        0x5A,
        0x79,
    ]
)

# Confirmed via Telink.java's static initializer: f28879m, the default LTK
# (long-term key) TelinkDeviceBleManager$pairMesh$2.java hands a device
# when no custom LTK is supplied.
DEFAULT_LTK = bytes(
    [
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC4,
        0xC5,
        0xC6,
        0xC7,
        0xD8,
        0xD9,
        0xDA,
        0xDB,
        0xDC,
        0xDD,
        0xDE,
        0xDF,
    ]
)

# Confirmed via TelinkDeviceBleManager$pairMesh$2.java's literal opcode
# bytes (4/5/6) for the encrypted NAME/PASSWORD/LTK mesh-credential-handoff
# writes - see build_mesh_credential_write().
_PAIR_CREDENTIAL_OPCODES = {"name": 4, "password": 5, "ltk": 6}

# Confirmed via C2185e.java (the DataReceivedCallback registered on
# pairMesh$2's confirmation ReadRequest): the callback only sets its
# success flag when the response's first byte is LITERALLY 7 - any other
# value (including 0) leaves it false, i.e. "not confirmed". Not merely
# "nonzero means success" as a naive read might assume. This lines up
# with the same "literal = ordinal+1" pattern already confirmed for the
# NAME/PASSWORD/LTK opcodes above (4/5/6 = enum ordinals 3/4/5) -
# ordinal 6 is PAIR_CONFIRM, and 6+1=7, a semantically sensible match
# for "pairing confirmed" under that same hypothesis.
PAIR_CONFIRM_BYTE = 7


def _pad16(data: bytes) -> bytes:
    """Truncates/zero-pads to exactly 16 bytes - Telink's own UTF-8-encode-
    then-pad-to-16 convention (Telink.m13406d) for both mesh name/password
    strings and raw key/nonce material."""
    return data[:16].ljust(16, b"\x00")


def _aes_ecb_encrypt(key: bytes, data: bytes) -> bytes:
    """One AES-ECB block operation, WITH Telink's confirmed byte-reversal
    quirk applied to both the key and the data before encrypting, and to
    the result afterward: `AES(reversed(key)).encrypt(reversed(data))`,
    then reversed back. Confirmed exactly via python-dimond's real,
    working `encrypt()` (dimond/__init__.py:29-35) - this is real,
    load-bearing Telink SDK behavior, not a decompiler artifact.

    `key`/`data` must each be exactly 16 bytes already (every call site
    pads via _pad16() first) - not re-validated here."""
    cipher = Cipher(algorithms.AES(key[::-1]), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(data[::-1])[::-1]


def key_encrypt(name: bytes, password: bytes, key: bytes) -> bytes:
    """`data = XOR(pad16(name), pad16(password))`; `return AES_ECB(key,
    data)` - confirmed via python-dimond's real `key_encrypt()`
    (dimond/__init__.py:45-49). Used both for the initial pairing write
    (key=R_APP padded to 16) and, via generate_sk(), for session-key
    derivation (key=XOR(meshName,meshPass) there instead - a different
    role for the same two building blocks, not the same call)."""
    xored = bytes(a ^ b for a, b in zip(_pad16(name), _pad16(password), strict=True))
    return _aes_ecb_encrypt(key, xored)


def generate_sk(name: bytes, password: bytes, data1: bytes, data2: bytes) -> bytes:
    """`key = XOR(pad16(name), pad16(password))`; `data = data1[0:8] +
    data2[0:8]`; `return AES_ECB(key, data)` - confirmed via python-dimond's
    real `generate_sk()` (dimond/__init__.py:37-43). `data1`/`data2` are
    R_app/R_dev (in that order) when deriving the session key."""
    key = bytes(a ^ b for a, b in zip(_pad16(name), _pad16(password), strict=True))
    data = (data1[:8] + data2[:8])[:16].ljust(16, b"\x00")
    return _aes_ecb_encrypt(key, data)


def build_pairing_write(mesh_name: str, mesh_password: str) -> bytes:
    """Builds the 17-byte initial pairing-characteristic write:
    `[0x0C] + R_APP + key_encrypt(mesh_name, mesh_password,
    key=pad16(R_APP))[0:8]` - confirmed via
    TelinkDeviceBleManager.m14334v ("authenticate")'s general-case branch.

    `build_pairing_write(FACTORY_MESH_NAME, FACTORY_MESH_PASSWORD)`
    reproduces `FACTORY_DEFAULT_PAIRING_WRITE` exactly - both are given
    directly (not derived from each other) as a cross-check that this
    formula generalizes correctly to the pre-baked factory-default
    constant found separately in the decompiled source.
    """
    ciphertext = key_encrypt(
        mesh_name.encode("utf-8"), mesh_password.encode("utf-8"), _pad16(R_APP)
    )[:8]
    return bytes([0x0C]) + R_APP + ciphertext


def derive_session_key(
    mesh_name: str, mesh_password: str, r_app: bytes, r_dev: bytes
) -> bytes:
    """Derives the AES session key from both sides' random contributions -
    confirmed via TelinkDeviceBleManager$..d (C2184d.mo14353a, the pairing-
    characteristic read-response callback) and python-dimond's real
    `connect()`. `r_dev` is bytes [1:9] of the device's response to the
    `build_pairing_write()` write."""
    return generate_sk(
        mesh_name.encode("utf-8"), mesh_password.encode("utf-8"), r_app, r_dev
    )


def verify_pairing_response(
    mesh_name: str, mesh_password: str, response: bytes
) -> bool:
    """Real mutual-auth check the actual Cync app performs on the pairing
    response, confirmed via C2184d.java's DataReceivedCallback -
    contradicting this module's own earlier assumption (based only on
    python-dimond, which skips it) that the real app performs no
    verification at all. The device is expected to prove it derived the
    same R_dev/key material by echoing back a proof value:
    `response[9:17]` should equal `key_encrypt(mesh_name, mesh_password,
    key=pad16(r_dev))[0:8]`, where `r_dev = response[1:9]`.

    This is a CLIENT-side sanity check only - the device doesn't care
    whether the phone/client verifies its response, so a failed/skipped
    check does not by itself prevent provisioning from working (this is
    exactly why python-dimond can skip it and still work). Treat a
    verification failure as a strong signal something is misunderstood
    about the response format (e.g. wrong mesh_name/mesh_password, or a
    device that responds differently than expected) rather than a fatal
    error - provision_device() logs it as a warning, not a raised
    exception.

    Returns False (rather than raising) if `response` is too short to
    contain a proof value (< 17 bytes) - not every device/response variant
    is guaranteed to include one.
    """
    if len(response) < 17:
        return False
    r_dev = response[1:9]
    expected_proof = key_encrypt(
        mesh_name.encode("utf-8"), mesh_password.encode("utf-8"), _pad16(r_dev)
    )[:8]
    return response[9:17] == expected_proof


def build_mesh_credential_write(kind: str, value: bytes, session_key: bytes) -> bytes:
    """Builds one of the 3 mesh-credential-handoff writes ("pairMesh") that
    permanently assign a device its target mesh name/password/LTK, sent
    after the session key above has been derived. Confirmed via
    TelinkDeviceBleManager$pairMesh$2.java: for each of name/password/ltk,
    UTF-8-encode + pad to 16 bytes, AES-ECB-encrypt with the session key,
    take the first 8 bytes, prepend a literal opcode byte (4=name,
    5=password, 6=ltk), then zero-pad the resulting 9 bytes to 17 for the
    GATT write.

    kind: one of "name", "password", "ltk". value: the raw UTF-8 mesh name/
    password bytes, or DEFAULT_LTK/a custom 16-byte LTK for kind="ltk".
    """
    if kind not in _PAIR_CREDENTIAL_OPCODES:
        raise ValueError(
            f"kind must be one of {sorted(_PAIR_CREDENTIAL_OPCODES)}, got {kind!r}"
        )
    opcode = _PAIR_CREDENTIAL_OPCODES[kind]
    ciphertext = _aes_ecb_encrypt(session_key, _pad16(value))[:8]
    payload = bytes([opcode]) + ciphertext
    return payload.ljust(17, b"\x00")


class PairingError(Exception):
    """Raised when a real BLE pairing/mesh-join attempt fails - a real
    device response (or lack of one) was involved, not just a local
    programming error. See the specific message for which step failed."""


async def scan_for_unprovisioned_devices(timeout: float = 10.0):
    """EXPERIMENTAL, UNTESTED: scans for nearby BLE advertisements and
    returns the ones that look like brand-new, never-provisioned Telink
    devices - confirmed filter logic (BleDeviceScanner.java): manufacturer
    data keyed by TELINK_COMPANY_ID (0x0211) present, AND the advertised
    local name equal to FACTORY_ADVERTISED_NAME ("telink_mesh1", Telink's
    stock unprovisioned-node name). No BLE-level scan filter is used by
    the real app either - everything is filtered in application code
    after an unfiltered OS-level scan, so this does the same.

    Looser than the real app's own filter: BleDeviceScanner.java also
    validates a device-type byte within the manufacturer data against
    the full GE/Cync product catalog, rejecting unrecognized types
    outright - not replicated here, so this may surface non-Cync Telink
    devices in scan results if any happen to be nearby (not incorrect,
    just more permissive).

    Returns a list of bleak `BLEDevice` objects. Requires the `bleak`
    optional dependency (`pip install cync_lan[ble]`).
    """
    try:
        from bleak import BleakScanner
    except ImportError as e:
        raise RuntimeError(
            "bleak is required for BLE provisioning - install it with "
            "'pip install cync_lan[ble]'"
        ) from e

    discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
    found = []
    for _address, (device, adv) in discovered.items():
        if TELINK_COMPANY_ID not in (adv.manufacturer_data or {}):
            continue
        if (adv.local_name or device.name) != FACTORY_ADVERTISED_NAME:
            continue
        found.append(device)
    return found


async def provision_device(
    address: str,
    mesh_name: str,
    mesh_password: str,
    ltk: bytes = DEFAULT_LTK,
    timeout: float = 15.0,
) -> None:
    """EXPERIMENTAL, UNTESTED AGAINST REAL HARDWARE: connects to a brand-
    new/factory-default device at `address` (see
    scan_for_unprovisioned_devices()) and hands it the given mesh
    name/password/LTK, making it a permanent member of that mesh.

    Confirmed flow (TelinkDeviceBleManager.m14334v + m14318B +
    TelinkDeviceBleManager$pairMesh$2.java):
    1. Connect, discover GATT services.
    2. Write FACTORY_DEFAULT_PAIRING_WRITE to the pairing characteristic
       (the pre-baked bootstrap handshake for a factory-default device).
    3. Read the pairing characteristic's response; bytes [1:9] are the
       device's own random contribution ("R_dev").
    4. Derive the session key from R_APP + R_dev, using the FACTORY mesh
       name/password (not the target mesh) as the key material - this is
       the bootstrap session, not the final one.
    5. Write the target mesh name, password, and LTK (3 separate writes,
       each confirmed by build_mesh_credential_write()) using that session
       key, queued together.
    6. Read the pairing characteristic once more to confirm the device
       accepted the new credentials.

    Raises PairingError if the device doesn't respond as expected at any
    step. Does NOT hand the device WiFi credentials (a separate step, see
    docs/ble_provisioning_protocol.md's "WiFi credential handoff" section -
    only relevant for WiFi-capable device types, not implemented here yet).

    Requires the `bleak` optional dependency (`pip install cync_lan[ble]`).
    """
    try:
        from bleak import BleakClient
    except ImportError as e:
        raise RuntimeError(
            "bleak is required for BLE provisioning - install it with "
            "'pip install cync_lan[ble]'"
        ) from e

    lp = f"provision_device[{address}]:"
    logger.warning(
        f"{lp} EXPERIMENTAL, untested against real hardware - please report success "
        f"or failure (with the exact error/traceback) regardless of outcome."
    )
    async with BleakClient(address, timeout=timeout) as client:
        logger.info(f"{lp} connected, writing factory-default pairing handshake")
        await client.write_gatt_char(
            PAIRING_CHAR_UUID, FACTORY_DEFAULT_PAIRING_WRITE, response=True
        )
        response = await client.read_gatt_char(PAIRING_CHAR_UUID)
        if len(response) < 9:
            raise PairingError(
                f"{lp} pairing response too short ({len(response)} bytes, need >= 9): "
                f"{response.hex()}"
            )
        r_dev = response[1:9]
        if not verify_pairing_response(
            FACTORY_MESH_NAME, FACTORY_MESH_PASSWORD, response
        ):
            logger.warning(
                f"{lp} device's pairing response did not pass the mutual-auth proof "
                f"check the real app performs (see verify_pairing_response()'s docstring) - "
                f"continuing anyway, since this doesn't block the device from accepting "
                f"pairing, but it's a signal something may be misunderstood if the "
                f"following steps fail: {response.hex()}"
            )
        session_key = derive_session_key(
            FACTORY_MESH_NAME, FACTORY_MESH_PASSWORD, R_APP, r_dev
        )
        logger.info(f"{lp} session key derived, handing off target mesh credentials")

        name_write = build_mesh_credential_write(
            "name", mesh_name.encode("utf-8"), session_key
        )
        password_write = build_mesh_credential_write(
            "password", mesh_password.encode("utf-8"), session_key
        )
        ltk_write = build_mesh_credential_write("ltk", ltk, session_key)
        for payload in (name_write, password_write, ltk_write):
            await client.write_gatt_char(PAIRING_CHAR_UUID, payload, response=True)

        confirm = await client.read_gatt_char(PAIRING_CHAR_UUID)
        if not confirm or confirm[0] != PAIR_CONFIRM_BYTE:
            raise PairingError(
                f"{lp} device did not confirm the new mesh credentials "
                f"(expected first byte {PAIR_CONFIRM_BYTE}): {confirm.hex() if confirm else '(empty)'}"
            )
        logger.info(f"{lp} provisioned successfully onto mesh {mesh_name!r}")


def _parse_cli(argv=None):
    parser = argparse.ArgumentParser(
        prog="cync-lan-ble-provision",
        description=(
            "EXPERIMENTAL, untested against real hardware - BLE provisioning "
            "for brand-new (factory-default) Cync/C-by-GE devices. See "
            "docs/ble_provisioning_protocol.md."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan", help="Scan for nearby unprovisioned devices")
    scan_parser.add_argument(
        "--timeout", type=float, default=10.0, help="Scan duration in seconds"
    )

    provision_parser = sub.add_parser(
        "provision", help="Provision a device onto a target mesh"
    )
    provision_parser.add_argument(
        "address", help="BLE address of the device to provision"
    )
    provision_parser.add_argument("mesh_name", help="Target mesh name")
    provision_parser.add_argument("mesh_password", help="Target mesh password")
    provision_parser.add_argument(
        "--timeout", type=float, default=15.0, help="Connection timeout in seconds"
    )

    return parser.parse_args(argv)


async def _async_main(args) -> int:
    if args.command == "scan":
        devices = await scan_for_unprovisioned_devices(timeout=args.timeout)
        if not devices:
            print("No unprovisioned devices found.")
            return 0
        print(f"Found {len(devices)} unprovisioned device(s):")
        for device in devices:
            print(f"  {device.address}  {device.name}")
        return 0
    if args.command == "provision":
        try:
            await provision_device(
                args.address, args.mesh_name, args.mesh_password, timeout=args.timeout
            )
        except PairingError as e:
            print(f"Provisioning failed: {e}", file=sys.stderr)
            return 1
        print("Provisioned successfully.")
        return 0
    return 1


def main(argv=None) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    args = _parse_cli(argv)
    sys.exit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
