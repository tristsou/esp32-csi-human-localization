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
EXAMPLE_CONFIG_PATH = os.path.join(BASE_DIR, "config", "devices.example.json")
LOCAL_CONFIG_PATH = os.path.join(BASE_DIR, "config", "devices.local.json")

# CSI_TRACKER_CONFIG, if set, is an explicit choice and is also where Save
# writes back to. Otherwise prefer a previously-saved local config over the
# tracked example file, so edits survive a restart without touching git.
if "CSI_TRACKER_CONFIG" in os.environ:
    DEFAULT_CONFIG_PATH = os.environ["CSI_TRACKER_CONFIG"]
elif os.path.isfile(LOCAL_CONFIG_PATH):
    DEFAULT_CONFIG_PATH = LOCAL_CONFIG_PATH
else:
    DEFAULT_CONFIG_PATH = EXAMPLE_CONFIG_PATH

app = FastAPI(title="CSI Human Localization Tracker")

config = AppConfig.load(DEFAULT_CONFIG_PATH)
session = TrackingSession(config, LOG_DIR)


class StartRequest(BaseModel):
    mode: str
    num_people: int | None = 2
    log_file: str | None = None
    speed: float | None = 1.0


class RoomModel(BaseModel):
    width_m: float
    height_m: float


class DeviceModel(BaseModel):
    id: str
    label: str
    x_m: float
    y_m: float
    source: str = "serial"
    port: str = ""
    baud: int = 921600
    host: str = ""
    tcp_port: int = 0


class ConfigModel(BaseModel):
    room: RoomModel
    calibration_seconds: int
    max_people: int
    devices: list[DeviceModel]


@app.get("/api/config")
async def get_config():
    return config.to_dict()


@app.put("/api/config")
async def put_config(req: ConfigModel):
    global config
    if session.state not in ("idle", "stopped"):
        return {"ok": False, "error": "Cannot change configuration while a session is running"}
    config = AppConfig.from_dict(req.model_dump())
    session.config = config
    return {"ok": True, "config": config.to_dict()}


@app.post("/api/config/save")
async def save_config():
    try:
        config.validate()
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    save_path = DEFAULT_CONFIG_PATH if "CSI_TRACKER_CONFIG" in os.environ else LOCAL_CONFIG_PATH
    config.save(save_path)
    return {"ok": True, "path": save_path}


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
    try:
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
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
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
