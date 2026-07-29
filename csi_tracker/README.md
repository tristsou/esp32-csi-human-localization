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
   position. When a session is configured for at most 1 person
   (`max_people: 1` in the Configuration tab), the
   tracker skips this search entirely and solves the raw disturbance field
   directly — exact and much cheaper, since there's never a second person's
   contribution to disentangle. Solved positions are then associated with
   existing tracks (nearest-neighbor gating) or spawned as new tracks (up to
   `max_people`). Each track is smoothed with a constant-velocity filter so
   motion stays continuous. The room view draws a dotted line from each
   tracked person to every device, with opacity/thickness scaled by that
   device's current disturbance — a visual hint at which devices are
   actually driving the triangulation at any moment.
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
at your copy), or use the **Configuration** tab in the UI (see below), to
describe your room and 2–6 devices:

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

Then open `http://localhost:8000`. Override the bind address/port with
`CSI_TRACKER_HOST` / `CSI_TRACKER_PORT` (defaults `0.0.0.0` / `8000`):

```bash
CSI_TRACKER_PORT=9000 python3 run.py
```

## Signals tab

A **Signals** tab sits between **Room** and **Configuration** for hardware
monitoring and debugging: one card per configured device, each showing

- a **liveness** chip (`waiting` / `live` / `stale` / `dead`, glyph + word) and
  current **packet rate** in the card header,
- a **signal vs. baseline** sparkline (raw CSI amplitude vs. the calibrated
  "empty room" mean),
- a **disturbance vs. detection threshold** sparkline (the same threshold the
  Room view uses to decide a device is "active"),
- an **RSSI** sparkline (dBm).

History is accumulated **in the browser** from the last ~30 seconds of
WebSocket frames — there's no server-side history buffer or new endpoint, so
this works identically in demo, live, and playback modes. Liveness is derived
from how long it's been since a device's last reading arrived (not from the
reading's own timestamp), so it correctly reflects real elapsed time even
during fast-forwarded playback of an old log. A device that stops sending
data flips from `live` to `stale` to `dead` within a few seconds, which is the
main thing this tab is for: a device that goes quiet still looks fine in the
Room view (it just keeps showing its last known position), but shows up
immediately here.

Hovering a card shows a synchronized crosshair across its three charts plus a
tooltip with the exact signal, baseline, disturbance, RSSI, and rate at that
point; the side panel also lists current values in a plain table. In demo
mode, RSSI is synthetic (derived from the same disturbance value as signal),
so its sparkline mirrors the signal chart — that's expected, not a bug.

## Configuration tab

The UI has a **Configuration** tab alongside **Room**, showing room size,
calibration duration, `max_people`, and the device list as an editable form. It
loads from the file on startup (see [Setup](#setup) for the path precedence).
Edits are held in memory and take effect the next time you press **Start** — the
file on disk isn't touched until you click **Save to file**, which writes to
`config/devices.local.json` (gitignored) so local device ports/positions never
end up in a commit. `CSI_TRACKER_CONFIG`, if set, takes priority on load and is
also where Save writes back to. The editor is disabled while a session is
running; **Revert to file** discards in-memory edits and reloads from disk.
Server-side validation (2–6 devices, unique ids, `max_people` 1–3) runs when you
press Start, and blocks with an error message if it fails.

## Modes (selectable in the UI)

- **Demo** — realistic simulated walkers, no hardware required. The number of
  walkers matches `max_people` from the Configuration tab. Each walker wanders
  goal-directed rather than bouncing off walls:
  it picks a waypoint away from the walls, walks toward it at a slow human
  pace (~0.4–0.7 m/s) with a little steering noise, eases speed/heading
  through turns and on approach, curves gently away if it nears a wall
  instead of bouncing off it, then pauses briefly on arrival before picking
  the next waypoint. Good for exercising the full pipeline and UI.
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
