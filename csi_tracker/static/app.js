const canvas = document.getElementById("room-canvas");
const ctx = canvas.getContext("2d");

const modeSelect = document.getElementById("mode-select");
const demoPeopleSelect = document.getElementById("demo-people");
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

let latestState = null;

function resizeCanvasBackingStore() {
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(300, Math.floor(rect.width));
  canvas.height = Math.max(200, Math.floor(rect.height));
}
window.addEventListener("resize", resizeCanvasBackingStore);
resizeCanvasBackingStore();

function updateModeVisibility() {
  const mode = modeSelect.value;
  document.querySelectorAll(".demo-only").forEach((el) => el.classList.toggle("hidden", mode !== "demo"));
  document.querySelectorAll(".playback-only").forEach((el) => el.classList.toggle("hidden", mode !== "playback"));
}
modeSelect.addEventListener("change", updateModeVisibility);
updateModeVisibility();

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

startBtn.addEventListener("click", async () => {
  const mode = modeSelect.value;
  const payload = { mode };
  if (mode === "demo") payload.num_people = parseInt(demoPeopleSelect.value, 10);
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
  renderBadge(state);
  renderCalibration(state);
  renderKv(state);
  renderPeopleList(state);
  renderDeviceList(state);
  renderRoom(state);
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
    const active = d.disturbance > 4;
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

function hexWithAlpha(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
