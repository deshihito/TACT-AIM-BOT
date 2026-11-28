"""map_data の日替わりマップ判定の検証。

2026-11-28 17:43 JST の実地データで固定する: 本家はマップ1で対戦中で、
実プレイヤー11人の座標はマップ1の壁タイルとだけ矛盾しなかった
(マップ2では2人、マップ3では3人が壁の中)。当時のローカル時刻起点の
計算はマップ2を返していた。
"""
from __future__ import annotations

from datetime import datetime, timezone

from map_data import current_map_number

# 2026-11-28 17:43 JST = 08:43 UTC
T_LIVE = datetime(2026, 11, 28, 8, 43, tzinfo=timezone.utc).timestamp()


def test_live_moment_is_map1():
    assert current_map_number("831274817", now=T_LIVE) == "1"


def test_unknown_project():
    assert current_map_number("999999999", now=T_LIVE) is None
