const canvas = document.getElementById("room-canvas");
const ctx = canvas.getContext("2d");

const modeSelect = document.getElementById("mode-select");
const playbackSelect = document.getElementById("playback-select");
const startBtn = document.getElementById("start-btn");
const stopBtn = document.getElementById("stop-btn");
const stateBadge = document.getElementById("state-badge");
const calibrationWrap = document.getElementById("calibration-bar-wrap");
const calibrationFill = document.getElementById("calibration-fill");
const calibrationLabel = document.getElementById("calibration-label");
const kvMode = document.getElementById("kv-mode");
const kvState = document.getElementById("kv-state");
const kvPeopleCount = document.getElementById("kv-people-count");
const kvMaxPeople = document.getElementById("kv-max-people");
const peopleList = document.getElementById("people-list");
const deviceList = document.getElementById("device-list");

const viewRoom = document.getElementById("view-room");
const viewSignals = document.getElementById("view-signals");
const viewConfig = document.getElementById("view-config");
const signalCards = document.getElementById("signal-cards");
const signalTable = document.getElementById("signal-table");
const signalTooltip = document.getElementById("signal-tooltip");
const configFieldset = document.getElementById("config-fieldset");
const configSaveBtn = document.getElementById("config-save-btn");
const configRevertBtn = document.getElementById("config-revert-btn");
const configStatus = document.getElementById("config-status");
const cfgRoomW = document.getElementById("cfg-room-w");
const cfgRoomH = document.getElementById("cfg-room-h");
const cfgCalib = document.getElementById("cfg-calib");
const cfgMaxPeople = document.getElementById("cfg-max-people");
const cfgDeviceRows = document.getElementById("cfg-device-rows");
const cfgAddDevice = document.getElementById("cfg-add-device");

let latestState = null;
let configDraft = null;
let configLocked = false;

const HISTORY_SECONDS = 30;
const HISTORY_CAP = 200; // ~30s at the ~6.7Hz WS broadcast rate
const SERIES = { signal: "#3987e5", disturbance: "#d95926", rssi: "#199e70" };
const history = new Map(); // device id -> [{t, signal, baseline, rssi, rate, disturbance}]
let signalsVisible = false;
let signalCardKey = "";

function resizeCanvasBackingStore() {
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(300, Math.floor(rect.width));
  canvas.height = Math.max(200, Math.floor(rect.height));
}
window.addEventListener("resize", resizeCanvasBackingStore);
window.addEventListener("resize", () => { if (signalsVisible) resizeSignalCanvases(); });
resizeCanvasBackingStore();

function updateModeVisibility() {
  const mode = modeSelect.value;
  document.querySelectorAll(".playback-only").forEach((el) => el.classList.toggle("hidden", mode !== "playback"));
}
modeSelect.addEventListener("change", updateModeVisibility);
updateModeVisibility();

function setActiveTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  viewRoom.classList.toggle("hidden", name !== "room");
  viewSignals.classList.toggle("hidden", name !== "signals");
  viewConfig.classList.toggle("hidden", name !== "config");
  if (name === "room") resizeCanvasBackingStore();
  signalsVisible = name === "signals";
  if (signalsVisible) {
    resizeSignalCanvases();
    if (latestState) renderSignals(latestState);
  }
}
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => setActiveTab(b.dataset.tab)));

async function loadLogList() {
  const res = await fetch("/api/logs");
  const data = await res.json();
  playbackSelect.innerHTML = "";
  for (const name of data.sessions) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    playbackSelect.appendChild(opt);
  }
}
loadLogList();

async function loadConfig() {
  const res = await fetch("/api/config");
  configDraft = await res.json();
  renderConfigForm();
}
loadConfig();

function renderConfigForm() {
  cfgRoomW.value = configDraft.room.width_m;
  cfgRoomH.value = configDraft.room.height_m;
  cfgCalib.value = configDraft.calibration_seconds;
  cfgMaxPeople.value = configDraft.max_people;
  renderDeviceRows();
}

function renderDeviceRows() {
  cfgDeviceRows.innerHTML = configDraft.devices
    .map(
      (d, i) => `
      <div class="cfg-device-row" data-index="${i}">
        <input data-field="id" value="${d.id}" placeholder="id" />
        <input data-field="label" value="${d.label}" placeholder="label" />
        <input data-field="x_m" type="number" step="0.1" value="${d.x_m}" />
        <input data-field="y_m" type="number" step="0.1" value="${d.y_m}" />
        <select data-field="source">
          <option value="serial" ${d.source === "serial" ? "selected" : ""}>serial</option>
          <option value="tcp" ${d.source === "tcp" ? "selected" : ""}>tcp</option>
        </select>
        <input data-field="port" value="${d.port}" placeholder="port" ${d.source !== "serial" ? "hidden" : ""} />
        <input data-field="baud" type="number" value="${d.baud}" ${d.source !== "serial" ? "hidden" : ""} />
        <input data-field="host" value="${d.host}" placeholder="host" ${d.source !== "tcp" ? "hidden" : ""} />
        <input data-field="tcp_port" type="number" value="${d.tcp_port}" ${d.source !== "tcp" ? "hidden" : ""} />
        <button data-action="remove" type="button" title="Remove device">x</button>
      </div>`
    )
    .join("");
  applyConfigLock();
}

cfgDeviceRows.addEventListener("input", (e) => {
  const row = e.target.closest(".cfg-device-row");
  if (!row || !e.target.dataset.field) return;
  const dev = configDraft.devices[parseInt(row.dataset.index, 10)];
  const field = e.target.dataset.field;
  dev[field] = ["x_m", "y_m", "baud", "tcp_port"].includes(field) ? Number(e.target.value) : e.target.value;
  if (field === "source") renderDeviceRows();
});

cfgDeviceRows.addEventListener("click", (e) => {
  if (e.target.dataset.action !== "remove" || configLocked) return;
  const row = e.target.closest(".cfg-device-row");
  configDraft.devices.splice(parseInt(row.dataset.index, 10), 1);
  renderDeviceRows();
});

cfgRoomW.addEventListener("input", () => { configDraft.room.width_m = Number(cfgRoomW.value); });
cfgRoomH.addEventListener("input", () => { configDraft.room.height_m = Number(cfgRoomH.value); });
cfgCalib.addEventListener("input", () => { configDraft.calibration_seconds = Number(cfgCalib.value); });
cfgMaxPeople.addEventListener("input", () => { configDraft.max_people = Number(cfgMaxPeople.value); });

cfgAddDevice.addEventListener("click", () => {
  const n = configDraft.devices.length + 1;
  configDraft.devices.push({
    id: `esp32-${n}`,
    label: `Device ${n}`,
    x_m: 0,
    y_m: 0,
    source: "serial",
    port: "",
    baud: 921600,
    host: "",
    tcp_port: 0,
  });
  renderDeviceRows();
});

async function applyConfig() {
  const res = await fetch("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(configDraft),
  });
  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "Failed to apply configuration");
    return false;
  }
  return true;
}

configSaveBtn.addEventListener("click", async () => {
  if (!(await applyConfig())) return;
  const res = await fetch("/api/config/save", { method: "POST" });
  const data = await res.json();
  setConfigStatus(data.ok ? `Saved to ${data.path}` : data.error, !data.ok);
});

configRevertBtn.addEventListener("click", async () => {
  await fetch("/api/config/reload", { method: "POST" });
  await loadConfig();
  setConfigStatus("Reverted to file contents", false);
});

function setConfigStatus(msg, isError) {
  configStatus.textContent = msg || "";
  configStatus.classList.toggle("config-status-error", !!isError);
}

function renderConfigLock(state) {
  const locked = !(state.state === "idle" || state.state === "stopped");
  if (locked === configLocked) return;
  configLocked = locked;
  applyConfigLock();
}

function applyConfigLock() {
  configFieldset.disabled = configLocked;
  configSaveBtn.disabled = configLocked;
  configRevertBtn.disabled = configLocked;
  setConfigStatus(configLocked ? "Locked while a session is running." : "", false);
}

startBtn.addEventListener("click", async () => {
  if (!(await applyConfig())) return;
  const mode = modeSelect.value;
  const payload = { mode };
  if (mode === "demo") payload.num_people = configDraft.max_people;
  if (mode === "playback") payload.log_file = playbackSelect.value;

  const res = await fetch("/api/session/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "Failed to start session");
    return;
  }
  startBtn.disabled = true;
  stopBtn.disabled = false;
});

stopBtn.addEventListener("click", async () => {
  await fetch("/api/session/stop", { method: "POST" });
  startBtn.disabled = false;
  stopBtn.disabled = true;
});

function connectWebSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (event) => {
    latestState = JSON.parse(event.data);
    render(latestState);
  };
  ws.onclose = () => setTimeout(connectWebSocket, 1000);
  ws.onerror = () => ws.close();
}
connectWebSocket();

function render(state) {
  recordHistory(state);
  renderBadge(state);
  renderCalibration(state);
  renderKv(state);
  renderPeopleList(state);
  renderDeviceList(state);
  renderRoom(state);
  renderConfigLock(state);
  if (signalsVisible) renderSignals(state);
}

function recordHistory(state) {
  const now = Date.now();
  const liveIds = new Set(state.devices.map((d) => d.id));
  for (const id of history.keys()) {
    if (!liveIds.has(id)) history.delete(id);
  }
  for (const d of state.devices) {
    let buf = history.get(d.id);
    if (!buf) {
      buf = [];
      history.set(d.id, buf);
    }
    buf.push({
      t: now,
      signal: d.signal,
      baseline: state.state === "calibrating" ? null : d.baseline_mean,
      rssi: d.rssi,
      rate: d.packet_rate,
      disturbance: d.disturbance,
    });
    const cutoff = now - HISTORY_SECONDS * 1000;
    let start = 0;
    while (start < buf.length && buf[start].t < cutoff) start++;
    if (start > 0) buf.splice(0, start);
    if (buf.length > HISTORY_CAP) buf.splice(0, buf.length - HISTORY_CAP);
  }
}

function renderBadge(state) {
  stateBadge.textContent = state.state;
  stateBadge.className = `badge badge-${state.state}`;
}

function renderCalibration(state) {
  if (state.state === "calibrating") {
    calibrationWrap.classList.remove("hidden");
    const pct = Math.round(state.calibration_progress * 100);
    calibrationFill.style.width = `${pct}%`;
    calibrationLabel.textContent = `Calibrating empty room… ${pct}%`;
  } else {
    calibrationWrap.classList.add("hidden");
  }
}

function renderKv(state) {
  kvMode.textContent = state.mode || "–";
  kvState.textContent = state.state;
  kvPeopleCount.textContent = state.people.length;
  kvMaxPeople.textContent = state.max_people;
}

function renderPeopleList(state) {
  if (state.people.length === 0) {
    peopleList.innerHTML = '<div class="empty-note">No people detected yet.</div>';
    return;
  }
  peopleList.innerHTML = state.people
    .map(
      (p) => `
      <div class="person-row">
        <span class="swatch" style="background:${p.color}"></span>
        <span class="meta"><span class="id">Person #${p.id}</span> · ${p.speed_mps.toFixed(2)} m/s</span>
        <span class="meta">${(p.confidence * 100).toFixed(0)}%</span>
      </div>`
    )
    .join("");
}

function renderDeviceList(state) {
  deviceList.innerHTML = state.devices
    .map(
      (d) => `
      <div class="device-row">
        <span class="device-name">${d.label}</span>
        <span class="device-signal">${d.disturbance.toFixed(1)}</span>
      </div>`
    )
    .join("");
}

function renderRoom(state) {
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const roomW = state.room.width_m || 6;
  const roomH = state.room.height_m || 4;
  const margin = 30;
  const scaleX = (w - margin * 2) / roomW;
  const scaleY = (h - margin * 2) / roomH;
  const scale = Math.min(scaleX, scaleY);
  const offsetX = margin;
  const offsetY = margin;

  const toPx = (xm, ym) => [offsetX + xm * scale, offsetY + ym * scale];

  // Room outline
  ctx.strokeStyle = "#33332f";
  ctx.lineWidth = 1;
  ctx.strokeRect(offsetX, offsetY, roomW * scale, roomH * scale);

  // Devices
  for (const d of state.devices) {
    const [px, py] = toPx(d.x, d.y);
    const active = d.disturbance > (state.detect_threshold ?? 2.0);
    ctx.fillStyle = active ? "#fab219" : "#4a4a45";
    ctx.fillRect(px - 6, py - 6, 12, 12);
    ctx.fillStyle = "#c3c2b7";
    ctx.font = "11px -apple-system, sans-serif";
    ctx.fillText(d.label, px + 9, py + 4);
  }

  // Person-to-device lines: dotted, with opacity/thickness scaled by how
  // much that device's disturbance is currently contributing — a visual
  // hint at which devices are driving the triangulation for each person.
  const maxDisturbance = Math.max(1, ...state.devices.map((d) => d.disturbance));
  for (const p of state.people) {
    for (const d of state.devices) {
      const strength = Math.max(0, d.disturbance) / maxDisturbance;
      if (strength < 0.05) continue;
      const [px, py] = toPx(p.x, p.y);
      const [dx, dy] = toPx(d.x, d.y);
      ctx.beginPath();
      ctx.setLineDash([4, 5]);
      ctx.strokeStyle = hexWithAlpha(p.color, 0.15 + strength * 0.55);
      ctx.lineWidth = 0.5 + strength * 2.5;
      ctx.moveTo(px, py);
      ctx.lineTo(dx, dy);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  // People trails + markers
  for (const p of state.people) {
    if (p.trail && p.trail.length > 1) {
      ctx.beginPath();
      ctx.strokeStyle = hexWithAlpha(p.color, 0.35);
      ctx.lineWidth = 2;
      p.trail.forEach(([tx, ty], idx) => {
        const [px, py] = toPx(tx, ty);
        if (idx === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();
    }

    const [px, py] = toPx(p.x, p.y);
    ctx.beginPath();
    ctx.fillStyle = hexWithAlpha(p.color, Math.max(0.4, p.confidence));
    ctx.arc(px, py, 14, 0, Math.PI * 2);
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#1a1a19";
    ctx.stroke();

    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 12px -apple-system, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(String(p.id), px, py);
    ctx.textAlign = "start";
    ctx.textBaseline = "alphabetic";
  }
}

const LIVENESS_INFO = {
  waiting: { cls: "live-waiting", glyph: "○", word: "waiting" },
  live: { cls: "live-live", glyph: "●", word: "live" },
  stale: { cls: "live-stale", glyph: "◐", word: "stale" },
  dead: { cls: "live-dead", glyph: "✕", word: "dead" },
};

function renderSignals(state) {
  const key = state.devices.map((d) => d.id).join(",");
  if (key !== signalCardKey) {
    signalCardKey = key;
    buildSignalCards(state.devices);
    resizeSignalCanvases();
  }

  for (const d of state.devices) {
    const card = signalCards.querySelector(`.signal-card[data-id="${cssEscape(d.id)}"]`);
    if (!card) continue;
    const info = LIVENESS_INFO[d.liveness] || LIVENESS_INFO.waiting;
    const chip = card.querySelector(".live-chip");
    chip.className = `live-chip ${info.cls}`;
    chip.textContent = `${info.glyph} ${info.word}`;
    card.querySelector(".signal-rate").textContent = `${d.packet_rate.toFixed(1)} Hz`;

    const buf = history.get(d.id) || [];
    drawPlot(card.querySelector(".plot-signal"), buf, state, [
      { key: "signal", color: SERIES.signal },
      { key: "baseline", color: "var(--text-muted)", thin: true },
    ]);
    drawPlot(card.querySelector(".plot-disturbance"), buf, state, [
      { key: "disturbance", color: SERIES.disturbance },
      { key: "threshold", color: "var(--text-muted)", thin: true, constant: state.detect_threshold },
    ]);
    drawPlot(card.querySelector(".plot-rssi"), buf, state, [{ key: "rssi", color: SERIES.rssi }]);
  }

  renderSignalTable(state);
}

function cssEscape(s) {
  return String(s).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}

function buildSignalCards(devices) {
  if (devices.length === 0) {
    signalCards.innerHTML = '<div class="empty-note">No devices configured. Add one in the Configuration tab.</div>';
    return;
  }
  signalCards.innerHTML = devices
    .map(
      (d) => `
      <div class="signal-card" data-id="${d.id}">
        <div class="signal-card-head">
          <span class="device-name"></span>
          <span class="live-chip live-waiting">○ waiting</span>
          <span class="signal-rate">0.0 Hz</span>
        </div>
        <div class="signal-plot-wrap">
          <canvas class="signal-plot plot-signal"></canvas>
          <span class="signal-plot-label">Signal / baseline</span>
        </div>
        <div class="signal-plot-wrap">
          <canvas class="signal-plot plot-disturbance"></canvas>
          <span class="signal-plot-label">Disturbance / threshold</span>
        </div>
        <div class="signal-plot-wrap">
          <canvas class="signal-plot plot-rssi"></canvas>
          <span class="signal-plot-label">RSSI (dBm)</span>
        </div>
      </div>`
    )
    .join("");
  for (const d of devices) {
    signalCards.querySelector(`.signal-card[data-id="${cssEscape(d.id)}"] .device-name`).textContent = d.label;
  }
}

function resizeSignalCanvases() {
  signalCards.querySelectorAll("canvas.signal-plot").forEach((cv) => {
    const rect = cv.getBoundingClientRect();
    cv.width = Math.max(100, Math.floor(rect.width));
    cv.height = Math.max(40, Math.floor(rect.height));
  });
}

function resolveColor(c) {
  if (!c.startsWith("var(")) return c;
  return getComputedStyle(document.documentElement).getPropertyValue(c.slice(4, -1)).trim();
}

function drawPlot(canvas, buf, state, seriesSpecs) {
  if (!canvas) return;
  const w = canvas.width;
  const h = canvas.height;
  const pctx = canvas.getContext("2d");
  pctx.clearRect(0, 0, w, h);

  pctx.strokeStyle = resolveColor("var(--border)");
  pctx.lineWidth = 1;
  pctx.beginPath();
  pctx.moveTo(0, h - 0.5);
  pctx.lineTo(w, h - 0.5);
  pctx.stroke();

  const points = buf.filter((p) => seriesSpecs.some((s) => s.constant !== undefined || p[s.key] != null));
  if (points.length < 2) {
    pctx.fillStyle = resolveColor("var(--text-muted)");
    pctx.font = "11px -apple-system, sans-serif";
    pctx.textAlign = "center";
    pctx.textBaseline = "middle";
    if (points.length === 1) {
      const p = points[0];
      for (const spec of seriesSpecs) {
        const v = spec.constant !== undefined ? spec.constant : p[spec.key];
        if (v == null) continue;
        pctx.fillStyle = resolveColor(spec.color);
        pctx.beginPath();
        pctx.arc(w - 4, h / 2, 2.5, 0, Math.PI * 2);
        pctx.fill();
      }
    } else if (state.state === "idle" || state.state === "stopped" || !state.mode) {
      pctx.fillText("No readings yet", w / 2, h / 2);
    }
    return;
  }

  const now = Date.now();
  const windowStart = now - HISTORY_SECONDS * 1000;
  const toX = (t) => ((t - windowStart) / (HISTORY_SECONDS * 1000)) * w;

  let min = Infinity;
  let max = -Infinity;
  for (const p of points) {
    for (const spec of seriesSpecs) {
      const v = spec.constant !== undefined ? spec.constant : p[spec.key];
      if (v == null) continue;
      if (v < min) min = v;
      if (v > max) max = v;
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return;
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const padding = (max - min) * 0.1;
  min -= padding;
  max += padding;
  const toY = (v) => h - ((v - min) / (max - min)) * h;

  for (const spec of seriesSpecs) {
    pctx.beginPath();
    pctx.strokeStyle = resolveColor(spec.color);
    pctx.lineWidth = spec.thin ? 1 : 2;
    let started = false;
    if (spec.constant !== undefined) {
      const y = toY(spec.constant);
      pctx.moveTo(0, y);
      pctx.lineTo(w, y);
    } else {
      for (const p of points) {
        const v = p[spec.key];
        if (v == null) {
          started = false;
          continue;
        }
        const x = toX(p.t);
        const y = toY(v);
        if (!started) {
          pctx.moveTo(x, y);
          started = true;
        } else {
          pctx.lineTo(x, y);
        }
      }
    }
    pctx.stroke();
  }
}

function renderSignalTable(state) {
  if (state.devices.length === 0) {
    signalTable.innerHTML = '<div class="empty-note">No devices configured.</div>';
    return;
  }
  signalTable.innerHTML = state.devices
    .map(
      (d) => `
      <div class="device-row">
        <span class="device-name">${d.label}</span>
        <span class="device-signal">${d.signal.toFixed(1)} / ${d.rssi.toFixed(0)} dBm</span>
      </div>`
    )
    .join("");
}

signalCards.addEventListener("pointermove", (e) => {
  const card = e.target.closest(".signal-card");
  if (!card) {
    signalTooltip.classList.add("hidden");
    return;
  }
  const id = card.dataset.id;
  const buf = history.get(id);
  if (!buf || buf.length === 0) {
    signalTooltip.classList.add("hidden");
    return;
  }
  const wrap = e.target.closest(".signal-plot-wrap") || card.querySelector(".signal-plot-wrap");
  const rect = wrap.querySelector("canvas").getBoundingClientRect();
  const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
  const now = Date.now();
  const targetT = now - HISTORY_SECONDS * 1000 + frac * HISTORY_SECONDS * 1000;
  let nearest = buf[0];
  let bestDelta = Math.abs(buf[0].t - targetT);
  for (const p of buf) {
    const delta = Math.abs(p.t - targetT);
    if (delta < bestDelta) {
      bestDelta = delta;
      nearest = p;
    }
  }

  const crossX = ((nearest.t - (now - HISTORY_SECONDS * 1000)) / (HISTORY_SECONDS * 1000)) * rect.width;
  card.querySelectorAll("canvas.signal-plot").forEach((cv) => {
    const cctx = cv.getContext("2d");
    cctx.save();
    cctx.strokeStyle = resolveColor("var(--text-muted)");
    cctx.lineWidth = 1;
    cctx.beginPath();
    const cx = (crossX / rect.width) * cv.width;
    cctx.moveTo(cx, 0);
    cctx.lineTo(cx, cv.height);
    cctx.stroke();
    cctx.restore();
  });

  const label = card.querySelector(".device-name").textContent;
  signalTooltip.innerHTML = "";
  const title = document.createElement("div");
  title.className = "tt-row";
  title.style.marginBottom = "4px";
  const titleName = document.createElement("span");
  titleName.className = "tt-name";
  titleName.textContent = label;
  title.appendChild(titleName);
  signalTooltip.appendChild(title);

  const rows = [
    ["Signal", nearest.signal != null ? nearest.signal.toFixed(2) : "–"],
    ["Baseline", nearest.baseline != null ? nearest.baseline.toFixed(2) : "–"],
    ["Disturbance", nearest.disturbance != null ? nearest.disturbance.toFixed(2) : "–"],
    ["RSSI", nearest.rssi != null ? `${nearest.rssi.toFixed(1)} dBm` : "–"],
    ["Rate", nearest.rate != null ? `${nearest.rate.toFixed(1)} Hz` : "–"],
  ];
  for (const [name, value] of rows) {
    const row = document.createElement("div");
    row.className = "tt-row";
    const n = document.createElement("span");
    n.className = "tt-name";
    n.textContent = name;
    const v = document.createElement("span");
    v.className = "tt-value";
    v.textContent = value;
    row.appendChild(n);
    row.appendChild(v);
    signalTooltip.appendChild(row);
  }

  signalTooltip.classList.remove("hidden");
  signalTooltip.style.left = `${e.clientX + 14}px`;
  signalTooltip.style.top = `${e.clientY + 14}px`;
});

signalCards.addEventListener("pointerleave", () => {
  signalTooltip.classList.add("hidden");
});

function hexWithAlpha(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
