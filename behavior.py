"""プレイヤーの行動データ収集と分析 (behavior.json に永続化)。

連携ユーザーに限らず、オンライン中の全プレイヤーを名前単位で
サンプリングし、/analyze の元データにする。プレイヤーIDは接続ごとに
変わるため、同一性はプレイヤー名 (連携と同じ前提) で持つ。

収集するもの (サンプリング間隔 SAMPLE_SECONDS 秒):
- 使っているキャラ / 武器 / その組み合わせ (編成)
- チームカラー、スペシャルウェポン所持、HP比率
- 位置 (200ユニット単位のヒートグリッド、ロビー滞在)
- 活動時間帯 (JST の時刻別サンプル数)
イベント: キル/デス (record_kill)、与ダメージ (record_damage)
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
            self._get(attacker, now)["damage"] += damage

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

    def _percentile(self, metric: str, value: float) -> float:
        """全プレイヤー (30サンプル以上) の中での順位を 0..100 で返す。"""
        pool = [
            _metrics(p)[metric]
            for p in self.players.values()
            if p["samples"] >= 30
        ]
        if len(pool) < 8:
            return -1.0  # 母数が少なく算出不能
        below = sum(1 for v in pool if v <= value)
        return below / len(pool) * 100

    def analyze(self, name: str) -> dict | None:
        p = self.get(name)
        if p is None:
            return None
        samples = p["samples"]
        denom = max(samples, 1)
        hours_played = samples * SAMPLE_SECONDS / 3600
        m = _metrics(p)

        def axis(metric: str, fixed_cap: float) -> float:
            pct = self._percentile(metric, m[metric])
            if pct >= 0:
                return pct
            return min(100.0, m[metric] / fixed_cap * 100) if fixed_cap > 0 else 0.0

        power = {
            "火力": axis("dmg_per_hour", 4000),
            "撃破": axis("kills_per_hour", 30),
            "生存": axis("survival", 1.0),
            "積極性": axis("engagements_per_hour", 45),
            "活動量": axis("play_hours", 20),
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
    """戦闘力の各軸の生指標。"""
    hours = max(p["samples"] * SAMPLE_SECONDS / 3600, 1e-9)
    deaths_per_hour = p["deaths"] / hours
    return {
        "dmg_per_hour": p["damage"] / hours,
        "kills_per_hour": p["kills"] / hours,
        # 生存: デス頻度が低いほど高く、平均HP比率も加味
        "survival": max(0.0, 1.0 - deaths_per_hour / 30) * 0.6
        + (p["hp_sum"] / p["samples"] if p["samples"] else 0) * 0.4,
        "engagements_per_hour": (p["kills"] + p["deaths"]) / hours,
        "play_hours": hours,
    }
