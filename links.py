"""Discord ユーザーと TACT-AIM プレイヤー (ユーザーネーム) の連携。

TACT-AIM のプレイヤー名は接続ユーザーネームそのもの (原作が username
ブロックを名前としてレコードに書く) で、TurboWarp は URL パラメータ
?username= でそれを指定できる。そこで /link でワンタイムコードを発行し、
turbowarp.org/<project>?username=player<コード> から参加したプレイヤーを
クラウド側で検出して、発行した Discord ユーザーと紐付ける。
/link に username オプションを付けた場合は、その Scratch ユーザーネーム
での参加を検出して紐付ける (名前は先着で、他人の連携済みは取れない)。

連携の特典はゲーム内で付与する: 初回連携の確定時に、Bot のダミー
プレイヤーが連携者の足元で「70ダメージ与えられた」被弾イベントを
送り、連携者の Match_damage を加算する。原作 Respawn のボーナス
(floor(Match_damage / 35) 個のコイン) で、次回リスポーン時に
コインが2個落ちる (voter.grant_damage)。同一ユーザーの再連携では
付与しない。

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
_NAME_RE = re.compile(r"^player(\d{7})$", re.IGNORECASE)


class LinkStore:
    def __init__(self, path: Path):
        self.path = path
        # code -> {"discord_id": int, "channel_id": int, "expires": float}
        self.pending: dict[str, dict] = {}
        # 名前 (小文字化) -> {"discord_id": int, "channel_id": int, "expires": float}
        self.pending_names: dict[str, dict] = {}
        # discord_id(str) -> {"name": str, "code": str, "linked_at": float, "rewarded": bool}
        self.links: dict[str, dict] = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.links = {str(k): dict(v) for k, v in data.get("links", {}).items()}
        except Exception:
            self.links = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"links": self.links}),
                encoding="utf-8",
            )
        except Exception:
            pass

    # --- コード発行 ---

    def _clear_pending_of(self, discord_id: int, now: float) -> None:
        """期限切れの予約と、そのユーザーの既存の予約 (コード/名前) を消す。"""
        self.pending = {
            c: p
            for c, p in self.pending.items()
            if p["expires"] > now and p["discord_id"] != discord_id
        }
        self.pending_names = {
            n: p
            for n, p in self.pending_names.items()
            if p["expires"] > now and p["discord_id"] != discord_id
        }

    def issue(self, discord_id: int, channel_id: int, now: float | None = None) -> str:
        """ワンタイムコードを発行する。同じユーザーの古い予約は破棄する。"""
        now = time.time() if now is None else now
        self._clear_pending_of(discord_id, now)
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

    def issue_name(
        self, discord_id: int, channel_id: int, name: str, now: float | None = None
    ) -> str | None:
        """自分の Scratch ユーザーネームでの連携を予約する。

        成功したら None、失敗したら理由を返す。名前は大文字小文字を
        区別せずに照合する。他人の連携済み・予約中の名前は取れない。
        """
        now = time.time() if now is None else now
        key = name.lower()
        for did, v in self.links.items():
            if v.get("name", "").lower() == key and int(did) != discord_id:
                return "その名前は既に別のユーザーに連携されています"
        p = self.pending_names.get(key)
        if p and p["expires"] > now and p["discord_id"] != discord_id:
            return "その名前は現在ほかのユーザーが予約中です"
        self._clear_pending_of(discord_id, now)
        self.pending_names[key] = {
            "discord_id": discord_id,
            "channel_id": channel_id,
            "expires": now + CODE_TTL_SEC,
        }
        return None

    @staticmethod
    def code_of(player_name: str | None) -> str | None:
        """プレイヤー名が player<7桁コード> の形ならコードを返す。"""
        if not player_name:
            return None
        m = _NAME_RE.match(player_name)
        return m.group(1) if m else None

    # --- 連携の確定 ---

    def try_complete(self, player_name: str, now: float | None = None) -> dict | None:
        """オンラインプレイヤー名が予約 (コード/名前) と一致したら確定する。

        確定したら {"discord_id", "channel_id", "name", "reward"} を返す
        (reward は今回が初回連携でゲーム内特典の対象かどうか)。
        """
        now = time.time() if now is None else now
        if not player_name:
            return None
        code = self.code_of(player_name)
        if code is not None:
            p = self.pending.get(code)
            if p is not None:
                del self.pending[code]
                if p["expires"] <= now:
                    return None
                return self._complete(p, player_name, code, now)
        p = self.pending_names.get(player_name.lower())
        if p is not None:
            del self.pending_names[player_name.lower()]
            if p["expires"] <= now:
                return None
            return self._complete(p, player_name, "", now)
        return None

    def _complete(self, p: dict, player_name: str, code: str, now: float) -> dict:
        did = str(p["discord_id"])
        prev = self.links.get(did, {})
        # 旧形式 (bonus) で記録された初回付与済みも引き継ぐ
        already = bool(prev.get("rewarded") or prev.get("bonus"))
        self.links[did] = {
            "name": player_name,
            "code": code,
            "linked_at": now,
            "rewarded": True,
        }
        self._save()
        return {
            "discord_id": p["discord_id"],
            "channel_id": p["channel_id"],
            "name": player_name,
            "reward": not already,
        }

    # --- 参照 ---

    def link_for_user(self, discord_id: int) -> dict | None:
        return self.links.get(str(discord_id))

    def user_for_name(self, player_name: str) -> int | None:
        for did, v in self.links.items():
            if v.get("name") == player_name:
                return int(did)
        return None
