// TACT-AIM リアルタイム観戦クライアント。
// 地形は /map (map_render.py) と同じ素材・規則でオフスクリーンに事前描画し、
// プレイヤーは WebSocket のスナップショット間を補間して毎フレーム描く。

const MAP_WORLD = [-1500, -1100, 4750, 1100];
const VW = 2000, VH = 704;

const TYPE_COSTUME = {
  1: "wall", 3: "bush", 6: "spike_wall", 18: "grid_wall", 27: "mud",
  25: "shield", 19: "booster", 7: "warp1", 2: "door",
  20: "flash_wall{s}", 21: "heal_wall{s}", 22: "laser_wall{s}",
  23: "warp_floor{s}", 24: "energy_wall{s}", 28: "team_gem{s}",
  4: "map_pointer", 5: "pvp_manager", 8: "auto_buffer",
  9: "teaming_machine", 10: "information_base", 11: "locker",
  12: "minigame_hub1", 13: "minigame_hub2", 14: "telescope",
  16: "reporter", 17: "computer", 26: "record_player",
};
const ROTATING = new Set([2, 19, 22]);
const TILE_COLORS = {
  1: "#9ca3af", 18: "#6b7280", 6: "#fb923c", 3: "#16a34a", 27: "#a05a28",
  25: "#22d3ee", 20: "#facc15", 21: "#4ade80", 22: "#dc2626", 23: "#c084fc",
  24: "#60a5fa", 19: "#2dd4bf", 28: "#f472b6", 15: "#fde047",
  2: "#cbd5e1", 7: "#64748b",
};
const STATION_TYPES = new Set([4, 5, 8, 9, 10, 11, 12, 13, 14, 16, 17, 26]);
const PALETTE = [
  "#f87171", "#fbbf24", "#34d399", "#60a5fa", "#c084fc", "#f472b6",
  "#2dd4bf", "#fb923c", "#a3e635", "#7dd3fc", "#fde047", "#f0abfc",
];
const TEAM_NAMES = {
  1: "red", 2: "orange", 3: "yellow", 4: "green",
  5: "cyan", 6: "blue", 7: "purple", 8: "magenta",
};
const TEAM_HUES = { 1: 0, 2: 25, 3: 58, 4: 108, 5: 180, 6: 234, 7: 270, 8: 306 };

const canvas = document.getElementById("view");
const ctx = canvas.getContext("2d");
const hudTitle = document.getElementById("title");
const hudCount = document.getElementById("count");
const hudState = document.getElementById("state");

let mapsData = null;
let terrain = null;       // {key, canvas}
let assets = new Map();   // project -> {manifest, images: Map}
let prevSnap = null, curSnap = null, prevAt = 0, curAt = 0;

function toPx(x, y) {
  return [
    (x - MAP_WORLD[0]) / (MAP_WORLD[2] - MAP_WORLD[0]) * VW,
    VH - (y - MAP_WORLD[1]) / (MAP_WORLD[3] - MAP_WORLD[1]) * VH,
  ];
}

async function loadAssets(project) {
  if (assets.has(project)) return assets.get(project);
  const entry = { manifest: null, images: new Map() };
  assets.set(project, entry);
  try {
    const res = await fetch(`assets/${project}/manifest.json`);
    if (!res.ok) return entry;
    entry.manifest = await res.json();
    await Promise.all(Object.keys(entry.manifest).map(async (key) => {
      const img = new Image();
      img.src = `assets/${project}/${key}.png`;
      await img.decode().catch(() => {});
      if (img.naturalWidth) entry.images.set(key, img);
    }));
  } catch { /* 素材なしは色タイルで描く */ }
  return entry;
}

async function buildTerrain(project, mapNum) {
  const key = `${project}/${mapNum}`;
  if (terrain && terrain.key === key) return;
  if (!mapsData) {
    const res = await fetch("maps.json");
    mapsData = await res.json();
  }
  const entry = mapsData[project];
  const off = document.createElement("canvas");
  off.width = VW; off.height = VH;
  const g = off.getContext("2d");
  g.fillStyle = "#0d1117";
  g.fillRect(0, 0, VW, VH);
  terrain = { key, canvas: off };
  if (!entry) return;
  const tiles = [...entry.base, ...(entry.maps[mapNum || "1"] || [])];
  const { manifest, images } = await loadAssets(project);
  const tilePx = 50 / (MAP_WORLD[2] - MAP_WORLD[0]) * VW;
  for (const [gx, gy, typ, f5 = 0, f6 = 0] of tiles) {
    const [cx, cy] = toPx(gx * 50, gy * 50);
    let key2 = null;
    const template = TYPE_COSTUME[typ];
    if (typ === 15) key2 = f5 > 0 ? "skill_spot0" : "skill_spot1";
    else if (template) key2 = template.replace("{s}", f6 < 0 ? "1" : "0");
    const img = key2 && images.get(key2);
    if (img && manifest[key2]) {
      const [wu, hu] = manifest[key2];
      const w = tilePx * wu / 50, h = tilePx * hu / 50;
      g.save();
      g.translate(cx, cy);
      if (ROTATING.has(typ)) g.rotate(-(90 - f5) * Math.PI / 180);
      g.drawImage(img, -w / 2, -h / 2, w, h);
      g.restore();
    } else if (STATION_TYPES.has(typ)) {
      const s = tilePx * 0.32;
      g.fillStyle = "#e2e8f0";
      g.fillRect(cx - s, cy - s, s * 2, s * 2);
    } else {
      const s = tilePx / 2 + 0.5;
      g.fillStyle = TILE_COLORS[typ] || "#64748b";
      g.fillRect(cx - s, cy - s, s * 2, s * 2);
    }
  }
  g.strokeStyle = "#475569";
  g.lineWidth = 2;
  g.strokeRect(0, 0, VW - 1, VH - 1);
}

function drawPlayers(now) {
  if (!curSnap) return;
  const span = Math.max(1, curAt - prevAt);
  const t = Math.min(1, (now - curAt) / span);
  const prevById = new Map((prevSnap?.players || []).map((p) => [p.id, p]));
  ctx.font = "19px system-ui, sans-serif";
  curSnap.players.forEach((p, i) => {
    const q = prevById.get(p.id);
    const x = q ? q.x + (p.x - q.x) * t : p.x;
    const y = q ? q.y + (p.y - q.y) * t : p.y;
    const [px, py] = toPx(x, y);
    if (p.sp) {
      ctx.beginPath();
      ctx.arc(px, py, 17, 0, Math.PI * 2);
      ctx.strokeStyle = "#22c55e";
      ctx.lineWidth = 3;
      ctx.stroke();
    }
    if (p.tc) {
      ctx.beginPath();
      ctx.arc(px, py, 13, 0, Math.PI * 2);
      ctx.strokeStyle = `hsl(${TEAM_HUES[p.tc] || 0} 75% 60%)`;
      ctx.lineWidth = 3;
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.arc(px, py, 9, 0, Math.PI * 2);
    ctx.fillStyle = PALETTE[i % PALETTE.length];
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2;
    ctx.stroke();
    const title = p.name || `ID ${p.id}`;
    let sub = `${p.char} | HP ${p.hp}/${p.max}`;
    if (p.tc) sub += ` | Team: ${TEAM_NAMES[p.tc] || p.tc}`;
    ctx.fillStyle = "#e2e8f0";
    ctx.fillText(title, px + 14, py - 8);
    ctx.fillStyle = "#94a3b8";
    ctx.font = "16px system-ui, sans-serif";
    ctx.fillText(sub, px + 14, py + 14);
    ctx.font = "19px system-ui, sans-serif";
  });
}

function frame(now) {
  const vw = window.innerWidth, vh = window.innerHeight;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== vw * dpr || canvas.height !== vh * dpr) {
    canvas.width = vw * dpr;
    canvas.height = vh * dpr;
  }
  const scale = Math.min(vw / VW, vh / VH);
  ctx.setTransform(dpr * scale, 0, 0, dpr * scale,
    dpr * (vw - VW * scale) / 2, dpr * (vh - VH * scale) / 2);
  ctx.fillStyle = "#0d1117";
  ctx.fillRect(-VW, -VH, VW * 3, VH * 3);
  if (terrain) ctx.drawImage(terrain.canvas, 0, 0);
  drawPlayers(now);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

function connect() {
  const url = new URL("ws", location.href);
  url.protocol = url.protocol.replace("http", "ws");
  const ws = new WebSocket(url);
  ws.onopen = () => { hudState.textContent = "接続中"; };
  ws.onmessage = async (ev) => {
    const snap = JSON.parse(ev.data);
    if (snap.type !== "state") return;
    prevSnap = curSnap; prevAt = curAt;
    curSnap = snap; curAt = performance.now();
    hudTitle.textContent = snap.festival ? "TACT-AIM blood (フェス)" : "TACT-AIM";
    hudCount.textContent = `オンライン: ${snap.players.length}人`;
    hudState.textContent = "";
    await buildTerrain(snap.project, snap.map);
  };
  ws.onclose = () => {
    hudState.textContent = "切断されました。再接続します…";
    setTimeout(connect, 3000);
  };
  ws.onerror = () => ws.close();
}
connect();

// Discord アクティビティとして開かれた場合の ready ハンドシェイク (任意)。
(async () => {
  try {
    const cfg = await (await fetch("config.json")).json();
    if (!cfg.client_id) return;
    const { DiscordSDK } = await import(
      "https://cdn.jsdelivr.net/npm/@discord/embedded-app-sdk@1/+esm"
    );
    const sdk = new DiscordSDK(cfg.client_id);
    await Promise.race([
      sdk.ready(),
      new Promise((_, rej) => setTimeout(rej, 5000)),
    ]);
  } catch { /* 通常ブラウザでは何もしない */ }
})();
