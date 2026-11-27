"""TurboWarp クラウドへの読み取り専用スクレイピングクライアント。

プロトコル (公開仕様): WebSocket で handshake {user, project_id} を送ると、
現在値が set メッセージで全件返り、以後は更新のたびに set が push される。
改行区切り JSON 行。

TACT-AIM の保存方式: 各プレイヤーは自分のレコードを CLOUD1..10 のうち
ランダムな1本へ書く (原作 Cloud 手順)。そのためスナップショット1回では
全員は揃わず、一定時間観測して ID 単位で蓄積する必要がある。
"""
from __future__ import annotations

import asyncio
import json
import time

import websockets

from record import PlayerRecord, parse_record, seq_is_newer
from stats import StatsTracker

CLOUD_URL = "wss://clouddata.turbowarp.org"
PROJECT_ID = "831274817"  # TACT-AIM (uk-400)
# TurboWarp のボット規約: カスタムクライアントは連絡先入り User-Agent が必須
USER_AGENT = "tact-aim-bot/0.1 (read-only monitor; contact: github.com/deshihito)"
# ユーザー名は "player" + 数字 の形式が TurboWarp に受理される
USERNAME = "player12345"

CLOUD_VARS = [f"☁ CLOUD{i}" for i in range(1, 11)]
EXTRA_VARS = ["☁ Member1"] + [f"☁ Chat{i}" for i in range(1, 6)]


class TactAimCloud:
    def __init__(self, online_window: float = 45.0, project_id: str = PROJECT_ID):
        self.online_window = online_window
        # 現在のスクレイピング対象。通常は PROJECT_ID、フェス登録中はフェス側。
        self.project_id = project_id
        self._ws = None
        self.values: dict[str, str] = {}
        self.players: dict[str, PlayerRecord] = {}
        self._seen_at: dict[str, float] = {}
        self.connected = False
        self.connect_count = 0
        self.update_count = 0
        self.last_update_at: float | None = None
        self.started_at = time.monotonic()
        # デコード失敗の診断 (形式違いの版だと黙って0人になるため可視化する)
        self.parse_failures = 0
        self.seen_lengths: set[int] = set()
        # セッション統計 (キル/デス/ダメージ)
        self.stats = StatsTracker()
        # 名前チャンクの蓄積: {player_id: {位置: 2文字}}。集まると正式名になる
        self._name_chunks: dict[str, dict[int, str]] = {}

    def _note_name(self, rec: PlayerRecord) -> None:
        if rec.name_index is not None:
            self._name_chunks.setdefault(rec.player_id, {})[rec.name_index] = rec.name_chars

    def full_name(self, player_id: str) -> str | None:
        """蓄積したチャンクから正式なユーザー名を組み立てる。

        未取得チャンクは "??"、末尾の詰め物 "=" は取り除く。
        名前らしい中身が無ければ None。
        """
        chunks = self._name_chunks.get(player_id)
        if not chunks:
            return None
        real = [i for i, c in chunks.items() if c != "=="]
        if not real:
            return None
        last = max(real)
        name = "".join(chunks.get(i, "??") for i in range(last + 1)).rstrip("=")
        return name or None

    @property
    def is_festival(self) -> bool:
        return self.project_id != PROJECT_ID

    async def set_project(self, project_id: str) -> bool:
        """スクレイピング対象を切り替える。蓄積は破棄し、接続中なら張り替える。

        戻り値: 実際に切り替えたら True (同じ対象なら False)。
        """
        if project_id == self.project_id:
            return False
        self.project_id = project_id
        self.values.clear()
        self.players.clear()
        self._seen_at.clear()
        self.last_update_at = None
        self.parse_failures = 0
        self.seen_lengths.clear()
        self.stats.reset()
        self._name_chunks.clear()
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        return True

    # --- 状態参照 ---
    def online_players(self) -> list[PlayerRecord]:
        now = time.monotonic()
        return [
            rec
            for pid, rec in self.players.items()
            if now - self._seen_at.get(pid, 0) <= self.online_window
        ]

    def tracked_count(self) -> int:
        return len(self.players)

    # --- 受信処理 ---
    def _handle_set(self, name: str, value: str) -> None:
        self.values[name] = value
        self.update_count += 1
        self.last_update_at = time.monotonic()
        if name not in CLOUD_VARS:
            return
        rec = parse_record(value)
        if rec is None:
            if len(value) >= 100 and value.isdigit():
                self.parse_failures += 1
                self.seen_lengths.add(len(value))
            return
        old = self.players.get(rec.player_id)
        if old is None or seq_is_newer(rec.seq, old.seq):
            self.stats.observe(old, rec)
            self._note_name(rec)
            self.players[rec.player_id] = rec
            self._seen_at[rec.player_id] = time.monotonic()
        elif old is not None and rec.seq == old.seq:
            self._seen_at[rec.player_id] = time.monotonic()

    async def _connect_once(self) -> None:
        async with websockets.connect(
            CLOUD_URL,
            user_agent_header=USER_AGENT,
            origin="https://turbowarp.org",
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            self._ws = ws
            await ws.send(
                json.dumps(
                    {
                        "method": "handshake",
                        "user": USERNAME,
                        "project_id": self.project_id,
                    }
                )
                + "\n"
            )
            self.connected = True
            self.connect_count += 1
            try:
                async for message in ws:
                    for line in str(message).split("\n"):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if msg.get("method") in ("set", "create") and isinstance(
                            msg.get("name"), str
                        ):
                            self._handle_set(msg["name"], str(msg.get("value", "")))
            finally:
                if self._ws is ws:
                    self._ws = None

    async def run_forever(self) -> None:
        """切断時は指数バックオフで再接続し続ける。"""
        backoff = 2.0
        while True:
            try:
                await self._connect_once()
                backoff = 2.0
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
