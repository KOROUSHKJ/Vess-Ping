import asyncio
import inspect
import logging
import os
import socket
import ssl
import sys
import uuid
from urllib.parse import parse_qs, urlparse
import websockets

# Configure clean logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("VLESSKeepAlive")

# Pre-configured with your VLESS link
DEFAULT_VLESS_URL = (
    "vless://Default@johnson-ae836d17.dockfly.app:443"
    "?encryption=none&security=tls&type=ws&host=johnson-ae836d17.dockfly.app"
    "&path=/ws/Default&sni=johnson-ae836d17.dockfly.app&fp=chrome&alpn=http/1.1#Luffy-Default"
)

VLESS_URL = os.environ.get("VLESS_URL", DEFAULT_VLESS_URL)
PING_INTERVAL = int(os.environ.get("PING_INTERVAL", "20"))  # Seconds between keep-alive pings

# Tunnel destination (using loopback keeps traffic lightweight)
DEST_HOST = os.environ.get("DEST_HOST", "127.0.0.1")
DEST_PORT = int(os.environ.get("DEST_PORT", "80"))


def parse_vless_config(raw_url: str):
    """Parses a vless:// URI into a WebSocket URL, HTTP headers, SNI, and UUID bytes."""
    parsed = urlparse(raw_url)
    query = parse_qs(parsed.query)

    vless_id = parsed.username or "Default"
    host_domain = parsed.hostname or "johnson-ae836d17.dockfly.app"
    port = parsed.port or 443

    security = query.get("security", ["tls"])[0]
    path = query.get("path", ["/ws/Default"])[0]
    sni = query.get("sni", [host_domain])[0]
    header_host = query.get("host", [host_domain])[0]

    scheme = "wss" if security in ("tls", "reality") else "ws"
    ws_url = f"{scheme}://{host_domain}:{port}{path}"

    headers = {
        "Host": header_host,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    # Convert identifier to 16-byte VLESS payload format
    try:
        uuid_bytes = uuid.UUID(vless_id).bytes
    except Exception:
        uuid_bytes = vless_id.encode("utf-8").ljust(16, b"\x00")[:16]

    return ws_url, headers, sni, uuid_bytes


def get_header_kwarg():
    """Detects whether websockets library requires 'additional_headers' or 'extra_headers'."""
    try:
        sig = inspect.signature(websockets.connect)
        if "additional_headers" in sig.parameters:
            return "additional_headers"
        if "extra_headers" in sig.parameters:
            return "extra_headers"
    except Exception:
        pass

    ws_version = getattr(websockets, "__version__", "")
    if ws_version and int(ws_version.split(".")[0]) >= 13:
        return "additional_headers"
    return "extra_headers"


HEADER_KWARG_NAME = get_header_kwarg()


def build_vless_header(uuid_bytes: bytes, target_host: str, target_port: int) -> bytes:
    """Constructs a binary VLESS protocol request header matching main.py parse_vless_header()."""
    header = bytearray([0x00])              # VLESS Version 0
    header.extend(uuid_bytes)               # 16-byte UUID space
    header.append(0x00)                     # Addon length: 0
    header.append(0x01)                     # Command: 1 (TCP)
    header.extend(target_port.to_bytes(2, "big"))

    try:
        ip_bytes = socket.inet_aton(target_host)
        header.append(0x01)                 # IPv4 Address
        header.extend(ip_bytes)
    except socket.error:
        domain_bytes = target_host.encode("utf-8")
        header.append(0x02)                 # Domain Address
        header.append(len(domain_bytes))
        header.extend(domain_bytes)

    return bytes(header)


async def run_vless_keepalive():
    """Establishes and maintains an active VLESS connection continuously."""
    ws_url, headers, sni, uuid_bytes = parse_vless_config(VLESS_URL)

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.server_hostname = sni

    vless_payload = build_vless_header(uuid_bytes, DEST_HOST, DEST_PORT)

    ws_kwargs = {
        "ssl": ssl_ctx,
        "ping_interval": 20,
        "ping_timeout": 20,
        HEADER_KWARG_NAME: headers,
    }

    while True:
        try:
            logger.info(f"[VLESS WS] Connecting to {ws_url}...")

            async with websockets.connect(ws_url, **ws_kwargs) as ws:
                logger.info("[VLESS WS] Connection established! Sending VLESS binary handshake...")

                # 1. Send the VLESS handshake header
                await ws.send(vless_payload)
                logger.info("[VLESS WS] Handshake accepted! VLESS connection is continuously ACTIVE.")

                # 2. Continuous keep-alive loop over the open socket connection
                while True:
                    await asyncio.sleep(PING_INTERVAL)
                    pong_waiter = await ws.ping()
                    await pong_waiter
                    logger.info("[VLESS WS] Heartbeat ping/pong frame exchanged.")

        except (websockets.exceptions.ConnectionClosed, Exception) as err:
            logger.error(f"[VLESS WS] Connection dropped: {err}")
            logger.info("[VLESS WS] Reconnecting in 10 seconds...")
            await asyncio.sleep(10)


async def main():
    ws_url, _, sni, _ = parse_vless_config(VLESS_URL)
    logger.info("================================================")
    logger.info("   Pure VLESS Config Keep-Alive Active          ")
    logger.info(f"   Target Endpoint : {ws_url}")
    logger.info(f"   SNI Server Name : {sni}")
    logger.info("================================================")
    await run_vless_keepalive()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user.")
        sys.exit(0)
