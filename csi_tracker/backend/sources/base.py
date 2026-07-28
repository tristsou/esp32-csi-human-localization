import asyncio
import time
from dataclasses import dataclass


@dataclass
class Reading:
    device_id: str
    timestamp: float
    signal: float
    rssi: float = 0.0


class SignalSource:
    """
    Common interface for anything that can feed per-device CSI readings into
    the tracking pipeline: live serial/TCP devices, the demo simulator, or a
    log-file playback reader.
    """

    async def start(self):
        raise NotImplementedError

    async def stop(self):
        raise NotImplementedError

    async def readings(self):
        """Async generator yielding Reading objects as they become available."""
        raise NotImplementedError
        yield  # pragma: no cover


def now() -> float:
    return time.time()


async def merge_queues(queue: asyncio.Queue):
    while True:
        item = await queue.get()
        if item is None:
            return
        yield item
