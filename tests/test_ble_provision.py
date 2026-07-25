"""Tests for src/cync_lan/ble_provision.py's crypto/framing primitives and
CLI argument parsing.

Non-HA-dependent (imports `cync_lan.ble_provision` directly), living
alongside the rest of the suite so the same
`pytest tests/components/cync_lan/` invocation picks it up - same pattern
as test_xlink_legacy.py/test_devices.py.

Deliberately does NOT exercise real BLE I/O (scan_for_unprovisioned_devices/
provision_device against actual hardware) - that can only be validated by
running the module's CLI against a real device. The bleak-dependent tests
below mock bleak's classes to test this module's OWN logic (filtering,
error handling) in isolation, and are skipped entirely if bleak isn't
installed (it's an optional dependency - `pip install cync_lan[ble]`).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cync_lan.ble_provision import (
    DEFAULT_LTK,
    FACTORY_ADVERTISED_NAME,
    FACTORY_DEFAULT_PAIRING_WRITE,
    FACTORY_MESH_NAME,
    FACTORY_MESH_PASSWORD,
    PAIR_CONFIRM_BYTE,
    PAIRING_CHAR_UUID,
    R_APP,
    TELINK_COMPANY_ID,
    PairingError,
    _pad16,
    _parse_cli,
    build_mesh_credential_write,
    build_pairing_write,
    derive_session_key,
    generate_sk,
    key_encrypt,
    provision_device,
    scan_for_unprovisioned_devices,
    verify_pairing_response,
)

bleak = pytest.importorskip(
    "bleak", reason="bleak is an optional dependency (cync_lan[ble])"
)


def test_build_pairing_write_reproduces_the_factory_default_constant():
    """The single most important correctness check in this module: the
    general formula, applied to the factory mesh name/password, must
    reproduce the EXACT 17-byte constant hardcoded in the real decompiled
    app (Telink.java's f28878l) - independent confirmation the crypto
    primitives are implemented correctly, not just internally
    self-consistent."""
    assert build_pairing_write(FACTORY_MESH_NAME, FACTORY_MESH_PASSWORD) == (
        FACTORY_DEFAULT_PAIRING_WRITE
    )


def test_build_pairing_write_shape():
    result = build_pairing_write("some_mesh", "some_password")
    assert len(result) == 17
    assert result[0] == 0x0C
    assert result[1:9] == R_APP


def test_build_pairing_write_differs_for_different_mesh_credentials():
    a = build_pairing_write("mesh_a", "password_a")
    b = build_pairing_write("mesh_b", "password_b")
    assert a != b
    # R_APP and the opcode byte are always the same regardless of mesh identity
    assert a[:9] == b[:9]


def test_key_encrypt_is_deterministic():
    result1 = key_encrypt(b"name", b"password", key=b"0123456789abcdef")
    result2 = key_encrypt(b"name", b"password", key=b"0123456789abcdef")
    assert result1 == result2
    assert len(result1) == 16


def test_generate_sk_depends_on_all_four_inputs():
    baseline = generate_sk(b"mesh", b"pass", b"AAAAAAAA", b"BBBBBBBB")
    assert generate_sk(b"different", b"pass", b"AAAAAAAA", b"BBBBBBBB") != baseline
    assert generate_sk(b"mesh", b"different", b"AAAAAAAA", b"BBBBBBBB") != baseline
    assert generate_sk(b"mesh", b"pass", b"CCCCCCCC", b"BBBBBBBB") != baseline
    assert generate_sk(b"mesh", b"pass", b"AAAAAAAA", b"DDDDDDDD") != baseline


def test_derive_session_key_matches_generate_sk_directly():
    r_app = R_APP
    r_dev = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    assert derive_session_key("mesh", "pass", r_app, r_dev) == generate_sk(
        b"mesh", b"pass", r_app, r_dev
    )


def test_build_mesh_credential_write_shape_and_opcodes():
    session_key = b"0" * 16
    name_write = build_mesh_credential_write("name", b"my_mesh", session_key)
    password_write = build_mesh_credential_write("password", b"my_pass", session_key)
    ltk_write = build_mesh_credential_write("ltk", DEFAULT_LTK, session_key)

    assert len(name_write) == len(password_write) == len(ltk_write) == 17
    assert name_write[0] == 4
    assert password_write[0] == 5
    assert ltk_write[0] == 6
    # bytes 9-17 are the zero-padding after the 9-byte [opcode+ciphertext] core
    assert name_write[9:] == b"\x00" * 8


def test_build_mesh_credential_write_rejects_invalid_kind():
    with pytest.raises(ValueError):
        build_mesh_credential_write("bogus", b"value", b"0" * 16)


def test_build_mesh_credential_write_differs_per_kind():
    session_key = b"0" * 16
    value = b"same_value_used_for_all_three_kinds"
    writes = {
        kind: build_mesh_credential_write(kind, value, session_key)
        for kind in ("name", "password", "ltk")
    }
    assert len(set(writes.values())) == 3


def test_pairing_error_is_an_exception():
    assert issubclass(PairingError, Exception)


def test_parse_cli_scan():
    args = _parse_cli(["scan", "--timeout", "5"])
    assert args.command == "scan"
    assert args.timeout == 5.0


def test_parse_cli_provision():
    args = _parse_cli(["provision", "AA:BB:CC:DD:EE:FF", "my_mesh", "my_pass"])
    assert args.command == "provision"
    assert args.address == "AA:BB:CC:DD:EE:FF"
    assert args.mesh_name == "my_mesh"
    assert args.mesh_password == "my_pass"


def test_parse_cli_requires_a_command():
    with pytest.raises(SystemExit):
        _parse_cli([])


def _fake_adv(manufacturer_data=None, local_name=None):
    return MagicMock(manufacturer_data=manufacturer_data or {}, local_name=local_name)


async def test_scan_filters_by_company_id_and_name():
    # MagicMock(name=...) sets the mock's own repr name, not a `.name`
    # attribute - must be assigned separately to fake a BLEDevice.name.
    matching_device = MagicMock(address="AA:AA:AA:AA:AA:AA")
    matching_device.name = FACTORY_ADVERTISED_NAME
    wrong_name_device = MagicMock(address="BB:BB:BB:BB:BB:BB")
    wrong_name_device.name = "something_else"
    no_manufacturer_data_device = MagicMock(address="CC:CC:CC:CC:CC:CC")
    no_manufacturer_data_device.name = FACTORY_ADVERTISED_NAME

    discovered = {
        "AA:AA:AA:AA:AA:AA": (
            matching_device,
            _fake_adv(manufacturer_data={TELINK_COMPANY_ID: b"\x01"}),
        ),
        "BB:BB:BB:BB:BB:BB": (
            wrong_name_device,
            _fake_adv(manufacturer_data={TELINK_COMPANY_ID: b"\x01"}),
        ),
        "CC:CC:CC:CC:CC:CC": (no_manufacturer_data_device, _fake_adv()),
    }

    with patch("bleak.BleakScanner.discover", new=AsyncMock(return_value=discovered)):
        found = await scan_for_unprovisioned_devices(timeout=1.0)

    assert found == [matching_device]


async def test_provision_device_writes_pairing_then_credentials_then_confirms():
    fake_client = MagicMock()
    fake_client.write_gatt_char = AsyncMock()
    fake_client.read_gatt_char = AsyncMock(
        side_effect=[
            bytes([0x0D])
            + b"\x01\x02\x03\x04\x05\x06\x07\x08",  # pairing response, R_dev
            bytes(
                [PAIR_CONFIRM_BYTE]
            ),  # confirmation - must be literally 7, not just nonzero
        ]
    )
    fake_client_cls = MagicMock(return_value=fake_client)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("bleak.BleakClient", fake_client_cls):
        await provision_device("AA:BB:CC:DD:EE:FF", "target_mesh", "target_pass")

    # 1 pairing write + 3 credential writes (name/password/ltk) = 4 total
    assert fake_client.write_gatt_char.await_count == 4
    first_call = fake_client.write_gatt_char.await_args_list[0]
    assert first_call.args[0] == PAIRING_CHAR_UUID
    assert first_call.args[1] == FACTORY_DEFAULT_PAIRING_WRITE


async def test_provision_device_raises_on_short_pairing_response():
    fake_client = MagicMock()
    fake_client.write_gatt_char = AsyncMock()
    fake_client.read_gatt_char = AsyncMock(return_value=b"\x01\x02")
    fake_client_cls = MagicMock(return_value=fake_client)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("bleak.BleakClient", fake_client_cls):
        with pytest.raises(PairingError):
            await provision_device("AA:BB:CC:DD:EE:FF", "target_mesh", "target_pass")


async def test_provision_device_raises_when_device_does_not_confirm():
    fake_client = MagicMock()
    fake_client.write_gatt_char = AsyncMock()
    fake_client.read_gatt_char = AsyncMock(
        side_effect=[
            bytes([0x0D]) + b"\x01\x02\x03\x04\x05\x06\x07\x08",
            b"\x00",  # explicit rejection
        ]
    )
    fake_client_cls = MagicMock(return_value=fake_client)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("bleak.BleakClient", fake_client_cls):
        with pytest.raises(PairingError):
            await provision_device("AA:BB:CC:DD:EE:FF", "target_mesh", "target_pass")


async def test_provision_device_raises_for_nonzero_but_wrong_confirmation_byte():
    """Regression test: C2185e.java's real DataReceivedCallback only
    treats a LITERAL byte value of 7 as confirmation - any other value,
    including a plausible-looking nonzero one, must still be treated as
    a failure. (An earlier version of this code incorrectly accepted any
    nonzero byte.)"""
    fake_client = MagicMock()
    fake_client.write_gatt_char = AsyncMock()
    fake_client.read_gatt_char = AsyncMock(
        side_effect=[
            bytes([0x0D]) + b"\x01\x02\x03\x04\x05\x06\x07\x08",
            bytes([PAIR_CONFIRM_BYTE + 1]),  # nonzero, but not the confirmed value
        ]
    )
    fake_client_cls = MagicMock(return_value=fake_client)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("bleak.BleakClient", fake_client_cls):
        with pytest.raises(PairingError):
            await provision_device("AA:BB:CC:DD:EE:FF", "target_mesh", "target_pass")


def _build_valid_pairing_response(
    mesh_name: str, mesh_password: str, r_dev: bytes, status: int = 0x0D
) -> bytes:
    """Reference builder matching the real device's expected response
    shape (status byte + R_dev + mutual-auth proof), for testing
    verify_pairing_response() against a self-consistent, known-correct
    response rather than only ever mocking success/failure."""
    proof = key_encrypt(
        mesh_name.encode("utf-8"), mesh_password.encode("utf-8"), _pad16(r_dev)
    )[:8]
    return bytes([status]) + r_dev + proof


def test_verify_pairing_response_accepts_a_correctly_constructed_response():
    r_dev = bytes([1, 2, 3, 4, 5, 6, 7, 8])
    response = _build_valid_pairing_response("test_mesh", "test_pass", r_dev)
    assert verify_pairing_response("test_mesh", "test_pass", response) is True


def test_verify_pairing_response_rejects_wrong_mesh_credentials():
    r_dev = bytes([1, 2, 3, 4, 5, 6, 7, 8])
    response = _build_valid_pairing_response("test_mesh", "test_pass", r_dev)
    assert verify_pairing_response("test_mesh", "wrong_pass", response) is False
    assert verify_pairing_response("wrong_mesh", "test_pass", response) is False


def test_verify_pairing_response_rejects_too_short_response():
    assert verify_pairing_response("test_mesh", "test_pass", bytes(10)) is False
