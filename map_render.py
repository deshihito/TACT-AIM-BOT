"""プレイヤー配置マップの PNG レンダリング (Pillow)。

地形は原作のコスチューム素材 (assets/<project_id>/) をタイルごとに貼る。
素材のない種別やプロジェクトでは色タイル/グリッドにフォールバックする。
重い処理なので呼び出し側で asyncio.to_thread 経由で実行すること。
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from record import PlayerRecord

ASSETS_DIR = Path(__file__).with_name("assets")

# 種別 → コスチュームキー。{s} は f6 の符号で決まる接尾辞 (負なら 1)
TYPE_COSTUME = {
    1: "wall", 3: "bush", 6: "spike_wall", 18: "grid_wall", 27: "mud",
    25: "shield", 19: "booster", 7: "warp1", 2: "door",
    20: "flash_wall{s}", 21: "heal_wall{s}", 22: "laser_wall{s}",
    23: "warp_floor{s}", 24: "energy_wall{s}", 28: "team_gem{s}",
    4: "map_pointer", 5: "pvp_manager", 8: "auto_buffer",
    9: "teaming_machine", 10: "information_base", 11: "locker",
    12: "minigame_hub1", 13: "minigame_hub2", 14: "telescope",
    16: "reporter", 17: "computer", 26: "record_player",
}
ROTATING_TYPES = {2, 19, 22}  # f5 (角度) で回転する種別

_asset_cache: dict[str, tuple[dict, dict]] = {}


def _load_assets(project_id: str):
    """(manifest, {key: PIL Image}) を返す。素材が無ければ None。"""
    if project_id in _asset_cache:
        return _asset_cache[project_id]
    d = ASSETS_DIR / project_id
    manifest_path = d / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    images = {}
    for key in manifest:
        p = d / f"{key}.png"
        if p.exists():
            images[key] = Image.open(p).convert("RGBA")
    result = (manifest, images)
    _asset_cache[project_id] = result
    return result


def _tile_image(assets, typ: int, f5: float, f6: float, tile_px: float,
                cache: dict) -> Image.Image | None:
    manifest, images = assets
    template = TYPE_COSTUME.get(typ)
    if template is None:
        return None
    if typ == 15:
        key = "skill_spot0" if f5 > 0 else "skill_spot1"
    elif "{s}" in template:
        key = template.format(s=1 if f6 < 0 else 0)
    else:
        key = template
    src = images.get(key)
    if src is None:
        return None
    angle = (90 - f5) if typ in ROTATING_TYPES else 0
    cache_key = (key, angle, round(tile_px))
    if cache_key in cache:
        return cache[cache_key]
    w_units, h_units = manifest[key]
    tw = max(1, round(tile_px * w_units / 50))
    th = max(1, round(tile_px * h_units / 50))
    img = src.resize((tw, th), Image.LANCZOS)
    if angle:
        img = img.rotate(angle, resample=Image.BICUBIC, expand=True)
    cache[cache_key] = img
    return img

WORLD = (-1000, -2000, 5000, 2000)  # x_min, y_min, x_max, y_max (グリッド表示用)
MAP_WORLD = (-1500, -1100, 4750, 1100)  # 実マップ表示用 (タイル範囲由来)
IMG_W, IMG_H = 1200, 800
TILE_COLORS = {
    1: (156, 163, 175),   # Wall
    18: (107, 114, 128),  # Grid wall
    6: (251, 146, 60),    # Spike wall
    3: (22, 163, 74),     # Bush
    27: (160, 90, 40),    # Mud
    25: (34, 211, 238),   # Shield
    20: (250, 204, 21),   # Flash wall
    21: (74, 222, 128),   # Heal wall
    22: (220, 38, 38),    # Laser wall
    23: (192, 132, 252),  # Warp floor
    24: (96, 165, 250),   # Energy wall
    19: (45, 212, 191),   # Booster
    28: (244, 114, 182),  # Team gem
    15: (253, 224, 71),   # Skill spot
    2: (203, 213, 225),
    7: (100, 116, 139),
}
STATION_TYPES = {4, 5, 8, 9, 10, 11, 12, 13, 14, 16, 17, 26}  # 施設類は小さく描く
STATION_COLOR = (226, 232, 240)
BG = (13, 17, 23)
GRID = (31, 41, 55)
AXIS = (71, 85, 105)
TEXT = (226, 232, 240)
SUBTEXT = (148, 163, 184)
PALETTE = [
    (248, 113, 113), (251, 191, 36), (52, 211, 153), (96, 165, 250),
    (192, 132, 252), (244, 114, 182), (45, 212, 191), (251, 146, 60),
    (163, 230, 53), (125, 211, 252), (253, 224, 71), (240, 171, 252),
]


def _font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _make_to_px(world, w: int, h: int):
    x0, y0, x1, y1 = world

    def to_px(x: float, y: float) -> tuple[float, float]:
        return (
            (x - x0) / (x1 - x0) * w,
            h - (y - y0) / (y1 - y0) * h,
        )

    return to_px


def _to_px(x: int, y: int) -> tuple[float, float]:
    return _make_to_px(WORLD, IMG_W, IMG_H)(x, y)


def render_map_png(players: list[PlayerRecord], tiles: list | None = None,
                   project_id: str = "") -> bytes:
    if tiles is not None:
        return _render_with_tiles(players, tiles, project_id)
    img = Image.new("RGB", (IMG_W, IMG_H), BG)
    d = ImageDraw.Draw(img)
    grid_font = _font(16)
    # グリッド (500単位) と座標ラベル
    gx = WORLD[0]
    while gx <= WORLD[2]:
        px, _ = _to_px(gx, 0)
        d.line([(px, 0), (px, IMG_H)], fill=AXIS if gx == 0 else GRID, width=1)
        d.text((px + 4, IMG_H - 22), str(gx), fill=SUBTEXT, font=grid_font)
        gx += 500
    gy = WORLD[1]
    while gy <= WORLD[3]:
        _, py = _to_px(0, gy)
        d.line([(0, py), (IMG_W, py)], fill=AXIS if gy == 0 else GRID, width=1)
        d.text((6, py + 4), str(gy), fill=SUBTEXT, font=grid_font)
        gy += 500
    d.rectangle([0, 0, IMG_W - 1, IMG_H - 1], outline=AXIS, width=2)
    _draw_players(d, players, _to_px, IMG_W, IMG_H)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _render_with_tiles(players: list[PlayerRecord], tiles: list,
                       project_id: str = "") -> bytes:
    w, h = 2000, 704
    to_px = _make_to_px(MAP_WORLD, w, h)
    img = Image.new("RGBA", (w, h), BG + (255,))
    d = ImageDraw.Draw(img)
    tile_px = 50 / (MAP_WORLD[2] - MAP_WORLD[0]) * w
    assets = _load_assets(project_id) if project_id else None
    tile_cache: dict = {}
    for rec in tiles:
        gx, gy, typ = rec[0], rec[1], rec[2]
        f5 = rec[3] if len(rec) > 3 else 0
        f6 = rec[4] if len(rec) > 4 else 0
        cx, cy = to_px(gx * 50, gy * 50)
        tile_img = None
        if assets is not None:
            tile_img = _tile_image(assets, typ, f5, f6, tile_px, tile_cache)
        if tile_img is not None:
            img.alpha_composite(
                tile_img, (round(cx - tile_img.width / 2), round(cy - tile_img.height / 2))
            )
        elif typ in STATION_TYPES:
            s = tile_px * 0.32
            d.rectangle([cx - s, cy - s, cx + s, cy + s], fill=STATION_COLOR)
        else:
            s = tile_px / 2 + 0.5
            d.rectangle(
                [cx - s, cy - s, cx + s, cy + s],
                fill=TILE_COLORS.get(typ, (100, 116, 139)),
            )
    d.rectangle([0, 0, w - 1, h - 1], outline=AXIS, width=2)
    _draw_players(d, players, to_px, w, h)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _draw_players(d: ImageDraw.ImageDraw, players: list[PlayerRecord], to_px,
                  img_w: int, img_h: int) -> None:

    label_font = _font(19)
    sub_font = _font(16)
    placed_rects: list[tuple[float, float, float, float]] = []

    def label_pos(px, py, w):
        # ラベル (2行・高さ44) の配置候補。既存ラベルと重ならない最初の候補を使う
        raw = [
            (px + 14, py - 20), (px + 14, py + 8), (px - 14 - w, py - 20),
            (px - 14 - w, py + 8), (px + 14, py - 52), (px + 14, py + 40),
            (px - 14 - w, py - 52), (px - 14 - w, py + 40),
        ]
        candidates = [
            (min(max(lx, 2), max(2, img_w - w - 2)), min(max(ly, 2), max(2, img_h - 46)))
            for lx, ly in raw
        ]
        for lx, ly in candidates:
            rect = (lx, ly, lx + w, ly + 44)
            if any(
                lx < r[2] + 4 and rect[2] > r[0] - 4 and ly < r[3] + 4 and rect[3] > r[1] - 4
                for r in placed_rects
            ):
                continue
            placed_rects.append(rect)
            return lx, ly
        lx, ly = candidates[0]
        placed_rects.append((lx, ly, lx + w, ly + 44))
        return lx, ly

    for i, r in enumerate(players):
        px, py = to_px(r.x, r.y)
        color = PALETTE[i % len(PALETTE)]
        if r.special_weapon:
            d.ellipse([px - 16, py - 16, px + 16, py + 16], outline=(34, 197, 94), width=3)
        d.ellipse([px - 9, py - 9, px + 9, py + 9], fill=color, outline=(255, 255, 255), width=2)
        title = f"{r.player_id} {r.character}"
        w = d.textlength(title, font=label_font)
        lx, ly = label_pos(px, py, w)
        d.text((lx, ly), title, fill=TEXT, font=label_font)
        d.text((lx, ly + 22), f"HP {r.hp}/{r.max_hp}", fill=SUBTEXT, font=sub_font)
