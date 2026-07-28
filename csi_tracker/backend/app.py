import asyncio
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import AppConfig
from .logging_writer import SessionLogger
from .session import TrackingSession

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")
LOG_DIR = os.path.join(BASE_DIR, "logs")
DEFAULT_CONFIG_PATH = os.environ.get(
    "CSI_TRACKER_CONFIG", os.path.join(BASE_DIR, "config", "devices.example.json")
)

app = FastAPI(title="CSI Human Localization Tracker")

config = AppConfig.load(DEFAULT_CONFIG_PATH)
session = TrackingSession(config, LOG_DIR)


class StartRequest(BaseModel):
    mode: str
    num_people: int | None = 2
    log_file: str | None = None
    speed: float | None = 1.0


@app.get("/api/config")
async def get_config():
    return {
        "room": {"width_m": config.room.width_m, "height_m": config.room.height_m},
        "calibration_seconds": config.calibration_seconds,
        "max_people": config.max_people,
        "devices": [
            {"id": d.id, "label": d.label, "x_m": d.x_m, "y_m": d.y_m, "source": d.source}
            for d in config.devices
        ],
    }


@app.post("/api/config/reload")
async def reload_config(path: str | None = None):
    global config
    config = AppConfig.load(path or DEFAULT_CONFIG_PATH)
    session.config = config
    return {"ok": True}


@app.get("/api/logs")
async def list_logs():
    return {"sessions": SessionLogger.list_sessions(LOG_DIR)}


@app.post("/api/session/start")
async def start_session(req: StartRequest):
    if req.mode == "playback":
        if not req.log_file:
            return {"ok": False, "error": "log_file is required for playback mode"}
        log_path = os.path.join(LOG_DIR, req.log_file)
        if not os.path.isfile(log_path):
            return {"ok": False, "error": f"log file not found: {req.log_file}"}
        await session.start("playback", log_path=log_path, speed=req.speed or 1.0)
    elif req.mode == "demo":
        await session.start("demo", num_people=req.num_people or 2)
    elif req.mode == "live":
        await session.start("live")
    else:
        return {"ok": False, "error": f"unknown mode: {req.mode}"}
    return {"ok": True}


@app.post("/api/session/stop")
async def stop_session():
    await session.stop()
    return {"ok": True}


@app.get("/api/session/state")
async def get_state():
    return session.snapshot()


@app.websocket("/ws")
async def websocket_stream(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json(session.snapshot())
            await asyncio.sleep(0.15)
    except WebSocketDisconnect:
        pass


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("shutdown")
async def shutdown():
    await session.stop()
