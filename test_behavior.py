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
    assert set(a["power"]) == {"エイム", "火力", "キル効率", "生存", "立ち回り", "積極性"}


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
    assert a["hits"] == 1  # record_damage は命中1件ごとに呼ばれる
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
    # linked3 の火力: 生指標は 1050/0.25=4200/時。絶対 (飽和1500) = 73.7、
    # mid-rank は (3 + 0.5) / 8 = 43.75、母数8の重み 8/12 で合成
    linked = s.analyze("linked3", pop)["power"]["火力"]
    assert abs(linked - (8 / 12 * 43.75 + 4 / 12 * 4200 / 5700 * 100)) < 0.1
    # 母数に巨大ダメージの部外者が入ると相対順位が下がる
    assert s.analyze("linked3")["power"]["火力"] < linked


def test_tied_zero_scores_not_inflated(tmp_path):
    """全員 0 ダメ同点でも火力/エイムが上位に張り付かない (旧 <= 方式の回帰)。"""
    s = BehaviorStore(tmp_path / "b.json")
    for j in range(8):
        for _ in range(30):
            _sample(s, name=f"p{j}")
    a = s.analyze("p0", {f"p{j}" for j in range(8)})
    # mid-rank は同点全員 50、絶対基準は 0 → 合成は 50 * 8/12 = 33.3
    assert a["power"]["火力"] < 40
    assert a["power"]["エイム"] < 40


def test_power_spread_by_playstyle(tmp_path):
    """プレイスタイルが違えばスコアが分かれる (全員同じ感じの回帰防止)。"""
    s = BehaviorStore(tmp_path / "b.json")
    # ロビー待機: 30回、移動なし・戦績なし
    for i in range(30):
        _sample(s, name="Idle", x=100, hp_ratio=1.0, now=1000.0 + i * 10)
    #  roaming 戦闘: 30回、毎回大きく移動しながら別セルを踏む
    for i in range(30):
        _sample(
            s, name="Roamer", x=600 + (i % 10) * 600, y=-1000 + (i // 10) * 900,
            hp_ratio=0.6, now=1000.0 + i * 10,
        )
    for _ in range(40):
        s.record_damage("Roamer", 50, now=2000.0)
    for _ in range(10):
        s.record_kill("Roamer", "Idle", now=2000.0)
    idle = s.analyze("Idle")
    roamer = s.analyze("Roamer")
    assert roamer["power_score"] > idle["power_score"] + 20
    assert roamer["power"]["立ち回り"] > idle["power"]["立ち回り"] + 30
    assert roamer["power"]["エイム"] > idle["power"]["エイム"]
    assert roamer["power"]["キル効率"] > idle["power"]["キル効率"]
    # 待機組の生存は満点に張り付かない (同点 mid-rank + HP のみ)
    assert idle["power"]["生存"] < 100


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
