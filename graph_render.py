"""オンライン人数の推移グラフ (Pillow)。重い処理なので to_thread で呼ぶこと。"""
from __future__ import annotations

import io
import time

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 440
PAD_L, PAD_R, PAD_T, PAD_B = 56, 16, 16, 36
BG = (13, 17, 23)
GRID = (31, 41, 55)
LINE = (96, 165, 250)
FILL = (30, 58, 95)
TEXT = (148, 163, 184)


def _font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()


def render_trend_png(points: list[tuple[float, int]]) -> bytes:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    font = _font(16)
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B

    # 表示窓は常に直近24時間に固定
    now = time.time()
    t1 = now
    t0 = now - 24 * 3600
    points = [p for p in points if p[0] >= t0]
    y_max = max([n for _, n in points], default=0)
    y_max = max(5, y_max + 1)

    def px(t: float) -> float:
        return PAD_L + (t - t0) / (t1 - t0) * plot_w

    def py(n: float) -> float:
        return PAD_T + plot_h - n / y_max * plot_h

    # 横グリッドと人数ラベル
    step = max(1, y_max // 5)
    v = 0
    while v <= y_max:
        d.line([(PAD_L, py(v)), (W - PAD_R, py(v))], fill=GRID, width=1)
        d.text((10, py(v) - 8), str(v), fill=TEXT, font=font)
        v += step
    # 時刻ラベル (6分割)
    for i in range(7):
        t = t0 + (t1 - t0) * i / 6
        label = time.strftime("%H:%M", time.localtime(t))
        d.text((min(px(t) - 18, W - 44), H - 26), label, fill=TEXT, font=font)

    if len(points) >= 2:
        xy = [(px(t), py(n)) for t, n in points]
        d.polygon(xy + [(xy[-1][0], py(0)), (xy[0][0], py(0))], fill=FILL)
        d.line(xy, fill=LINE, width=3)
        lx, ly = xy[-1]
        d.ellipse([lx - 5, ly - 5, lx + 5, ly + 5], fill=LINE)
    d.rectangle([0, 0, W - 1, H - 1], outline=GRID, width=2)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
