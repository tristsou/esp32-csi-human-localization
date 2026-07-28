import re
from dataclasses import dataclass
from math import sqrt

CSV_FIELDS = [
    "type", "role", "mac", "rssi", "rate", "sig_mode", "mcs", "bandwidth",
    "smoothing", "not_sounding", "aggregation", "stbc", "fec_coding", "sgi",
    "noise_floor", "ampdu_cnt", "channel", "secondary_channel", "local_timestamp",
    "ant", "sig_len", "rx_state", "real_time_set", "real_timestamp", "len", "csi_data",
]

CSI_BRACKET_RE = re.compile(r"\[(.*)\]")


@dataclass
class CsiFrame:
    device_id: str
    rssi: int
    local_timestamp: float
    amplitude: float

    @property
    def signal(self) -> float:
        return self.amplitude


def parse_csi_line(device_id: str, line: str) -> CsiFrame | None:
    """
    Parses a single `CSI_DATA,...` CSV line as emitted by the ESP32 firmware
    (see _components/csi_component.h) into a CsiFrame with a scalar signal
    strength summarizing the frame (mean CSI amplitude across subcarriers).
    """
    line = line.strip()
    if not line.startswith("CSI_DATA"):
        return None

    match = CSI_BRACKET_RE.search(line)
    if not match:
        return None

    header = line[: match.start()]
    parts = header.strip(",").split(",")
    if len(parts) < 19:
        return None

    try:
        rssi = int(parts[3])
        local_timestamp = float(parts[18])
    except (ValueError, IndexError):
        return None

    raw_values = [int(x) for x in match.group(1).split(" ") if x not in ("", "\r")]
    amplitude = _mean_amplitude(raw_values)

    return CsiFrame(device_id=device_id, rssi=rssi, local_timestamp=local_timestamp, amplitude=amplitude)


def _mean_amplitude(raw: list) -> float:
    if len(raw) < 2:
        return 0.0
    pairs = len(raw) // 2
    total = 0.0
    for i in range(pairs):
        imaginary = raw[i * 2]
        real = raw[i * 2 + 1]
        total += sqrt(imaginary * imaginary + real * real)
    return total / pairs
