import asyncio
import logging
import os
import sys
import urllib.request
import urllib.error

# Configure clean logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("DockflyWakeBot")

# --- Configuration & Constants ---
APP_URL = "https://johnson-ae836d17.dockfly.app"
HTTP_PING_INTERVAL = 180       # Seconds (3 min) between checks

# --- Dockfly API Wake-Up Credentials ---
DOCKFLY_API_TOKEN = "paat_yn92ycn3_dddd693a700f02227959e5b0c3376aef275c2d03d742acbc6489d35328a22e80"
DOCKFLY_WAKE_URL = "https://api.dockfly.app/projects/019f7985-494f-75ca-ab9d-08f2a0bda2e8/services/019f938a-4aab-7492-9e46-2817ae836d17/start"


def _send_api_wake_request():
    """Sends a direct POST request to Dockfly API to wake up the container."""
    try:
        req = urllib.request.Request(
            DOCKFLY_WAKE_URL,
            data=b"{}",  # Empty JSON body required for POST
            headers={
                "Authorization": f"Bearer {DOCKFLY_API_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "ApplyBuild-WakeBot/1.0"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            logger.info(f"[API Wake Trigger] Command sent! Status: {resp.status}")
    except urllib.error.HTTPError as e:
        logger.error(f"[API Wake Trigger] Failed with HTTP Error: {e.code} - {e.reason}")
    except Exception as e:
        logger.error(f"[API Wake Trigger] Connection error: {e}")


async def http_keepalive_loop():
    """Checks if server is alive, and only triggers the API wake command if it's asleep."""
    while True:
        is_awake = False
        
        # 1. Check if the server is already awake
        try:
            req = urllib.request.Request(
                APP_URL, 
                headers={"User-Agent": "ApplyBuild-WakeBot/1.0"}
            )
            
            def check_alive():
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status
            
            status_code = await asyncio.to_thread(check_alive)
            
            if status_code == 200:
                logger.info("[Status Check] Server is ALIVE. Skipping API wake command.")
                is_awake = True
            else:
                logger.info(f"[Status Check] Server returned {status_code}. Triggering wake up...")
                
        except Exception as e:
            logger.info(f"[Status Check] Server is ASLEEP or unreachable. Triggering wake up...")

        # 2. If asleep, hit the Dockfly API to wake it
        if not is_awake:
            await asyncio.to_thread(_send_api_wake_request)
            
            # Wait 10 seconds for the container's cold-start
            await asyncio.sleep(10)
            
            # Verify it actually woke up
            try:
                req = urllib.request.Request(APP_URL, headers={"User-Agent": "ApplyBuild-WakeBot/1.0"})
                def verify_alive():
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        return resp.status
                
                verify_status = await asyncio.to_thread(verify_alive)
                logger.info(f"[Post-Wake Check] Server successfully booted! Status: {verify_status}")
            except Exception as e:
                logger.warning(f"[Post-Wake Check] Server might still be booting: {e}")

        # 3. Wait 3 minutes before checking again
        await asyncio.sleep(HTTP_PING_INTERVAL)


async def handle_platform_health_check(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Answers the internal ping from apply.build so the worker isn't killed."""
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
    logger.info("================================================")
    logger.info("   Dockfly Auto-Wake Bot Active                 ")
    logger.info(f"   Target URL : {APP_URL}")
    logger.info("================================================")

    await asyncio.gather(
        start_dummy_http_server(),
        http_keepalive_loop()
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user.")
        sys.exit(0)
