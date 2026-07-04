"""App factory. Dev: uvicorn spindlegraph.main:app --port 8787 (Vite proxies
/api and /ws). Non-dev: serves the built frontend from frontend/dist."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import db as dbm
from .api.routes import router
from .events import bus

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    dbm.init_db()
    yield


app = FastAPI(title="SpindleGraph", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.websocket("/ws/projects/{project_id}")
async def project_ws(ws: WebSocket, project_id: int):
    await ws.accept()
    q = bus.subscribe(project_id)
    try:
        while True:
            event = await q.get()
            await ws.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(project_id, q)


if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="ui")
