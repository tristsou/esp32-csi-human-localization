import asyncio
import math
import random

from .base import Reading, now

BASELINE_AMPLITUDE = 40.0
PERSON_DISTURBANCE_PEAK = 25.0
DISTURBANCE_FALLOFF_M = 3.5
NOISE_STD = 0.5
SAMPLE_INTERVAL_S = 0.05
PERSON_SPEED_MPS = 0.6


class SimulatedPerson:
    """A synthetic walker with smooth, continuous, non-teleporting motion."""

    def __init__(self, room_w, room_h, rng: random.Random):
        self.rng = rng
        self.room_w = room_w
        self.room_h = room_h
        self.x = rng.uniform(room_w * 0.2, room_w * 0.8)
        self.y = rng.uniform(room_h * 0.2, room_h * 0.8)
        self.heading = rng.uniform(0, 2 * math.pi)
        self.speed = PERSON_SPEED_MPS * rng.uniform(0.6, 1.3)
        self._retarget_timer = 0.0

    def step(self, dt: float):
        self._retarget_timer -= dt
        if self._retarget_timer <= 0:
            self.heading += self.rng.uniform(-1.2, 1.2)
            self._retarget_timer = self.rng.uniform(1.0, 3.5)

        nx = self.x + math.cos(self.heading) * self.speed * dt
        ny = self.y + math.sin(self.heading) * self.speed * dt

        if nx < 0.3 or nx > self.room_w - 0.3:
            self.heading = math.pi - self.heading
            nx = min(max(nx, 0.3), self.room_w - 0.3)
        if ny < 0.3 or ny > self.room_h - 0.3:
            self.heading = -self.heading
            ny = min(max(ny, 0.3), self.room_h - 0.3)

        self.x, self.y = nx, ny


class DemoSource:
    """
    Generates realistic simulated CSI amplitude readings for N devices as if
    `num_people` walkers were moving continuously through the room. Useful
    for exercising the full tracking + UI pipeline without real hardware.
    """

    def __init__(self, devices, room, num_people: int = 2, seed: int | None = None):
        self.devices = devices
        self.room = room
        self.num_people = max(1, min(3, num_people))
        self.rng = random.Random(seed)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task = None
        self._running = False
        self.people = [SimulatedPerson(room.width_m, room.height_m, self.rng) for _ in range(self.num_people)]

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
        while self._running:
            for person in self.people:
                person.step(SAMPLE_INTERVAL_S)

            for device in self.devices:
                disturbance = 0.0
                for person in self.people:
                    dist = math.hypot(person.x - device.x_m, person.y - device.y_m)
                    disturbance += PERSON_DISTURBANCE_PEAK * math.exp(-dist / DISTURBANCE_FALLOFF_M)

                noise = self.rng.gauss(0, NOISE_STD)
                signal = BASELINE_AMPLITUDE + disturbance + noise
                reading = Reading(device_id=device.id, timestamp=now(), signal=signal, rssi=-40 - disturbance / 2)
                self._queue.put_nowait(reading)

            await asyncio.sleep(SAMPLE_INTERVAL_S)
