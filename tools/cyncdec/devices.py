"""Extract and query Cync device types, model IDs, and hardware capabilities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .index import Index


@dataclass
class DeviceTypeInfo:
    name: str
    value: int | None
    category: str
    capabilities: list[str]


def extract_device_types(idx: "Index") -> list[DeviceTypeInfo]:
    """Parse DeviceType.java to extract all hardware device types and numeric values."""
    dt_file = idx.src / "com/gelighting/cbygekit/product/DeviceType.java"
    if not dt_file.exists():
        return []

    content = dt_file.read_text(encoding="utf-8", errors="replace")
    device_types: list[DeviceTypeInfo] = []

    # Find static inner class names extending DeviceType
    matches = re.findall(r"public static final class ([A-Za-z0-9_]+) extends DeviceType", content)
    unique_names = sorted(set(matches))

    for name in unique_names:
        lname = name.lower()
        if any(x in lname for x in ["light", "bulb", "strip", "tile", "clife", "csleep", "fullcolor", "softwhite", "tunable", "singlechip"]):
            cat = "Light"
        elif any(x in lname for x in ["switch", "dimmer", "fourwire", "noneutral"]):
            cat = "Switch"
        elif "plug" in lname:
            cat = "Plug"
        elif "fan" in lname:
            cat = "Fan"
        elif "thermostat" in lname:
            cat = "Thermostat"
        elif "motion" in lname or "sensor" in lname:
            cat = "Sensor"
        elif "camera" in lname:
            cat = "Camera"
        else:
            cat = "Other"

        caps = []
        if "color" in lname or "rgb" in lname:
            caps.append("RGB")
        if "temperature" in lname or "tunable" in lname or "csleep" in lname:
            caps.append("CCT")
        if "dimmer" in lname or "dimming" in lname:
            caps.append("Dimming")
        if "tile" in lname or "hexagon" in lname:
            caps.append("TileLayout")
        if "wirefree" in lname:
            caps.append("BatterySleeping")

        # Search for ID assigned in class block
        val = None
        class_block = re.search(r"class " + re.escape(name) + r" extends DeviceType\s*\{([^}]+)\}", content)
        if class_block:
            id_match = re.search(r"super\((\d+)", class_block.group(1))
            if id_match:
                val = int(id_match.group(1))

        device_types.append(DeviceTypeInfo(name=name, value=val, category=cat, capabilities=caps))

    return sorted(device_types, key=lambda d: (d.category, d.name))
