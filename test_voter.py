"""voter.py と通報票集計 (cloud.report_votes) のテスト。

実行: python test_voter.py (または python -m pytest test_voter.py)
"""
import time

from cloud import TactAimCloud
from record import decode_name_chunk, encode_name_chunk, parse_record
from voter import VOTER_USERNAME, build_record, votes_needed


def test_build_record_main():
    raw = build_record("7001", 12345, 5, False, "21", "8001",
                       encode_name_chunk(VOTER_USERNAME, 3))
    assert len(raw) == 246
    rec = parse_record(raw)
    assert rec is not None
    assert rec.player_id == "7001"
    assert rec.seq == 12345
    assert rec.version == 5
    assert (rec.hp, rec.max_hp) == (140, 140)
    assert rec.character == "Cat"
    assert rec.skill_type == "21"
    assert rec.skill_value == "8001"
    assert rec.team_color == 0
    assert not rec.special_weapon


def test_build_record_blood():
    raw = build_record("7001", 12345, 1, True, "21", "8001",
                       encode_name_chunk(VOTER_USERNAME, 0))
    assert len(raw) == 247
    rec = parse_record(raw)
    assert rec is not None
    assert rec.version == 1
    assert rec.blood_flag == "0"
    assert rec.skill_type == "21"


def test_name_chunk_round_trip():
    # 全位置のチャンクを集めるとユーザー名が復元できる (監視側の復元と同じ手順)
    chars: dict[int, str] = {}
    for pos in range(10):
        chunk = encode_name_chunk(VOTER_USERNAME, pos)
        assert len(chunk) == 5
        two, idx = decode_name_chunk(chunk)
        assert idx == pos
        chars[pos] = two
    name = "".join(chars[i] for i in range(10)).rstrip("=")
    assert name == VOTER_USERNAME


def test_votes_needed():
    assert votes_needed(0) is None
    assert votes_needed(5) is None
    assert votes_needed(6) == 2
    assert votes_needed(9) == 3
    assert votes_needed(12) == 4
    assert votes_needed(15) == 5
    assert votes_needed(20) == 7
    assert votes_needed(30) == 10


def _feed(cloud: TactAimCloud, voter_id: str, seq: int, target: str | None):
    skill = ("21", target) if target else ("22", "1000")
    raw = build_record(voter_id, seq, 5, False, skill[0], skill[1],
                       encode_name_chunk(VOTER_USERNAME, 0))
    cloud._handle_set("☁ CLOUD1", raw)


def test_report_tally():
    cloud = TactAimCloud()
    _feed(cloud, "7001", 100, "8001")
    _feed(cloud, "7001", 101, "8001")  # 同じ投票者の再送は1票のまま
    assert cloud.report_votes("8001") == 1
    _feed(cloud, "7002", 102, "8001")
    assert cloud.report_votes("8001") == 2
    _feed(cloud, "7003", 103, None)  # kind22 は票ではない
    assert cloud.report_votes("8001") == 2
    _feed(cloud, "8001", 104, "8001")  # 自分への通報は数えない
    assert cloud.report_votes("8001") == 2
    _feed(cloud, "7004", 105, "9002")  # 別の対象への票は混ざらない
    assert cloud.report_votes("8001") == 2
    assert cloud.report_votes("9002") == 1
    # 窓 (300秒) を過ぎた票は失効する
    for voters in cloud._reports.values():
        for vid in voters:
            voters[vid] = time.monotonic() - 301
    assert cloud.report_votes("8001") == 0
    assert cloud.report_votes("9002") == 0


if __name__ == "__main__":
    test_build_record_main()
    test_build_record_blood()
    test_name_chunk_round_trip()
    test_votes_needed()
    test_report_tally()
    print("all tests passed")
