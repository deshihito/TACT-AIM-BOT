"""behavior.py (行動データ収集と分析) の検証。"""
from __future__ import annotations

from behavior import BehaviorStore


def _sample(s: BehaviorStore, name="Hero", **kw):
    args = dict(
        char="Cat",
        weapon="Rifle",
        team_color=1,
        special=False,
        x=2000,
        y=0,
        hp_ratio=0.8,
        hour=20,
        now=1000.0,
    )
    args.update(kw)
    s.sample(name, **args)


def test_sample_and_analyze(tmp_path):
    s = BehaviorStore(tmp_path / "b.json")
    for _ in range(30):
        _sample(s)
    for _ in range(10):
        _sample(s, char="Gobo", weapon="Shotgun", x=100, hour=21)
    a = s.analyze("hero")  # 大小無視で引ける
    assert a is not None
    assert a["samples"] == 40
    assert a["top_chars"][0] == ("Cat", 75.0)
    assert a["top_weapons"][0][0] == "Rifle"
    assert a["top_combos"][0][0] == ("Cat", "Rifle")
    assert a["lobby_pct"] == 25.0
    assert a["peak_hour"] == 20
    assert a["heat_max"] == 30
    assert a["top_zones"]  # バトルエリアのゾーン集計がある
    assert 0 <= a["power_score"] <= 100
    assert set(a["power"]) == {"火力", "撃破", "生存", "積極性", "活動量"}


def test_kill_damage_events(tmp_path):
    s = BehaviorStore(tmp_path / "b.json")
    _sample(s, name="Hero")
    s.record_kill("Hero", "Foe", now=1001.0)
    s.record_kill("Hero", "Foe", now=1002.0)
    s.record_kill("Foe", "Hero", now=1003.0)
    s.record_damage("Hero", 120, now=1004.0)
    a = s.analyze("Hero")
    assert (a["kills"], a["deaths"]) == (2, 1)
    assert a["kd"] == 2.0
    assert a["damage"] == 120
    assert s.analyze("Foe")["deaths"] == 2


def test_percentile_population(tmp_path):
    s = BehaviorStore(tmp_path / "b.json")
    for j in range(8):
        for _ in range(30):
            _sample(s, name=f"linked{j}")
        s.record_damage(f"linked{j}", j * 350, now=1001.0)
    for _ in range(30):
        _sample(s, name="Outsider")
    s.record_damage("Outsider", 10**7, now=1001.0)
    pop = {f"linked{j}" for j in range(8)}
    # 母数を連携8人に限ると linked3 は 4/8 = 50
    assert s.analyze("linked3", pop)["power"]["火力"] == 50.0
    # 母数を全員 (9人) にすると巨大ダメージの部外者が入って 4/9 になる
    assert abs(s.analyze("linked3")["power"]["火力"] - 400 / 9) < 0.01


def test_unknown_player(tmp_path):
    s = BehaviorStore(tmp_path / "b.json")
    assert s.analyze("nobody") is None
    assert s.get("") is None


def test_persistence(tmp_path):
    path = tmp_path / "b.json"
    s = BehaviorStore(path)
    _sample(s)
    s.record_kill("Hero", None, now=1001.0)
    s.flush()
    s2 = BehaviorStore(path)
    assert s2.get("Hero")["samples"] == 1
    assert s2.get("Hero")["kills"] == 1
