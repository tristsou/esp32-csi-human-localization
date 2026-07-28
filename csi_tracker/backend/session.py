import asyncio
import time

from .calibration import Calibrator
from .logging_writer import SessionLogger
from .sources.demo import DemoSource
from .sources.live import LiveSource
from .sources.playback import PlaybackSource
from .tracker import MultiPersonTracker

TRACKER_TICK_S = 0.1


class TrackingSession:
    """
    Owns the lifecycle of a single tracking run: picks the signal source for
    the requested mode (demo / live / playback), drives it through
    calibration then tracking, logs everything to disk, and exposes the
    latest state for the WebSocket broadcaster / REST endpoints.
    """

    def __init__(self, config, log_dir: str):
        self.config = config
        self.log_dir = log_dir
        self.mode = None
        self.source = None
        self.calibrator = None
        self.tracker = None
        self.logger = None
        self._device_signal_latest = {}
        self._running = False
        self._tasks = []
        self.state = "idle"  # idle | calibrating | tracking | stopped
        self.num_people_demo = 2
        self.playback_log = None

    async def start(self, mode: str, **opts):
        await self.stop()

        self.mode = mode
        self.config.validate()
        device_ids = [d.id for d in self.config.devices]

        self.calibrator = Calibrator(device_ids, duration_s=self.config.calibration_seconds)
        self.tracker = MultiPersonTracker(self.config.devices, self.config.room, self.calibrator, self.config.max_people)
        self.logger = SessionLogger(self.log_dir)
        self._device_signal_latest = {d: 0.0 for d in device_ids}

        if mode == "demo":
            self.num_people_demo = opts.get("num_people", 2)
            self.source = DemoSource(self.config.devices, self.config.room, num_people=self.num_people_demo)
        elif mode == "live":
            self.source = LiveSource(self.config.devices)
        elif mode == "playback":
            log_path = opts["log_path"]
            self.playback_log = log_path
            self.source = PlaybackSource(log_path, speed=opts.get("speed", 1.0))
        else:
            raise ValueError(f"Unknown mode: {mode}")

        self._running = True
        self.state = "calibrating"
        self.calibrator.begin()
        self.logger.log_event("session_start", mode=mode)

        await self.source.start()
        self._tasks.append(asyncio.create_task(self._consume_readings()))
        self._tasks.append(asyncio.create_task(self._tracker_loop()))

    async def stop(self):
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        if self.source:
            await self.source.stop()
            self.source = None
        if self.logger:
            self.logger.log_event("session_stop")
            self.logger.close()
            self.logger = None
        self.state = "idle"

    async def _consume_readings(self):
        async for reading in self.source.readings():
            self._device_signal_latest[reading.device_id] = reading.signal
            self.logger.log_reading(reading.device_id, reading.timestamp, reading.signal, reading.rssi)

            if self.state == "calibrating":
                self.calibrator.observe(reading.device_id, reading.signal)
            else:
                self.tracker.observe(reading.device_id, reading.signal)

    async def _tracker_loop(self):
        while self._running:
            if self.state == "calibrating":
                self.calibrator.tick()
                if self.calibrator.is_done:
                    self.state = "tracking"
                    self.logger.log_event("calibration_done", baselines={
                        d: {"mean": b.mean, "std": b.std} for d, b in self.calibrator.baselines.items()
                    })
            elif self.state == "tracking":
                tracks = self.tracker.step()
                self.logger.log_tracks(time.time(), tracks, self.mode)

            await asyncio.sleep(TRACKER_TICK_S)

    def snapshot(self):
        return {
            "state": self.state,
            "mode": self.mode,
            "calibration_progress": self.calibrator.progress if self.calibrator else 0.0,
            "devices": [
                {
                    "id": d.id,
                    "label": d.label,
                    "x": d.x_m,
                    "y": d.y_m,
                    "signal": round(self._device_signal_latest.get(d.id, 0.0), 2),
                    "disturbance": round(self.tracker.latest_disturbance.get(d.id, 0.0), 2) if self.tracker else 0.0,
                    "baseline_mean": round(self.calibrator.baseline_for(d.id).mean, 2) if self.calibrator else 0.0,
                }
                for d in self.config.devices
            ],
            "room": {"width_m": self.config.room.width_m, "height_m": self.config.room.height_m},
            "people": [t.to_dict() for t in sorted(self.tracker.tracks.values(), key=lambda t: t.id)] if self.tracker else [],
            "max_people": self.config.max_people,
        }
