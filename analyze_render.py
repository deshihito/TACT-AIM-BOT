"""/analyze の PNG レンダリング (Pillow)。

behavior.analyze() の結果 dict を1枚のカード画像にする:
戦闘力 (レーダー+スコア)、戦績、編成傾向 (キャラ/武器/組み合わせ)、
行動のクセ (位置ヒートマップ、活動時間帯、各種比率)。
重い処理なので呼び出し側で asyncio.to_thread 経由で実行すること。
"""
from __future__ import annotations

import io
import math
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from behavior import HEAT_H, HEAT_W

BG = (13, 17, 23)
PANEL = (22, 27, 34)
PANEL_LINE = (48, 54, 61)
TEXT = (226, 232, 240)
SUBTEXT = (148, 163, 184)
ACCENT = (88, 166, 255)
GREEN = (63, 185, 80)
ORANGE = (219, 109, 40)
PURPLE = (188, 140, 255)

W = 1020
MARGIN = 36

_FONT_FILES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "DejaVuSans.ttf",
]
_font_path: str | None = None


def _font(size: int):
    global _font_path
    if _font_path is None:
        for cand in _FONT_FILES:
            try:
                ImageFont.truetype(cand, 12)
                _font_path = cand
                break
            except Exception:
                continue
        else:
            return ImageFont.load_default()
    try:
        return ImageFont.truetype(_font_path, size)
    except Exception:
        return ImageFont.load_default()


def _panel(d: ImageDraw.ImageDraw, box, title: str) -> None:
    d.rounded_rectangle(box, radius=10, fill=PANEL, outline=PANEL_LINE, width=1)
    d.text((box[0] + 16, box[1] + 10), title, fill=SUBTEXT, font=_font(19))


def _bar(d, x, y, w, label, pct, color, font_size=20, max_label=250):
    f = _font(font_size)
    while label and d.textlength(label, font=f) > max_label:
        label = label[:-2] + "…" if len(label) > 2 else label[:-1]
    d.text((x, y), label, fill=TEXT, font=f)
    lw = d.textlength(label, font=f)
    bx = x + lw + 12
    bw = max(40.0, w - lw - 76)
    d.rounded_rectangle((bx, y + 3, bx + bw, y + 19), radius=8, fill=(33, 38, 45))
    if pct > 0:
        d.rounded_rectangle(
            (bx, y + 3, bx + bw * min(pct, 100) / 100, y + 19),
            radius=8,
            fill=color,
        )
    d.text((bx + bw + 10, y), f"{pct:.0f}%", fill=SUBTEXT, font=_font(font_size))


def _heat_color(v: float):
    """0..1 → 寒色→暖色のグラデーション。"""
    stops = [(13, 17, 40), (20, 60, 160), (0, 170, 200), (250, 200, 40), (240, 60, 40)]
    pos = max(0.0, min(1.0, v)) * (len(stops) - 1)
    i = int(pos)
    if i >= len(stops) - 1:
        return stops[-1]
    f = pos - i
    a, b = stops[i], stops[i + 1]
    return tuple(round(a[c] + (b[c] - a[c]) * f) for c in range(3))


def _radar(img: Image.Image, cx: float, cy: float, r: float, power: dict) -> None:
    labels = list(power.keys())
    values = [power[k] for k in labels]
    n = len(labels)
    d = ImageDraw.Draw(img)
    angles = [-math.pi / 2 + 2 * math.pi * i / n for i in range(n)]

    def pt(angle, dist):
        return (cx + dist * math.cos(angle), cy + dist * math.sin(angle))

    for ring in (0.25, 0.5, 0.75, 1.0):
        poly = [pt(a, r * ring) for a in angles]
        d.polygon(poly, outline=PANEL_LINE)
    for a in angles:
        d.line((cx, cy, *pt(a, r)), fill=PANEL_LINE)
    poly = [pt(a, r * max(0.02, min(1.0, v / 100))) for a, v in zip(angles, values)]
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.polygon(poly, fill=ACCENT + (90,), outline=ACCENT + (255,), width=2)
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"), (0, 0))
    d = ImageDraw.Draw(img)
    for (x, y), label, v in zip(poly, labels, values):
        d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=ACCENT)
    for a, label, v in zip(angles, labels, values):
        x, y = pt(a, r + 30)
        text = f"{label} {v:.0f}"
        tw = d.textlength(text, font=_font(19))
        d.text((x - tw / 2, y - 11), text, fill=TEXT, font=_font(19))


def render_analyze_png(a: dict) -> bytes:
    img = Image.new("RGB", (W, 1440), BG)
    d = ImageDraw.Draw(img)

    # --- ヘッダー ---
    d.text((MARGIN, 24), "プレイヤー分析", fill=SUBTEXT, font=_font(22))
    d.text((MARGIN, 52), a["name"], fill=TEXT, font=_font(44))
    ph = a["play_hours"]
    last = datetime.fromtimestamp(a["last"], ZoneInfo("Asia/Tokyo"))
    sub = (
        f"プレイ時間 約{int(ph)}時間{int(ph % 1 * 60)}分 / 観測 {a['samples']}回"
        f" / 最終観測 {last:%m-%d %H:%M}"
    )
    d.text((MARGIN, 108), sub, fill=SUBTEXT, font=_font(20))

    # --- 戦績ストリップ ---
    stats = [
        ("キル", f"{a['kills']}", ACCENT),
        ("デス", f"{a['deaths']}", ORANGE),
        ("K/D", f"{a['kd']:.2f}", GREEN),
        ("与ダメージ", f"{a['damage']:,}", PURPLE),
    ]
    bw = (W - MARGIN * 2 - 18 * 3) / 4
    for i, (label, val, color) in enumerate(stats):
        x = MARGIN + i * (bw + 18)
        box = (x, 148, x + bw, 238)
        d.rounded_rectangle(box, radius=10, fill=PANEL, outline=PANEL_LINE)
        d.text((x + 16, box[1] + 10), label, fill=SUBTEXT, font=_font(19))
        d.text((x + 16, box[1] + 36), val, fill=color, font=_font(38))

    # --- 戦闘力 (左) ---
    _panel(d, (MARGIN, 262, 500, 700), "戦闘力")
    d.text((MARGIN + 16, 292), f"{a['power_score']:.0f}", fill=ACCENT, font=_font(72))
    sw = d.textlength(f"{a['power_score']:.0f}", font=_font(72))
    d.text((MARGIN + 24 + sw, 330), "/ 100", fill=SUBTEXT, font=_font(24))
    _radar(img, MARGIN + 232, 520, 104, a["power"])
    d = ImageDraw.Draw(img)

    # --- 編成 (右) ---
    rx = 524
    _panel(d, (rx, 262, W - MARGIN, 700), "よく使う編成 (キャラ × 武器)")
    y = 300
    if a["top_combos"]:
        for (char, weapon), pct in a["top_combos"]:
            _bar(d, rx + 16, y, W - MARGIN - rx - 32, f"{char} × {weapon}", pct, ACCENT)
            y += 30
    else:
        d.text((rx + 16, y), "データ不足", fill=SUBTEXT, font=_font(20))
        y += 30
    y += 6
    d.text((rx + 16, y), "キャラ", fill=SUBTEXT, font=_font(19))
    y += 26
    for name, pct in a["top_chars"]:
        _bar(d, rx + 16, y, W - MARGIN - rx - 32, name, pct, GREEN)
        y += 30
    y += 6
    d.text((rx + 16, y), "武器", fill=SUBTEXT, font=_font(19))
    y += 26
    for name, pct in a["top_weapons"]:
        _bar(d, rx + 16, y, W - MARGIN - rx - 32, name, pct, ORANGE)
        y += 30

    # --- 行動のクセ ---
    _panel(d, (MARGIN, 724, W - MARGIN, 1190), "行動のクセ (位置ヒートマップ)")
    cell = 13
    hx, hy = MARGIN + 16, 776
    heat_max = max(1, a["heat_max"])
    for gy in range(HEAT_H):
        for gx in range(HEAT_W):
            n = a["heat"].get((gx, HEAT_H - 1 - gy), 0)
            v = math.log1p(n) / math.log1p(heat_max) if n else 0.0
            color = _heat_color(v) if n else (24, 28, 36)
            d.rectangle(
                (hx + gx * cell, hy + gy * cell, hx + (gx + 1) * cell - 1, hy + (gy + 1) * cell - 1),
                fill=color,
            )
    # ロビー境界 (x=450)
    lobby_px = hx + (450 - (-500)) / 200 * cell
    d.line((lobby_px, hy, lobby_px, hy + HEAT_H * cell), fill=TEXT, width=1)
    d.text((hx + 2, hy + HEAT_H * cell + 6), "ロビー | バトルエリア", fill=SUBTEXT, font=_font(17))

    sx = hx + HEAT_W * cell + 28
    sy = 776
    d.text((sx, sy), "主なエリア", fill=SUBTEXT, font=_font(19))
    sy += 28
    if a["top_zones"]:
        for zone, pct in a["top_zones"]:
            _bar(d, sx, sy, W - MARGIN - sx - 16, zone, pct, PURPLE, font_size=19)
            sy += 32
    else:
        d.text((sx, sy), "データ不足", fill=SUBTEXT, font=_font(19))
        sy += 32
    sy += 14
    facts = [
        ("ロビー滞在", f"{a['lobby_pct']:.0f}%"),
        ("平均HP", f"{a['avg_hp_pct']:.0f}%"),
        ("SP武器所持", f"{a['special_pct']:.0f}%"),
        ("ピーク時間帯", f"{a['peak_hour']}時台" if a["peak_hour"] is not None else "-"),
    ]
    for label, val in facts:
        d.text((sx, sy), label, fill=SUBTEXT, font=_font(19))
        d.text((sx + 130, sy), val, fill=TEXT, font=_font(19))
        sy += 30

    # 活動時間帯 (下段、幅いっぱい)
    d.text((MARGIN + 16, 1218), "活動時間帯 (JST)", fill=SUBTEXT, font=_font(19))
    hours = a["hours"]
    hmax = max(1, max(hours))
    chart_x, chart_y, chart_w, chart_h = MARGIN + 16, 1256, W - MARGIN * 2 - 32, 110
    slot = chart_w / 24
    for h in range(24):
        bh = chart_h * hours[h] / hmax if hours[h] else 0
        color = ACCENT if h == a["peak_hour"] else (52, 64, 84)
        if bh > 0:
            d.rectangle(
                (chart_x + h * slot + 1, chart_y + chart_h - bh, chart_x + (h + 1) * slot - 1, chart_y + chart_h),
                fill=color,
            )
        if h % 6 == 0:
            d.text((chart_x + h * slot, chart_y + chart_h + 4), f"{h}", fill=SUBTEXT, font=_font(15))

    d.text(
        (MARGIN, 1398),
        "データは Bot が観測した範囲のみ。戦闘力は連携プレイヤー全体との相対評価 (母数が少ない場合は固定基準)。",
        fill=SUBTEXT,
        font=_font(16),
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
