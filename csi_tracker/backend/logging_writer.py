import json
import time
from pathlib import Path


class SessionLogger:
    """
    Buffers reading + track-state records in memory and periodically flushes
    them to a JSONL file on disk, so a crash or restart loses at most one
    flush interval of data. The log format is intentionally simple (one JSON
    object per line) so it can be replayed by PlaybackSource or loaded for
    offline model training/analysis.
    """

    def __init__(self, log_dir: str, flush_interval_s: float = 2.0, session_name: str | None = None):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.flush_interval_s = flush_interval_s
        name = session_name or time.strftime("session_%Y%m%d_%H%M%S")
        self.path = self.log_dir / f"{name}.jsonl"
        self._buffer = []
        self._last_flush = time.time()
        self._file = open(self.path, "a", buffering=1)

    def log_reading(self, device_id: str, timestamp: float, signal: float, rssi: float = 0.0):
        self._buffer.append({
            "kind": "reading",
            "timestamp": timestamp,
            "device_id": device_id,
            "signal": signal,
            "rssi": rssi,
        })
        self._maybe_flush()

    def log_tracks(self, timestamp: float, tracks: list, mode: str):
        self._buffer.append({
            "kind": "tracks",
            "timestamp": timestamp,
            "mode": mode,
            "tracks": tracks,
        })
        self._maybe_flush()

    def log_event(self, event: str, **payload):
        self._buffer.append({
            "kind": "event",
            "timestamp": time.time(),
            "event": event,
            **payload,
        })
        self.flush()

    def _maybe_flush(self):
        if time.time() - self._last_flush >= self.flush_interval_s:
            self.flush()

    def flush(self):
        if not self._buffer:
            return
        for record in self._buffer:
            self._file.write(json.dumps(record) + "\n")
        self._file.flush()
        self._buffer.clear()
        self._last_flush = time.time()

    def close(self):
        self.flush()
        self._file.close()

    @classmethod
    def list_sessions(cls, log_dir: str):
        path = Path(log_dir)
        if not path.exists():
            return []
        return sorted((p.name for p in path.glob("*.jsonl")), reverse=True)
