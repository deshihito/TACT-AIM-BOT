"""オンライン人数の履歴。1分ごとに記録し、直近24時間分を JSON に保存する。"""
from __future__ import annotations

import json
import time
from pathlib import Path

MAX_POINTS = 1440  # 1分間隔で24時間分


class History:
    def __init__(self, path: Path):
        self.path = path
        self.points: list[tuple[float, int]] = []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.points = [(float(t), int(n)) for t, n in data][-MAX_POINTS:]
        except Exception:
            self.points = []

    def add(self, count: int, ts: float | None = None) -> None:
        self.points.append((ts if ts is not None else time.time(), count))
        self.points = self.points[-MAX_POINTS:]
        try:
            self.path.write_text(
                json.dumps(self.points), encoding="utf-8"
            )
        except Exception:
            pass
