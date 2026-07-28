import asyncio

from ..csi_parser import parse_csi_line
from .base import Reading, now


class LiveSource:
    """
    Reads CSI CSV lines from up to 6 ESP32 devices concurrently (serial or
    TCP) and yields a merged stream of Reading objects. Requires `pyserial`
    for serial-connected devices.
    """

    def __init__(self, devices):
        self.devices = devices
        self._queue: asyncio.Queue = asyncio.Queue()
        self._tasks = []
        self._running = False

    async def start(self):
        self._running = True
        for device in self.devices:
            if device.source == "serial":
                self._tasks.append(asyncio.create_task(self._read_serial(device)))
            elif device.source == "tcp":
                self._tasks.append(asyncio.create_task(self._read_tcp(device)))
            else:
                raise ValueError(f"Unknown device source: {device.source}")

    async def stop(self):
        self._running = False
        for task in self._tasks:
            task.cancel()
        await self._queue.put(None)

    async def readings(self):
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def _read_serial(self, device):
        try:
            import serial  # pyserial
        except ImportError as exc:
            raise RuntimeError(
                "pyserial is required for live serial mode. Install with `pip install pyserial`."
            ) from exc

        loop = asyncio.get_event_loop()
        ser = serial.Serial(device.port, device.baud, timeout=1)
        try:
            while self._running:
                line = await loop.run_in_executor(None, ser.readline)
                self._handle_line(device.id, line.decode("utf-8", errors="ignore"))
        finally:
            ser.close()

    async def _read_tcp(self, device):
        reader, _writer = await asyncio.open_connection(device.host, device.tcp_port)
        while self._running:
            line = await reader.readline()
            if not line:
                break
            self._handle_line(device.id, line.decode("utf-8", errors="ignore"))

    def _handle_line(self, device_id: str, line: str):
        frame = parse_csi_line(device_id, line)
        if frame is None:
            return
        reading = Reading(device_id=device_id, timestamp=now(), signal=frame.amplitude, rssi=frame.rssi)
        self._queue.put_nowait(reading)
