"""Discord ユーザーと TACT-AIM プレイヤー (ユーザーネーム) の連携。

TACT-AIM のプレイヤー名は接続ユーザーネームそのもの (原作が username
ブロックを名前としてレコードに書く) で、TurboWarp は URL パラメータ
?username= でそれを指定できる。そこで /link でワンタイムコードを発行し、
turbowarp.org/<project>?username=player<コード> から参加したプレイヤーを
クラウド側で検出して、発行した Discord ユーザーと紐付ける。

連携の特典として、Bot 側のコイン台帳に初期コイン +2 を付与する
(同一ユーザーの再連携では付与しない)。コインは Bot 側だけの台帳で、
ゲーム内の通貨とは無関係。

保存先は history と同じ永続データ領域 (リポジトリ外)。
pending コードはメモリ上のみ (再起動で失効してよい)。
"""
from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path

CODE_TTL_SEC = 600  # 発行コードの有効期限 (10分)
LINK_BONUS = 2  # 連携時の初期コイン
_NAME_RE = re.compile(r"^player(\d{7})$", re.IGNORECASE)


class LinkStore:
    def __init__(self, path: Path):
        self.path = path
        # code -> {"discord_id": int, "channel_id": int, "expires": float}
        self.pending: dict[str, dict] = {}
        # discord_id(str) -> {"name": str, "code": str, "linked_at": float, "bonus": bool}
        self.links: dict[str, dict] = {}
        # discord_id(str) -> 残高
        self.coins: dict[str, int] = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.links = {str(k): dict(v) for k, v in data.get("links", {}).items()}
            self.coins = {str(k): int(v) for k, v in data.get("coins", {}).items()}
        except Exception:
            self.links = {}
            self.coins = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"links": self.links, "coins": self.coins}),
                encoding="utf-8",
            )
        except Exception:
            pass

    # --- コード発行 ---

    def issue(self, discord_id: int, channel_id: int, now: float | None = None) -> str:
        """ワンタイムコードを発行する。同じユーザーの古いコードは破棄する。"""
        now = time.time() if now is None else now
        self.pending = {
            c: p
            for c, p in self.pending.items()
            if p["expires"] > now and p["discord_id"] != discord_id
        }
        used = set(self.pending) | {v.get("code") for v in self.links.values()}
        while True:
            code = str(secrets.randbelow(9_000_000) + 1_000_000)
            if code not in used:
                break
        self.pending[code] = {
            "discord_id": discord_id,
            "channel_id": channel_id,
            "expires": now + CODE_TTL_SEC,
        }
        return code

    @staticmethod
    def code_of(player_name: str | None) -> str | None:
        """プレイヤー名が player<7桁コード> の形ならコードを返す。"""
        if not player_name:
            return None
        m = _NAME_RE.match(player_name)
        return m.group(1) if m else None

    # --- 連携の確定 ---

    def try_complete(self, player_name: str, now: float | None = None) -> dict | None:
        """オンラインプレイヤー名が pending コードと一致したら連携を確定する。

        確定したら {"discord_id", "channel_id", "name", "bonus", "balance"}
        を返す (bonus は今回付与した初期コイン。0 は付与済みの再連携)。
        """
        now = time.time() if now is None else now
        code = self.code_of(player_name)
        if code is None:
            return None
        p = self.pending.get(code)
        if p is None:
            return None
        if p["expires"] <= now:
            del self.pending[code]
            return None
        del self.pending[code]
        did = str(p["discord_id"])
        already = bool(self.links.get(did, {}).get("bonus"))
        bonus = 0
        if not already:
            bonus = LINK_BONUS
            self.coins[did] = self.coins.get(did, 0) + LINK_BONUS
        self.links[did] = {
            "name": player_name,
            "code": code,
            "linked_at": now,
            "bonus": True,
        }
        self._save()
        return {
            "discord_id": p["discord_id"],
            "channel_id": p["channel_id"],
            "name": player_name,
            "bonus": bonus,
            "balance": self.coins.get(did, 0),
        }

    # --- 参照 ---

    def link_for_user(self, discord_id: int) -> dict | None:
        return self.links.get(str(discord_id))

    def user_for_name(self, player_name: str) -> int | None:
        for did, v in self.links.items():
            if v.get("name") == player_name:
                return int(did)
        return None

    def balance(self, discord_id: int) -> int:
        return self.coins.get(str(discord_id), 0)
