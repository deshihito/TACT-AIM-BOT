"""links.py (Discord とプレイヤーの連携) の検証。"""
from __future__ import annotations

from pathlib import Path

from links import LINK_BONUS, LinkStore


def _store(tmp_path: Path) -> LinkStore:
    return LinkStore(tmp_path / "links.json")


def test_issue_and_complete_grants_bonus(tmp_path):
    s = _store(tmp_path)
    code = s.issue(111, 999, now=1000.0)
    assert len(code) == 7 and code.isdigit()
    done = s.try_complete(f"player{code}", now=1100.0)
    assert done is not None
    assert done["discord_id"] == 111
    assert done["bonus"] == LINK_BONUS
    assert done["balance"] == LINK_BONUS
    assert s.balance(111) == LINK_BONUS
    assert s.link_for_user(111)["name"] == f"player{code}"
    assert s.user_for_name(f"player{code}") == 111


def test_code_is_one_time(tmp_path):
    s = _store(tmp_path)
    code = s.issue(111, 999, now=1000.0)
    assert s.try_complete(f"player{code}", now=1001.0) is not None
    assert s.try_complete(f"player{code}", now=1002.0) is None


def test_relink_does_not_grant_bonus_twice(tmp_path):
    s = _store(tmp_path)
    c1 = s.issue(111, 999, now=1000.0)
    s.try_complete(f"player{c1}", now=1001.0)
    c2 = s.issue(111, 999, now=2000.0)
    done = s.try_complete(f"player{c2}", now=2001.0)
    assert done is not None
    assert done["bonus"] == 0
    assert s.balance(111) == LINK_BONUS
    assert s.link_for_user(111)["name"] == f"player{c2}"


def test_expired_code_rejected(tmp_path):
    s = _store(tmp_path)
    code = s.issue(111, 999, now=1000.0)
    assert s.try_complete(f"player{code}", now=1000.0 + 601) is None
    assert s.link_for_user(111) is None


def test_reissue_invalidates_previous_code(tmp_path):
    s = _store(tmp_path)
    c1 = s.issue(111, 999, now=1000.0)
    c2 = s.issue(111, 999, now=1001.0)
    assert c1 != c2
    assert s.try_complete(f"player{c1}", now=1002.0) is None
    assert s.try_complete(f"player{c2}", now=1002.0) is not None


def test_non_matching_names_ignored(tmp_path):
    s = _store(tmp_path)
    s.issue(111, 999, now=1000.0)
    for name in (None, "", "MASTER", "player123", "player12345678", "xplayer1234567"):
        assert s.try_complete(name, now=1001.0) is None
    assert s.link_for_user(111) is None


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "links.json"
    s = LinkStore(path)
    code = s.issue(222, 888, now=1000.0)
    s.try_complete(f"player{code}", now=1001.0)
    s2 = LinkStore(path)
    assert s2.balance(222) == LINK_BONUS
    assert s2.link_for_user(222)["name"] == f"player{code}"
    # 再起動後に同じコードで新規発行が衝突しない
    for _ in range(20):
        assert s2.issue(333, 888, now=2000.0) != code
