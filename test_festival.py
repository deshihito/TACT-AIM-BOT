"""フェス判定のテスト。実行: python test_festival.py

期待値は原作 Title script6 の式 (UTC 基準の daysSince2000 + 0.375、
9820 < d < 9852、奇数がフェス) から JST の暦日で導いたもの。
フェス日は 2026-11-21 から隔日で 12-21 まで、境界は JST 00:00。
"""
from datetime import datetime, timedelta, timezone

from festival import festival_day_number, is_festival

JST = timezone(timedelta(hours=9))


def ts(y, m, d, hh=12, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=JST).timestamp()


# 暦日ごとの期待値 (正午で判定)
expected = {
    (2026, 11, 20): False,  # 窓の前 (d=9820)
    (2026, 11, 21): True,   # 初日 (d=9821)
    (2026, 11, 22): False,
    (2026, 11, 27): True,
    (2026, 11, 28): False,  # 2026-11-28 の実害ケース
    (2026, 11, 29): True,
    (2026, 12, 21): True,   # 最終日 (d=9851)
    (2026, 12, 22): False,  # 窓の後 (d=9852)
}
for (y, m, d), want in expected.items():
    got = is_festival(ts(y, m, d))
    assert got == want, f"{y}-{m}-{d}: got {got}, want {want}"

# 境界は JST 00:00 (15:00 で切り替わらないこと)
assert not is_festival(ts(2026, 11, 28, 15, 3))
assert not is_festival(ts(2026, 11, 28, 23, 59))
assert is_festival(ts(2026, 11, 29, 0, 0))
assert is_festival(ts(2026, 11, 29, 23, 59))
assert not is_festival(ts(2026, 11, 30, 0, 0))

# 日番号の実測アンカー
assert festival_day_number(ts(2026, 11, 28)) == 9828
assert festival_day_number(ts(2026, 11, 21)) == 9821
assert festival_day_number(ts(2026, 12, 21)) == 9851

print("all tests passed")
