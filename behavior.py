"""プレイヤーの行動データ収集と分析 (behavior.json に永続化)。

収集と分析の表示は全プレイヤーが対象。ただし戦闘力の相対評価
(パーセンタイル) の母数は連携プレイヤーのみ (ランキング系は
連携限定という方針。呼び出し側が連携名の集合を渡す)。
名前単位でサンプリングし、/analyze の元データにする。プレイヤーIDは
接続ごとに変わるため、同一性はプレイヤー名 (連携と同じ前提) で持つ。

収集するもの (サンプリング間隔 SAMPLE_SECONDS 秒):
- 使っているキャラ / 武器 / その組み合わせ (編成)
- チームカラー、スペシャルウェポン所持、HP比率
- 位置 (200ユニット単位のヒートグリッド、ロビー滞在、移動量)
- 活動時間帯 (JST の時刻別サンプル数)
イベント: キル/デス (record_kill)、与ダメージ=命中1件 (record_damage)

戦闘力は6軸 (エイム/火力/キル効率/生存/立ち回り/積極性)。旧軸 (撃破/活動量) は
全員が似た値に潰れる問題があった: パーセンタイルが同点全員を上位に
数える (<= 比較) ため 0 キル同士が揃って高スコアになり、母数が少ない
ときの固定基準フォールバックでは「0/0/生存100/0/活動ほぼ0」で
全員ほぼ同点になった。新方式は同点を中位に数える mid-rank、絶対基準
(飽和曲線) と相対評価の母数サイズに応じたブレンド、時間あたり指標の
分母フロア (0.25時間) で潰れと暴れの両方を抑える。
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

SAMPLE_SECONDS = 10

# ヒートグリッド: x∈[-500, 6900), y∈[-1500, 1500) を 200 単位で割る
HEAT_CELL = 200
HEAT_X0, HEAT_Y0 = -500, -1500
HEAT_W, HEAT_H = 37, 15

LOBBY_X = 450  # これ未満はロビー側 (原作のチーム選択域)

TEAM_COLOR_LABELS = {
    0: "未所属", 1: "赤", 2: "オレンジ", 3: "黄", 4: "緑",
    5: "水色", 6: "青", 7: "紫", 8: "ピンク",
}

# ゾーン表示 (バトルエリアを 3x3 に割った呼び名)
ZONE_ROWS = ["上", "中", "下"]
ZONE_COLS = ["左", "中央", "右"]


def _new_player(name: str, now: float) -> dict:
    return {
        "name": name,
        "samples": 0,
        "first": now,
        "last": now,
        "chars": {},
        "weapons": {},
        "combos": {},
        "teams": {},
        "special": 0,
        "lobby": 0,
        "hp_sum": 0.0,
        "heat": {},
        "hours": {},
        "kills": 0,
        "deaths": 0,
        "damage": 0,
        "hits": 0,
        "move_sum": 0.0,
        "move_n": 0,
        "last_x": None,
        "last_y": None,
        "last_sample": None,
    }


def _bump(d: dict, key: str, n: int = 1) -> None:
    d[key] = d.get(key, 0) + n


class BehaviorStore:
    def __init__(self, path: Path):
        self.path = path
        self.players: dict[str, dict] = {}
        self._dirty = 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.players = {str(k): dict(v) for k, v in data.get("players", {}).items()}
        except Exception:
            self.players = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"players": self.players}), encoding="utf-8"
            )
        except Exception:
            pass

    def _get(self, name: str, now: float) -> dict:
        key = name.lower()
        p = self.players.get(key)
        if p is None:
            p = _new_player(name, now)
            self.players[key] = p
        p["name"] = name
        p["last"] = max(p["last"], now)
        return p

    # --- 収集 ---

    def sample(
        self,
        name: str,
        *,
        char: str,
        weapon: str | None,
        team_color: int,
        special: bool,
        x: int,
        y: int,
        hp_ratio: float,
        hour: int,
        now: float,
    ) -> None:
        p = self._get(name, now)
        p["samples"] += 1
        # 移動量 (立ち回り軸): 前回サンプルから 30 秒以内なら距離を加算。
        # セッションをまたぐ跳躍やテレポート級の跳びは 2500 で頭打ちにする。
        last_x, last_y, last_t = p.get("last_x"), p.get("last_y"), p.get("last_sample")
        if last_x is not None and last_t is not None and 0 <= now - last_t <= 30:
            dist = ((x - last_x) ** 2 + (y - last_y) ** 2) ** 0.5
            p["move_sum"] = p.get("move_sum", 0.0) + min(dist, 2500.0)
            p["move_n"] = p.get("move_n", 0) + 1
        p["last_x"], p["last_y"], p["last_sample"] = x, y, now
        if char:
            _bump(p["chars"], char)
        if weapon:
            _bump(p["weapons"], weapon)
            if char:
                _bump(p["combos"], f"{char}|{weapon}")
        _bump(p["teams"], str(team_color))
        if special:
            p["special"] += 1
        if x < LOBBY_X:
            p["lobby"] += 1
        p["hp_sum"] += hp_ratio
        gx = (x - HEAT_X0) // HEAT_CELL
        gy = (y - HEAT_Y0) // HEAT_CELL
        if 0 <= gx < HEAT_W and 0 <= gy < HEAT_H:
            _bump(p["heat"], f"{gx},{gy}")
        _bump(p["hours"], str(hour))
        self._dirty += 1
        if self._dirty >= 30:
            self._dirty = 0
            self._save()

    def record_kill(self, killer: str | None, victim: str | None, now: float) -> None:
        if killer:
            self._get(killer, now)["kills"] += 1
        if victim:
            self._get(victim, now)["deaths"] += 1

    def record_damage(self, attacker: str | None, damage: int, now: float) -> None:
        if attacker and damage > 0:
            p = self._get(attacker, now)
            p["damage"] += damage
            p["hits"] = p.get("hits", 0) + 1  # damage_log は命中1件ごとに来る

    def flush(self) -> None:
        if self._dirty:
            self._dirty = 0
            self._save()

    # --- 分析 ---

    def get(self, name: str) -> dict | None:
        if not name:
            return None
        return self.players.get(name.lower())

    @staticmethod
    def _top(counter: dict, n: int, total: int) -> list[tuple[str, float]]:
        items = sorted(counter.items(), key=lambda kv: -kv[1])[:n]
        return [(k, (v / total * 100 if total else 0.0)) for k, v in items]

    def _pool(
        self, metric: str, population: set[str] | None
    ) -> list[float]:
        """母数 (30サンプル以上) の生指標リスト。

        population は母数に含めるプレイヤー名 (小文字) の集合で、
        None なら全プレイヤー。戦闘力の相対評価は連携プレイヤーのみを
        母数にする想定で、呼び出し側が連携名の集合を渡す。
        """
        return [
            _metrics(p)[metric]
            for key, p in self.players.items()
            if p["samples"] >= 30 and (population is None or key in population)
        ]

    @staticmethod
    def _score(value: float, pool: list[float], *, sat: float = 0.0) -> float:
        """生指標 → 0..100。絶対基準と相対評価 (mid-rank) のブレンド。

        - 絶対基準: sat > 0 なら飽和曲線 100*v/(v+sat) (v=sat で 50)。
          sat == 0 なら value は 0..1 正規化済みとして 100 倍する。
        - 相対評価: 同点は中位で数える ((未満) + 0.5*(同点)) / 母数。
          旧方式の <= 比較は同点全員を最上位にし、全員同スコアの主因だった。
        - 母数が 4 未満なら絶対基準のみ、多いほど相対の重みが上がる
          (母数 12 で相対 100%)。旧方式の「母数不足→固定基準に全切替」
          のような段差を作らない。
        """
        absolute = 100.0 * value / (value + sat) if sat > 0 and value > 0 else (
            min(100.0, max(0.0, value * 100)) if sat <= 0 else 0.0
        )
        if len(pool) < 4:
            return absolute
        less = sum(1 for v in pool if v < value)
        equal = sum(1 for v in pool if v == value)
        pct = (less + 0.5 * equal) / len(pool) * 100
        w = min(1.0, len(pool) / 12)
        return w * pct + (1 - w) * absolute

    def analyze(self, name: str, population: set[str] | None = None) -> dict | None:
        p = self.get(name)
        if p is None:
            return None
        samples = p["samples"]
        denom = max(samples, 1)
        hours_played = samples * SAMPLE_SECONDS / 3600
        m = _metrics(p)

        def axis(metric: str, sat: float = 0.0) -> float:
            return self._score(m[metric], self._pool(metric, population), sat=sat)

        power = {
            "エイム": axis("aim", sat=250),
            "火力": axis("dmg_per_hour", sat=1500),
            "キル効率": axis("kill_efficiency", sat=3.0),
            "生存": axis("survival"),
            "立ち回り": axis("positioning"),
            "積極性": axis("engagements_per_hour", sat=25),
        }
        score = sum(power.values()) / len(power)

        # ゾーン (バトルエリアのみ、3x3)
        zones: Counter = Counter()
        for key, n in p["heat"].items():
            gx, gy = (int(v) for v in key.split(","))
            wx = HEAT_X0 + gx * HEAT_CELL
            wy = HEAT_Y0 + gy * HEAT_CELL
            if wx < LOBBY_X:
                continue
            col = min(2, max(0, (wx - LOBBY_X) * 3 // 6000))
            row = min(2, max(0, (wy + 1500) * 3 // 3000))
            zones[f"{ZONE_COLS[col]}{ZONE_ROWS[row]}"] += n
        battle_samples = sum(zones.values())

        hours = [p["hours"].get(str(h), 0) for h in range(24)]
        peak_hour = max(range(24), key=lambda h: hours[h]) if any(hours) else None

        return {
            "name": p["name"],
            "samples": samples,
            "play_hours": hours_played,
            "first": p["first"],
            "last": p["last"],
            "kills": p["kills"],
            "deaths": p["deaths"],
            "hits": p.get("hits", 0),
            "kd": (p["kills"] / p["deaths"]) if p["deaths"] else float(p["kills"]),
            "damage": p["damage"],
            "top_chars": self._top(p["chars"], 3, denom),
            "top_weapons": self._top(p["weapons"], 3, denom),
            "top_combos": [
                (tuple(k.split("|", 1)), pct)
                for k, pct in self._top(p["combos"], 3, denom)
            ],
            "top_teams": [
                (TEAM_COLOR_LABELS.get(int(k), k), pct)
                for k, pct in self._top(p["teams"], 3, denom)
            ],
            "special_pct": p["special"] / denom * 100,
            "lobby_pct": p["lobby"] / denom * 100,
            "avg_hp_pct": p["hp_sum"] / denom * 100,
            "top_zones": self._top(dict(zones), 3, battle_samples) if battle_samples else [],
            "hours": hours,
            "peak_hour": peak_hour,
            "heat": {tuple(int(v) for v in k.split(",")): n for k, n in p["heat"].items()},
            "heat_max": max(p["heat"].values(), default=0),
            "power": power,
            "power_score": score,
        }


def _metrics(p: dict) -> dict[str, float]:
    """戦闘力6軸の生指標。

    時間あたり指標の分母は 0.25 時間でフロアする (30サンプル=5分で
    1キルしただけで毎時換算が跳ねて全員飽和するのを防ぐ)。
    hits は新設フィールドなので、旧データ (hits 無し・damage 有り) は
    平均ヒットダメ 30 として推定する。
    """
    samples = p["samples"]
    hours = samples * SAMPLE_SECONDS / 3600
    denom = max(hours, 0.25)
    damage = p["damage"]
    hits = p.get("hits", 0)
    if not hits and damage:
        hits = max(1, round(damage / 30))
    deaths_per_hour = p["deaths"] / denom
    # キル効率: 与ダメあたりのキル (少ないダメで仕留めるほど高い)。
    # 分母に +400 の平滑化を入れ、1キルの偶然で跳ねないようにする。
    # K/D 寄りの係数 (キル占有率) で最大 ±40% 補正する。
    kd_factor = 0.6
    if p["kills"] + p["deaths"]:
        kd_factor += 0.4 * p["kills"] / (p["kills"] + p["deaths"])
    # 立ち回り (0..1): 戦闘エリア滞在・移動量・訪れたセル数の広さ
    battle_ratio = 1.0 - p["lobby"] / samples if samples else 0.0
    move_avg = p.get("move_sum", 0.0) / p["move_n"] if p.get("move_n") else 0.0
    positioning = (
        0.4 * battle_ratio
        + 0.3 * min(len(p["heat"]) / 25, 1.0)
        + 0.3 * min(move_avg / 900, 1.0)
    )
    return {
        # エイム: 命中頻度 × 1発の重さ (damage と hits の幾何平均 / 時)
        "aim": (damage * hits) ** 0.5 / denom if hits else 0.0,
        "dmg_per_hour": damage / denom,
        "kill_efficiency": p["kills"] / (damage + 400) * 1000 * kd_factor,
        # 生存: デス頻度が低いほど高く、平均HP比率も加味。ただし
        # ロビーに居るだけの「死ななさ」は割り引く (戦闘滞在率で補正)
        "survival": (
            max(0.0, 1.0 - deaths_per_hour / 30) * 0.6
            + (p["hp_sum"] / samples if samples else 0) * 0.4
        )
        * (0.4 + 0.6 * battle_ratio),
        "positioning": positioning,
        # 積極性: 交戦 (キル/デス) に命中関与も少し混ぜる
        "engagements_per_hour": (p["kills"] + p["deaths"] + hits * 0.2) / denom,
        "play_hours": hours,
    }
