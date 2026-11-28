"""activity_server のロングポーリング (/snapshot/wait) の検証。

フェイクのクラウドを相手に実サーバーを起動し、
- 初回 (since=-1) は最新スナップショットが即返ること
- since が最新と一致している間は保留され、状態が変わった瞬間に返ること
- 従来の /snapshot も生きていること
を確認する。
"""
from __future__ import annotations

import asyncio
import os
import time

import aiohttp
import pytest

import activity_server


class FakeCloud:
    project_id = "831274817"
    is_festival = False

    def __init__(self) -> None:
        self.recent_kills: list[dict] = []

    def online_players(self) -> list:
        return []

    def full_name(self, player_id: str):
        return {"0001": "Killer", "0002": "Victim"}.get(player_id)


@pytest.fixture()
def fake_cloud(monkeypatch):
    monkeypatch.setenv("ACTIVITY_PORT", "18777")
    monkeypatch.setenv("ACTIVITY_BASE", "")
    activity_server._snap_state.update({"v": 0, "raw": "", "payload": ""})
    return FakeCloud()


async def _run(fake_cloud: FakeCloud) -> None:
    runner = await activity_server.start_server(fake_cloud)  # type: ignore[arg-type]
    try:
        base = "http://127.0.0.1:18777"
        async with aiohttp.ClientSession() as s:
            # 初回: 最新スナップショット (v 付き) が返る
            async with s.get(f"{base}/snapshot/wait", params={"since": -1}) as r:
                assert r.status == 200
                snap = await r.json()
            assert snap["type"] == "state"
            v = snap["v"]
            assert isinstance(v, int) and v >= 1

            # since が最新と一致している間は保留され、変化で返る
            async def _wait():
                async with s.get(f"{base}/snapshot/wait", params={"since": v}) as r:
                    assert r.status == 200
                    return await r.json()

            task = asyncio.create_task(_wait())
            await asyncio.sleep(0.6)
            assert not task.done()  # 変化が無いので保留中のはず
            fake_cloud.recent_kills.append(
                {"seq": 1, "k": "0001", "v": "0002", "at": time.monotonic()}
            )
            snap2 = await asyncio.wait_for(task, timeout=3)
            assert snap2["v"] > v
            assert snap2["kills"][0]["kn"] == "Killer"
            assert snap2["kills"][0]["vn"] == "Victim"

            # 従来の /snapshot も生きている
            async with s.get(f"{base}/snapshot") as r:
                assert r.status == 200
                legacy = await r.json()
            assert legacy["type"] == "state"
    finally:
        await runner.cleanup()


def test_snapshot_wait_longpoll(fake_cloud):
    asyncio.run(_run(fake_cloud))
