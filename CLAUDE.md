# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A fork of the [ESP32-CSI-Tool](https://stevenmhernandez.github.io/ESP32-CSI-Tool/) for collecting WiFi Channel State
Information (CSI) from ESP32 microcontrollers, used for WiFi sensing / device-free human localization research. This
is embedded C++ (ESP-IDF) firmware plus small Python analysis utilities — there is no application build in the
traditional sense; "building" means compiling and flashing firmware per sub-project.

## Repository layout

Three independent, flashable ESP-IDF projects, each targeting a different role in a CSI collection setup:

- `active_sta/` — connects to an AP and transmits UDP packets to trigger CSI responses. Typically the CSI-TX node.
- `active_ap/` — runs a SoftAP that a station connects to; receives CSI from the station's traffic. Typically the CSI-RX node.
- `passive/` — puts the WiFi radio into promiscuous mode and passively sniffs CSI from ambient traffic on a channel, without an active connection.

Supporting directories:

- `_components/` — shared C++ header-only "components" (`csi_component.h`, `sd_component.h`, `time_component.h`,
  `sockets_component.h`, `input_component.h`, `nvs_component.h`) included directly by all three projects via relative
  paths (`../../_components/...`). This is the actual core logic; the per-project `main.cc` files are thin wiring
  around it. **Changes here affect all three sub-projects.**
- `python_utils/` — standalone scripts for post-processing serial output (timestamping, rate measurement, live
  plotting, raw→amplitude/phase parsing). Not part of the firmware build.
- `docs/` — GitHub Pages site (`index.html`) and BibTeX citations for related publications.

Each of `active_ap/`, `active_sta/`, `passive/` also has its own `README.md` with role-specific setup notes, and its
own `sdkconfig` (per-project ESP-IDF config, not shared).

## Build & flash commands

Requires **ESP-IDF v4.3** (this exact version — newer/older versions are not guaranteed to work). All commands are
run from inside one of the three sub-project directories (`active_ap/`, `active_sta/`, or `passive/`), not the repo
root — there is no top-level build.

```bash
cd active_ap   # or active_sta, or passive

idf.py menuconfig      # configure WiFi SSID/password/channel, baud rate, CSI options (see below)
idf.py flash monitor   # build, flash, and open the serial monitor (Ctrl+] to exit)
idf.py build           # build only
```

There is no test suite, linter, or CI in this repo — validation is done by flashing real hardware and inspecting
serial/CSV output.

### Key menuconfig settings (`ESP32 CSI Tool Config` submenu, backed by each project's `main/Kconfig.projbuild`)

- `WIFI_CHANNEL` — must match across all devices in an experiment (default channel 6 in code, README examples use 3).
- `ESP_WIFI_SSID` / `ESP_WIFI_PASSWORD` — must match between `active_ap` and `active_sta`; not shared automatically,
  update both projects' configs when changing.
- `SHOULD_COLLECT_CSI`, `SHOULD_COLLECT_ONLY_LLTF`, `SEND_CSI_TO_SERIAL`, `SEND_CSI_TO_SD` — feature toggles compiled
  into `_components/csi_component.h` / `sd_component.h` via `#ifdef CONFIG_*`.
- `PACKET_RATE` — `active_sta` only; target TX packets/sec (drives CSI sampling rate on the receiving side).
- Serial baud rate should be raised from the default (`Serial flasher config` and `Component config > ESP32-related`
  menus) to something like `921600` for higher CSI sampling throughput — see main `README.md` for details.

Configuration is **per sub-project and not synced automatically**; a change made via `menuconfig` in `active_ap` must
be manually mirrored in `active_sta`/`passive` if it needs to match (e.g. WiFi credentials, channel).

## Architecture notes

- All three `main.cc` files follow the same skeleton: `config_print()` (dump build/runtime config) → `nvs_init()` →
  `sd_init()` → role-specific WiFi init (`softap_init` / `station_init` / `passive_init`) → `csi_init(role_tag)`.
  `active_sta` additionally spins up a FreeRTOS task (`vTask_socket_transmitter_sta_loop`) that sends UDP datagrams
  to `192.168.4.1:2223` (the AP's default SoftAP address) to trigger CSI capture.
- CSI capture itself is registered once via `esp_wifi_set_csi_rx_cb` in `csi_init()` (`_components/csi_component.h`).
  The callback `_wifi_csi_cb` formats each CSI frame as a single CSV line prefixed `CSI_DATA,<role>,<mac>,...` and
  writes it through `outprintf()` (`_components/sd_component.h`), which fans out to serial and/or SD card depending
  on `SEND_CSI_TO_SERIAL` / `SEND_CSI_TO_SD`. The CSV header is defined in `_print_csi_csv_header()` in the same
  file — keep it in sync with the field order in `_wifi_csi_cb` if either changes.
- CSI payload encoding is controlled by `CSI_RAW` / `CSI_AMPLITUDE` / `CSI_PHASE` `#define`s at the top of
  `csi_component.h` (currently hardcoded to raw); amplitude/phase conversion is otherwise left to post-processing
  (see `python_utils/parse_csi.py`).
- Time handling (`_components/time_component.h`): the ESP32 has no RTC/network time by default, so timestamps are
  either the steady (monotonic, always available) clock or the real-time clock once set via a `SETTIME: <unix_ts>`
  string sent over serial input (handled by `input_component.h` → `time_set()`). `active_ap` also pushes its own
  timestamp to connecting stations over HTTP so only the AP's clock needs to be set manually. The `real_time_set`
  boolean and both timestamps are included in every CSV row.
- SD card support (`_components/sd_component.h`) is optional and auto-detected at boot; pinout is hardcoded for a
  specific board (MISO=2, MOSI=15, CLK=14, CS=13, e.g. TTGO T8 V1.7). Output files are named `/sdcard/<N>.csv`,
  auto-incrementing to the first unused index.
- Because `_components/*.h` are headers with function/variable definitions (not just declarations) included
  directly into each `main.cc` translation unit, they are effectively single-TU globals — do not include the same
  header from more than one `.cc` file in a given project.

## Working with collected data

Firmware emits one `CSI_DATA,...` CSV line per captured frame over serial (and/or to SD card). Typical pipeline:

```bash
idf.py monitor | grep "CSI_DATA" > my-experiment.csv                        # raw capture
idf.py monitor | python ../python_utils/serial_append_time.py > my.csv      # capture + host timestamp
idf.py monitor | python ../python_utils/serial_measure_rate.py              # check sampling rate live
idf.py monitor | python ../python_utils/serial_plot_csi_live.py             # live-plot amplitude of subcarrier #44
```

`python_utils/parse_csi.py` shows how to convert the raw CSI int list (interleaved imaginary/real pairs) in the last
CSV column into amplitude/phase per subcarrier — the same math as the `CSI_AMPLITUDE`/`CSI_PHASE` branches in
`csi_component.h`, done host-side instead of on-device.
