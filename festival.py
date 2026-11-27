"""フェス期間の判定 (原作 Title script6 の式を再現)。

原作 16.9: floor(daysSince2000 + 0.375) が 9821..9851 の期間だけ
Festival フラグが立ち、その中では日数の偶奇で 0/1 が毎日入れ替わる
(2日で1周期)。期間外はフラグ 0 = 通常。境界はローカル時刻 15:00。
窓の数値は原作の版に紐づく定数で、原作が更新されれば変わりうる。
"""
from __future__ import annotations

import math
import time
from datetime import datetime

WINDOW_MIN = 9820  # この値より大きい日が対象
WINDOW_MAX = 9852  # この値より小さい日が対象


def festival_day_number(now: float | None = None) -> int:
    now = now if now is not None else time.time()
    days = (datetime.fromtimestamp(now) - datetime(2000, 1, 1)).total_seconds() / 86400
    return math.floor(days + 0.375)


def is_festival(now: float | None = None) -> bool:
    d = festival_day_number(now)
    return WINDOW_MIN < d < WINDOW_MAX and d % 2 == 1
