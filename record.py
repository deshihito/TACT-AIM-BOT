"""TACT-AIM クラウドレコードのパーサ。

ワイヤ形式は TACT-AIM-distilled の src/distilled/Pen_All/cloud.ts
(Load cloud data 手順) で確定した固定長 246 文字の数字文字列。
先頭から順に固定幅で切る。意味のあるフィールドだけ名前を付け、
残りは raw のまま保持する (テスト段階なので推測で意味を付けない)。

派生版 "TACT-AIM blood" (project 1297721083、フェス登録先) は
63フィールド目 'Blood Pico debuff?' (1文字) が末尾に追加され 247 文字。
先頭246文字の配置は本家と同一で、キャラ名簿だけ41種に拡張されている
(本家の project.json の Cloud memo / Character リストで確認済み)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

RECORD_LEN = 246
BLOOD_RECORD_LEN = 247  # blood版: 末尾に 'Blood Pico debuff?' (1文字)

# キャラ名簿: Character リスト (15 幅レコード) の先頭フィールド、出現順。
CHARACTERS = [
    "Cat", "Gobo", "Giga", "Nano", "Pico", "Tera", "Spiky Gobo", "Bio Tera",
    "Dark Nano", "Cat Bot", "Cyborg Pico", "Lava Gobo", "Golden Bot",
    "Ninja Giga", "Lightning Giga", "Pumpkin", "Shield Pico", "Sacred Tera",
    "Snowman", "Military Pico", "Combat Nano", "April Cat", "The Player",
]

# blood版の追加キャラ (index 23..40、本家 project.json の Character リスト順)
BLOOD_CHARACTERS = CHARACTERS + [
    "Blood cat", "Shark Gobo", "Alien", "Vampire Cat", "Blood Gobo",
    "Cosmos Cat", "Blood Giga", "Actress Giga", "Blood Nano", "Phantom Pico",
    "Burning Tera", "Blood Pico", "Knight Nano", "Bomber Nano", "Blood Tera",
    "Chaos Cat", "Ghost Cat", "Sanguine Nano",
]

# 名前チャンクの復号表: 2桁コード (11..97) -> 文字。
# Pen/All の Font コスチューム (コード-10 番) の名前 6 文字目と一致する。
_NAME_CHARS = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "-?_!#%&'()\"@[]*+;:,./\\<>="
)


def decode_name_chunk(raw5: str) -> str:
    """末尾5文字 [2桁コード][2桁コード][ literal 1文字] -> 最大3文字。

    名前はチャンク単位で回転送信されるため、これは名前全体ではなく
    その時点のチャンク。コード "00" は詰め物 "="。
    """
    out = []
    for i in (0, 2):
        pair = raw5[i : i + 2]
        if pair == "00":
            out.append("=")
        elif pair.isdigit() and 11 <= int(pair) <= 97:
            out.append(_NAME_CHARS[int(pair) - 11])
        else:
            out.append("?")
    out.append(raw5[4] if len(raw5) > 4 else "")
    return "".join(out)


@dataclass
class HitEvent:
    attacker_id: str
    damage: int
    kind: str  # "00" = キル、それ以外は種別コード (raw)

    @property
    def is_kill(self) -> bool:
        return self.kind == "00"


@dataclass
class PlayerRecord:
    player_id: str
    seq: int
    version: int
    x: int
    y: int
    motion: list[int]
    team_id: str
    shots: list[str]  # 非ゼロの発射イベント (7文字パック) のみ
    character: str
    character_offset: int
    hits: list[HitEvent]  # このプレイヤーが受けた被弾申告 (最大6件)
    hp: int
    max_hp: int
    attack: float  # 攻撃係数 (フィールド24: (値-100)/100)
    chat_code: str
    skill_point: int
    vel_x: int  # 速度フィールド54 (-50 オフセット済み、生値)
    vel_y: int
    name_chunk: str
    raw: str = field(repr=False)
    blood_flag: str = ""  # blood版のみ: 末尾 'Blood Pico debuff?' の生値

    @property
    def active_shots(self) -> int:
        return len(self.shots)


def _int(s: str, default: int = 0) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


class _Cursor:
    def __init__(self, s: str):
        self.s = s
        self.pos = 0

    def take(self, width: int) -> str:
        v = self.s[self.pos : self.pos + width]
        self.pos += width
        return v

    def take_int(self, width: int, offset: int = 0) -> int:
        return _int(self.take(width)) + offset


def parse_record(value: str) -> PlayerRecord | None:
    """246文字 (本家) / 247文字 (blood版) レコードをパースする。

    形式が合わなければ None。
    """
    if not value or len(value) not in (RECORD_LEN, BLOOD_RECORD_LEN) or not value.isdigit():
        return None
    is_blood = len(value) == BLOOD_RECORD_LEN
    roster = BLOOD_CHARACTERS if is_blood else CHARACTERS
    blood_flag = value[RECORD_LEN] if is_blood else ""
    c = _Cursor(value[:RECORD_LEN])
    player_id = c.take(4)
    seq = c.take_int(5)
    version = c.take_int(1)
    x = c.take_int(4, -5000)
    y = c.take_int(4, -5000)
    motion = [c.take_int(3, -500) for _ in range(7)]
    team_id = c.take(4)
    c.take(4)  # f13: Laser sight length (-1000)
    shots = [s for s in (c.take(7) for _ in range(9)) if s != "0000000"]
    char_raw = c.take_int(2)
    character_offset = (char_raw - 10) * 15
    char_idx = character_offset // 15
    character = (
        roster[char_idx] if 0 <= char_idx < len(roster) else f"#{char_raw}"
    )
    c.take(1)  # f16
    c.take_int(3, -100)  # f17
    c.take(1)  # f18
    c.take_int(2, 1)  # f19
    hits = []
    for _ in range(6):
        h = c.take(9)
        if h != "000000000":
            hits.append(
                HitEvent(
                    attacker_id=h[0:4],
                    damage=_int(h[4:7]) - 300,
                    kind=h[7:9],
                )
            )
    hp = c.take_int(4, -2000)
    max_hp = c.take_int(4, -2000)
    c.take(2)  # f23
    attack = (c.take_int(3) - 100) / 100
    for _ in range(5):
        c.take(1)  # f25..f29
    skill_point = c.take_int(3, -100)  # f30
    c.take(4)  # f31
    c.take(7)  # f32
    c.take(1)  # f33
    chat_code = c.take(2)  # f34
    c.take(2)  # スキル節: 種別
    c.take(4)  # スキル節: 値
    c.take(1)  # f44
    c.take(1)  # f45
    # [46] は静的データ経由 (ワイヤ上に独立フィールドなし)
    c.take_int(3, -500)  # f47 ペットX
    c.take_int(3, -500)  # f48 ペットY
    c.take_int(3, -500)  # f49 ペット向き
    c.take_int(3, -100)  # f50 ペットアニメ
    c.take(1)  # f52
    c.take(1)  # f53
    vel_x = c.take_int(2, -50)  # f54
    vel_y = c.take_int(2, -50)  # f55
    c.take(4)  # f56 チーム招待
    c.take_int(3, -550)  # f61
    name_chunk = decode_name_chunk(c.take(5))  # f62 名前チャンク
    return PlayerRecord(
        player_id=player_id,
        seq=seq,
        version=version,
        x=x,
        y=y,
        motion=motion,
        team_id=team_id,
        shots=shots,
        character=character,
        character_offset=character_offset,
        hits=hits,
        hp=hp,
        max_hp=max_hp,
        attack=attack,
        chat_code=chat_code,
        skill_point=skill_point,
        vel_x=vel_x,
        vel_y=vel_y,
        name_chunk=name_chunk,
        blood_flag=blood_flag,
        raw=value,
    )


def seq_is_newer(new: int, old: int) -> bool:
    """seq (World_timer, 90000 ラップ) の鮮度判定。原作 Load cloud data と同じ式。"""
    diff = (new - old + 45000) % 89999 - 45000
    return diff >= 1
