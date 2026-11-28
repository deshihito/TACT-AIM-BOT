"""Discord アクティビティ (リアルタイム観戦) 用の配信サーバー。

Bot プロセス内で aiohttp サーバーを立て、web/ の静的ファイルと
assets/・maps.json を配信し、WebSocket (/ws) でプレイヤーの
スナップショットを 0.5 秒ごとに流す。観戦専用で認証は無い。

公開方法 (Discord Developer Portal の URL マッピング先) は README 参照。
ポートは環境変数 ACTIVITY_PORT (既定 8765)。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from aiohttp import web

from cloud import TactAimCloud
from map_data import current_map_number

BASE = Path(__file__).with_name("web")
ASSETS = Path(__file__).with_name("assets")
MAPS_JSON = Path(__file__).with_name("maps.json")

_clients: set[web.WebSocketResponse] = set()


def snapshot(cloud: TactAimCloud) -> dict:
    players = []
    for r in sorted(cloud.online_players(), key=lambda p: p.player_id):
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
            }
        )
    return {
        "type": "state",
        "project": cloud.project_id,
        "map": current_map_number(cloud.project_id),
        "festival": cloud.is_festival,
        "players": players,
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


async def _broadcast_loop(app: web.Application) -> None:
    cloud: TactAimCloud = app["cloud"]
    last = ""
    while True:
        await asyncio.sleep(0.5)
        if not _clients:
            continue
        payload = json.dumps(snapshot(cloud))
        if payload == last:
            continue
        last = payload
        for ws in list(_clients):
            try:
                await ws.send_str(payload)
            except Exception:
                _clients.discard(ws)


async def _config_handler(request: web.Request) -> web.Response:
    return web.json_response({"client_id": request.app.get("client_id", "")})


async def start_server(cloud: TactAimCloud, client_id: str = "") -> web.AppRunner:
    app = web.Application()
    app["cloud"] = cloud
    app["client_id"] = client_id
    app.router.add_get("/config.json", _config_handler)
    app.router.add_get("/ws", _ws_handler)
    app.router.add_static("/assets/", ASSETS, show_index=False)
    app.router.add_get("/maps.json", lambda req: web.FileResponse(MAPS_JSON))
    app.router.add_static("/", BASE, show_index=True)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("ACTIVITY_PORT", "8765"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    app["broadcast_task"] = asyncio.create_task(_broadcast_loop(app))
    print(f"Activity server listening on :{port}")
    return runner
