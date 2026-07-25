"""Diagnostics support for Cync LAN (diagnostics, gold).

Shaped around what has actually been hard to diagnose from a bug report:

- **Versions.** Nearly every confusing failure in this integration's history
  turned out to be a version mismatch between it, the `cync-lan` library and
  Home Assistant. That should be the first thing in the report, not something
  to ask for in a follow-up.
- **Device classification.** Which platform a device lands on is derived from
  several `CyncDevice` properties at once, and a device appearing as the wrong
  entity type is a recurring report. Dump the inputs to that decision rather
  than the conclusion.
- **Connection detail per session**, not just a count - "my devices are
  offline" needs to distinguish nothing connected, connected but not ready,
  and connected but stuck in MITM.
- **The environment.** This integration configures the underlying library
  through process-wide environment variables read at import time, so what is
  actually in the environment is load-bearing and invisible from anywhere else.

Everything here is best-effort: a diagnostics download that raises leaves the
user with nothing to attach to their report, which is worse than a field
reading "unavailable". `_safe()` guards every attribute read that crosses into
the library.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from .const import DOMAIN

# mac/wifi_mac identify the user's hardware; the rest are credentials or
# secrets. CYNC_SECRET_KEY encrypts the on-disk cloud token cache, so it is
# every bit as sensitive as the password itself.
TO_REDACT = {
    "account_password",
    "account_username",
    "mac",
    "wifi_mac",
    "CYNC_ACCOUNT_PASSWORD",
    "CYNC_ACCOUNT_USERNAME",
    "CYNC_SECRET_KEY",
    "mesh_password",
}

# Environment variables this integration sets on the library's behalf. Listed
# explicitly rather than dumping everything matching CYNC_*, so a variable
# added later cannot leak something sensitive into a report by default.
_ENV_KEYS = (
    "CYNC_ACCOUNT_USERNAME",
    "CYNC_ACCOUNT_PASSWORD",
    "CYNC_SECRET_KEY",
    "CYNC_CONFIG_DIR",
    "CYNC_BASE_DIR",
    "CYNC_PORT",
    "CYNC_MAX_TCP_CONN",
)


def _safe(getter: Callable[[], Any]) -> Any:
    """Read a value that may raise or be missing.

    The library's classification properties compute from optional metadata and
    can raise on a partially-identified device - exactly the state a user is
    most likely to be reporting.
    """
    try:
        return getter()
    except Exception as err:  # noqa: BLE001 - a diagnostics dump must not fail
        return f"<unavailable: {type(err).__name__}: {err}>"


def _library_version() -> str:
    try:
        from importlib.metadata import version

        return version("cync-lan")
    except Exception as err:  # noqa: BLE001
        return f"<unknown: {err}>"


def _integration_version(hass: HomeAssistant, entry: ConfigEntry) -> Any:
    try:
        integration = hass.data["integrations"][DOMAIN]
        return str(integration.version)
    except Exception:  # noqa: BLE001
        # Falls back to reading the manifest we ship next to this file.
        try:
            import json

            manifest = Path(__file__).parent / "manifest.json"
            return json.loads(manifest.read_text())["version"]
        except Exception as err:  # noqa: BLE001
            return f"<unknown: {err}>"


def _config_file_stat(path: str) -> dict[str, Any]:
    """Blocking - call via the executor. The exported device list going stale
    or missing is a common root cause, and its mtime says so at a glance."""
    try:
        p = Path(path)
        if not p.exists():
            return {"path": path, "exists": False}
        st = p.stat()
        return {
            "path": path,
            "exists": True,
            "size_bytes": st.st_size,
            "modified": st.st_mtime,
        }
    except Exception as err:  # noqa: BLE001
        return {"path": path, "error": f"{type(err).__name__}: {err}"}


def _device_entry(runtime_data: Any, node: Any) -> dict[str, Any]:
    bridge = runtime_data.bridge
    state = _safe(lambda: bridge.get_state(node.id))
    entity_state: Any = None
    if state is not None and not isinstance(state, str):
        entity_state = {
            "power": _safe(lambda: state.power),
            "brightness": _safe(lambda: state.brightness),
            "temperature": _safe(lambda: state.temperature),
            "rgb": _safe(lambda: (state.red, state.green, state.blue)),
        }

    session = _safe(lambda: node.tcp_session)
    return {
        "id": node.id,
        "name": _safe(lambda: node.name),
        "type": _safe(lambda: node.type),
        "home_id": _safe(lambda: node.home_id),
        "version": _safe(lambda: node.version_str),
        "mac": _safe(lambda: node.mac),
        "wifi_mac": _safe(lambda: node.wifi_mac),
        "supported": _safe(lambda: bool(node.metadata and node.metadata.supported)),
        "model": _safe(
            lambda: node.metadata.model_string if node.metadata else None
        ),
        "classification": _safe(
            lambda: str(node.metadata.type) if node.metadata else None
        ),
        # The inputs to platform routing. A device showing up as the wrong
        # entity type is diagnosed from these, not from the entity itself -
        # note is_light and is_switch are deliberately not mutually exclusive
        # with the raw classification above (a dimmable wired switch is
        # classified SWITCH but routes to light.py).
        "capabilities": {
            "is_light": _safe(lambda: node.is_light),
            "is_switch": _safe(lambda: node.is_switch),
            "is_plug": _safe(lambda: node.is_plug),
            "is_fan_controller": _safe(lambda: node.is_fan_controller),
            "is_dimmable": _safe(lambda: node.is_dimmable),
            "is_sol_lamp": _safe(lambda: node.is_sol_lamp),
            "is_hvac": _safe(lambda: node.is_hvac),
            "has_motion_sensor": _safe(lambda: node.has_motion_sensor),
            "supports_rgb": _safe(lambda: node.supports_rgb),
            "supports_temperature": _safe(lambda: node.supports_temperature),
            "has_multi_entities": _safe(lambda: node.has_multi_entities),
            "has_wifi": _safe(lambda: node.has_wifi),
            "bt_only": _safe(lambda: node.bt_only),
        },
        "sub_entity_ids": _safe(
            lambda: sorted(node.entities) if node.entities else []
        ),
        "online": _safe(lambda: runtime_data.bridge.is_online(node.id)),
        "motion": _safe(lambda: runtime_data.bridge.get_motion(node.id)),
        "state": entity_state,
        # A BTLE-only device has no session of its own and is reachable only
        # via whichever WiFi device is relaying it.
        "has_own_tcp_session": session is not None and not isinstance(session, str),
        "relay_source": _safe(
            lambda: node.relay_source.node.id
            if node.relay_source and node.relay_source.node
            else None
        ),
        "indicator_led": _safe(
            lambda: vars(runtime_data.bridge.get_indicator_led(node.id)).copy()
        ),
    }


def _session_entry(ip: str, session: Any) -> dict[str, Any]:
    return {
        "ip_address": ip,
        "device_id": _safe(lambda: session.node.id if session.node else None),
        "closed": _safe(lambda: session.is_closed()),
        # ready_to_control gates whether commands are sent at all, so a
        # connected-but-not-ready session is its own distinct failure mode.
        "ready_to_control": _safe(lambda: session.ready_to_control),
        "mitm_mode": _safe(lambda: session.mitm_mode),
        "is_app": _safe(lambda: session.is_app),
        "allowed_to_connect": _safe(lambda: session.allowed_to_connect),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    runtime_data = entry.runtime_data
    ncync_server = runtime_data.ncync_server
    bridge = runtime_data.bridge

    config_path = _safe(lambda: os.environ.get("CYNC_CONFIG_DIR"))
    export_file = None
    if isinstance(config_path, str) and config_path:
        export_file = await hass.async_add_executor_job(
            _config_file_stat, str(Path(config_path) / "cync_mesh.yaml")
        )

    devices = [
        _device_entry(runtime_data, node)
        for node in _safe(lambda: list(ncync_server.node_devices.values())) or []
    ]

    sessions = []
    raw_sessions = _safe(lambda: dict(ncync_server.tcp_connections))
    if isinstance(raw_sessions, dict):
        sessions = [_session_entry(ip, s) for ip, s in raw_sessions.items()]

    data = {
        # First, because it is the first question worth asking of any report.
        "versions": {
            "integration": _integration_version(hass, entry),
            "cync_lan_library": _library_version(),
            "home_assistant": HA_VERSION,
            "python": sys.version.split()[0],
        },
        "entry": {
            "data": dict(entry.data),
            "options": dict(entry.options),
            "state": str(entry.state),
        },
        "server": {
            "running": _safe(lambda: ncync_server.running),
            "shutting_down": _safe(lambda: ncync_server.shutting_down),
            "host": _safe(lambda: ncync_server.host),
            "port": _safe(lambda: ncync_server.port),
            "known_devices": len(devices),
            "connected_sessions": len(sessions),
            "connection_attempts": _safe(
                lambda: dict(ncync_server.tcp_conn_attempts)
            ),
        },
        "sessions": sessions,
        # Straight from the cloud export - a mismatch against `devices` above
        # usually means the export is stale.
        "cloud_export": {
            "file": export_file,
            "group_count": len(runtime_data.groups or {}),
            "scene_ids": sorted((runtime_data.scenes or {}).keys()),
            "schedule_ids": sorted((runtime_data.schedules or {}).keys()),
        },
        "bridge": {
            # Non-empty with an empty scene/schedule list means the mesh is
            # reporting devices the cloud export has never heard of.
            "unknown_device_sightings": _safe(
                lambda: dict(bridge._unknown_device_sightings)
            ),
            "seen_threshold": bridge.UNKNOWN_DEVICE_SEEN_THRESHOLD,
            "trigger_cooldown_seconds": bridge.UNKNOWN_DEVICE_TRIGGER_COOLDOWN_SECONDS,
            "has_triggered_re_export": _safe(
                lambda: bridge._last_unknown_device_trigger is not None
            ),
            "app_mesh_active": _safe(lambda: bridge.get_app_mesh_active()),
            "app_wifi_active": _safe(lambda: bridge.get_app_wifi_active()),
        },
        # This integration drives the library entirely through these, read at
        # import time, so their values are load-bearing and visible nowhere
        # else. Credentials among them are redacted below.
        "environment": {key: os.environ.get(key) for key in _ENV_KEYS},
        "devices": devices,
        "raw_topics": {
            k: v.decode(errors="replace") if isinstance(v, bytes) else v
            for k, v in _safe(lambda: dict(bridge.raw_topics)).items()
        }
        if isinstance(_safe(lambda: dict(bridge.raw_topics)), dict)
        else {},
    }
    return async_redact_data(data, TO_REDACT)
