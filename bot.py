"""TACT-AIM 独立 Discord Bot (テスト版)。

役割は1つだけ: TurboWarp クラウド (TACT-AIM 16.9) を読み取り専用で
スクレイピングし、Discord で情報を出す。チート検出・執行はしない。

コマンド (prefix: !ta):
  !ta online  - 現在オンラインのプレイヤー一覧
  !ta status  - クラウド接続状態・統計
  !ta raw     - CLOUD変数の生サマリ (デバッグ用)

トークンは環境変数 DISCORD_TOKEN (.env) から読む。
"""
from __future__ import annotations

import os
import time

import discord
from discord.ext import commands, tasks

from cloud import CLOUD_VARS, EXTRA_VARS, TactAimCloud

cloud = TactAimCloud()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!ta ", intents=intents)


def fmt_players() -> str:
    players = sorted(cloud.online_players(), key=lambda r: r.player_id)
    if not players:
        return "オンラインのプレイヤーはいません（観測範囲内）。"
    lines = [f"**オンライン: {len(players)}人**"]
    for r in players:
        hits = ""
        if r.hits:
            last = r.hits[-1]
            hits = f" / 被弾: {last.attacker_id}から{last.damage}" + (
                " (キル)" if last.is_kill else ""
            )
        lines.append(
            f"- ID {r.player_id} | {r.character} | HP {r.hp}/{r.max_hp} "
            f"| 座標 ({r.x}, {r.y}) | チーム {r.team_id} | 名前片 '{r.name_chunk}'{hits}"
        )
    return "\n".join(lines)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    if not update_presence.is_running():
        update_presence.start()


@tasks.loop(seconds=30)
async def update_presence():
    n = len(cloud.online_players())
    state = "接続中" if cloud.connected else "再接続中"
    await bot.change_presence(
        activity=discord.Game(name=f"TACT-AIM {n}人オンライン ({state})")
    )


@bot.command(name="online")
async def online(ctx):
    await ctx.send(fmt_players())


@bot.command(name="status")
async def status(ctx):
    uptime = int(time.monotonic() - cloud.started_at)
    last = (
        f"{int(time.monotonic() - cloud.last_update_at)}秒前"
        if cloud.last_update_at
        else "なし"
    )
    await ctx.send(
        "**TACT-AIM Bot 状態**\n"
        f"- クラウド接続: {'接続中' if cloud.connected else '切断 (再接続中)'} "
        f"(接続回数 {cloud.connect_count})\n"
        f"- 受信した更新: {cloud.update_count}件 / 最終受信 {last}\n"
        f"- 追跡中プレイヤー: {cloud.tracked_count()}人 "
        f"(オンライン判定 {len(cloud.online_players())}人)\n"
        f"- 稼働時間: {uptime}秒"
    )


@bot.command(name="raw")
async def raw(ctx):
    lines = ["**CLOUD変数サマリ**"]
    for name in CLOUD_VARS + EXTRA_VARS:
        v = cloud.values.get(name)
        if v is None:
            lines.append(f"- {name}: (未受信)")
        else:
            lines.append(f"- {name}: {len(v)}文字, 先頭 {v[:24]}…")
    await ctx.send("\n".join(lines))


async def main():
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("DISCORD_TOKEN が未設定です (.env を確認)")
    async with bot:
        bot.loop.create_task(cloud.run_forever())
        await bot.start(token)


if __name__ == "__main__":
    import asyncio

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    asyncio.run(main())
