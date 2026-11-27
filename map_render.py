"""プレイヤー配置マップの PNG レンダリング (Pillow)。

ワールド座標の範囲は原作データ由来: マップ端の座標群が x ≈ -1000..5000、
y ≈ -2000..2000 (スポーン (3200, 0)、チーム戦タレット (3200, -2000))。
重い処理なので呼び出し側で asyncio.to_thread 経由で実行すること。
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from record import PlayerRecord

WORLD = (-1000, -2000, 5000, 2000)  # x_min, y_min, x_max, y_max
IMG_W, IMG_H = 1200, 800
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


def _to_px(x: int, y: int) -> tuple[float, float]:
    x0, y0, x1, y1 = WORLD
    px = (x - x0) / (x1 - x0) * IMG_W
    py = IMG_H - (y - y0) / (y1 - y0) * IMG_H
    return px, py


def render_map_png(players: list[PlayerRecord]) -> bytes:
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

    label_font = _font(19)
    sub_font = _font(16)
    placed_rects: list[tuple[float, float, float, float]] = []

    def label_pos(px, py, w):
        # ラベル (2行・高さ44) の配置候補。既存ラベルと重ならない最初の候補を使う
        candidates = [
            (px + 14, py - 20), (px + 14, py + 8), (px - 14 - w, py - 20),
            (px - 14 - w, py + 8), (px + 14, py - 52), (px + 14, py + 40),
            (px - 14 - w, py - 52), (px - 14 - w, py + 40),
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
        px, py = _to_px(r.x, r.y)
        color = PALETTE[i % len(PALETTE)]
        d.ellipse([px - 9, py - 9, px + 9, py + 9], fill=color, outline=(255, 255, 255), width=2)
        title = f"{r.player_id} {r.character}"
        w = d.textlength(title, font=label_font)
        lx, ly = label_pos(px, py, w)
        d.text((lx, ly), title, fill=TEXT, font=label_font)
        d.text((lx, ly + 22), f"HP {r.hp}/{r.max_hp}", fill=SUBTEXT, font=sub_font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
