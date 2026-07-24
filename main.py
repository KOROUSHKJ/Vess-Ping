async def run_vless_keepalive():
    """Establishes an authentic VLESS connection over WebSocket."""
    ws_url = f"wss://{TARGET_DOMAIN}/ws/{VLESS_UUID}"
    ssl_ctx = ssl.create_default_context()
    vless_payload = build_vless_header(DEST_HOST, DEST_PORT)

    headers = {
        "Host": TARGET_DOMAIN,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    while True:
        try:
            logger.info(f"[VLESS WS] Connecting to {ws_url}...")
            
            # Compatible with both newer (v13+) and older websockets versions
            try:
                connect_cm = websockets.connect(ws_url, ssl=ssl_ctx, additional_headers=headers)
            except TypeError:
                connect_cm = websockets.connect(ws_url, ssl=ssl_ctx, extra_headers=headers)

            async with connect_cm as ws:
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
