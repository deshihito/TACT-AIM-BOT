"""通報票方式の /ban 執行: Bot が参加者として自分の票を入れる。

原作 (Pen_All) の強制停止は CSPID (不正疑いリスト) で、入り口は通報投票:
- 参加者は自分のレコードの静的データ枠に kind21=対象ID を載せて送る
  (送信側はローテーション kind 10..25 のうち毎回ランダムな1つを選ぶ)。
- 受信したクライアントは送信者を投票者として Blocked player に記録し、
  在席 > 5 かつ投票者が必要数を超えると対象を CSPID に入れる。
  CSPID 入りした ID のレコードはそのクライアントで全て棄却され、
  発射も処理されなくなる (= 事実上の停止。CSPID は約300秒周期で消去
  されるが、票が残っていれば再投入される)。

このモジュールは他人のレコードには一切触らない。Bot 自身の ID・
レコードだけを使い、/ban の間だけ参加者として接続して通報票を
入れ続ける。/ban が実行されると、既存の通報票の有無や在席人数に
かかわらず Bot の票の送信を開始する。
"""
from __future__ import annotations

import asyncio
import json
import math
import random

import websockets

from cloud import CLOUD_URL, CLOUD_VARS, TactAimCloud
from record import encode_name_chunk

# TurboWarp のボット規約: カスタムクライアントは連絡先入り User-Agent が必須
USER_AGENT = "tact-aim-bot/0.1 (report voter; contact: github.com/deshihito)"
VOTER_USERNAME = "player24680"
# TurboWarp のクラウドサーバーは接続ごとの set を間引く (実測: 到達率は
# 送信間隔 0.25秒で約1/3、1秒で約8割、ただし到達の絶対数は速い方が多い)。
# 連続送信で欠けを埋める前提で 0.3 秒間隔にする (到達は毎秒1件前後)。
TICK_SECONDS = 0.3


def votes_needed(active: int) -> int | None:
    """在席 active 人のとき CSPID 成立に必要な通報者数 (原作 Cloud 手順の式)。

    条件: 在席 > 5 かつ 投票者数×4文字 > (ceil(在席×2/3) - 1) × 2文字。
    在席 5 人以下では通報自体が成立しないので None。
    """
    if active <= 5:
        return None
    return (math.ceil(active * 2 / 3) - 1) // 2 + 1


def build_record(
    player_id: str,
    seq: int,
    version: int,
    blood: bool,
    skill_type: str,
    skill_value: str,
    name_chunk: str,
) -> str:
    """Bot 自身のレコード (246文字 / blood は247文字) を組み立てる。

    フィールド配置は record.parse_record と対。ベースエリアで静止する
    傍観者プレイヤーとして振る舞う (HP満タン、発射なし、チーム未所属)。
    """
    parts = [
        player_id,  # ID
        f"{seq:05d}",  # seq (World_timer)
        str(version),  # バージョン
        "5000",  # X = 0
        "5000",  # Y = 0
        "500" * 7,  # モーション 7つ (全て 0)
        player_id,  # Team_ID (未所属 = 自分の ID)
        "1000",  # f13 Laser sight (0)
        "0000000" * 9,  # 発射イベント (なし)
        "10",  # キャラ = Cat
        "0",  # f16
        "100",  # f17 (0)
        "0",  # f18
        "00",  # f19
        "000000000" * 6,  # 被弾イベント (なし)
        "2140",  # HP = 140
        "2140",  # MaxHP = 140
        "01",  # f23
        "200",  # 攻撃係数 = 1.00
        "0",  # f25 Special skin
        "0",  # f26 Scoping
        "0",  # f27
        "0",  # f28 Team color (未所属)
        "0",  # f29 Team menu
        "100",  # スキルポイント (0)
        "0000",  # f31
        "0000000",  # f32
        "0",  # f33
        "00",  # チャット (なし)
        skill_type,  # 静的データ枠: 種別
        skill_value,  # 静的データ枠: 値
        "0",  # f44 Turreting
        "0",  # f45 Special weapon
        "500" * 3,  # ペット X/Y/向き (0)
        "100",  # ペットアニメ (0)
        "0",  # f52
        "0",  # f53
        "50",  # 速度 X (0)
        "50",  # 速度 Y (0)
        "0000",  # f56 チーム招待
        "550",  # f61 (0)
    ]
    if blood:
        parts.append("0")  # blood版 'Blood Pico debuff?'
    parts.append(name_chunk)  # 名前チャンク (5文字)
    return "".join(parts)


class VoterManager:
    """BAN 中の対象へ通報票を入れ続ける参加者クライアント。"""

    def __init__(self, cloud: TactAimCloud):
        self.cloud = cloud
        self._targets: set[str] = set()
        self._task: asyncio.Task | None = None
        self._ws = None
        self._ws_project: str | None = None
        self._drain_task: asyncio.Task | None = None
        self.player_id: str | None = None
        self._last_seq = 0
        self._name_pos = 0
        self._target_idx = 0

    # --- 公開 API ---
    def is_banning(self, player_id: str) -> bool:
        return player_id in self._targets

    def banning_ids(self) -> list[str]:
        return sorted(self._targets)

    async def start_ban(self, player_id: str) -> bool:
        if player_id in self._targets:
            return False
        self._targets.add(player_id)
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        return True

    async def stop_ban(self, player_id: str) -> bool:
        if player_id not in self._targets:
            return False
        self._targets.discard(player_id)
        if not self._targets:
            await self._shutdown()
        return True

    async def stop_all(self) -> None:
        self._targets.clear()
        await self._shutdown()

    # --- 内部 ---
    async def _shutdown(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._close_ws()

    def _pick_player_id(self) -> str:
        while True:
            pid = f"{random.randint(0, 9999):04d}"
            if pid not in self.cloud.players:
                return pid

    def _format(self) -> tuple[int, bool]:
        """現在のプロジェクトの (バージョン, blood形式か) を観測レコードから得る。"""
        sample = next(iter(self.cloud.players.values()), None)
        if sample is None:
            return 5, self.cloud.is_festival
        return sample.version, len(sample.raw) == 247

    async def _ensure_ws(self):
        if self._ws is not None and self._ws_project == self.cloud.project_id:
            return self._ws
        await self._close_ws()
        ws = await websockets.connect(
            CLOUD_URL,
            user_agent_header=USER_AGENT,
            origin="https://turbowarp.org",
            ping_interval=20,
            ping_timeout=20,
        )
        await ws.send(
            json.dumps(
                {
                    "method": "handshake",
                    "user": VOTER_USERNAME,
                    "project_id": self.cloud.project_id,
                }
            )
            + "\n"
        )
        self._ws = ws
        self._ws_project = self.cloud.project_id
        self._drain_task = asyncio.create_task(self._drain(ws))
        return ws

    async def _drain(self, ws) -> None:
        try:
            async for _ in ws:
                pass
        except Exception:
            pass

    async def _close_ws(self) -> None:
        if self._drain_task is not None:
            self._drain_task.cancel()
            self._drain_task = None
        ws, self._ws = self._ws, None
        self._ws_project = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def _run(self) -> None:
        if self.player_id is None:
            self.player_id = self._pick_player_id()
        backoff = 2.0
        while self._targets:
            try:
                ws = await self._ensure_ws()
                version, blood = self._format()
                world = max(
                    (rec.seq for rec in self.cloud.players.values()),
                    default=0,
                )
                self._last_seq = max(self._last_seq + 1, world + 1)
                targets = sorted(self._targets)
                target = targets[self._target_idx % len(targets)]
                self._target_idx += 1
                self._name_pos = (self._name_pos + 1) % 10
                record = build_record(
                    self.player_id,
                    self._last_seq,
                    version,
                    blood,
                    "21",  # 静的データ枠 kind21 = 通報
                    target,
                    encode_name_chunk(VOTER_USERNAME, self._name_pos),
                )
                # set には user と project_id が必須 (無いと中継されない)
                await ws.send(
                    json.dumps(
                        {
                            "method": "set",
                            "user": VOTER_USERNAME,
                            "project_id": self.cloud.project_id,
                            "name": random.choice(CLOUD_VARS),
                            "value": record,
                        }
                    )
                    + "\n"
                )
                backoff = 2.0
                await asyncio.sleep(TICK_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"ban voter error: {e}", flush=True)
                await self._close_ws()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
