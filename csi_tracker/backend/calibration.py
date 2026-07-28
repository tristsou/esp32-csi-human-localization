import time
from dataclasses import dataclass, field


@dataclass
class DeviceBaseline:
    device_id: str
    mean: float = 0.0
    std: float = 1.0
    samples: list = field(default_factory=list)

    def add(self, value: float):
        self.samples.append(value)

    def finalize(self):
        n = len(self.samples)
        if n == 0:
            self.mean, self.std = 0.0, 1.0
            return
        self.mean = sum(self.samples) / n
        variance = sum((s - self.mean) ** 2 for s in self.samples) / n
        self.std = max(variance ** 0.5, 0.5)
        self.samples = []


class Calibrator:
    """
    Collects an empty-room signal baseline per device over a fixed window
    (default 10s). Tracking should not begin until `is_done` is True; the
    tracker then compares live readings against `baseline_for(device_id)`
    to derive a disturbance signal.
    """

    def __init__(self, device_ids, duration_s: float = 10.0):
        self.duration_s = duration_s
        self.baselines = {d: DeviceBaseline(device_id=d) for d in device_ids}
        self._start_time = None
        self._done = False

    def begin(self):
        self._start_time = time.time()
        self._done = False
        for baseline in self.baselines.values():
            baseline.samples = []

    def observe(self, device_id: str, signal: float):
        if self._done or device_id not in self.baselines:
            return
        self.baselines[device_id].add(signal)

    def tick(self):
        if self._done or self._start_time is None:
            return
        if time.time() - self._start_time >= self.duration_s:
            for baseline in self.baselines.values():
                baseline.finalize()
            self._done = True

    @property
    def is_done(self) -> bool:
        return self._done

    @property
    def progress(self) -> float:
        if self._start_time is None:
            return 0.0
        if self._done:
            return 1.0
        elapsed = time.time() - self._start_time
        return min(elapsed / self.duration_s, 1.0)

    def baseline_for(self, device_id: str) -> DeviceBaseline:
        return self.baselines.get(device_id, DeviceBaseline(device_id=device_id))
