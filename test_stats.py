"""stats.py と map_render.py のテスト。

実行: python test_stats.py
"""
from record import HitEvent, PlayerRecord
from stats import StatsTracker


def rec(pid, hits):
    return PlayerRecord(
        player_id=pid, seq=1, version=5, x=0, y=0, motion=[0] * 7,
        team_id=pid, shots=[], character="Cat", character_offset=0,
        hits=hits, hp=150, max_hp=150, attack=1.0, chat_code="00",
        skill_point=0, vel_x=0, vel_y=0, name_chars="", name_index=None, raw="",
    )


def test_hit_diff_counting():
    st = StatsTracker()
    v1 = rec("1000", [])
    st.observe(None, v1)  # 初回は基準化のみ
    assert st.get("1000").hits_taken == 0

    hit = HitEvent(attacker_id="2000", damage=30, kind="01")
    v2 = rec("1000", [hit])
    st.observe(v1, v2)
    assert st.get("2000").damage_dealt == 30
    assert st.get("2000").hits_landed == 1
    assert st.get("1000").damage_taken == 30
    assert st.get("1000").deaths == 0

    # 同じレコードの再観測では二重計上しない
    st.observe(v2, v2)
    assert st.get("2000").damage_dealt == 30

    kill = HitEvent(attacker_id="2000", damage=120, kind="00")
    v3 = rec("1000", [hit, kill])
    st.observe(v2, v3)
    assert st.get("2000").kills == 1
    assert st.get("1000").deaths == 1
    assert st.get("2000").damage_dealt == 150
    print("stats: OK")


def test_map_render():
    from map_render import render_map_png

    png = render_map_png([rec("1000", []), rec("2000", [])])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 5000
    empty = render_map_png([])
    assert empty[:8] == b"\x89PNG\r\n\x1a\n"
    print("map_render: OK", len(png), "bytes")


if __name__ == "__main__":
    test_hit_diff_counting()
    test_map_render()
    print("all tests passed")
