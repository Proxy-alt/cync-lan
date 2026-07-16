from __future__ import annotations

import asyncio
import datetime
import logging
import ssl
import time
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional, Union

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from cync_lan.const import CYNC_LOG_NAME, CYNC_SRV_HOST, CYNC_SRV_PORT
from cync_lan.devices import CyncDevice, CyncTCPSession
from cync_lan.structs import EntityState, GlobalObject

if TYPE_CHECKING:
    # See structs.py's identical guard for why this is TYPE_CHECKING-only:
    # nothing here actually constructs a uvloop.Loop, only type-hints
    # against it, and a real uvloop install currently has no PyPI wheels for
    # newer CPython versions at all.
    import uvloop

__all__ = [
    "nCyncServer",
]
logger = logging.getLogger(CYNC_LOG_NAME)
g = GlobalObject()


class nCyncServer:
    """
    A class to represent a Cync LAN server that listens for connections from Cync Wi-Fi devices.
    The Wi-Fi devices translate messages, status updates and commands to/from the Cync BTLE mesh.
    """

    node_devices: Dict[int, CyncDevice] = {}
    tcp_connections: Dict[str, Optional[CyncTCPSession]] = {}
    app_tcp_connections: Dict[str, Optional[CyncTCPSession]] = {}
    shutting_down: bool = False
    running: bool = False
    host: str
    port: int
    cert_file: Optional[str] = None
    key_file: Optional[str] = None
    loop: Union[asyncio.AbstractEventLoop, uvloop.Loop]
    _server: Optional[asyncio.Server] = None
    lp: str = "nCync:"
    start_task: Optional[asyncio.Task] = None
    _instance: Optional["nCyncServer"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def get_dev_tcp_pool(self):
        # Was `or` - by De Morgan's law that's equivalent to "exclude only a
        # session that is BOTH closed AND the app simultaneously", which is
        # almost never true: a real device always has is_app=False, so
        # `not d.is_app` is always True, making the whole expression always
        # True regardless of is_closed(). Stale, already-closed sessions
        # (writer=None) never actually got filtered out of the broadcast
        # pool - confirmed via a real user's logs showing "writer is None,
        # can't write data!" thousands of times, once per command broadcast
        # per dead session still sitting in tcp_connections.
        return [d for d in self.tcp_connections.values() if not d.is_closed() and not d.is_app]

    def get_dev_tcp_pool_sync(self):
        return [d for d in self.tcp_connections.values() if not d.is_closed() and not d.is_app]

    def __init__(self, node_map: Dict[int, "CyncDevice"]):
        self.node_devices: Dict[int, "CyncDevice"] = node_map
        # nCyncServer is a singleton (__new__ above always returns the same
        # instance), but __init__ still runs on every construction - these
        # were left as class-level defaults instead of being reset here, so
        # a second construction within the same process (an entry reload,
        # or unload+re-add without a full HA restart) inherited whatever
        # state stop() left behind on the same instance. shutting_down in
        # particular: stop() sets it True and nothing ever set it back to
        # False, so every connection after a reload got permanently
        # rejected by can_connect() with "CyncLAN server is shutting down,
        # rejecting new connection..." - confirmed via a real user's log
        # showing that message continuously for 5+ hours after a reload,
        # alongside 100k+ "writer is None" messages from the dead sessions
        # that could never be replaced because nothing new could connect.
        self.tcp_connections: Dict[str, Optional[CyncTCPSession]] = {}
        self.app_tcp_connections: Dict[str, Optional[CyncTCPSession]] = {}
        self.tcp_conn_attempts: dict = {}
        self.shutting_down = False
        self.running = False
        self._server = None
        self.start_task = None
        self.ssl_context: Optional[ssl.SSLContext] = None
        self.host: str = CYNC_SRV_HOST
        self.port: str = CYNC_SRV_PORT
        g.reload_env()
        self.cert_file = g.env.cync_srv_ssl_cert
        self.key_file = g.env.cync_srv_ssl_key
        self.loop: Union[asyncio.AbstractEventLoop, uvloop.Loop] = (
            asyncio.get_event_loop()
        )

    async def remove_tcp_device(
        self, device: Union[CyncTCPSession, str]
    ) -> Optional[CyncTCPSession]:
        """
        Remove a TCP device from the server's device list.
        :param device: The CyncTCPDevice to remove.
        """
        dev = None
        lp = f"{self.lp}remove_tcp_device:"
        if isinstance(device, str):
            # if device is a string, it is the address
            if device in self.tcp_connections:
                device = self.tcp_connections[device]

        if isinstance(device, CyncTCPSession):
            if device.ip_address in self.tcp_connections:
                dev = self.tcp_connections.pop(device.ip_address, None)
                if dev is not None:
                    logger.debug(
                        f"{lp} Removed TCP device {device.ip_address} from server.tcp_devices."
                    )
                    # "state_topic": f"{self.topic}/status/bridge/tcp_devices/connected",
                    if g.mqtt_client is not None:
                        await g.mqtt_client.publish(
                            f"{g.env.mqtt_topic}/status/bridge/tcp_devices/connected",
                            str(len(self.tcp_connections)).encode(),
                        )
            else:
                logger.warning(
                    f"{lp} Device {device.ip_address} not found in TCP devices."
                )
        await self._update_app_stats()
        return dev

    async def add_tcp_device(self, device: CyncTCPSession):
        """
        Add a TCP device to the server's device list.
        :param device: The CyncTCPDevice to add.
        """
        lp = f"{self.lp}add_tcp_conn:"
        self.tcp_connections[device.ip_address] = device
        logger.debug(f"{lp} Adding {device.ip_address}")
        await self._update_app_stats()
        await device.start_tasks()

    async def _update_app_stats(self):
        """Publish count and IPs of connected apps."""
        if not g.mqtt_client:
            return
        apps = self.app_tcp_connections.values()
        # app_ips = [d.ip_address for d in apps]
        # todo: add app ip addresses as an attribute
        await g.mqtt_client.publish(
            f"{g.env.mqtt_topic}/status/bridge/apps/connected", str(len(apps)).encode()
        )

        devs = self.get_dev_tcp_pool_sync()
        await g.mqtt_client.publish(
            f"{g.env.mqtt_topic}/status/bridge/tcp_devices/connected",
            str(len(devs)).encode(),
        )


    @staticmethod
    def _ensure_self_signed_cert(cert_path: str, key_path: str) -> None:
        """Generate a self-signed cert/key pair if either file is missing.

        The standalone add-on's Dockerfile pre-generates these via an
        `openssl req -x509 ...` build step, so this was never needed there -
        but nothing does that for a pip/HACS-installed HA custom_component,
        which has no build step at all. Confirmed via a real HA install:
        server.start() crashed with FileNotFoundError on load_cert_chain
        because the cert/key simply never existed. Matches the Docker
        build's own parameters (RSA 4096, CN=*.xlink.cn, self-signed,
        ~3650 days) so behavior is identical either way. Blocking
        (file + crypto) work - callers must run this off the event loop.
        """
        cert_file, key_file = Path(cert_path), Path(key_path)
        if cert_file.exists() and key_file.exists():
            return
        cert_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.parent.mkdir(parents=True, exist_ok=True)

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "*.xlink.cn")]
        )
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=3650))
            .sign(private_key, hashes.SHA256())
        )

        key_file.write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    async def create_ssl_context(self):
        # async-dependency: cert generation and load_cert_chain are both
        # blocking (file + crypto) work - keep off the event loop the same
        # way as cloud_api.py's token-cache read/write.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._ensure_self_signed_cert, self.cert_file, self.key_file
        )
        # Allow the server to use a self-signed certificate
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        await loop.run_in_executor(
            None, ssl_context.load_cert_chain, self.cert_file, self.key_file
        )
        # turn off all the SSL verification
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        # figured out from debugging using socat
        # AES256-SHA256 to cloud
        # devices: ECDHE-RSA-AES256-GCM-SHA384
        # tls 1.2
        ciphers = [
            "ECDHE-RSA-AES256-GCM-SHA384",
            "ECDHE-RSA-AES128-GCM-SHA256",
            "ECDHE-RSA-AES256-SHA384",
            "ECDHE-RSA-AES128-SHA256",
            "ECDHE-RSA-AES256-SHA",
            "ECDHE-RSA-AES128-SHA",
            "ECDHE-RSA-DES-CBC3-SHA",
            "AES256-GCM-SHA384",
            "AES128-GCM-SHA256",
            "AES256-SHA256",
            "AES128-SHA256",
            "AES256-SHA",
            "AES128-SHA",
            "DES-CBC3-SHA",
        ]
        ssl_context.set_ciphers(":".join(ciphers))
        return ssl_context

    async def start(self):
        lp = f"{self.lp}start:"
        logger.debug(
            f"{lp} Creating SSL context - key: {self.key_file}, cert: {self.cert_file}"
        )
        try:
            self.ssl_context = await self.create_ssl_context()
            self._server = await asyncio.start_server(
                self._register_new_connection,
                host=self.host,
                port=self.port,
                ssl=self.ssl_context,  # Pass the SSL context to enable SSL/TLS
            )
        except asyncio.CancelledError as ce:
            logger.debug(f"{lp} Server start cancelled: {ce}")
            # propagate the cancellation
            raise ce
        except Exception as e:
            logger.exception("%s Failed to start server: %s" % (lp, e))
        else:
            logger.info(
                f"{lp} bound to {self.host}:{self.port} - Waiting for connections from Cync devices, if you dont"
                f" see any, check your DNS redirection, VLAN and firewall settings."
            )
            self.running = True
            try:
                if g.mqtt_client:
                    await g.mqtt_client.publish(
                        f"{g.env.mqtt_topic}/status/bridge/tcp_server/running",
                        "ON".encode(),
                    )
                async with self._server:
                    await self._server.serve_forever()
            except asyncio.CancelledError as ce:
                raise ce
            except Exception as e:
                logger.exception("%s Server Exception: %s" % (self.lp, e))
            else:
                logger.debug(
                    f"{lp} DEBUG>>> AFTER self._server.serve_forever() <<<DEBUG"
                )

    async def stop(self):
        try:
            self.shutting_down = True
            lp = f"{self.lp}stop:"
            device: CyncTCPSession
            devices = list(self.tcp_connections.values())
            if devices:
                logger.debug(
                    f"{lp} Shutting down, closing connections to {len(devices)} devices..."
                )
                for device in devices:
                    try:
                        await device.close()
                    except asyncio.CancelledError as ce:
                        logger.debug(f"{lp} Device close cancelled: {ce}")
                        # propagate the cancellation
                        raise ce
                    except Exception as e:
                        logger.exception(
                            "%s Error closing Cync Wi-Fi device connection: %s"
                            % (lp, e)
                        )
                    else:
                        logger.debug(f"{lp} Cync Wi-Fi device connection closed")
            else:
                logger.debug(f"{lp} No Cync Wi-Fi devices connected!")

            if self._server:
                if self._server.is_serving():
                    logger.debug(f"{lp} shutting down NOW...")
                    self._server.close()
                    await self._server.wait_closed()
                    if g.mqtt_client:
                        await g.mqtt_client.publish(
                            f"{g.env.mqtt_topic}/status/bridge/tcp_server/running",
                            "OFF".encode(),
                        )
                    logger.debug(f"{lp} shut down!")
                else:
                    logger.debug(f"{lp} not running!")

        except asyncio.CancelledError as ce:
            logger.debug(f"{lp} Server stop cancelled: {ce}")
            # propagate the cancellation
            raise ce
        except Exception as e:
            logger.exception(f"{lp} Error during server shutdown: {e}")
        else:
            logger.info(f"{lp} Server stopped successfully.")
        finally:
            if self.start_task and not self.start_task.done():
                logger.debug(f"{lp} FINISHING: Cancelling start task")
                self.start_task.cancel()

    async def _register_new_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        dev2add = None
        client_addr: str = writer.get_extra_info("peername")[0]
        if client_addr in self.tcp_conn_attempts:
            self.tcp_conn_attempts[client_addr] += 1
        else:
            self.tcp_conn_attempts[client_addr] = 1
        lp = f"{self.lp}new_conn:{client_addr}:"
        existing_device = await self.remove_tcp_device(client_addr)
        if existing_device is not None:
            _add_str = ""
            if not existing_device.mitm_mode:
                _add_str = " closing and"
                await existing_device.close()
            logger.debug(
                f"{lp} Existing TCP session found, gracefully{_add_str} replacing..."
            )
        try:
            if existing_device is not None:
                if existing_device.allowed_to_connect is False:
                    # Was missing `await` - can_connect() is async, so this
                    # always compared a coroutine object to False (always
                    # False) and the rejection branch below never ran. A
                    # previously-disallowed device (e.g. rejected for
                    # exceeding CYNC_MAX_TCP_CONN) would silently get
                    # "resurrected" and re-added on every reconnect attempt
                    # instead of being re-evaluated, which the coroutine
                    # itself was never even awaited to run in the first
                    # place - contributing to real-world connection-count
                    # exhaustion and "writer is None" errors from stale
                    # sessions never being cleared. Confirmed via a real
                    # user's logs: repeated "coroutine 'can_connect' was
                    # never awaited" RuntimeWarning plus persistent "server
                    # max TCP connections reached" churn.
                    can_connect = await existing_device.can_connect()
                    if can_connect is False:
                        del existing_device
                        existing_device = None
                        dev2add = None
                if existing_device is not None:
                    existing_device.reader = reader
                    existing_device.writer = writer
                    existing_device.ip_address = client_addr
                    await existing_device.existing_init()
                    dev2add = existing_device
            else:
                dev2add = CyncTCPSession(reader, writer, client_addr)
            if dev2add is not None:
                await self.add_tcp_device(dev2add)
        except asyncio.CancelledError as ce:
            logger.debug(f"{lp} Connection cancelled: {ce}")
            # propagate the cancellation
            raise ce
        except Exception as e:
            logger.exception(f"{lp} Error creating new Cync Wi-Fi device: {e}")
