"""record.py のテスト。実クラウドで採取した実レコードで検証する。

実行: python -m pytest test_record.py  (または python test_record.py)
"""
from record import BLOOD_CHARACTERS, parse_record

# TACT-AIM blood (project 1297721083) の実レコード (2026-11-27 採取、247文字)
BLOOD_SAMPLES = [
    # (record, player_id, character, hp, max_hp)
    ("1832192301500050005715214796035045305391832100000000000000000000000000000000000000000000000000000000000000000012110200000000000000000000000000000000000000000000000000000000021302130642000010014069230000000000250000005005005001002050500000550000005",
     "1832", "Giga", 130, 130),
    ("9742190501754952845895305015895205005899742100000000001050500000000000000000000000000000000000000000000000000030010500000000000000000000000000000000000000000000000000000000021402140681950111224874120000000000101006005005005001001150500000550045540",
     "9742", "Combat Nano", 140, 140),
    ("1761625131687053224575075298464704957891761100000000000000000000000000000000000000000000000000000000000000000010010100000000000000000000000000000000000000000000000000000000021502150062250010016500000000000000131006005005005001002050500000550011351",
     "1761", "Cat", 150, 150),
]


def test_blood_records():
    for raw, pid, char, hp, max_hp in BLOOD_SAMPLES:
        assert len(raw) == 247
        rec = parse_record(raw)
        assert rec is not None, raw[:20]
        assert rec.player_id == pid
        assert rec.character == char, (rec.character, char)
        assert (rec.hp, rec.max_hp) == (hp, max_hp)
        # debuff旗は f61 の直後 (raw[241])、名前チャンクはその後ろ raw[242:247]
        assert rec.blood_flag == "0"
        assert rec.name_chunk == {"1832": "==5", "9742": "IR0", "1761": "ay1"}[pid]


def test_blood_roster_extension():
    assert len(BLOOD_CHARACTERS) == 41
    assert BLOOD_CHARACTERS[23] == "Blood cat"
    assert BLOOD_CHARACTERS[38] == "Chaos Cat"


def test_reject_garbage():
    assert parse_record("") is None
    assert parse_record("123") is None
    assert parse_record("a" * 246) is None
    assert parse_record("1" * 245) is None
    assert parse_record("1" * 248) is None


if __name__ == "__main__":
    test_blood_records()
    test_blood_roster_extension()
    test_reject_garbage()
    print("all tests passed")
