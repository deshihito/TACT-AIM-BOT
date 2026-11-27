"""オンライン人数の履歴。1分ごとに記録し、24時間より古い分は破棄する。

保存先はリポジトリの外 (永続データ領域) を想定する。デプロイで
リポジトリを入れ替えても消えず、溜まり続けることもない。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

WINDOW_SEC = 24 * 3600
MAX_POINTS = 1440  # 1分間隔で24時間分 (保険)


def data_dir() -> Path:
    """リポジトリ外の永続データ置き場。"""
    d = Path.home() / ".local" / "share" / "tact-aim-bot"
    d.mkdir(parents=True, exist_ok=True)
    return d


class History:
    def __init__(self, path: Path):
        self.path = path
        self.points: list[tuple[float, int]] = []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.points = [(float(t), int(n)) for t, n in data]
        except Exception:
            self.points = []
        self._prune(time.time())

    def _prune(self, now: float) -> None:
        cutoff = now - WINDOW_SEC
        self.points = [p for p in self.points if p[0] >= cutoff][-MAX_POINTS:]

    def add(self, count: int, ts: float | None = None) -> None:
        ts = ts if ts is not None else time.time()
        self.points.append((ts, count))
        self._prune(ts)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.points), encoding="utf-8")
        except Exception:
            pass
