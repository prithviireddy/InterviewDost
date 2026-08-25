"""
/api/v1/stt — Deepgram STT proxy WebSocket.

Opens a WebSocket connection to Deepgram on behalf of the browser client and
forwards audio bytes in both directions (no auth required — mirrors original).

Client sends: raw audio bytes (binary frames)
Server sends: Deepgram transcript JSON (text frames) + { "type": "connected" }
"""
import asyncio
import json

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from config import get_settings

router = APIRouter()
settings = get_settings()

_DG_URL = "wss://api.deepgram.com/v1/listen?model=nova-2&smart_format=true&endpointing=300&vad_events=true"


@router.websocket("/api/v1/stt")
async def stt_ws(websocket: WebSocket):
    await websocket.accept()

    headers = {"Authorization": f"Token {settings.deepgram_api_key}"}
    try:
        try:
            connect_ctx = websockets.connect(_DG_URL, additional_headers=headers, open_timeout=10)
        except TypeError:
            connect_ctx = websockets.connect(_DG_URL, extra_headers=headers, open_timeout=10)

        async with connect_ctx as dg_ws:
            # Notify client that the Deepgram connection is ready
            await websocket.send_text(json.dumps({"type": "connected"}))

            async def client_to_dg():
                """Forward binary audio frames from the browser to Deepgram."""
                try:
                    while True:
                        data = await websocket.receive_bytes()
                        if dg_ws.open:
                            await dg_ws.send(data)
                except (WebSocketDisconnect, Exception):
                    # Client disconnected — close Deepgram too
                    if dg_ws.open:
                        await dg_ws.close()

            async def dg_to_client():
                """Forward Deepgram transcript messages back to the browser."""
                try:
                    async for msg in dg_ws:
                        try:
                            if isinstance(msg, bytes):
                                await websocket.send_bytes(msg)
                            else:
                                await websocket.send_text(msg)
                        except Exception:
                            break
                except Exception:
                    pass

            # Run both directions concurrently; stop when either side closes
            await asyncio.gather(client_to_dg(), dg_to_client(), return_exceptions=True)

    except (WebSocketDisconnect, Exception):
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
