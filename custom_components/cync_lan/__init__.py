"""The Cync LAN integration.

Deliberately does NOT reuse cync_lan.main.CyncLAN.start() - that method
registers process-wide SIGINT/SIGTERM handlers via
`asyncio.get_event_loop().add_signal_handler(...)`, which would fight with
Home Assistant's own shutdown handling if invoked here. Instead this module
replicates only the device-server startup steps that are safe to run inside
HA's process (build the node map, start the local TCP listener as a
tracked task), and skips CyncLAN's CLI-oriented signal handling and export
HTTP server entirely.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval

from .bridge import CyncLanBridge
from .const import (
    CONF_ACCOUNT_PASSWORD,
    CONF_ACCOUNT_USERNAME,
    CONF_EXPORT_REFRESH_INTERVAL,
    CONF_LOCAL_PORT,
    DEFAULT_EXPORT_REFRESH_INTERVAL_HOURS,
    DEFAULT_LOCAL_PORT,
    DOMAIN,
)
from .util import configure_environment

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.FAN,
    Platform.LIGHT,
    # No Platform.NUMBER yet: the only candidate (motion sensor sensitivity/
    # timeout settings) is still unwired pending a real packet capture to
    # confirm the outbound command bytes (see devices.py's
    # _build_motion_sensor_settings_payload) - add it once that lands rather
    # than shipping speculative entities.
    Platform.SWITCH,
]

# How long to wait for the TCP listener to either bind or fail before
# deciding setup didn't work (test-before-setup, bronze).
_BIND_TIMEOUT = 5.0
_BIND_POLL_INTERVAL = 0.1


@dataclass
class CyncLanRuntimeData:
    """Stored on ConfigEntry.runtime_data (runtime-data, bronze)."""

    bridge: CyncLanBridge
    ncync_server: "object"  # cync_lan.server.nCyncServer
    server_task: asyncio.Task
    unsub_refresh: object = None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    configure_environment(
        hass, entry.data[CONF_ACCOUNT_USERNAME], entry.data[CONF_ACCOUNT_PASSWORD]
    )
    os.environ["CYNC_PORT"] = str(
        entry.options.get(CONF_LOCAL_PORT, DEFAULT_LOCAL_PORT)
    )

    # Imported after configure_environment() runs - cync_lan.const reads its
    # env-var-backed constants at import time, so environment must be set
    # first (see util.configure_environment's docstring).
    from cync_lan.const import CYNC_CONFIG_FILE_PATH
    from cync_lan.server import nCyncServer
    from cync_lan.structs import GlobalObject
    from cync_lan.utils import parse_config

    cfg_file = Path(CYNC_CONFIG_FILE_PATH)
    if not cfg_file.exists():
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="config_missing",
            translation_placeholders={"path": str(cfg_file)},
        )

    try:
        node_map = await parse_config(cfg_file)
    except Exception as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="config_parse_failed",
            translation_placeholders={"error": str(err)},
        ) from err

    g = GlobalObject()
    bridge = CyncLanBridge(hass, entry.entry_id)
    g.mqtt_client = bridge
    g.ncync_server = ncync_server = nCyncServer(node_map)

    server_task = hass.loop.create_task(
        ncync_server.start(), name=f"cync_lan_server_{entry.entry_id}"
    )

    # test-before-setup: give the listener a chance to actually bind (or
    # fail - e.g. port already in use) before treating setup as successful,
    # rather than reporting success and only finding out about a dead
    # listener when the first device fails to connect.
    waited = 0.0
    while not ncync_server.running and waited < _BIND_TIMEOUT:
        if server_task.done():
            # start() returned/raised without ever setting running=True
            exc = server_task.exception() if not server_task.cancelled() else None
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="listener_start_failed",
                translation_placeholders={"error": str(exc)},
            )
        await asyncio.sleep(_BIND_POLL_INTERVAL)
        waited += _BIND_POLL_INTERVAL
    if not ncync_server.running:
        server_task.cancel()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="listener_bind_failed",
            translation_placeholders={
                "port": str(entry.options.get(CONF_LOCAL_PORT, DEFAULT_LOCAL_PORT)),
                "timeout": str(_BIND_TIMEOUT),
            },
        )

    runtime_data = CyncLanRuntimeData(
        bridge=bridge, ncync_server=ncync_server, server_task=server_task
    )
    entry.runtime_data = runtime_data

    refresh_hours = entry.options.get(
        CONF_EXPORT_REFRESH_INTERVAL, DEFAULT_EXPORT_REFRESH_INTERVAL_HOURS
    )
    if refresh_hours > 0:
        from datetime import timedelta

        async def _periodic_refresh(_now) -> None:
            await _refresh_export_and_reload_if_changed(hass, entry, cfg_file)

        runtime_data.unsub_refresh = async_track_time_interval(
            hass, _periodic_refresh, timedelta(hours=refresh_hours)
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _refresh_export_and_reload_if_changed(
    hass: HomeAssistant, entry: ConfigEntry, cfg_file: Path
) -> None:
    """Periodically re-pull the cloud export so newly added Cync devices show
    up without the user manually removing/re-adding the integration
    (contributes to dynamic-devices, gold - see quality_scale.yaml for the
    parts of that rule this doesn't fully satisfy yet)."""
    from cync_lan.cloud_api import CyncCloudAPI
    from cync_lan.utils import parse_config

    try:
        before = cfg_file.stat().st_mtime if cfg_file.exists() else None
        api = CyncCloudAPI()
        await api.export_config_file()
        after = cfg_file.stat().st_mtime if cfg_file.exists() else None
        if before != after:
            new_map = await parse_config(cfg_file)
            if set(new_map) != set(entry.runtime_data.ncync_server.node_devices):
                _LOGGER.info(
                    "Cync device list changed on periodic refresh, reloading entry"
                )
                await hass.config_entries.async_reload(entry.entry_id)
    except Exception:  # noqa: BLE001 - a failed background refresh must not crash HA
        _LOGGER.exception("Periodic Cync export refresh failed")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime_data: CyncLanRuntimeData = entry.runtime_data
    if runtime_data.unsub_refresh is not None:
        runtime_data.unsub_refresh()
    await runtime_data.ncync_server.stop()
    runtime_data.server_task.cancel()
    try:
        await runtime_data.server_task
    except asyncio.CancelledError:
        pass
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
