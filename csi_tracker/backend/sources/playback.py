import asyncio
import json

from .base import Reading


class PlaybackSource:
    """
    Replays a previously recorded `readings.jsonl` log file (see logging.py)
    at (roughly) the original relative timing, so the same tracker pipeline
    used for live/demo modes can be re-run over recorded data.
    """

    def __init__(self, log_path: str, speed: float = 1.0):
        self.log_path = log_path
        self.speed = speed
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task = None
        self._running = False

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        await self._queue.put(None)

    async def readings(self):
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def _run(self):
        prev_ts = None
        try:
            with open(self.log_path) as f:
                for line in f:
                    if not self._running:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("kind") != "reading":
                        continue

                    ts = record["timestamp"]
                    if prev_ts is not None:
                        gap = (ts - prev_ts) / max(self.speed, 0.001)
                        if gap > 0:
                            await asyncio.sleep(min(gap, 1.0))
                    prev_ts = ts

                    reading = Reading(
                        device_id=record["device_id"],
                        timestamp=ts,
                        signal=record["signal"],
                        rssi=record.get("rssi", 0.0),
                    )
                    self._queue.put_nowait(reading)
        finally:
            await self._queue.put(None)
