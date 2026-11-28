"""links.py (Discord とプレイヤーの連携) の検証。"""
from __future__ import annotations

from pathlib import Path

from links import LinkStore


def _store(tmp_path: Path) -> LinkStore:
    return LinkStore(tmp_path / "links.json")


def test_issue_and_complete(tmp_path):
    s = _store(tmp_path)
    code = s.issue(111, 999, now=1000.0)
    assert len(code) == 7 and code.isdigit()
    done = s.try_complete(f"player{code}", now=1100.0)
    assert done is not None
    assert done["discord_id"] == 111
    assert s.link_for_user(111)["name"] == f"player{code}"
    assert s.user_for_name(f"player{code}") == 111


def test_code_is_one_time(tmp_path):
    s = _store(tmp_path)
    code = s.issue(111, 999, now=1000.0)
    assert s.try_complete(f"player{code}", now=1001.0) is not None
    assert s.try_complete(f"player{code}", now=1002.0) is None


def test_relink_updates_name(tmp_path):
    s = _store(tmp_path)
    c1 = s.issue(111, 999, now=1000.0)
    s.try_complete(f"player{c1}", now=1001.0)
    c2 = s.issue(111, 999, now=2000.0)
    done = s.try_complete(f"player{c2}", now=2001.0)
    assert done is not None
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


def test_name_claim_flow(tmp_path):
    s = _store(tmp_path)
    assert s.issue_name(111, 999, "Tako_Tomoketa", now=1000.0) is None
    # 大文字小文字が違っても照合する
    done = s.try_complete("tako_tomoketa", now=1001.0)
    assert done is not None
    assert done["discord_id"] == 111
    assert s.user_for_name("tako_tomoketa") == 111


def test_name_claim_conflicts(tmp_path):
    s = _store(tmp_path)
    assert s.issue_name(111, 999, "shared-name", now=1000.0) is None
    # 予約中は他人が取れない
    assert s.issue_name(222, 999, "Shared-Name", now=1001.0) is not None
    s.try_complete("shared-name", now=1002.0)
    # 連携済みも他人は取れない
    assert s.issue_name(222, 999, "shared-name", now=2000.0) is not None
    # 本人の再予約はよい
    assert s.issue_name(111, 999, "shared-name", now=2000.0) is None


def test_name_claim_expires(tmp_path):
    s = _store(tmp_path)
    assert s.issue_name(111, 999, "late-name", now=1000.0) is None
    assert s.try_complete("late-name", now=1000.0 + 601) is None
    # 期限切れ後は他人が予約できる
    assert s.issue_name(222, 999, "late-name", now=2000.0) is None


def test_code_issue_clears_name_claim(tmp_path):
    s = _store(tmp_path)
    assert s.issue_name(111, 999, "my-name", now=1000.0) is None
    s.issue(111, 999, now=1001.0)
    assert s.try_complete("my-name", now=1002.0) is None


def test_coins(tmp_path):
    s = _store(tmp_path)
    assert s.balance(111) == 0
    assert s.add_coins(111, 5) == 5
    assert s.add_coins(111, 5) == 10
    assert s.balance(111) == 10
    assert s.balance(222) == 0


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "links.json"
    s = LinkStore(path)
    code = s.issue(222, 888, now=1000.0)
    s.try_complete(f"player{code}", now=1001.0)
    s.add_coins(222, 15)
    s2 = LinkStore(path)
    assert s2.link_for_user(222)["name"] == f"player{code}"
    assert s2.balance(222) == 15
    # 再起動後に同じコードで新規発行が衝突しない
    for _ in range(20):
        assert s2.issue(333, 888, now=2000.0) != code
