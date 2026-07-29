import asyncio
import math
import random
import time

from .base import Reading, now

BASELINE_AMPLITUDE = 40.0
PERSON_DISTURBANCE_PEAK = 25.0
DISTURBANCE_FALLOFF_M = 3.5
NOISE_STD = 0.5
SAMPLE_INTERVAL_S = 0.05
PERSON_SPEED_MPS = 0.55

# Keep destinations (and therefore most of a walk) away from the walls —
# real people spend most of their time in the middle of a room, not
# skimming along its edges, and this also keeps them away from the harder
# multilateration geometry near walls/corners.
WALL_MARGIN_M = 0.9
# Soft steering-away force that kicks in inside this band near a wall, so a
# walker curves away before ever reaching WALL_MARGIN_M instead of bouncing
# off it like a billiard ball.
WALL_AVOID_BAND_M = 0.6


class SimulatedPerson:
    """
    A synthetic walker with smooth, continuous, non-teleporting motion,
    modeled as goal-directed wandering rather than a random-heading bounce:
    pick an interior waypoint, walk toward it (with a little steering noise
    and easing as it nears the target), pause briefly on arrival like a
    person stopping to look at something, then pick a new waypoint.
    """

    def __init__(self, room_w, room_h, rng: random.Random):
        self.rng = rng
        self.room_w = room_w
        self.room_h = room_h
        self.x = rng.uniform(room_w * 0.2, room_w * 0.8)
        self.y = rng.uniform(room_h * 0.2, room_h * 0.8)
        self.heading = rng.uniform(0, 2 * math.pi)
        self.speed = 0.0
        self.target_speed = PERSON_SPEED_MPS * rng.uniform(0.7, 1.25)
        self.target_x, self.target_y = self._pick_waypoint()
        self.pause_timer = 0.0

    def _pick_waypoint(self):
        margin = WALL_MARGIN_M
        return (
            self.rng.uniform(margin, self.room_w - margin),
            self.rng.uniform(margin, self.room_h - margin),
        )

    def step(self, dt: float):
        if self.pause_timer > 0:
            self.pause_timer -= dt
            self.speed = max(0.0, self.speed - dt * 1.5)
            return

        dx = self.target_x - self.x
        dy = self.target_y - self.y
        dist_to_target = math.hypot(dx, dy)

        if dist_to_target < 0.15:
            self.target_x, self.target_y = self._pick_waypoint()
            self.pause_timer = self.rng.uniform(1.0, 3.0)
            self.target_speed = PERSON_SPEED_MPS * self.rng.uniform(0.7, 1.25)
            return

        desired_heading = math.atan2(dy, dx)

        # Soft wall avoidance: steer the desired heading away from any wall
        # the walker is currently close to, blended in proportionally as it
        # gets closer — a gentle curve rather than a bounce, and waypoints
        # never lie in this band in the first place so it rarely engages.
        avoid_x, avoid_y = 0.0, 0.0
        if self.x < WALL_AVOID_BAND_M:
            avoid_x += (WALL_AVOID_BAND_M - self.x) / WALL_AVOID_BAND_M
        elif self.x > self.room_w - WALL_AVOID_BAND_M:
            avoid_x -= (WALL_AVOID_BAND_M - (self.room_w - self.x)) / WALL_AVOID_BAND_M
        if self.y < WALL_AVOID_BAND_M:
            avoid_y += (WALL_AVOID_BAND_M - self.y) / WALL_AVOID_BAND_M
        elif self.y > self.room_h - WALL_AVOID_BAND_M:
            avoid_y -= (WALL_AVOID_BAND_M - (self.room_h - self.y)) / WALL_AVOID_BAND_M

        target_dx, target_dy = math.cos(desired_heading), math.sin(desired_heading)
        blended_x = target_dx + avoid_x * 1.5
        blended_y = target_dy + avoid_y * 1.5
        desired_heading = math.atan2(blended_y, blended_x)

        # Ease heading toward the desired direction rather than snapping to
        # it, and add a little noise so the path isn't a perfectly straight
        # line — small human-like wobble rather than a robotic beeline.
        heading_diff = math.atan2(math.sin(desired_heading - self.heading), math.cos(desired_heading - self.heading))
        self.heading += heading_diff * min(1.0, dt * 3.0) + self.rng.uniform(-0.08, 0.08)

        # Ease speed toward the target speed, and slow down when approaching
        # the waypoint or turning sharply, like a person actually would.
        turn_slowdown = 1.0 - min(1.0, abs(heading_diff) / math.pi) * 0.6
        approach_slowdown = min(1.0, dist_to_target / 0.6)
        eased_target_speed = self.target_speed * turn_slowdown * approach_slowdown
        self.speed += (eased_target_speed - self.speed) * min(1.0, dt * 2.0)

        nx = self.x + math.cos(self.heading) * self.speed * dt
        ny = self.y + math.sin(self.heading) * self.speed * dt
        self.x = min(max(nx, 0.05), self.room_w - 0.05)
        self.y = min(max(ny, 0.05), self.room_h - 0.05)


class DemoSource:
    """
    Generates realistic simulated CSI amplitude readings for N devices as if
    `num_people` walkers were moving continuously through the room. Useful
    for exercising the full tracking + UI pipeline without real hardware.
    """

    def __init__(self, devices, room, num_people: int = 2, seed: int | None = None, calibration_seconds: float = 0.0):
        self.devices = devices
        self.room = room
        self.num_people = max(1, min(3, num_people))
        self.rng = random.Random(seed)
        self.calibration_seconds = calibration_seconds
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task = None
        self._running = False
        self.people = [SimulatedPerson(room.width_m, room.height_m, self.rng) for _ in range(self.num_people)]

    async def start(self):
        self._running = True
        # The session calibrates a baseline before tracking begins, and that
        # baseline is only meaningful if it's sampled from an empty room —
        # exactly like a real deployment, where nobody stands in front of the
        # sensors while they're being calibrated. Hold the walkers off the
        # field for the same duration so calibration doesn't bake a random
        # snapshot of someone's position into the "no person" baseline,
        # which otherwise makes later disturbance readings reflect only the
        # change in position since calibration rather than true disturbance.
        self._calibration_deadline = time.monotonic() + self.calibration_seconds
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
            calibrating = time.monotonic() < self._calibration_deadline
            if not calibrating:
                for person in self.people:
                    person.step(SAMPLE_INTERVAL_S)

            for device in self.devices:
                disturbance = 0.0
                if not calibrating:
                    for person in self.people:
                        dist = math.hypot(person.x - device.x_m, person.y - device.y_m)
                        disturbance += PERSON_DISTURBANCE_PEAK * math.exp(-dist / DISTURBANCE_FALLOFF_M)

                noise = self.rng.gauss(0, NOISE_STD)
                signal = BASELINE_AMPLITUDE + disturbance + noise
                reading = Reading(device_id=device.id, timestamp=now(), signal=signal, rssi=-40 - disturbance / 2)
                self._queue.put_nowait(reading)

            await asyncio.sleep(SAMPLE_INTERVAL_S)
