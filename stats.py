"""セッション統計: レコードの被弾申告の差分から戦績を集計する。

キル認定は被害者側レコードの被弾イベント (kind "00") が根拠になるため、
被害者レコードが新しい seq で更新された時に「増えた被弾イベント」だけを
数えることで、キル/デス/与ダメ/被ダメを二重計上なく積み上げられる。
Bot 起動 (または対象切替) からの累計で、永続化はしない。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from record import HitEvent, PlayerRecord


@dataclass
class PlayerStats:
    kills: int = 0
    deaths: int = 0
    damage_dealt: int = 0
    damage_taken: int = 0
    hits_landed: int = 0  # 相手に当てた回数
    hits_taken: int = 0  # 被弾回数

    @property
    def kd(self) -> float:
        return self.kills / self.deaths if self.deaths else float(self.kills)


class StatsTracker:
    def __init__(self):
        self.by_player: dict[str, PlayerStats] = {}

    def reset(self) -> None:
        self.by_player.clear()

    def get(self, player_id: str) -> PlayerStats:
        return self.by_player.setdefault(player_id, PlayerStats())

    def observe(self, old: PlayerRecord | None, new: PlayerRecord) -> None:
        """被害者レコードの更新を観測する。old=None (初回) は基準化のみ。"""
        if old is None:
            self.get(new.player_id)
            return
        old_hits = Counter((h.attacker_id, h.damage, h.kind) for h in old.hits)
        victim = self.get(new.player_id)
        for h in new.hits:
            key = (h.attacker_id, h.damage, h.kind)
            if old_hits.get(key, 0) > 0:
                old_hits[key] -= 1
                continue
            self._count_hit(victim, h)

    def _count_hit(self, victim: PlayerStats, h: HitEvent) -> None:
        attacker = self.get(h.attacker_id)
        attacker.damage_dealt += h.damage
        attacker.hits_landed += 1
        victim.damage_taken += h.damage
        victim.hits_taken += 1
        if h.is_kill:
            attacker.kills += 1
            victim.deaths += 1
