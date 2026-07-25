"""Diagnostics support for Cync LAN (diagnostics, gold)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

TO_REDACT = {"account_password", "mac", "wifi_mac"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    runtime_data = entry.runtime_data
    ncync_server = runtime_data.ncync_server
    devices = []
    for node in ncync_server.node_devices.values():
        devices.append(
            {
                "id": node.id,
                "name": node.name,
                "type": node.type,
                "supported": bool(node.metadata and node.metadata.supported),
                "classification": str(node.metadata.type) if node.metadata else None,
                "mac": node.mac,
                "wifi_mac": node.wifi_mac,
                "bt_only": node.bt_only,
                "online": runtime_data.bridge.is_online(node.id),
            }
        )
    data = {
        "entry": {
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "server": {
            "running": ncync_server.running,
            "host": ncync_server.host,
            "port": ncync_server.port,
            "connected_tcp_devices": len(ncync_server.tcp_connections),
        },
        "raw_topics": {
            k: v.decode(errors="replace") if isinstance(v, bytes) else v
            for k, v in runtime_data.bridge.raw_topics.items()
        },
        "devices": devices,
    }
    return async_redact_data(data, TO_REDACT)
