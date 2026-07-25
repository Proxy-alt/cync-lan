"""Shared fixtures for the cync-lan-mqtt test suite.

MQTTClient and GlobalObject are both process-wide singletons, so every test
that touches either must reset it - otherwise whichever test ran first owns
the state for the rest of the session.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from cync_lan.structs import GlobalObject

from cync_lan_mqtt.mqtt_client import MQTTClient


@pytest.fixture(autouse=True)
def reset_singletons():
    MQTTClient._instance = None
    g = GlobalObject()
    previous_server, previous_mqtt = g.ncync_server, g.mqtt_client
    yield
    g.ncync_server, g.mqtt_client = previous_server, previous_mqtt
    MQTTClient._instance = None


@pytest.fixture
def client() -> MQTTClient:
    """An MQTTClient with its broker I/O stubbed out."""
    c = MQTTClient()
    c.publish = AsyncMock(return_value=True)
    c.publish_json_msg = AsyncMock(return_value=True)
    c._connected = True
    return c


@pytest.fixture
def fake_server() -> MagicMock:
    server = MagicMock()
    server.node_devices = {}
    GlobalObject().ncync_server = server
    return server
