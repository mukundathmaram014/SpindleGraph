"""App factory. Dev: uvicorn spindlegraph.main:app --port 8787 (Vite proxies
/api and /ws). Non-dev: serves the built frontend from frontend/dist."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from . import db as dbm
from .api.routes import router
from .events import bus

# repo clone: frontend/dist; installed package: bundled spindlegraph/static
FRONTEND_DIST = next(
    (p for p in (Path(__file__).resolve().parents[2] / "frontend" / "dist",
                 Path(__file__).resolve().parent / "static")
     if p.is_dir()), None)


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


class SPAStaticFiles(StaticFiles):
    """Serve the built SPA, but tell browsers never to cache the HTML entry
    point. Vite's asset filenames are content-hashed (immutable), so only
    index.html needs to revalidate — otherwise a cached index.html keeps
    pointing at a stale JS bundle and new features silently don't appear."""

    async def get_response(self, path: str, scope) -> Response:
        resp = await super().get_response(path, scope)
        if "text/html" in resp.headers.get("content-type", ""):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


if FRONTEND_DIST is not None:
    app.mount("/", SPAStaticFiles(directory=str(FRONTEND_DIST), html=True), name="ui")
