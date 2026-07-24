"""Structural (duck-typed) contracts `devices.py`/`utils.py` rely on for
`GlobalObject.mqtt_client`/`.export_server`, without importing the concrete
classes that implement them.

Those concrete classes (`MQTTClient`, `ExportServer`) live in the separate
`cync-lan-mqtt` add-on package, which depends on this package - core can't
import from a package that depends on core. `custom_components/cync_lan/bridge.py`'s
`CyncLanBridge` (a Home Assistant integration) is a second, independent
implementation of `MqttSink` that never touches MQTT at all. Every method
below was confirmed against the actual `g.mqtt_client.<method>()`/
`g.export_server.<method>()` call sites in `devices.py`/`utils.py`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from cync_lan.devices import CyncDevice
    from cync_lan.structs import EntityState, FanSpeed

__all__ = ["MqttSink", "StoppableService"]


@runtime_checkable
class MqttSink(Protocol):
    """The call surface `devices.py` expects from `GlobalObject.mqtt_client`."""

    topic: str

    async def pub_online(self, device_id: int, status: bool) -> bool: ...

    async def parse_entity_state(
        self, entity_state: "EntityState", from_pkt: Optional[str] = None
    ) -> bool: ...

    async def publish_motion_state(
        self, node: "CyncDevice", motion: bool, from_pkt: Optional[str] = None
    ) -> bool: ...

    async def update_fan_percent(self, node: "CyncDevice", perc: int) -> bool: ...

    async def update_fan_speed(self, node: "CyncDevice", speed: "FanSpeed") -> bool: ...

    async def update_entity_power(
        self, node: "CyncDevice", state: int, sub_id: Optional[int] = None
    ) -> bool: ...

    async def update_brightness(
        self, node: "CyncDevice", bri: int, sub_id: Optional[int] = None
    ) -> bool: ...

    async def update_temperature(
        self, node: "CyncDevice", temp: int, sub_id: Optional[int] = None
    ) -> bool: ...

    async def update_rgb(
        self,
        node: "CyncDevice",
        rgb: tuple[int, int, int],
        sub_id: Optional[int] = None,
    ) -> bool: ...

    async def add_mitm_button(self, node: "CyncDevice") -> None: ...

    async def remove_mitm_button(self, node: "CyncDevice") -> None: ...

    async def mark_app_wifi_active(self, timeout: float = 60.0) -> None: ...

    async def mark_app_mesh_active(self, timeout: float = 60.0) -> None: ...

    def report_unknown_device_id(self, dev_id: int) -> None: ...

    def get_startup_topic_state_sync(
        self, topic_str: str, timeout_seconds: float = 3.0
    ) -> Optional[str]: ...

    async def stop(self) -> None: ...


@runtime_checkable
class StoppableService(Protocol):
    """The call surface `utils.py`'s shutdown path expects from
    `GlobalObject.export_server` (`GlobalObject.ncync_server`/`.cloud_api`
    are concrete core types with their own real `.stop()`/`.close()`)."""

    async def stop(self) -> None: ...
