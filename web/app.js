// TACT-AIM リアルタイム観戦クライアント。
// 地形は /map (map_render.py) と同じ素材・規則でオフスクリーンに事前描画する。
// プレイヤーは原作 draw.ts の規則でキャラコスチュームを重ねて描き、
// 発射パックから原作 Player shot / Shot create を再現して弾を生成し、
// Bullet list スクリプトの解釈系 (combat.ts の Bullet action 相当) で
// 30fps シミュレーションして描く。本家クライアントの俯瞰描画の再現が目的。

const MAP_WORLD = [-1500, -1100, 4750, 1100];
const VW = 2000, VH = 704;
const DEG = Math.PI / 180;
const UNIT = VW / (MAP_WORLD[2] - MAP_WORLD[0]); // 1ユニットあたりのpx

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
const TEAM_HUES = { 1: 0, 2: 25, 3: 58, 4: 108, 5: 180, 6: 234, 7: 270, 8: 306 };
// 弾が当たるタイル種別 (原作 Bullet wall の構築規則)
const BULLET_WALL_TYPES = new Set([1, 2, 6, 20, 21, 22, 24, 25, 28]);
const GIMMICK_TYPES = new Set([20, 21, 22, 24, 28]);

const canvas = document.getElementById("view");
const ctx = canvas.getContext("2d");
const hudTitle = document.getElementById("title");
const hudCount = document.getElementById("count");
const hudState = document.getElementById("state");
const playersEl = document.getElementById("players");
const menuBtn = document.getElementById("menubtn");
const followbar = document.getElementById("followbar");
const followName = document.getElementById("followname");
const followExit = document.getElementById("followexit");

const TILE_PX = 50 * UNIT;  // 1マス (50ユニット) の仮想 px

let mapsData = null;
let terrain = null;       // {key, canvas}
let assets = new Map();   // project -> {manifest, images: Map}
let curProject = null;
let curTiles = [];        // 現在のマップのタイル一覧 (フォロー時のライブ描画用)
let weaponsData = null;   // {items, scripts, skills}
let prevSnap = null, curSnap = null, prevAt = 0, curAt = 0;
let simPlayers = [];      // 最新スナップショットのプレイヤー (弾シミュレーション用)
let wallSet = new Set();  // "gx/gy" (弾が当たるマス)
let gimmicks = [];        // ギミックタイル (マップ順)
let followId = null;      // フォロー中のプレイヤーID (null=全体表示)
let view = { s: 1, ox: 0, oy: 0, vw: 0, vh: 0 }; // 現在の変換 (逆変換用)

function toPx(x, y) {
  return [
    (x - MAP_WORLD[0]) / (MAP_WORLD[2] - MAP_WORLD[0]) * VW,
    VH - (y - MAP_WORLD[1]) / (MAP_WORLD[3] - MAP_WORLD[1]) * VH,
  ];
}

const num = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};
const int = (v) => Math.trunc(num(v));
const rand = (a, b) => a + Math.random() * (b - a);
const wrap180 = (d) => ((d + 180) % 360 + 360) % 360 - 180;
// Scratch の = 比較 (数値同士は数値として、それ以外は大文字小文字を区別しない)
function scratchEq(a, b) {
  const na = Number(a), nb = Number(b);
  if (a !== "" && b !== "" && Number.isFinite(na) && Number.isFinite(nb)) return na === nb;
  return String(a).toLowerCase() === String(b).toLowerCase();
}

// ---------- 素材 ----------

async function loadAssets(project) {
  if (assets.has(project)) return assets.get(project);
  const entry = { manifest: null, images: new Map(), gameLoading: false };
  assets.set(project, entry);
  try {
    const res = await fetch(`assets/${project}/manifest.json`);
    if (!res.ok) return entry;
    entry.manifest = await res.json();
    // 地形キー ("/" を含まない) だけ先に読み、ゲーム素材は後から読み込む
    const terrainKeys = Object.keys(entry.manifest).filter((k) => !k.includes("/"));
    await Promise.all(terrainKeys.map((key) => loadImage(entry, project, key)));
    loadGameAssets(project);
  } catch { /* 素材なしは色タイルで描く */ }
  return entry;
}

async function loadImage(entry, project, key) {
  const img = new Image();
  img.src = `assets/${project}/${encodeURIComponent(key.replaceAll("/", "__") + ".png")}`;
  await img.decode().catch(() => {});
  if (img.naturalWidth) entry.images.set(key, img);
}

function loadGameAssets(project) {
  const entry = assets.get(project);
  if (!entry || !entry.manifest || entry.gameLoading) return;
  entry.gameLoading = true;
  const keys = Object.keys(entry.manifest).filter((k) => k.includes("/"));
  (async () => {
    for (let i = 0; i < keys.length; i += 24) {
      await Promise.all(keys.slice(i, i + 24).map((key) => loadImage(entry, project, key)));
    }
  })();
}

async function loadWeapons(project) {
  try {
    const res = await fetch(`assets/${project}/weapons.json`);
    if (!res.ok) return;
    weaponsData = await res.json();
    // ロード前に届いていたスナップショットの発射を取りこぼさないよう再処理する
    if (curSnap) for (const p of curSnap.players) processShots(p);
  } catch { /* 武器データが無ければ弾は再現しない */ }
}

function imgEntry(key) {
  const entry = curProject ? assets.get(curProject) : null;
  if (!entry || !entry.manifest) return null;
  const img = entry.images.get(key);
  const m = entry.manifest[key];
  if (!img || !m) return null;
  return { img, m };
}

function hasImg(key) {
  return !!imgEntry(key);
}

// コスチュームをワールド座標 (x, y) へ原作の向き規則で描く。
// Scratch の向き dir (0=上, 時計回り) は canvas では (dir-90) 度回転にあたる
// (地形の回転タイルと同じ規則)。サイズは原作どおり実寸 (sizeMul は Stamp 用)。
function drawCostume(key, x, y, dir, scaleMul = 1, alpha = 1) {
  const ent = imgEntry(key);
  if (!ent) return false;
  const { img, m } = ent;
  const w = m[0], h = m[1];
  const cx = m.length > 2 ? m[2] : w / 2, cy = m.length > 3 ? m[3] : h / 2;
  const [px, py] = toPx(x, y);
  ctx.save();
  ctx.translate(px, py);
  ctx.rotate((dir - 90) * DEG);
  ctx.globalAlpha = alpha;
  ctx.drawImage(img, -cx * UNIT * scaleMul, -cy * UNIT * scaleMul,
    w * UNIT * scaleMul, h * UNIT * scaleMul);
  ctx.restore();
  return true;
}

// Stamp 命令のコスチューム名から素材キーを探す (F/ → B/ → W/ → C/ の順)
function drawStampCostume(name, x, y, dir, scaleMul, alpha) {
  if (name.startsWith("@")) {
    return drawCostume("WA/" + name.slice(1), x, y, dir, scaleMul, alpha);
  }
  for (const prefix of ["F/", "B/", "W/", "C/"]) {
    if (drawCostume(prefix + name, x, y, dir, scaleMul, alpha)) return true;
  }
  return false;
}

// ---------- 地形 ----------

// タイル1枚をコンテキスト g (仮想 px 座標系) へ描く。
// buildTerrain のオフスクリーン焼き込みと、フォロー時のライブ描画で共用。
function drawTile(g, images, manifest, tile) {
  const [gx, gy, typ, f5 = 0, f6 = 0] = tile;
  const [cx, cy] = toPx(gx * 50, gy * 50);
  let key2 = null;
  const template = TYPE_COSTUME[typ];
  if (typ === 15) key2 = f5 > 0 ? "skill_spot0" : "skill_spot1";
  else if (template) key2 = template.replace("{s}", f6 < 0 ? "1" : "0");
  const img = key2 && images.get(key2);
  if (img && manifest && manifest[key2]) {
    const [wu, hu] = manifest[key2];
    const w = TILE_PX * wu / 50, h = TILE_PX * hu / 50;
    g.save();
    g.translate(cx, cy);
    if (ROTATING.has(typ)) g.rotate(-(90 - f5) * Math.PI / 180);
    g.drawImage(img, -w / 2, -h / 2, w, h);
    g.restore();
  } else if (STATION_TYPES.has(typ)) {
    const s = TILE_PX * 0.32;
    g.fillStyle = "#e2e8f0";
    g.fillRect(cx - s, cy - s, s * 2, s * 2);
  } else {
    const s = TILE_PX / 2 + 0.5;
    g.fillStyle = TILE_COLORS[typ] || "#64748b";
    g.fillRect(cx - s, cy - s, s * 2, s * 2);
  }
}

// 本家風の床: 白地に 50ユニット (1マス) 間隔のグリッド線。
// 範囲はワールド座標 [wx0, wx1] × [wy0, wy1]、線幅は呼び出し側の単位で指定。
function drawFloor(g, wx0, wy0, wx1, wy1, lineWidth) {
  // 床は原作の背景 (Stage の "Normal") の単色 #999999。グリッド線は
  // 原作 Draw_square と同じくマスの境界 (中心 gx*50 から 25 ずれた位置)
  // に引く。中心に引くとタイルと半マスずれて見える。
  const [px0, py1] = toPx(wx0, wy0);
  const [px1, py0] = toPx(wx1, wy1);
  g.fillStyle = "#999999";
  g.fillRect(px0, py0, px1 - px0, py1 - py0);
  g.strokeStyle = "#b5b5b5";
  g.lineWidth = lineWidth;
  g.beginPath();
  for (let bx = Math.ceil((wx0 + 25) / 50) * 50 - 25; bx <= wx1; bx += 50) {
    const [x] = toPx(bx, 0);
    g.moveTo(x, py0);
    g.lineTo(x, py1);
  }
  for (let by = Math.ceil((wy0 + 25) / 50) * 50 - 25; by <= wy1; by += 50) {
    const [, y] = toPx(0, by);
    g.moveTo(px0, y);
    g.lineTo(px1, y);
  }
  g.stroke();
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
  drawFloor(g, MAP_WORLD[0], MAP_WORLD[1], MAP_WORLD[2], MAP_WORLD[3], 1);
  terrain = { key, canvas: off };
  wallSet = new Set();
  gimmicks = [];
  curTiles = [];
  if (!entry) return;
  const tiles = [...entry.base, ...(entry.maps[mapNum || "1"] || [])];
  curTiles = tiles;
  for (const [gx, gy, typ] of tiles) {
    if (BULLET_WALL_TYPES.has(typ)) wallSet.add(`${gx}/${gy}`);
  }
  gimmicks = tiles.filter((t) => GIMMICK_TYPES.has(t[2]));
  const { manifest, images } = await loadAssets(project);
  for (const tile of tiles) drawTile(g, images, manifest, tile);
  g.strokeStyle = "#475569";
  g.lineWidth = 2;
  g.strokeRect(0, 0, VW - 1, VH - 1);
}

// フォロー時の地形ライブ描画 (拡大でぼやけるためビットマップは使わない)。
// 現在の変換で見えている範囲 (+1マス余裕) だけを毎フレーム描く。
function drawTerrainLive() {
  const vx0 = (0 - view.ox) / view.s, vy0 = (0 - view.oy) / view.s;
  const vx1 = (view.vw - view.ox) / view.s, vy1 = (view.vh - view.oy) / view.s;
  const wx0 = vx0 / UNIT + MAP_WORLD[0] - 50, wx1 = vx1 / UNIT + MAP_WORLD[0] + 50;
  const wy0 = MAP_WORLD[1] + (VH - vy1) / UNIT - 50;
  const wy1 = MAP_WORLD[1] + (VH - vy0) / UNIT + 50;
  const entry = curProject ? assets.get(curProject) : null;
  drawFloor(ctx, wx0, wy0, wx1, wy1, 2 * UNIT);
  for (const tile of curTiles) {
    const tx = tile[0] * 50, ty = tile[1] * 50;
    if (tx < wx0 - 50 || tx > wx1 + 50 || ty < wy0 - 50 || ty > wy1 + 50) continue;
    drawTile(ctx, entry?.images || new Map(), entry?.manifest || null, tile);
  }
  ctx.strokeStyle = "#475569";
  ctx.lineWidth = 2;
  ctx.strokeRect(0, 0, VW - 1, VH - 1);
}

// ---------- 弾の生成 (原作 Player shot / Shot create の再現) ----------

let bullets = [];
let pendingBullets = [];
const seenShots = new Map();   // 原作 Players shot と同じ (送信者ID + パック) で既読管理
const skillUse = new Map();    // スキル使用回数 (10回目以降は原作でも発動しない)
const gimmickReady = new Map(); // ギミックの再発動時刻 (原作のタイマー相当)

function spawnBullet(owner, type, x, y, dir, vals, team) {
  pendingBullets.push({
    owner, type, team, dead: false,
    f: [null, owner, type, x, y, dir,
      vals[0] ?? 0, vals[1] ?? 0, vals[2] ?? 0, vals[3] ?? 0, vals[4] ?? 0],
    tracer: null, laser: null, circle: null, stamps: [], hit: 0, firstCache: {},
  });
}

function flushBullets() {
  if (!pendingBullets.length) return;
  bullets.push(...pendingBullets);
  pendingBullets = [];
  if (bullets.length > 800) bullets.splice(0, bullets.length - 800);
}

// 発射者のレコード P から Shot create の分岐を再現する。
// weapon は弾種別名、create_type は Item list の生成種別文字列。
function shotCreate(P, weapon, ctype, direction, e, charge) {
  const facing = P.mo[0];
  const muzzle = (dist, side) => [
    P.x + Math.sin(facing * DEG) * dist + Math.sin((facing + 90) * DEG) * side,
    P.y + Math.cos(facing * DEG) * dist + Math.cos((facing + 90) * DEG) * side,
  ];
  const fire = (type, x, y, dir, v) => spawnBullet(P.id, type, x, y, dir, v, P.team);
  const Z = [0, 0, 0, 0, 0];
  // 魔法: 体力コスト (末尾3桁-100) を払える時だけ
  if (ctype.includes("魔法")) {
    const cost = int(ctype.slice(-3)) - 100;
    if (cost < P.hp) {
      if (ctype.includes("中央")) fire(weapon, P.x, P.y, direction, Z);
      if (ctype.includes("普通") || ctype.includes("回転")) {
        const [mx, my] = muzzle(50, 0);
        fire(weapon, mx, my, direction,
          [ctype.includes("回転") ? direction : 0, 0, 0, 0, 0]);
      }
      if (ctype.includes("背面")) {
        for (let k = 0; k < 2; k++) {
          const bx = P.x + Math.sin(facing * DEG) * -50
            + rand(Math.abs(Math.cos(facing * DEG)) * 20, Math.abs(Math.cos(facing * DEG)) * -20);
          const by = P.y + Math.cos(facing * DEG) * -50
            + rand(Math.abs(Math.sin(facing * DEG)) * 20, Math.abs(Math.sin(facing * DEG)) * -20);
          fire(weapon, bx, by, direction, Z);
        }
      }
    }
    return;
  }
  // 設置・中央: その場に置く (方眼は50グリッドへスナップ)
  if (ctype.includes("中央") || ctype.includes("設置")) {
    let x = P.x, y = P.y;
    if (ctype.includes("方眼")) {
      x = Math.round(P.x / 50) * 50;
      y = Math.round(P.y / 50) * 50;
    }
    fire(weapon, x, y, ctype.includes("設置") ? 90 : direction, Z);
    return;
  }
  // 通常射撃 (普通/反動/ランチャー/シールドキャノン)
  if (ctype.includes("普通") || ctype.includes("反動")
      || ctype.includes("ランチャー") || ctype === "シールドキャノン") {
    let mx, my;
    if (ctype.includes("ランチャー")) [mx, my] = muzzle(50, 15);
    else if (ctype === "シールドキャノン") [mx, my] = muzzle(60, 23);
    else if (ctype.includes("近接")) { mx = P.x; my = P.y; }
    else [mx, my] = muzzle(50, 0);
    fire(weapon, mx, my, direction, Z);
    if (ctype === "シールドキャノン") {
      for (let k = 0; k < 8; k++) {
        const [sx, sy] = muzzle(30, 23);
        fire("煙", sx, sy, direction + rand(160, 200), [rand(120, 150) / 10, 0, 0, 0, 0]);
      }
    }
    if (ctype === "煙ランチャー") {
      for (let k = 0; k < 5; k++) {
        const [sx, sy] = muzzle(30, 15);
        fire("煙", sx, sy, direction + rand(160, 200), [rand(110, 140) / 10, 0, 0, 0, 0]);
      }
    }
    return;
  }
  // 二丁拳銃・二種拳銃: チャージで左右が入れ替わる
  if (ctype.includes("二丁拳銃") || ctype.includes("二種拳銃")) {
    const side = P.sc ? 3 : 10;
    const cnt = ctype.includes("拡散") ? int(ctype.slice(6, 8)) - 10 : 1;
    for (let k = 0; k < Math.max(0, cnt); k++) {
      const name = ctype.includes("二種拳銃")
        ? weapon + (charge === "01" ? "1" : "2") : weapon;
      const [mx, my] = muzzle(50, charge === "01" ? side : -side);
      fire(name, mx, my, cnt === 1 ? direction : facing + rand(-e, e), Z);
    }
    return;
  }
  if (ctype === "Chat") return; // チャット吹き出しは再現しない
  if (ctype === "戦車重機関銃") {
    const [mx, my] = muzzle(10, -2);
    fire(weapon, mx, my, direction, Z);
    return;
  }
  if (ctype === "戦車発火弾") {
    let ang = 15, name = "戦車発煙弾";
    for (let k = 0; k < 2; k++) {
      let [mx, my] = muzzle(15, 12);
      fire(name, mx, my, direction + ang, Z);
      [mx, my] = muzzle(15, -12);
      fire(name, mx, my, direction - ang, Z);
      ang = 20; name = "戦車火炎弾";
    }
    return;
  }
  // 「N番設定VVV」: 値 VVV-500 を N 番目の値フィールドへ
  if (ctype.includes("番設定")) {
    const n = int(ctype[0]);
    const vals = [0, 0, 0, 0, 0];
    if (n - 6 >= 0 && n - 6 < 5) vals[n - 6] = int(ctype.slice(4, 7)) - 500;
    fire(weapon, P.x, P.y, direction, vals);
    return;
  }
  // 左右: 横へ40ずらして2発
  if (ctype.includes("左右")) {
    for (const s of [90, -90]) {
      fire(weapon,
        P.x + Math.sin((facing + s) * DEG) * 40,
        P.y + Math.cos((facing + s) * DEG) * 40, direction, Z);
    }
    return;
  }
  // ショットNN: NN-10 発を拡散 e でばらまく
  if (ctype.includes("ショット")) {
    const cnt = int(ctype.slice(4, 6)) - 10;
    for (let k = 0; k < Math.max(0, cnt); k++) {
      const [mx, my] = muzzle(50, 0);
      fire(weapon, mx, my, facing + rand(-e, e), Z);
    }
    return;
  }
  if (ctype === "1マスガード") {
    const d45 = Math.round(facing / 45) * 45;
    fire("C" + weapon,
      Math.round((P.x + Math.sin(d45 * DEG) * 50) / 50) * 50,
      Math.round((P.y + Math.cos(d45 * DEG) * 50) / 50) * 50, d45, Z);
    return;
  }
  if (ctype === "3マスガード" || ctype === "半円ガード") {
    const base = Math.round(facing / 90) * 90;
    const cx = Math.round((P.x + Math.sin(base * DEG) * 50) / 50) * 50;
    const cy = Math.round((P.y + Math.cos(base * DEG) * 50) / 50) * 50;
    let off = -50, dir = ctype === "半円ガード" ? base - 90 : base;
    for (let k = 0; k < 3; k++) {
      if (ctype === "半円ガード") dir += 45;
      fire((off === 0 ? "C" : "S") + weapon,
        cx + Math.sin((base + 90) * DEG) * off,
        cy + Math.cos((base + 90) * DEG) * off, dir, Z);
      off += 50;
    }
    return;
  }
  if (ctype === "ばらまき") {
    for (let k = 0; k < 5; k++) {
      const d = direction + 72 * k;
      fire(weapon, P.x, P.y, d, [d, 10, 0, 0, 0]);
    }
    return;
  }
  if (ctype.includes("回転投げ")) {
    const [mx, my] = muzzle(50, 0);
    fire(weapon, mx, my, direction, [direction, 0, 0, 0, 0]);
    return;
  }
  if (ctype.includes("特殊投げ")) {
    const [mx, my] = muzzle(50, 0);
    fire(weapon, mx, my, direction, [direction, 20, -20, 0, 0]);
    return;
  }
  if (ctype === "シェア投げ") {
    fire(weapon, P.x, P.y, direction, Z);
    const [mx, my] = muzzle(50, 0);
    fire("S" + weapon, mx, my, direction,
      [weapon === "シェアバリア" ? 15 : 0, 0, 0, 0, 0]);
    return;
  }
  if (ctype === "自分") {
    const [mx, my] = muzzle(50, 0);
    fire(weapon, mx, my, direction, Z);
    return;
  }
}

// 発射パック (7文字) を原作 Load cloud data の規則で解読する
function handlePack(p, pack) {
  const facing = p.mo[0];
  if (pack[0] === "7") {
    // チャージ系: チャージ弾の消去 (末尾 899) かアイテムドロップ
    const charge = pack.slice(1, 4);
    const itemNum = int(pack.slice(4, 7)) - 100;
    if (itemNum === 899) {
      const idx = bullets.findIndex(
        (b) => b.owner === p.id && num(b.f[6]) === num(charge));
      if (idx >= 0) bullets.splice(idx, 1);
    } else {
      const dir = 360 / (100 / int(charge.slice(1, 3)));
      const speed = 7 / (9 / int(charge[0])) + 3;
      spawnBullet(p.id, "Drop item", p.x, p.y, dir,
        [charge, itemNum, speed, 0, 0], p.team);
    }
    return;
  }
  const code = int(pack.slice(0, 3));
  if (code < 700) {
    // 通常武器: コード - 101 が Item list の位置
    const item = weaponsData.items[code - 101];
    if (!item) return;
    let e = num(item[7]);
    if (!p.sc && String(item[6]) === "1" && e < 2) e += 25;
    const direction = facing + rand(-e, e);
    shotCreate(p, String(item[10]), String(item[11]), direction, e, pack.slice(5, 7));
  } else if (code >= 900) {
    // スキル: 10回目以降は原作でも発動しない
    const si = code - 900;
    const key = `${p.id}#${si}`;
    const used = (skillUse.get(key) || 0) + 1;
    skillUse.set(key, used);
    if (used >= 10) return;
    spawnBullet(p.id, "スキル発動", p.x + rand(-20, 20), p.y + rand(-20, 20),
      rand(1, 360), [100, 0, 0, 0, 0], p.team);
    const skill = weaponsData.skills[si];
    if (!skill) return;
    for (let k = 0; k < 3; k++) {
      const effect = skill[3 + 2 * k], value = skill[4 + 2 * k];
      if (effect && weaponsData.scripts[String(effect)]) {
        shotCreate(p, String(effect), String(value ?? ""), facing, 0, "");
      }
    }
  } else {
    // マップギミック (800 + ギミック番号)
    const gi = code - 800;
    const tile = gimmicks[gi];
    if (!tile) return;
    const now = performance.now();
    if ((gimmickReady.get(gi) || 0) > now) return;
    const [gx, gy, typ, f5] = tile;
    const cx = gx * 50, cy = gy * 50;
    const table = {
      20: ["フラッシュ爆発", 25, 170, 450], 21: ["回復フラッシュ爆発", -10, 140, 600],
      22: ["レーザーギミック", 30, 0, 210], 24: ["エネルギーボール", 0, 0, 600],
      28: ["ジェムエネルギー", 0, 0, 1200],
    };
    const row = table[typ];
    if (!row) return;
    // チームジェムは自陣側では壊せない
    if (typ === 28 && ((p.team[0] === "1" && cx < 3200) || (p.team[0] === "2" && cx > 3200))) return;
    let sx = cx, sy = cy;
    if (typ === 22) {
      sx += Math.sin(f5 * DEG) * 36;
      sy += Math.cos(f5 * DEG) * 36;
    }
    spawnBullet(p.id, row[0], sx, sy, f5, [row[1], row[2], 0, 0, 0], p.team);
    gimmickReady.set(gi, now + row[3] / 30 * 1000);
  }
}

function processShots(p) {
  if (!weaponsData) return;
  for (const pack of p.shots || []) {
    const key = `${p.id}|${pack}`;
    if (seenShots.has(key)) continue;
    seenShots.set(key, 1);
    if (seenShots.size > 4000) {
      const it = seenShots.keys();
      for (let k = 0; k < 1000; k++) seenShots.delete(it.next().value);
    }
    handlePack(p, pack);
  }
}

// ---------- 弾シミュレーション (Bullet list スクリプトの解釈系) ----------

// 命令の引数個数 (Pen_All/combat.ts の Bullet action / Set bullet val が正)
const CMD_ARITY = {
  "if": 3, "if end": 0, "else": 0, "draw bullet": 3, "distance if": 2,
  "cs val": 3, "sound": 1, "step": 1, "roll step": 1, "length": 8,
  "damage": 9, "sword damage": 4, "break wall": 6, "delete": 6,
  "stamp": 3, "team stamp": 3, "create": 7, "set length": 2, "bush": 1,
  "set position": 2, "round slide": 3, "draw laser": 4, "laser": 6,
  "put buff": 3, "put buff+": 5, "guard": 8, "screen stamp": 3,
  "draw circle": 3,
  "mine stamp": 2, "color": 1, "homing": 2, "team homing": 2,
  "roll homing": 2, "hit if": 0, "search first": 2, "skill point": 1,
  "shield": 1, "auto heal": 1, "about damage val": 0, "about drop item": 0,
  "about money": 0, "about tektite": 0, "about present": 0,
  "about shield cannon": 0, "about jj-100": 0, "about chat": 0,
};

function resolveArg(tok, b) {
  if (typeof tok === "string" && tok.startsWith("[")) {
    const idx = parseInt(tok.slice(1), 10);
    return b.f[idx] ?? 0;
  }
  return tok;
}

function playerById(id) {
  return simPlayers.find((p) => p.id === id) || null;
}

// 原作 Search first: 被弾対象 (f16=0) の中から最寄りを返す (フレーム内キャッシュ)
function searchFirst(b, mode) {
  if (b.firstCache[mode]) return b.firstCache[mode];
  let best = null;
  for (const p of simPlayers) {
    if (!p.nh) continue;
    if (mode === "E" && p.team === b.team) continue;
    if (mode === "T" && (p.team !== b.team || p.id === b.owner)) continue;
    if (mode === "U" && p.team !== b.team) continue;
    if (mode === "M" && p.id !== b.owner) continue;
    const d = Math.hypot(p.x - num(b.f[3]), p.y - num(b.f[4]));
    if (!best || d < best.dist) best = { id: p.id, dist: d, x: p.x, y: p.y };
  }
  b.firstCache[mode] = best;
  return best;
}

function wallAt(x, y) {
  return wallSet.has(`${Math.round(x / 50)}/${Math.round(y / 50)}`);
}

function stepMove(b, speed, dirField) {
  const d = num(b.f[dirField]);
  b.f[3] = num(b.f[3]) + Math.sin(d * DEG) * num(speed);
  b.f[4] = num(b.f[4]) + Math.cos(d * DEG) * num(speed);
}

// 原作 Delete/Create の生成物 (特殊な生成は シールドファンネル のみ個別対応)
function deleteBullet(b, spawn) {
  b.dead = true;
  const [type, ...vals] = spawn;
  if (type === undefined || type === "-" || type === "") return;
  if (type === "シールドファンネル") {
    const d45 = Math.round(num(b.f[5]) / 45) * 45;
    for (const dd of [d45, d45 + 180]) {
      spawnBullet(b.owner, type,
        num(b.f[3]) + Math.sin(dd * DEG) * 36,
        num(b.f[4]) + Math.cos(dd * DEG) * 36, dd,
        [dd, 0, 0, 0, 0], b.team);
    }
    return;
  }
  spawnBullet(b.owner, String(type), num(b.f[3]), num(b.f[4]), num(b.f[5]),
    vals, b.team);
}

// 原作 CS val (FPS_temp = 1 前提)
function csValCmd(b, raw) {
  const field = int(resolveArg(raw[0], b));
  if (!(field >= 1 && field <= 10)) return;
  const type = String(resolveArg(raw[2], b));
  if (type === "S") {
    b.f[field] = resolveArg(raw[1], b);
    return;
  }
  if (type.startsWith("G")) {
    const step = num(type.slice(1));
    const src = num(b.f[int(raw[1])]);
    if (step && step < Math.abs(num(b.f[field]) - src)) {
      b.f[field] = Math.round(src / step) * step;
    }
    return;
  }
  const degree = num(resolveArg(raw[1], b));
  if (type === "C" || type === "M") b.f[field] = num(b.f[field]) + degree;
  else if (type === "*") b.f[field] = num(b.f[field]) * degree;
  else {
    const t = num(type);
    if (t) b.f[field] = num(b.f[field]) + (degree - num(b.f[field])) / t;
  }
}

function evalCond(v1, v2, op) {
  if (op === "<") return num(v1) < num(v2);
  if (op === "=") return scratchEq(v1, v2);
  if (op === "%") {
    const n = num(v2);
    const m = (a) => ((a % n) + n) % n;
    return m(Math.round(num(v1))) === 0 && m(Math.round(num(v1)) + 1) === 1;
  }
  return false;
}

function simBullet(b) {
  b.tracer = null;
  b.laser = null;
  b.circle = null;
  b.stamps = [];
  b.hit = 0;
  b.firstCache = {};
  const script = weaponsData?.scripts[b.type];
  if (!script) return;
  let IF = 0, TF = true;
  let i = 0;
  while (i < script.length) {
    const cmd = String(script[i]).toLowerCase();
    const arity = CMD_ARITY[cmd];
    if (arity === undefined) { i += 1; continue; }
    const rawArgs = script.slice(i + 1, i + 1 + arity);
    const V = rawArgs.map((a) => resolveArg(a, b));
    i += 1 + arity;
    const wasAct = IF === 0 || TF;
    switch (cmd) {
      case "if": {
        const c = evalCond(V[0], V[1], String(V[2]));
        if (!c) TF = false; else if (IF === 0) TF = true;
        IF = 1;
        break;
      }
      case "else": TF = !TF; break;
      case "if end": IF = 0; break;
      case "distance if": {
        const t = searchFirst(b, String(V[1]));
        const c = !!t && t.dist < num(V[0]);
        if (!c) TF = false; else if (IF === 0) TF = true;
        IF = 1;
        break;
      }
      case "hit if": {
        if (b.hit === 1) { if (IF === 0) TF = true; } else TF = false;
        IF = 1;
        break;
      }
      case "cs val": if (wasAct) csValCmd(b, rawArgs); break;
      case "step": if (wasAct) stepMove(b, V[0], 5); break;
      case "roll step": if (wasAct) stepMove(b, V[0], 6); break;
      case "round slide": {
        const fidx = int(V[1]);
        if (num(b.f[fidx]) < 0) {
          if (IF === 0) TF = true;
        } else {
          TF = false;
          if (wasAct) {
            stepMove(b, b.f[fidx], int(V[0]));
            b.f[fidx] = num(b.f[fidx]) - num(V[2]);
          }
        }
        IF = 1;
        break;
      }
      case "length": {
        if (wasAct) {
          const fidx = int(V[0]);
          b.f[fidx] = num(b.f[fidx]) + 1;
          if (num(b.f[fidx]) > num(V[1])) deleteBullet(b, V.slice(2));
        }
        break;
      }
      case "damage": {
        b.hit = 0;
        if (wasAct) {
          const dist = num(V[1]);
          if (dist !== 0) {
            const mode = num(V[0]) < 0 ? "T" : "E";
            let hit = false;
            if (String(V[2]) === "1") {
              hit = simPlayers.some((p) => p.nh
                && (mode === "E" ? p.team !== b.team : p.team === b.team && p.id !== b.owner)
                && Math.hypot(p.x - num(b.f[3]), p.y - num(b.f[4])) < dist);
            } else {
              const t = searchFirst(b, mode);
              hit = !!t && t.dist < dist;
            }
            if (hit) {
              b.hit = 1;
              const ct = String(V[3]);
              if (ct !== "ND" && ct !== "ON") deleteBullet(b, V.slice(3));
            }
          }
        }
        break;
      }
      case "sword damage": {
        b.hit = 0;
        if (wasAct) {
          const t = searchFirst(b, "E");
          if (t && t.dist < num(V[1])) {
            const dirTo = Math.atan2(t.x - num(b.f[3]), t.y - num(b.f[4])) / DEG;
            if (t.dist < 30 || Math.abs(wrap180(dirTo - num(b.f[5]))) < num(V[2])) {
              b.hit = 1;
              deleteBullet(b, ["-", "-", "-", "-", "-", "-"]);
            }
          }
        }
        break;
      }
      case "break wall":
        if (wasAct && wallAt(num(b.f[3]), num(b.f[4]))) deleteBullet(b, V.slice(0, 6));
        break;
      case "delete":
        if (wasAct) deleteBullet(b, V.slice(0, 6));
        break;
      case "create":
        if (wasAct) {
          spawnBullet(b.owner, String(V[0]), num(b.f[3]), num(b.f[4]),
            num(b.f[5]) + num(V[1]), V.slice(2), b.team);
        }
        break;
      case "stamp": case "team stamp":
        if (wasAct) b.stamps.push({ name: String(V[0]), size: num(V[1]), ghost: num(V[2]) });
        break;
      case "mine stamp":
        if (wasAct) b.stamps.push({ name: String(V[0]), size: num(V[1]), ghost: 50 });
        break;
      case "draw bullet":
        if (wasAct) {
          b.tracer = {
            color: String(V[0]) === "-" ? "#ff6b00" : String(V[0]),
            len: num(V[1]), size: num(V[2]),
          };
        }
        break;
      case "draw circle":
        // 実プロジェクト側の新命令 (蒸留スナップショットには無い):
        // 色・直径・透明度で円を描く (回復エフェクト等の拡大フェード)
        if (wasAct) {
          b.circle = { color: String(V[0]), size: num(V[1]), ghost: num(V[2]) };
        }
        break;
      case "draw laser": case "laser": {
        if (wasAct && String(V[0]) !== "-") {
          b.laser = {
            color: String(V[0]) === "Fire" ? "#ff0000" : String(V[0]),
            len: num(V[1]), size: num(V[2]),
            alpha: String(V[3]) === "T" ? 0.5
              : Math.min(1, Math.max(0.1, 1 - num(V[3]) / 100)),
          };
        }
        break;
      }
      case "set position": {
        if (wasAct) {
          const o = playerById(b.owner);
          if (o) {
            b.f[3] = o.x + Math.sin(num(V[0]) * DEG) * num(V[1]);
            b.f[4] = o.y + Math.cos(num(V[0]) * DEG) * num(V[1]);
          }
        }
        break;
      }
      case "set length": {
        if (wasAct) {
          const dir = num(b.f[5]);
          const sx = Math.sin(dir * DEG), sy = Math.cos(dir * DEG);
          let g = -50, ex = num(b.f[3]), ey = num(b.f[4]), wall = false;
          while (!(num(V[1]) < g || wall)) {
            g += 50;
            wall = wallAt(ex, ey);
            if (!wall) { ex += sx * 50; ey += sy * 50; }
          }
          while (!(g < 1 || !wall)) {
            ex -= sx * 5; ey -= sy * 5; g -= 5;
            wall = wallAt(ex, ey);
          }
          b.f[int(V[0])] = g;
        }
        break;
      }
      case "homing": case "team homing": case "roll homing": {
        if (wasAct) {
          const field = cmd === "roll homing" ? 6 : 5;
          const t = searchFirst(b, cmd === "team homing" ? "T" : "E");
          if (t) {
            const desired = Math.atan2(t.x - num(b.f[3]), t.y - num(b.f[4])) / DEG;
            const diff = wrap180(desired - num(b.f[field]));
            const turn = num(V[1]);
            if (Math.abs(diff) < turn) b.f[field] = desired;
            else b.f[field] = num(b.f[field]) + Math.sign(diff) * turn;
          }
          stepMove(b, V[0], field);
        }
        break;
      }
      case "search first":
        if (wasAct) searchFirst(b, String(V[0]));
        break;
      case "about shield cannon": {
        // 原作 About shield cannon 手順の要旨 (前進 + 至近で爆発)
        if (wasAct) {
          stepMove(b, 8, 5);
          const t = searchFirst(b, "E");
          if (t && t.dist < 35) {
            b.hit = 1;
            deleteBullet(b, ["爆発", 10, 8, 5, 0, 0]);
          }
          b.f[6] = num(b.f[6]) + 10;
          if (num(b.f[6]) > 120) b.f[6] = 0;
        }
        break;
      }
      case "about chat": {
        if (wasAct) {
          const o = playerById(b.owner);
          if (o) { b.f[3] = o.x; b.f[4] = o.y + num(b.f[9]); }
          b.f[7] = num(b.f[7]) + 6;
          if (num(b.f[7]) > 180) b.dead = true;
        }
        break;
      }
      case "about drop item": {
        if (wasAct) {
          const name = weaponsData?.items[int(b.f[7]) - 1]?.[0];
          if (name) {
            b.stamps.push({ name: "@" + name, size: 97, ghost: 0, upright: true });
          }
        }
        break;
      }
      case "about damage val": {
        if (wasAct) {
          b.stamps.push({ text: String(b.f[6]), size: num(b.f[7]), ghost: 0 });
        }
        break;
      }
      case "about money": case "about tektite": case "about present": {
        if (wasAct) {
          const near = simPlayers.some((p) =>
            Math.hypot(p.x - num(b.f[3]), p.y - num(b.f[4])) < 25);
          if (near) b.dead = true;
        }
        break;
      }
      default:
        // sound / bush / put buff / guard / screen stamp / color /
        // skill point / shield / auto heal / about jj-100 は観戦では無処理
        break;
    }
    if (b.dead) break;
  }
}

function stepSim() {
  flushBullets();
  for (const b of bullets) if (!b.dead) simBullet(b);
  bullets = bullets.filter((b) => !b.dead);
  flushBullets();
}

// ---------- 描画 ----------

function drawBullets() {
  ctx.lineCap = "round";
  for (const b of bullets) {
    const x = num(b.f[3]), y = num(b.f[4]), dir = num(b.f[5]);
    const [px, py] = toPx(x, y);
    if (b.tracer) {
      const [ex, ey] = toPx(
        x - Math.sin(dir * DEG) * b.tracer.len,
        y - Math.cos(dir * DEG) * b.tracer.len);
      ctx.beginPath();
      ctx.moveTo(px, py);
      ctx.lineTo(ex, ey);
      ctx.strokeStyle = b.tracer.color;
      ctx.lineWidth = Math.max(1, b.tracer.size * UNIT);
      ctx.stroke();
    }
    if (b.laser) {
      const [ex, ey] = toPx(
        x + Math.sin(dir * DEG) * b.laser.len,
        y + Math.cos(dir * DEG) * b.laser.len);
      ctx.beginPath();
      ctx.moveTo(px, py);
      ctx.lineTo(ex, ey);
      ctx.strokeStyle = b.laser.color;
      ctx.globalAlpha = b.laser.alpha;
      ctx.lineWidth = Math.max(1, b.laser.size * UNIT);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    if (b.circle) {
      ctx.beginPath();
      ctx.arc(px, py, Math.max(0.5, b.circle.size / 2 * UNIT), 0, Math.PI * 2);
      ctx.fillStyle = b.circle.color;
      ctx.globalAlpha = Math.min(1, Math.max(0, 1 - b.circle.ghost / 100));
      ctx.fill();
      ctx.globalAlpha = 1;
    }
    const bkey = b.type === "Money" && new Date().getMonth() === 11
      ? "B/Money star" : "B/" + b.type;
    drawCostume(bkey, x, y, dir);
    for (const st of b.stamps) {
      if (st.text !== undefined) {
        ctx.font = `bold ${Math.max(8, st.size * 0.22)}px system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.fillStyle = "#ffffff";
        ctx.fillText(st.text, px, py);
        ctx.textAlign = "left";
      } else {
        drawStampCostume(st.name, x, y, st.upright ? 90 : dir,
          st.size / 100, 1 - st.ghost / 100);
      }
    }
  }
}

function fallbackDot(p, x, y) {
  const [px, py] = toPx(x, y);
  ctx.beginPath();
  ctx.arc(px, py, 9, 0, Math.PI * 2);
  ctx.fillStyle = p.tc ? `hsl(${TEAM_HUES[p.tc] || 0} 75% 60%)` : "#f87171";
  ctx.fill();
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 2;
  ctx.stroke();
}

// プレイヤー1人の描画 (原作 draw.ts の重ね順: 手2 → 武器アイコン → 手1 →
// 持ち武器 → 体。特殊武器は Tank / Guardian / Shield Cannon の専用形態)
function drawPlayer(p, x, y, now) {
  const mo = p.mo || [0, 0, 0, 0, 0, 0, 0];
  const item = p.wpn ? weaponsData?.items[p.wpn - 1] : null;
  const wname = item ? String(item[0]) : "";
  const char = p.char;
  // f18 のバフ表示 (マゼンタ円)
  if (p.b18) {
    const [px, py] = toPx(x, y);
    ctx.beginPath();
    ctx.arc(px, py, 37.5 * UNIT, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(255, 0, 220, 0.25)";
    ctx.fill();
  }
  // バフ/デバフアイコン (原作は時刻で回転しながら周囲に置く)
  const base = now / 1000 * 80;
  for (let k = 0; k < (p.buf || 0); k++) drawCostume("C/Buff", x, y, base + 90 * k);
  for (let k = 0; k < (p.deb || 0); k++) drawCostume("C/Debuff", x, y, base + 45 + 90 * k);
  let bodyDrawn = false;
  if (p.sp) {
    if (["戦車主砲", "戦車重機関銃", "戦車発火弾"].includes(wname)) {
      drawCostume("C/Tank hand2", x + mo[4], y + mo[5], mo[6]);
      drawCostume("C/Tank hand1", x + mo[1], y + mo[2], mo[3]);
      bodyDrawn = drawCostume("C/Tank body", x, y, mo[0]);
    } else if (["ガーディアンスラッシュ", "ガーディアンクイック", "ガーディアンバースト"].includes(wname)) {
      drawCostume("C/Guardian hand2", x + mo[4] * 2, y + mo[5] * 2, mo[6]);
      drawCostume("C/Guardian hand1", x + mo[1] * 2, y + mo[2] * 2, mo[3]);
      bodyDrawn = drawCostume("C/Guardian body", x, y, mo[0]);
    } else if (["シールドキャノン", "シールドバッシュ", "シールドファンネル"].includes(wname)) {
      drawCostume("C/Shield Cannon", x + mo[1], y + mo[2], mo[3]);
      bodyDrawn = drawCostume("C/Shield Cannon body", x, y, mo[0]);
    }
    // それ以外の特殊武器は原作でも本体を描かない
  } else {
    const f = p.skin && hasImg(`C/${char} hand+`) ? "+" : "";
    drawCostume(`C/${char} hand${f}`, x + mo[4], y + mo[5], mo[6]);
    if (char !== "The Player" && wname) {
      drawCostume("WL/" + wname, x + mo[4], y + mo[5], mo[6]);
    }
    drawCostume(`C/${char} hand${f}`, x + mo[1], y + mo[2], mo[3]);
    if (char !== "The Player" && item) {
      const cat = String(item[1]);
      if (cat.includes("物理") && p.atk) {
        // 攻撃中は弾コスチュームを前方35へ振る
        drawCostume("B/" + wname,
          x + mo[1] + Math.sin(mo[3] * DEG) * 35,
          y + mo[2] + Math.cos(mo[3] * DEG) * 35, mo[3]);
      }
      const wkey = cat.includes("オーバーヒート") && !p.atk ? "WX/" + wname : "W/" + wname;
      drawCostume(wkey, x + mo[1], y + mo[2], mo[3]);
    }
    bodyDrawn = drawCostume(`C/${char} body${p.skin ? "+" : ""}`, x, y, mo[0]);
    if (!bodyDrawn && p.skin) {
      // body+ が無いキャラは body で代用 (原作では無表示になる救済)
      bodyDrawn = drawCostume(`C/${char} body`, x, y, mo[0]);
    }
    if (!bodyDrawn) fallbackDot(p, x, y);
  }
  // HPバー (原作は味方のみ表示だが、観戦では全員に出す)
  if (p.max > 0) {
    const [bx, by] = toPx(x, y + 41);
    const wHp = 52 * UNIT, hHp = Math.max(2.5, 12 * UNIT);
    ctx.fillStyle = "#272727";
    ctx.fillRect(bx - wHp / 2, by - hHp / 2, wHp, hHp);
    ctx.fillStyle = "#2aff00";
    const ratio = Math.max(0, Math.min(1, p.hp / p.max));
    ctx.fillRect(bx - wHp / 2, by - hHp / 2, wHp * ratio, hHp);
  }
  // チャット入力中は白バー、その上に名前 (原作の頭上表示)
  const [nx, ny] = toPx(x, y + 58);
  if (p.chat) {
    ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
    ctx.fillRect(nx - 36 * UNIT, ny - 9 * UNIT, 72 * UNIT, 18 * UNIT);
  }
  const label = p.name || `ID ${p.id}`;
  ctx.font = "600 15px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.lineWidth = 3;
  ctx.strokeStyle = "rgba(0, 0, 0, 0.7)";
  ctx.strokeText(label, nx, ny);
  ctx.fillStyle = p.tc ? `hsl(${TEAM_HUES[p.tc] || 0} 90% 68%)` : "#ffffff";
  ctx.fillText(label, nx, ny);
  ctx.textAlign = "left";
}

// 全プレイヤーの補間位置 (id -> [x, y])。描画とフォローカメラで共用。
function interpPositions(now) {
  const out = new Map();
  if (!curSnap) return out;
  const span = Math.max(1, curAt - prevAt);
  const t = Math.min(1, (now - curAt) / span);
  const prevById = new Map((prevSnap?.players || []).map((p) => [p.id, p]));
  for (const p of curSnap.players) {
    const q = prevById.get(p.id);
    out.set(p.id, [
      q ? q.x + (p.x - q.x) * t : p.x,
      q ? q.y + (p.y - q.y) * t : p.y,
    ]);
  }
  return out;
}

function drawPlayers(now, positions) {
  if (!curSnap) return;
  for (const p of curSnap.players) {
    const [x, y] = positions.get(p.id) || [p.x, p.y];
    drawPlayer(p, x, y, now);
  }
}

// ---------- プレイヤー選択 (フォロー) ----------

// 一覧はハンバーガーメニュー内にしまう (常時表示だと観戦の邪魔)。
menuBtn.addEventListener("click", () => playersEl.classList.toggle("open"));

function renderPlayerList() {
  playersEl.innerHTML = "";
  if (!curSnap) return;
  for (const p of curSnap.players) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip" + (p.id === followId ? " active" : "");
    b.textContent = p.name || `ID ${p.id}`;
    b.addEventListener("click", () => {
      setFollow(p.id === followId ? null : p.id);
      playersEl.classList.remove("open");
    });
    playersEl.appendChild(b);
  }
}

function setFollow(id) {
  followId = id;
  renderPlayerList();
  if (id === null) {
    followbar.style.display = "none";
  } else {
    const p = curSnap?.players.find((pl) => pl.id === id);
    followName.textContent = `${p?.name || `ID ${id}`} の視点`;
    followbar.style.display = "flex";
  }
}

followExit.addEventListener("click", () => setFollow(null));

// キャンバスのタップでも選択 (最も近いプレイヤー、画面 44px 以内)。
// フォロー中に何もない場所をタップしても解除しない。
canvas.addEventListener("pointerdown", (e) => {
  if (!curSnap || !view.s) return;
  const vx = (e.clientX - view.ox) / view.s;
  const vy = (e.clientY - view.oy) / view.s;
  const positions = interpPositions(performance.now());
  let best = null, bestD = 44 / view.s;
  for (const p of curSnap.players) {
    const pos = positions.get(p.id);
    if (!pos) continue;
    const [px, py] = toPx(pos[0], pos[1]);
    const d = Math.hypot(px - vx, py - vy);
    if (d < bestD) { bestD = d; best = p; }
  }
  if (best) setFollow(best.id);
});

let simAcc = 0, lastFrameAt = 0;
function frame(now) {
  const vw = window.innerWidth, vh = window.innerHeight;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== vw * dpr || canvas.height !== vh * dpr) {
    canvas.width = vw * dpr;
    canvas.height = vh * dpr;
  }
  const positions = interpPositions(now);
  // フォロー対象が消えたら全体表示に戻る
  let followPos = null;
  if (followId !== null && curSnap) {
    if (positions.has(followId)) followPos = positions.get(followId);
    else setFollow(null);
  }
  let s, ox, oy;
  if (followPos) {
    // 原作の視野 (480×360 ユニット) を対象中心で画面にフィットさせる
    const [cx, cy] = toPx(followPos[0], followPos[1]);
    s = Math.min(vw / (480 * UNIT), vh / (360 * UNIT));
    ox = vw / 2 - cx * s;
    oy = vh / 2 - cy * s;
  } else {
    s = Math.min(vw / VW, vh / VH);
    ox = (vw - VW * s) / 2;
    oy = (vh - VH * s) / 2;
  }
  view = { s, ox, oy, vw, vh };
  ctx.setTransform(dpr * s, 0, 0, dpr * s, dpr * ox, dpr * oy);
  ctx.fillStyle = "#0d1117";
  ctx.fillRect(-VW, -VH, VW * 3, VH * 3);
  if (followPos) drawTerrainLive();
  else if (terrain) ctx.drawImage(terrain.canvas, 0, 0);
  // 弾シミュレーションは原作の 30fps 固定ステップ
  if (lastFrameAt) simAcc += Math.min(250, now - lastFrameAt);
  lastFrameAt = now;
  while (simAcc >= 1000 / 30) {
    simAcc -= 1000 / 30;
    stepSim();
  }
  drawBullets();
  drawPlayers(now, positions);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

async function handleSnap(snap) {
  if (snap.type !== "state") return;
  if (snap.project !== curProject) {
    curProject = snap.project;
    weaponsData = null;
    bullets = [];
    pendingBullets = [];
    seenShots.clear();
    skillUse.clear();
    gimmickReady.clear();
    loadWeapons(snap.project);
  }
  prevSnap = curSnap; prevAt = curAt;
  curSnap = snap; curAt = performance.now();
  simPlayers = snap.players;
  // フォロー対象が退出したら全体表示に戻し、一覧と追随表示を同期する
  if (followId !== null && !snap.players.some((p) => p.id === followId)) {
    followId = null;
  }
  if (followId === null) {
    followbar.style.display = "none";
    renderPlayerList();
  } else {
    setFollow(followId);
  }
  hudTitle.textContent = snap.festival ? "TACT-AIM blood (フェス)" : "TACT-AIM";
  hudCount.textContent = `オンライン: ${snap.players.length}人`;
  hudState.textContent = "";
  for (const p of snap.players) processShots(p);
  await buildTerrain(snap.project, snap.map);
}

// 公開経路 (AGO Web のリライト中継) は WebSocket を通せないため、
// /snapshot を 0.5 秒ごとにポーリングし、変化があった時だけ反映する。
let lastPayload = "";
async function poll() {
  try {
    const res = await fetch("snapshot", { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    const text = await res.text();
    if (text !== lastPayload) {
      lastPayload = text;
      await handleSnap(JSON.parse(text));
    }
  } catch {
    hudState.textContent = "切断されました。再接続します…";
  } finally {
    setTimeout(poll, 500);
  }
}
poll();

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
