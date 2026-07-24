import asyncio
import inspect
import logging
import os
import socket
import ssl
import sys
import httpx
import websockets

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("VLESSKeepAlive")

# Configuration
TARGET_DOMAIN = os.environ.get("TARGET_DOMAIN", "johnson-ae836d17.dockfly.app")
VLESS_UUID = os.environ.get("VLESS_UUID", "Default")
PING_INTERVAL = int(os.environ.get("PING_INTERVAL", "30")) # Seconds between pings

# Tunnel target (using 127.0.0.1 avoids outbound firewall issues on Dockfly)
DEST_HOST = os.environ.get("DEST_HOST", "127.0.0.1")
DEST_PORT = int(os.environ.get("DEST_PORT", "80"))


def get_header_kwarg():
    """Inspects the websockets library to determine whether to use 'additional_headers' or 'extra_headers'."""
    try:
        sig = inspect.signature(websockets.connect)
        if "additional_headers" in sig.parameters:
            return "additional_headers"
        if "extra_headers" in sig.parameters:
            return "extra_headers"
    except Exception:
        pass
    
    # Fallback check based on websockets version
    ws_version = getattr(websockets, "__version__", "")
    if ws_version and int(ws_version.split(".")[0]) >= 13:
        return "additional_headers"
    return "extra_headers"


HEADER_KWARG_NAME = get_header_kwarg()


def build_vless_header(target_host: str, target_port: int) -> bytes:
    """Constructs a binary VLESS protocol request header matching main.py parse_vless_header()."""
    header = bytearray([0x00])          # VLESS Version 0
    header.extend(b"\x00" * 16)         # 16-byte UUID space
    header.append(0x00)                 # Addon length: 0
    header.append(0x01)                 # Command: 1 (TCP)
    header.extend(target_port.to_bytes(2, "big"))  # Port (2 bytes)

    try:
        ip_bytes = socket.inet_aton(target_host)
        header.append(0x01)             # Address Type: IPv4
        header.extend(ip_bytes)
    except socket.error:
        domain_bytes = target_host.encode("utf-8")
        header.append(0x02)             # Address Type: Domain
        header.append(len(domain_bytes))
        header.extend(domain_bytes)

    return bytes(header)


async def send_http_ping(client: httpx.AsyncClient):
    """Sends a standard HTTP request to keep Dockfly's edge router active."""
    url = f"https://{TARGET_DOMAIN}/health"
    try:
        res = await client.get(url)
        logger.info(f"[HTTP] Pinged {url} -> Status {res.status_code}")
    except Exception as err:
        logger.warning(f"[HTTP] Ping failed: {err}")


async def run_vless_keepalive():
    """Establishes an authentic VLESS connection over WebSocket."""
    ws_url = f"wss://{TARGET_DOMAIN}/ws/{VLESS_UUID}"
    ssl_ctx = ssl.create_default_context()
    vless_payload = build_vless_header(DEST_HOST, DEST_PORT)

    headers = {
        "Host": TARGET_DOMAIN,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    # Pass the dynamically resolved header parameter
    ws_kwargs = {
        "ssl": ssl_ctx,
        HEADER_KWARG_NAME: headers
    }

    while True:
        try:
            logger.info(f"[VLESS WS] Connecting to {ws_url} (using {HEADER_KWARG_NAME})...")
            
            async with websockets.connect(ws_url, **ws_kwargs) as ws:
                logger.info("[VLESS WS] Connected! Sending VLESS handshake header...")
                
                # 1. Send the binary VLESS header required by main.py
                await ws.send(vless_payload)
                logger.info("[VLESS WS] VLESS handshake accepted! Tunnel established.")

                async with httpx.AsyncClient(timeout=10.0) as http_client:
                    while True:
                        # 2. Send WebSocket ping frame
                        await ws.ping()
                        logger.info("[VLESS WS] Heartbeat ping sent.")
                        
                        # 3. Hit health endpoint for extra edge-router activity
                        await send_http_ping(http_client)
                        
                        await asyncio.sleep(PING_INTERVAL)

        except (websockets.exceptions.ConnectionClosed, Exception) as err:
            logger.error(f"[VLESS WS] Tunnel disconnected: {err}")
            logger.info("[VLESS WS] Reconnecting in 10 seconds...")
            await asyncio.sleep(10)


async def main():
    logger.info("================================================")
    logger.info("   VLESS Protocol Keep-Alive Worker Active      ")
    logger.info(f"   Target Domain : {TARGET_DOMAIN}")
    logger.info(f"   Inbound UUID  : {VLESS_UUID}")
    logger.info(f"   Header Param  : {HEADER_KWARG_NAME}")
    logger.info("================================================")
    await run_vless_keepalive()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user.")
        sys.exit(0)
