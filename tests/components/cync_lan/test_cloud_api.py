"""Tests for src/cync_lan/cloud_api.py's motion-sensor schedule decoding.

First direct unit tests for this file - it has no HA dependency (these are
plain sync/async functions against `cync_lan.cloud_api`, not the
`custom_components.cync_lan` package), living alongside the rest of the
suite so the same `pytest tests/components/cync_lan/` invocation picks
them up. See docs/cync_automations.md for the full research this decode
logic is based on.
"""

from __future__ import annotations

from cync_lan.cloud_api import (
    CyncCloudAPI,
    _decode_sensor_schedule_slot,
    _decode_sensor_schedules,
)


# Real (slightly malformed - see docs/cync_automations.md's data-quality
# caveat) example from a live account: duplicate id=1, no id=0.
REAL_RAW_SCHEDULES = [
    {"brightness": 100, "cct": 1, "displayName": "", "endTime": "2021-05-30 08:59", "id": 1, "isEnabled": True, "simpleMode": True, "startTime": "2021-05-30 06:00"},
    {"brightness": 100, "cct": 1, "displayName": "", "endTime": "2021-05-30 18:59", "id": 1, "isEnabled": True, "simpleMode": True, "startTime": "2021-05-30 09:00"},
    {"brightness": 100, "cct": 1, "displayName": "", "endTime": "2021-05-30 20:59", "id": 2, "isEnabled": True, "simpleMode": True, "startTime": "2021-05-30 19:00"},
    {"brightness": 100, "cct": 1, "displayName": "", "endTime": "2021-05-30 05:59", "id": 3, "isEnabled": True, "simpleMode": True, "startTime": "2021-05-30 21:00"},
]


def test_decode_slot_happy_path():
    slot = _decode_sensor_schedule_slot(
        {
            "brightness": 80,
            "cct": 50,
            "displayName": "",
            "endTime": "2021-05-30 08:59",
            "id": 0,
            "isEnabled": True,
            "simpleMode": True,
            "startTime": "2021-05-30 06:00",
        }
    )
    assert slot == {
        "slot_id": 0,
        "enabled": True,
        "mode": "simple",
        "start_time": "06:00",
        "end_time": "08:59",
        "brightness": 80,
        "cct": 50,
        "display_name": "",
    }


def test_decode_slot_disabled_and_occupancy_modes():
    base = {"id": 1, "startTime": "2021-05-30 06:00", "endTime": "2021-05-30 08:59"}

    disabled = _decode_sensor_schedule_slot({**base, "isEnabled": False, "simpleMode": True})
    assert disabled["mode"] == "disabled"
    assert disabled["enabled"] is False

    occupancy = _decode_sensor_schedule_slot({**base, "isEnabled": True, "simpleMode": False})
    assert occupancy["mode"] == "occupancy"

    simple = _decode_sensor_schedule_slot({**base, "isEnabled": True, "simpleMode": True})
    assert simple["mode"] == "simple"


def test_decode_slot_rejects_out_of_range_id():
    base = {"startTime": "2021-05-30 06:00", "endTime": "2021-05-30 08:59", "isEnabled": True}
    assert _decode_sensor_schedule_slot({**base, "id": 4}) is None
    assert _decode_sensor_schedule_slot({**base, "id": -1}) is None
    assert _decode_sensor_schedule_slot({**base, "id": None}) is None


def test_decode_slot_rejects_missing_times():
    base = {"id": 0, "isEnabled": True}
    assert _decode_sensor_schedule_slot({**base, "endTime": "2021-05-30 08:59"}) is None
    assert _decode_sensor_schedule_slot({**base, "startTime": "2021-05-30 06:00"}) is None
    assert _decode_sensor_schedule_slot(base) is None


def test_decode_schedules_duplicate_id_last_write_wins():
    decoded = _decode_sensor_schedules(REAL_RAW_SCHEDULES)
    assert set(decoded.keys()) == {"daytime", "evening", "sleep"}
    # The *second* id:1 entry (09:00-18:59) wins over the first (06:00-08:59).
    assert decoded["daytime"]["start_time"] == "09:00"
    assert decoded["daytime"]["end_time"] == "18:59"


def test_decode_schedules_empty_or_none_input():
    assert _decode_sensor_schedules([]) == {}
    assert _decode_sensor_schedules(None) == {}


def test_decode_schedules_skips_non_dict_entries():
    assert _decode_sensor_schedules(["garbage", None, 5]) == {}


def _minimal_home(group_extra: dict | None = None) -> dict:
    return {
        "name": "Our Home",
        "properties": {
            "bulbsArray": [
                {
                    "deviceID": "123456789005",
                    "displayName": "Utility Room Light",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "deviceType": 137,
                    "firmwareVersion": "1.0.0",
                }
            ],
            "groupsArray": [
                {
                    "groupID": 32770,
                    "displayName": "Utility Room",
                    "deviceIDArray": [5],
                    "isSubgroup": False,
                    **(group_extra or {}),
                }
            ],
        },
    }


async def test_parse_raw_export_includes_sensor_schedules():
    CyncCloudAPI._instance = None
    api = CyncCloudAPI()
    home = _minimal_home({"sensorSchedules": REAL_RAW_SCHEDULES})

    result = await api._parse_raw_export([home])

    group = result["exported_homes"]["Our Home"]["groups"][32770]
    assert set(group["sensor_schedules"].keys()) == {"daytime", "evening", "sleep"}
    assert group["name"] == "Utility Room"
    assert group["device_ids"] == [5]


async def test_parse_raw_export_group_without_schedules_gets_empty_dict():
    CyncCloudAPI._instance = None
    api = CyncCloudAPI()
    home = _minimal_home()  # no sensorSchedules key at all

    result = await api._parse_raw_export([home])

    group = result["exported_homes"]["Our Home"]["groups"][32770]
    assert group["sensor_schedules"] == {}
