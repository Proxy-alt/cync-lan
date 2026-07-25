"""MQTT-broker, Home Assistant MQTT-discovery, and HTTP-exporter constants
for the `cync-lan-mqtt` add-on. Split out of what was previously a single
`cync_lan/const.py` shared with the core protocol library - see that
package's own `const.py` for everything else (TCP/protocol, cloud auth,
TLS, effects, MITM logging)."""

import os

from cync_lan.const import (
    CYNC_BASE_DIR,
    CYNC_SRV_HOST,
    CYNC_VERSION,
    SRC_REPO_URL,
    YES_ANSWER,
)

__all__ = [
    "CYNC_EXPORT_HOST",
    "EXPORT_SRV_START_TASK_NAME",
    "MQTT_CLIENT_START_TASK_NAME",
    "nCYNC_START_TASK_NAME",
    "CYNC_MANUFACTURER",
    "CYNC_BRIDGE_OBJ_ID",
    "ORIGIN_STRUCT",
    "CYNC_BRIDGE_DEVICE_REGISTRY_CONF",
    "CYNC_ENABLE_EXPORTER",
    "CYNC_STATIC_DIR",
    "CYNC_EXPORT_PORT",
    "DEVICE_LWT_MSG",
    "CYNC_MQTT_CONN_DELAY",
    "CYNC_MQTT_HOST",
    "CYNC_MQTT_PORT",
    "CYNC_MQTT_USER",
    "CYNC_MQTT_PASS",
    "CYNC_TOPIC",
    "CYNC_HASS_TOPIC",
    "CYNC_HASS_STATUS_TOPIC",
    "CYNC_HASS_BIRTH_MSG",
    "CYNC_HASS_WILL_MSG",
    "CYNC_HASS_APP",
    "CYNC_MITM_ENTITIES",
    "MQTT_DEBUG",
    "MQTT_DEAD",
]

MQTT_DEBUG = os.environ.get("CYNC_MQTT_DEBUG", "1").casefold() in YES_ANSWER
MQTT_DEAD = os.environ.get("CYNC_MQTT_DEAD", "0").casefold() in YES_ANSWER

CYNC_EXPORT_HOST = os.environ.get("CYNC_EXPORT_HOST", CYNC_SRV_HOST)
CYNC_EXPORT_PORT = int(os.environ.get("CYNC_EXPORT_PORT", 23778))
CYNC_ENABLE_EXPORTER: bool = (
    os.environ.get("CYNC_ENABLE_EXPORTER", "1").casefold() in YES_ANSWER
)
CYNC_STATIC_DIR: str = os.environ.get("CYNC_STATIC_DIR", f"{CYNC_BASE_DIR}/www")
CYNC_HASS_APP = os.environ.get("CYNC_HASS_APP", "no") in YES_ANSWER

CYNC_MQTT_HOST = os.environ.get("CYNC_MQTT_HOST", "homeassistant.local")
CYNC_MQTT_PORT = os.environ.get("CYNC_MQTT_PORT", 1883)
CYNC_MQTT_USER = os.environ.get("CYNC_MQTT_USER")
CYNC_MQTT_PASS = os.environ.get("CYNC_MQTT_PASS")
CYNC_MQTT_CONN_DELAY: int = int(os.environ.get("CYNC_MQTT_CONN_DELAY", 10))
CYNC_TOPIC = os.environ.get("CYNC_TOPIC", "cync_lan")
CYNC_HASS_TOPIC = os.environ.get("CYNC_HASS_TOPIC", "homeassistant")
CYNC_HASS_STATUS_TOPIC = os.environ.get("CYNC_HASS_STATUS_TOPIC", "status")
CYNC_HASS_BIRTH_MSG = os.environ.get("CYNC_HASS_BIRTH_MSG", "online")
CYNC_HASS_WILL_MSG = os.environ.get("CYNC_HASS_WILL_MSG", "offline")
DEVICE_LWT_MSG: bytes = b"offline"

# Whether to expose a per-device "MITM Mode" switch entity in HASS. Off by default since
# it's a developer/reverse-engineering feature most users don't want cluttering their
# entity list; MITM mode itself is still usable via raw MQTT if needed.
CYNC_MITM_ENTITIES: bool = (
    os.environ.get("CYNC_MITM_ENTITIES", "no").casefold() in YES_ANSWER
)

CYNC_BRIDGE_DEVICE_REGISTRY_CONF: dict = {}
CYNC_BRIDGE_OBJ_ID: str = "cync_lan_bridge"
CYNC_MANUFACTURER = "Savant"
ORIGIN_STRUCT = {
    "name": "cync-lan",
    "sw_version": CYNC_VERSION,
    "support_url": SRC_REPO_URL,
}

EXPORT_SRV_START_TASK_NAME = "ExportServer_START"
MQTT_CLIENT_START_TASK_NAME = "MQTTClient_START"
nCYNC_START_TASK_NAME = "CyncLanServer_START"
