"""実マップのタイルデータ (maps.json) と日替わりマップの判定。

タイルは原作 project.json から抽出した [gx, gy, type] の一覧で、
ワールド座標は grid * 50。日替わり判定は原作 Map の式そのまま:
rand 表を (2000-01-01 からの日数 + 0.2666) で引き、3 が出たら 1/2 に引き直す。
チーム戦 (マップ3固定) かどうかはクラウドから分からないため通常戦前提。
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path

_DATA = json.loads(Path(__file__).with_name("maps.json").read_text(encoding="utf-8"))


def current_map_number(project_id: str, now: float | None = None) -> str | None:
    entry = _DATA.get(project_id)
    if entry is None:
        return None
    rotation = entry.get("rotation")
    if not rotation:
        return "1"
    now = now if now is not None else time.time()
    days = (datetime.fromtimestamp(now) - datetime(2000, 1, 1)).total_seconds() / 86400
    idx = math.floor(days + 0.2666)
    m = str(rotation[idx % len(rotation)])
    if m == "3":
        m = str(idx % 2 + 1)
    return m


def tiles_for(project_id: str, now: float | None = None) -> list | None:
    """base + 当日のマップのタイル一覧。未知のプロジェクトは None。"""
    entry = _DATA.get(project_id)
    if entry is None:
        return None
    m = current_map_number(project_id, now)
    return entry["base"] + entry["maps"].get(m, [])
