"""Discord アクティビティ (リアルタイム観戦) 用の配信サーバー。

Bot プロセス内で aiohttp サーバーを立て、web/ の静的ファイルと
assets/・maps.json を配信し、プレイヤーのスナップショットを
WebSocket (/ws) と HTTP (/snapshot) で流す。観戦専用で認証は無い。

公開方法 (Discord Developer Portal の URL マッピング先) は README 参照。
ポートは環境変数 ACTIVITY_PORT (既定 8765)。
agobot.app 配下の独立パスで公開する場合は ACTIVITY_BASE=/tact-aim のように
ベースパスを設定する (AGO の Web アプリが /tact-aim をこのサーバーへ
リライトで中継する。WebSocket は中継できないため、公開経由のクライアントは
/snapshot/wait のロングポーリングで受ける)。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from aiohttp import web

from cloud import TactAimCloud
from map_data import current_map_number

BASE = Path(__file__).with_name("web")
ASSETS = Path(__file__).with_name("assets")
MAPS_JSON = Path(__file__).with_name("maps.json")

_clients: set[web.WebSocketResponse] = set()

# ロングポーリング用の最新スナップショット (バージョン付き)。
# _publish_loop が変化を検出した時だけ更新する。
_snap_state: dict = {"v": 0, "raw": "", "payload": ""}
_waiters = 0


def _raw_int(raw: str, start: int, end: int) -> int:
    try:
        return int(raw[start:end])
    except ValueError:
        return 0


def snapshot(cloud: TactAimCloud) -> dict:
    players = []
    for r in sorted(cloud.online_players(), key=lambda p: p.player_id):
        # 描画・弾再現に要るが PlayerRecord に残らないフィールドは raw から切る
        # (オフセットは record.py のカーソル順と一致。blood 版の追加1文字は
        # 末尾側なので、ここで使う範囲は両形式で共通)。
        raw = r.raw
        players.append(
            {
                "id": r.player_id,
                "name": cloud.full_name(r.player_id),
                "char": r.character,
                "x": r.x,
                "y": r.y,
                "hp": r.hp,
                "max": r.max_hp,
                "sp": r.special_weapon,
                "tc": r.team_color,
                "team": r.team_id,
                # 原作のプレイヤー描画 (draw.ts) と発射再現に使う追加情報
                "mo": r.motion,  # [向き, 手1オフセットx/y, 手1向き, 手2オフセットx/y, 手2向き]
                "shots": r.shots,  # 発射パック (7文字) のリスト
                "vx": r.vel_x,
                "vy": r.vel_y,
                "wpn": max(0, _raw_int(raw, 113, 116) - 100),  # f17: 現在の武器 (Weapon name の1始まり番号、0=なし)
                "nh": raw[112:113] == "0",  # f16: 被弾対象になるか (Search first の候補条件)
                "b18": raw[116:117] == "1",  # f18: マゼンタ円のバフ表示
                "skin": raw[186:187] == "1",  # f25: "+" コスチュームを使うか
                "sc": raw[187:188] == "1",  # f26: スコープ中 (拡散の +25 補正条件)
                "atk": raw[188:189] == "1",  # f27: 攻撃中 (物理武器の振り表示)
                "chat": raw[205:206] == "1",  # f33: チャット入力中バー
                "buf": _raw_int(raw, 228, 229),  # f52: バフアイコン数
                "deb": _raw_int(raw, 229, 230),  # f53: デバフアイコン数
            }
        )
    # キル表示用: 直近8秒のキルイベント (名前は解決できなければ None のまま)
    now = time.monotonic()
    kills = [
        {
            "seq": e["seq"],
            "k": e["k"],
            "v": e["v"],
            "kn": cloud.full_name(e["k"]),
            "vn": cloud.full_name(e["v"]),
        }
        for e in cloud.recent_kills
        if now - e["at"] <= 8
    ]
    return {
        "type": "state",
        "project": cloud.project_id,
        "map": current_map_number(cloud.project_id),
        "festival": cloud.is_festival,
        "players": players,
        "kills": kills,
    }


async def _ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    _clients.add(ws)
    cloud: TactAimCloud = request.app["cloud"]
    await ws.send_str(json.dumps(snapshot(cloud)))
    try:
        async for _ in ws:
            pass
    finally:
        _clients.discard(ws)
    return ws


async def _publish_loop(app: web.Application) -> None:
    """0.2 秒ごとにスナップショットを再構築し、変化があった時だけ
    バージョンを上げて WebSocket へ流し、ロングポーリングの待機者を起こす。
    視聴者が居ない間は再構築自体をしない (負荷は視聴中だけ)。"""
    cloud: TactAimCloud = app["cloud"]
    while True:
        await asyncio.sleep(0.2)
        if not _clients and _waiters == 0:
            continue
        snap = snapshot(cloud)
        raw = json.dumps(snap)
        if raw == _snap_state["raw"]:
            continue
        _snap_state["raw"] = raw
        _snap_state["v"] += 1
        _snap_state["payload"] = json.dumps({**snap, "v": _snap_state["v"]})
        for ws in list(_clients):
            try:
                await ws.send_str(_snap_state["payload"])
            except Exception:
                _clients.discard(ws)


async def _config_handler(request: web.Request) -> web.Response:
    return web.json_response({"client_id": request.app.get("client_id", "")})


async def _snapshot_handler(request: web.Request) -> web.Response:
    # WebSocket を中継できない公開経路 (AGO Web のリライト) 用の HTTP 版。
    # フォールバック用の一回取得 (通常は /snapshot/wait を使う)。
    return web.json_response(snapshot(request.app["cloud"]))


async def _wait_handler(request: web.Request) -> web.Response:
    """ロングポーリング: クライアントの知っているバージョンと最新が
    違えば即返し、同じなら変化があるまで最大 20 秒保持して返す。
    変化が無い間は通信もスナップショット再構築も発生しない。"""
    global _waiters
    try:
        since = int(request.query.get("since", "-1"))
    except ValueError:
        since = -1
    _waiters += 1
    try:
        deadline = time.monotonic() + 20
        while True:
            if _snap_state["payload"] and _snap_state["v"] != since:
                return web.Response(
                    text=_snap_state["payload"], content_type="application/json"
                )
            if time.monotonic() >= deadline:
                return web.Response(status=204)
            await asyncio.sleep(0.1)
    finally:
        _waiters -= 1


def _base_path() -> str:
    base = os.environ.get("ACTIVITY_BASE", "").strip()
    if not base:
        return ""
    return "/" + base.strip("/")


@web.middleware
async def _no_cache(request: web.Request, handler):
    # Discord アクティビティの WebView は静的ファイルを強くキャッシュし、
    # デプロイ後も古い app.js を出し続けることがある (2026-11-28 の実害)。
    # no-cache (再検証) でも iOS の WebView が古い版を出し続けたため、
    # 全応答に no-store を付けて保存自体を禁止する。ファイルは小さいので
    # 毎回取得でも問題ない。
    response = await handler(request)
    response.headers["Cache-Control"] = "no-store"
    return response


async def _index_handler(request: web.Request) -> web.Response:
    # app.js の URL に更新時刻のバージョンを付け、キャッシュキーごと
    # 変える。ヘッダーを無視するキャッシュ (WebView / プロキシ) でも
    # デプロイのたびに別 URL になるため、古い版が残らない。
    html = (BASE / "index.html").read_text(encoding="utf-8")
    version = int((BASE / "app.js").stat().st_mtime)
    html = html.replace('src="app.js"', f'src="app.js?v={version}"')
    return web.Response(text=html, content_type="text/html")


async def start_server(cloud: TactAimCloud, client_id: str = "") -> web.AppRunner:
    app = web.Application(middlewares=[_no_cache])
    app["cloud"] = cloud
    app["client_id"] = client_id
    base = _base_path()
    app.router.add_get(f"{base}/config.json", _config_handler)
    app.router.add_get(f"{base}/snapshot", _snapshot_handler)
    app.router.add_get(f"{base}/snapshot/wait", _wait_handler)
    app.router.add_get(f"{base}/ws", _ws_handler)
    app.router.add_static(f"{base}/assets/", ASSETS, show_index=False)
    app.router.add_get(f"{base}/maps.json", lambda req: web.FileResponse(MAPS_JSON))
    if base:
        async def _to_slash(req: web.Request) -> web.Response:
            raise web.HTTPMovedPermanently(f"{base}/")

        app.router.add_get(base, _to_slash)
    app.router.add_get(f"{base}/", _index_handler)
    app.router.add_static(f"{base}/", BASE, show_index=False)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("ACTIVITY_PORT", "8765"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    app["broadcast_task"] = asyncio.create_task(_publish_loop(app))
    print(f"Activity server listening on :{port}", flush=True)
    return runner
