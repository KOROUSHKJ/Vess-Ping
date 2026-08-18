import asyncio
import logging
import os
import sys
import urllib.request
import urllib.error
from collections import deque
from datetime import datetime

# --- Analytics & Dashboard Memory ---
stats = {
    "date": datetime.now().strftime("%Y-%m-%d"),
    "wake_ups": 0,
    "deploys": 0,
    "failed_attempts": 0
}
log_history = deque(maxlen=60)  # Stores the last 60 log lines for the UI

class WebUIHandler(logging.Handler):
    """Custom logging handler that pipes logs directly to our web dashboard"""
    def emit(self, record):
        msg = self.format(record)
        log_history.appendleft(msg)

# Configure clean logging
logger = logging.getLogger("DockflyWakeBot")
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

# Console Output
ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(formatter)
logger.addHandler(ch)

# Dashboard Output
wh = WebUIHandler()
wh.setFormatter(formatter)
logger.addHandler(wh)

# --- Configuration & Constants ---
APP_URL = "https://karl-5d6932bd.dockfly.app"
HTTP_PING_INTERVAL = 180       # Seconds (3 min) between checks

# --- Dockfly API Credentials ---
DOCKFLY_API_TOKEN = "paat_gkvxe5ra_6b0b7ddafb1c21a1955f0ab9b3866e8fb0f043816b643b3b9f81a2035903c38f"
DOCKFLY_WAKE_URL = "https://api.dockfly.app/projects/01a0142c-b1d4-779a-9adc-1d3b55c4814c/services/01a0142d-b2ae-7aec-8028-2dc45d6932bd/start"
DOCKFLY_DEPLOY_URL = "https://api.dockfly.app/projects/01a0142c-b1d4-779a-9adc-1d3b55c4814c/services/01a0142d-b2ae-7aec-8028-2dc45d6932bd/deployments"


def reset_stats_if_new_day():
    current_date = datetime.now().strftime("%Y-%m-%d")
    if stats["date"] != current_date:
        stats["date"] = current_date
        stats["wake_ups"] = 0
        stats["deploys"] = 0
        logger.info("[System] Midnight rollover. Stats reset to 0.")


def _send_dockfly_command(url, action_name):
    """Sends a direct POST request to Dockfly API."""
    try:
        req = urllib.request.Request(
            url,
            data=b"{}",
            headers={
                "Authorization": f"Bearer {DOCKFLY_API_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "ApplyBuild-WakeBot/2.0"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            logger.info(f"[API {action_name}] Command accepted! Status: {resp.status}")
            return True
    except urllib.error.HTTPError as e:
        logger.error(f"[API {action_name}] Failed with HTTP Error: {e.code} - {e.reason}")
    except Exception as e:
        logger.error(f"[API {action_name}] Connection error: {e}")
    return False


async def http_keepalive_loop():
    """Main health-check loop with 5-strike redeploy logic."""
    while True:
        reset_stats_if_new_day()
        is_awake = False
        
        # 1. Check if the server is already awake
        try:
            req = urllib.request.Request(APP_URL, headers={"User-Agent": "ApplyBuild-WakeBot/2.0"})
            def check_alive():
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status
            
            status_code = await asyncio.to_thread(check_alive)
            
            if status_code == 200:
                logger.info("[Status Check] Server is ALIVE. Skipping API commands.")
                is_awake = True
                stats["failed_attempts"] = 0  # Reset fail streak
            else:
                logger.info(f"[Status Check] Server returned {status_code}. Checking rules...")
                
        except Exception as e:
            logger.info(f"[Status Check] Server is ASLEEP or unreachable. Checking rules...")

        # 2. Logic: Wake Up vs Force Deploy
        if not is_awake:
            if stats["failed_attempts"] >= 5:
                # We hit 5 strikes. Trigger a fresh deployment.
                logger.warning(f"⚠️ [Action] 5 consecutive failures! Triggering REDEPLOY...")
                success = await asyncio.to_thread(_send_dockfly_command, DOCKFLY_DEPLOY_URL, "Deploy")
                
                if success:
                    stats["deploys"] += 1
                    stats["failed_attempts"] = 0  # Reset streak after deploy
                    logger.info("⏳ Waiting 45 seconds for redeployment to finish building...")
                    await asyncio.sleep(45)
            else:
                # Normal wake up procedure
                logger.info(f"[Action] Triggering Wake Up... (Fail Streak: {stats['failed_attempts']}/5)")
                success = await asyncio.to_thread(_send_dockfly_command, DOCKFLY_WAKE_URL, "Wake Up")
                
                if success:
                    logger.info("⏳ Waiting 15 seconds for container's cold-start...")
                    await asyncio.sleep(15)
                
                # Verify post-wake
                try:
                    req = urllib.request.Request(APP_URL, headers={"User-Agent": "ApplyBuild-WakeBot/2.0"})
                    def verify_alive():
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            return resp.status
                    verify_status = await asyncio.to_thread(verify_alive)
                    
                    if verify_status == 200:
                        logger.info(f"[Post-Wake Check] Server successfully booted!")
                        stats["wake_ups"] += 1
                        stats["failed_attempts"] = 0
                    else:
                        logger.warning(f"[Post-Wake Check] HTTP {verify_status}. It might still be booting.")
                        stats["failed_attempts"] += 1
                except Exception as e:
                    logger.warning(f"[Post-Wake Check] Failed connection.")
                    stats["failed_attempts"] += 1

        # 3. Wait 3 minutes before next check
        await asyncio.sleep(HTTP_PING_INTERVAL)


async def handle_platform_health_check(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Serves the live HTML dashboard to james.apps.apply.build"""
    try:
        # Read the HTTP request headers (required to prevent socket errors)
        request_line = await reader.readline()
        while True:
            line = await reader.readline()
            if not line or line == b'\r\n':
                break

        # Format logs for HTML rendering
        formatted_logs = "\n".join([f"<div class='log-line'>{line}</div>" for line in log_history])
        
        # HTML UI (Dark Mode Dashboard)
        html_content = f"""<!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta http-equiv="refresh" content="30">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Apply.Build | WakeBot Dashboard</title>
            <style>
                body {{ font-family: 'Courier New', Courier, monospace; background-color: #121212; color: #e0e0e0; padding: 20px; }}
                .container {{ max-width: 900px; margin: 0 auto; }}
                .header {{ border-bottom: 2px solid #333; padding-bottom: 10px; margin-bottom: 20px; }}
                h1 {{ color: #00ffcc; margin: 0; }}
                .stats-grid {{ display: flex; gap: 15px; margin-bottom: 20px; }}
                .stat-box {{ background: #1e1e1e; padding: 20px; border-radius: 8px; flex: 1; text-align: center; border: 1px solid #333; }}
                .stat-box h2 {{ margin: 0; font-size: 2.5em; color: #00ffcc; }}
                .stat-box.danger h2 {{ color: #ff4444; }}
                .stat-box p {{ margin: 5px 0 0; color: #888; font-size: 0.9em; text-transform: uppercase; letter-spacing: 1px; }}
                .terminal {{ background: #000; padding: 15px; border-radius: 8px; border: 1px solid #333; height: 500px; overflow-y: auto; font-size: 14px; box-shadow: inset 0 0 10px #000; }}
                .log-line {{ padding: 4px 0; border-bottom: 1px dashed #222; }}
                .log-line:hover {{ background: #111; }}
                .refresh-note {{ color: #555; font-size: 12px; margin-top: 10px; text-align: right; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🤖 Dockfly Guardian Dashboard</h1>
                </div>
                
                <div class="stats-grid">
                    <div class="stat-box">
                        <h2>{stats['wake_ups']}</h2>
                        <p>Successful Wake Ups</p>
                    </div>
                    <div class="stat-box">
                        <h2>{stats['deploys']}</h2>
                        <p>Total Redeploys</p>
                    </div>
                    <div class="stat-box {'danger' if stats['failed_attempts'] >= 3 else ''}">
                        <h2>{stats['failed_attempts']} / 5</h2>
                        <p>Current Fail Streak</p>
                    </div>
                </div>
                
                <h3>Live Console (Date: {stats['date']})</h3>
                <div class="terminal">
                    {formatted_logs}
                </div>
                <div class="refresh-note">Dashboard auto-updates every 30 seconds.</div>
            </div>
        </body>
        </html>
        """

        # Construct raw HTTP Response
        response = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(html_content.encode('utf-8'))}\r\n"
            "Connection: close\r\n\r\n"
            f"{html_content}"
        )
        
        writer.write(response.encode("utf-8"))
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def start_dashboard_server():
    port = int(os.environ.get("PORT", "8080"))
    server = await asyncio.start_server(handle_platform_health_check, "0.0.0.0", port)
    logger.info(f"[Web Dashboard] Live and accessible at port {port}.")
    async with server:
        await server.serve_forever()


async def main():
    logger.info("================================================")
    logger.info("   Dockfly Guardian V2 Active                   ")
    logger.info(f"   Target URL : {APP_URL}")
    logger.info("================================================")

    await asyncio.gather(
        start_dashboard_server(),
        http_keepalive_loop()
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user.")
        sys.exit(0)
