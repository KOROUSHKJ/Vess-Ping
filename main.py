import asyncio
import logging
import os
import ssl
import sys
import httpx
import websockets

# Configure clean logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("KeepAliveBot")

# Configuration
TARGET_DOMAIN = os.environ.get("TARGET_DOMAIN", "johnson-ae836d17.dockfly.app")
VLESS_UUID = os.environ.get("VLESS_UUID", "Default")
PING_INTERVAL = int(os.environ.get("PING_INTERVAL", "60"))  # Seconds between pings

async def send_http_ping(client: httpx.AsyncClient):
    """Sends a standard HTTP GET request to reset Dockfly's edge router timer."""
    url = f"https://{TARGET_DOMAIN}/health"
    try:
        response = await client.get(url)
        logger.info(f"[HTTP] Pinged {url} -> Status {response.status_code}")
    except Exception as exc:
        logger.warning(f"[HTTP] Ping failed: {exc}")

async def run_vless_keepalive():
    """Maintains a continuous WebSocket tunnel and handles auto-reconnection."""
    ws_url = f"wss://{TARGET_DOMAIN}/ws/{VLESS_UUID}"
    ssl_context = ssl.create_default_context()

    while True:
        try:
            logger.info(f"[WS] Connecting to {ws_url}...")
            async with websockets.connect(ws_url, ssl=ssl_context) as ws:
                logger.info("[WS] Connection established! Tunnel active.")
                
                async with httpx.AsyncClient(timeout=10.0) as http_client:
                    while True:
                        # 1. Send a WebSocket ping frame
                        await ws.ping()
                        logger.info("[WS] Sent heartbeat ping frame.")
                        
                        # 2. Hit the HTTP health endpoint for extra redundancy
                        await send_http_ping(http_client)
                        
                        # Wait for the next ping cycle
                        await asyncio.sleep(PING_INTERVAL)

        except (websockets.exceptions.ConnectionClosed, Exception) as err:
            logger.error(f"[WS] Connection dropped: {err}")
            logger.info("[WS] Retrying connection in 10 seconds...")
            await asyncio.sleep(10)

async def main():
    logger.info("==============================================")
    logger.info("   Dockfly Keep-Alive Worker Active          ")
    logger.info(f"   Target Domain : {TARGET_DOMAIN}")
    logger.info(f"   Inbound UUID  : {VLESS_UUID}")
    logger.info(f"   Ping Interval : {PING_INTERVAL} seconds")
    logger.info("==============================================")
    
    await run_vless_keepalive()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user.")
        sys.exit(0)
