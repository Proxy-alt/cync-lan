"""Extract and query decompiled Cync Service classes and handlers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .index import Index


@dataclass
class ServiceInfo:
    name: str
    fqn: str
    category: str
    commands_referenced: list[str]


def extract_services(idx: "Index") -> list[ServiceInfo]:
    """Scan and analyze all Service classes in com.gelighting.cbygekit.services."""
    services: list[ServiceInfo] = []
    service_dir = idx.src / "com/gelighting/cbygekit/services"
    if not service_dir.exists():
        return []

    for path in service_dir.rglob("*.java"):
        name = path.stem
        if "Service" not in name and "Handler" not in name:
            continue

        rel_path = str(path.relative_to(idx.src)).replace("/", ".")
        fqn = rel_path.removesuffix(".java")
        content = path.read_text(encoding="utf-8", errors="replace")

        # Extract referenced Command classes
        cmd_matches = re.findall(r"([A-Z][A-Za-z0-9_]*Command)", content)
        unique_cmds = sorted(set(cmd_matches))

        # Categorize service
        lname = name.lower()
        if "show" in lname or "light" in lname or "color" in lname:
            cat = "Light / Show"
        elif "switch" in lname or "relay" in lname:
            cat = "Switch / Relay"
        elif "thermostat" in lname:
            cat = "Thermostat"
        elif "tile" in lname:
            cat = "Tile / Spatial"
        elif "motion" in lname or "sensor" in lname:
            cat = "Sensor"
        elif "routine" in lname or "schedule" in lname:
            cat = "Automation"
        else:
            cat = "Core Infrastructure"

        services.append(ServiceInfo(name=name, fqn=fqn, category=cat, commands_referenced=unique_cmds))

    return sorted(services, key=lambda s: (s.category, s.name))
