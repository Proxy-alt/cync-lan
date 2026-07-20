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
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_time_interval

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
from .services import async_setup_services, async_unload_services
from .util import configure_environment, refresh_cloud_export

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.FAN,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SCENE,
    Platform.SELECT,
    # Platform.SENSOR: read-only diagnostic entities only (motion-sensor
    # native schedule slots, sensor.py) - not a general-purpose sensor
    # platform yet.
    Platform.SENSOR,
    Platform.SWITCH,
    # Platform.NUMBER/SELECT above, plus the wifi-blink switch in
    # switch.py, cover indicator-LED settings as real config entities -
    # that command is confirmed working on real hardware (see
    # docs/mesh_opcodes.md), so the original "cmd_code is predicted, a
    # service is more honest than an entity" reasoning no longer applies to
    # it specifically. Motion-sensor tuning stays service-only
    # (services.py's experimental_set_motion_sensor_settings) since *that*
    # cmd_code is still unconfirmed on real hardware. Scene activation
    # (Platform.SCENE, scene.py) and schedule enable/disable (switch.py's
    # CyncLanScheduleSwitch) moved to real entities despite their own
    # predicted cmd_codes - unlike motion-sensor tuning, there's no
    # multi-field form to fill out first, so a real entity is a strict UX
    # improvement over the raw service with no added risk (same command,
    # same caveats, just reachable without knowing a numeric scene_id).
]

# How long to wait for the TCP listener to either bind or fail before
# deciding setup didn't work (test-before-setup, bronze).
_BIND_TIMEOUT = 5.0
_BIND_POLL_INTERVAL = 0.1

# How long to wait after setup before checking whether any device has
# actually connected (repair-issues, gold) - long enough that a device
# doing its normal boot-time DNS lookup and TCP handshake has had a real
# chance to show up, short enough that a genuine DNS misconfiguration gets
# flagged promptly rather than silently sitting broken for hours.
_NO_DEVICES_CHECK_DELAY = 600.0  # 10 minutes


@dataclass
class CyncLanRuntimeData:
    """Stored on ConfigEntry.runtime_data (runtime-data, bronze)."""

    bridge: CyncLanBridge
    ncync_server: "object"  # cync_lan.server.nCyncServer
    server_task: asyncio.Task
    groups: dict = None  # {group_id: {"name", "device_ids", "is_subgroup"}}
    scenes: dict = None  # {scene_id: {"name"}}
    schedules: dict = None  # {schedule_id: {"name", "scene_id", "enabled"}}
    unsub_refresh: object = None
    unsub_no_devices_check: object = None
    # Stashed by light.py's async_setup_entry so light groups can be added
    # later - e.g. from the options flow when the user enables/refreshes
    # them - without forcing a full entry reload, which would drop every
    # device's TCP connection just to add a handful of group entities. See
    # light.async_add_light_groups().
    light_add_entities: object = None  # AddEntitiesCallback
    created_light_group_ids: set = None  # group_ids already added as entities


def _import_cync_lan_symbols():
    """Import the upstream cync_lan package's heavy modules - meant to run
    inside an executor, not called directly from the event loop.

    This import chain pulls in pydantic (cync_lan.structs) and, via
    cync_lan.devices -> cync_lan.metadata.model_info, pydantic's dataclass
    decorator, which does its own blocking file read (package metadata
    discovery) the first time it's used. Both showed up as real "Detected
    blocking call ... inside the event loop" warnings on a real HA
    install, pointing at otherwise-unremarkable lines (an import
    statement, a bare @dataclass decorator) - the actual blocking I/O is
    inside Python's import machinery and pydantic's own internals, not
    anything this integration's code controls the timing of directly, so
    the whole import has to move off the loop rather than being chased
    call by call.
    """
    from cync_lan.const import CYNC_CONFIG_FILE_PATH
    from cync_lan.server import nCyncServer
    from cync_lan.structs import GlobalObject
    from cync_lan.utils import parse_config, parse_groups, parse_schedules, parse_scenes

    return (
        CYNC_CONFIG_FILE_PATH,
        nCyncServer,
        GlobalObject,
        parse_config,
        parse_groups,
        parse_scenes,
        parse_schedules,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await configure_environment(
        hass, entry.data[CONF_ACCOUNT_USERNAME], entry.data[CONF_ACCOUNT_PASSWORD]
    )
    os.environ["CYNC_PORT"] = str(
        entry.options.get(CONF_LOCAL_PORT, DEFAULT_LOCAL_PORT)
    )

    # Imported after configure_environment() runs - cync_lan.const reads its
    # env-var-backed constants at import time, so environment must be set
    # first (see util.configure_environment's docstring).
    (
        CYNC_CONFIG_FILE_PATH,
        nCyncServer,
        GlobalObject,
        parse_config,
        parse_groups,
        parse_scenes,
        parse_schedules,
    ) = await hass.async_add_executor_job(_import_cync_lan_symbols)

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

    # Not gated behind CONF_ENABLE_LIGHT_GROUPS - that option only controls
    # whether light.py creates group *entities* (dashboard clutter is the
    # concern it exists for). Group data itself is also the source for
    # motion-sensor schedule attributes on binary_sensor.py's entities,
    # which have nothing to do with that option - see
    # docs/cync_automations.md. light.py independently re-checks the option
    # before creating any group entities, so this doesn't change when those
    # appear.
    groups: dict = {}
    try:
        groups = await parse_groups(cfg_file)
    except Exception:  # noqa: BLE001 - groups are optional, must not block setup
        _LOGGER.exception("Failed to parse Cync device groups, continuing without them")

    # Scenes/Schedules ("Routines") - same best-effort, non-fatal pattern as
    # groups above. Source for scene.py's activatable scene entities and
    # switch.py's schedule-enable switches - see docs/cync_automations.md.
    scenes: dict = {}
    try:
        scenes = await parse_scenes(cfg_file)
    except Exception:  # noqa: BLE001 - scenes are optional, must not block setup
        _LOGGER.exception("Failed to parse Cync scenes, continuing without them")

    schedules: dict = {}
    try:
        schedules = await parse_schedules(cfg_file)
    except Exception:  # noqa: BLE001 - schedules are optional, must not block setup
        _LOGGER.exception("Failed to parse Cync schedules, continuing without them")

    async def _on_unknown_device_confirmed() -> None:
        # dynamic-devices (gold): a real new device was seen in a MeshInfo
        # dump - re-export now instead of waiting for the periodic refresh
        # timer (which may be hours away, or disabled entirely).
        await _refresh_export_and_reload_if_changed(hass, entry, cfg_file)

    g = GlobalObject()
    bridge = CyncLanBridge(
        hass, entry.entry_id, on_unknown_device=_on_unknown_device_confirmed
    )
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
        bridge=bridge,
        ncync_server=ncync_server,
        server_task=server_task,
        groups=groups,
        scenes=scenes,
        schedules=schedules,
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

    async def _check_no_devices_connected(_now) -> None:
        await _check_and_report_no_devices(hass, entry, ncync_server)

    runtime_data.unsub_no_devices_check = async_call_later(
        hass, _NO_DEVICES_CHECK_DELAY, _check_no_devices_connected
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_setup_services(hass)
    return True


def _no_devices_issue_id(entry_id: str) -> str:
    return f"no_devices_connected_{entry_id}"


async def _check_and_report_no_devices(
    hass: HomeAssistant, entry: ConfigEntry, ncync_server
) -> None:
    """repair-issues (gold): if nothing has connected by the time this
    fires, that's a near-certain sign the DNS redirection prerequisite
    (see README.md) isn't actually in place - surface it as an actionable
    repair instead of a warning buried in the log. Not fixable from within
    HA (the fix is a router/DNS change outside its control), so this is
    informational: it tells the user what to check, not a button that
    fixes it for them."""
    issue_id = _no_devices_issue_id(entry.entry_id)
    if not ncync_server.tcp_connections:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="no_devices_connected",
            translation_placeholders={"port": str(ncync_server.port)},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


async def _refresh_export_and_reload_if_changed(
    hass: HomeAssistant, entry: ConfigEntry, cfg_file: Path
) -> None:
    """Periodically re-pull the cloud export to catch devices added to or
    removed from the Cync account since setup.

    Removed devices (stale-devices, gold) are deleted from the device
    registry directly - no reload needed, HA cascades that into removing
    their entities too. Added devices still require a full entry reload
    (dynamic-devices, gold) since this integration doesn't yet keep a
    reference to each platform's async_add_entities callback for
    incremental addition - see quality_scale.yaml for the reasoning. A
    refresh with only removals, no additions, no longer reloads at all.
    """
    from cync_lan.utils import parse_config

    try:
        before = cfg_file.stat().st_mtime if cfg_file.exists() else None
        await refresh_cloud_export(hass)
        after = cfg_file.stat().st_mtime if cfg_file.exists() else None
        if before == after:
            return

        new_map = await parse_config(cfg_file)
        old_ids = set(entry.runtime_data.ncync_server.node_devices)
        new_ids = set(new_map)
        removed_ids = old_ids - new_ids
        added_ids = new_ids - old_ids

        if removed_ids:
            _remove_stale_devices(hass, entry, removed_ids)

        if added_ids:
            _LOGGER.info(
                "Cync account has %d new device(s), reloading entry to add them",
                len(added_ids),
            )
            await hass.config_entries.async_reload(entry.entry_id)
        elif removed_ids:
            _LOGGER.info(
                "Removed %d stale Cync device(s) from the device registry "
                "without a reload",
                len(removed_ids),
            )
    except Exception:  # noqa: BLE001 - a failed background refresh must not crash HA
        _LOGGER.exception("Periodic Cync export refresh failed")


def _remove_stale_devices(
    hass: HomeAssistant, entry: ConfigEntry, removed_dev_ids: set[int]
) -> None:
    from homeassistant.helpers import device_registry as dr

    device_reg = dr.async_get(hass)
    for dev_id in removed_dev_ids:
        identifier = (DOMAIN, f"{entry.entry_id}_{dev_id}")
        device = device_reg.async_get_device(identifiers={identifier})
        if device is not None:
            device_reg.async_remove_device(device.id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime_data: CyncLanRuntimeData = entry.runtime_data
    if runtime_data.unsub_refresh is not None:
        runtime_data.unsub_refresh()
    if runtime_data.unsub_no_devices_check is not None:
        runtime_data.unsub_no_devices_check()
    ir.async_delete_issue(hass, DOMAIN, _no_devices_issue_id(entry.entry_id))
    await runtime_data.ncync_server.stop()
    runtime_data.server_task.cancel()
    try:
        await runtime_data.server_task
    except asyncio.CancelledError:
        pass
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    async_unload_services(hass)
    return unloaded
