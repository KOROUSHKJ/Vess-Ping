import asyncio
import inspect
import logging
import os
import socket
import ssl
import sys
import urllib.request
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

DEFAULT_VLESS_URL = (
    "vless://Default@johnson-ae836d17.dockfly.app:443"
    "?encryption=none&security=tls&type=ws&host=johnson-ae836d17.dockfly.app"
    "&path=/ws/Default&sni=johnson-ae836d17.dockfly.app&fp=chrome&alpn=http/1.1#Luffy-Default"
)

VLESS_URL = os.environ.get("VLESS_URL", DEFAULT_VLESS_URL)
WS_PING_INTERVAL = 20          # Seconds between WebSocket pings
HTTP_PING_INTERVAL = 180       # Seconds (3 min) between HTTP requests to reset Dockfly timer
DEST_HOST = os.environ.get("DEST_HOST", "127.0.0.1")
DEST_PORT = int(os.environ.get("DEST_PORT", "80"))


def parse_vless_config(raw_url: str):
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
    http_url = f"https://{host_domain}/sub/{vless_id}"

    headers = {
        "Host": header_host,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    try:
        uuid_bytes = uuid.UUID(vless_id).bytes
    except Exception:
        uuid_bytes = vless_id.encode("utf-8").ljust(16, b"\x00")[:16]

    return ws_url, http_url, headers, sni, uuid_bytes


def get_header_kwarg():
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
    header = bytearray([0x00])
    header.extend(uuid_bytes)
    header.append(0x00)
    header.append(0x01)
    header.extend(target_port.to_bytes(2, "big"))

    try:
        ip_bytes = socket.inet_aton(target_host)
        header.append(0x01)
        header.extend(ip_bytes)
    except socket.error:
        domain_bytes = target_host.encode("utf-8")
        header.append(0x02)
        header.append(len(domain_bytes))
        header.extend(domain_bytes)

    return bytes(header)


def _send_http_request(url: str):
    """Sends an HTTP GET request to the edge load balancer using standard library."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info(f"[HTTP Edge Ping] Hitting {url} -> Status {resp.status}")
    except Exception as e:
        logger.info(f"[HTTP Edge Ping] Request registered at edge: {e}")


async def http_keepalive_loop(http_url: str):
    """Periodically sends HTTP requests to prevent platform scale-to-zero timeouts."""
    while True:
        await asyncio.to_thread(_send_http_request, http_url)
        await asyncio.sleep(HTTP_PING_INTERVAL)


async def run_vless_keepalive():
    ws_url, http_url, headers, sni, uuid_bytes = parse_vless_config(VLESS_URL)

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.server_hostname = sni

    vless_payload = build_vless_header(uuid_bytes, DEST_HOST, DEST_PORT)

    ws_kwargs = {
        "ssl": ssl_ctx,
        "ping_interval": 20,
        "ping_timeout": 20,
        HEADER_KWARG_NAME: headers,
    }

    # Start background HTTP pings
    asyncio.create_task(http_keepalive_loop(http_url))

    while True:
        try:
            logger.info(f"[VLESS WS] Connecting to {ws_url}...")

            async with websockets.connect(ws_url, **ws_kwargs) as ws:
                logger.info("[VLESS WS] Connection established! Sending VLESS binary handshake...")
                await ws.send(vless_payload)
                logger.info("[VLESS WS] Handshake accepted! Connection active.")

                while True:
                    await asyncio.sleep(WS_PING_INTERVAL)
                    pong_waiter = await ws.ping()
                    await pong_waiter
                    logger.info("[VLESS WS] Heartbeat ping frame exchanged.")

        except (websockets.exceptions.ConnectionClosed, Exception) as err:
            logger.error(f"[VLESS WS] Connection dropped: {err}")
            logger.info("[VLESS WS] Reconnecting in 10 seconds...")
            await asyncio.sleep(10)


async def handle_platform_health_check(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        await reader.read(1024)
        response = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/plain\r\n"
            "Content-Length: 12\r\n"
            "Connection: close\r\n\r\n"
            "Bot Active\n"
        )
        writer.write(response.encode("utf-8"))
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def start_dummy_http_server():
    port = int(os.environ.get("PORT", "8080"))
    server = await asyncio.start_server(handle_platform_health_check, "0.0.0.0", port)
    logger.info(f"[HTTP Server] Listening on 0.0.0.0:{port} for platform health checks.")
    async with server:
        await server.serve_forever()


async def main():
    ws_url, http_url, _, _, _ = parse_vless_config(VLESS_URL)
    logger.info("================================================")
    logger.info("   VLESS Dual-Mode Keep-Alive Active            ")
    logger.info(f"   Target Endpoint : {ws_url}")
    logger.info(f"   HTTP Edge Ping  : {http_url}")
    logger.info("================================================")

    await asyncio.gather(
        start_dummy_http_server(),
        run_vless_keepalive()
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user.")
        sys.exit(0)
