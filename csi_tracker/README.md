# CSI Human Localization Tracker

An interactive web UI that fuses CSI signal disturbance from 2–6 ESP32 devices
(configurable) to track 1–3 people moving through a room in real time. Each
person is assigned a persistent, uniquely colored ID that follows a smooth,
continuous motion model — tracks don't teleport and IDs aren't regenerated
mid-session.

This app is independent of the ESP32 firmware sub-projects (`active_ap/`,
`active_sta/`, `passive/`) at the repo root — it only consumes the `CSI_DATA`
CSV lines they emit over serial/TCP, or synthetic/replayed data in the other
two modes.

## How it works

1. **Calibration** — on session start, each configured device's baseline CSI
   amplitude is sampled for `calibration_seconds` (default 10s) with the room
   empty. This gives a per-device mean/std "no person" baseline.
2. **Tracking** — once calibration finishes, live readings are compared
   against each device's baseline to get a per-device disturbance magnitude.
   Disturbance from multiple people sums additively across every device, so
   positions for all currently-tracked people (plus a possible new arrival)
   are solved *jointly*: several candidate starting layouts are tried, each
   refined by alternating per-person least-squares solves, and scored by how
   well the combination of all solved positions explains the full observed
   field — the lowest-error layout wins. This multi-start search is what
   keeps IDs stable even when two or three people are close together, where
   a single greedy solve can lock onto a plausible-looking but wrong
   position. Solved positions are then associated with existing tracks
   (nearest-neighbor gating) or spawned as new tracks (up to `max_people`).
   Each track is smoothed with a constant-velocity filter so motion stays
   continuous.
3. **Logging** — every raw reading and every tracked frame is buffered and
   flushed to `logs/session_<timestamp>.jsonl` every 2 seconds, for later
   playback or offline model training.

## Setup

```bash
cd csi_tracker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Edit `config/devices.example.json` (or copy it and point `CSI_TRACKER_CONFIG`
at your copy) to describe your room and 2–6 devices:

```json
{
  "room": { "width_m": 6.0, "height_m": 4.0 },
  "calibration_seconds": 10,
  "max_people": 3,
  "devices": [
    { "id": "esp32-1", "label": "Corner A", "x_m": 0.0, "y_m": 0.0, "source": "serial", "port": "/dev/tty.usbserial-1410", "baud": 921600 }
  ]
}
```

Each device needs its physical `x_m`/`y_m` position in the room (used for
multilateration) and either `source: "serial"` (with `port`/`baud`) or
`source: "tcp"` (with `host`/`tcp_port`) for live mode.

## Run

```bash
CSI_TRACKER_CONFIG=./config/devices.example.json python3 run.py
```

Then open `http://localhost:8000`.

## Modes (selectable in the UI)

- **Demo** — realistic simulated walkers (1–3, chosen in the UI) with smooth
  random-walk motion, no hardware required. Good for exercising the full
  pipeline and UI.
- **Live feed** — reads real CSI CSV lines from the devices configured in
  `config/devices.example.json` over serial or TCP.
- **Playback** — replays a previously recorded `logs/*.jsonl` file through
  the same calibration + tracking pipeline, at (roughly) original timing.

Calibration runs automatically at the start of every mode, including
playback and demo, so the full pipeline — baseline, disturbance, tracking —
is exercised identically regardless of data source.

## Data for later training/analysis

Every session writes `logs/session_<timestamp>.jsonl`, one JSON object per
line, with three record kinds: `reading` (raw per-device signal + timestamp),
`tracks` (tracker output per tick), and `event` (session lifecycle markers
like `calibration_done` with the computed baselines). This is the same format
`PlaybackSource` reads, so any recorded session can be replayed later.
