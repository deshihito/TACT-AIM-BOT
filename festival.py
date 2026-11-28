"""フェス期間の判定 (原作 Title script6 の式を再現)。

原作 16.9 (sb3 のブロックで確認): floor(daysSince2000 + 0.375) が
9820 < d < 9852 の期間だけ、その日の偶奇で Festival を切り替える
(d が奇数なら 1、偶数なら 0)。期間外は 0 = 通常。

daysSince2000 は UTC 基準で数える。+0.375 (= 9時間) が JST の
オフセットを相殺するため、日付の境界は JST の 00:00 になる。
ローカル時刻の 2000-01-01 を起点にすると境界が 15:00 JST にずれ、
15:00〜24:00 の間だけ判定が1日進んでしまう (2026-11-28 の実害:
通常日なのにフェス表示になった)。窓の数値は原作の版に紐づく定数で、
原作が更新されれば変わりうる。
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone

WINDOW_MIN = 9820  # この値より大きい日が対象
WINDOW_MAX = 9852  # この値より小さい日が対象

EPOCH_UTC = datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp()


def festival_day_number(now: float | None = None) -> int:
    now = now if now is not None else time.time()
    days = (now - EPOCH_UTC) / 86400
    return math.floor(days + 0.375)


def is_festival(now: float | None = None) -> bool:
    d = festival_day_number(now)
    return WINDOW_MIN < d < WINDOW_MAX and d % 2 == 1
