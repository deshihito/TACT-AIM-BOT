"""TACT-AIM 独立 Discord Bot (テスト版)。

役割は1つだけ: TurboWarp クラウド (TACT-AIM 16.9) を読み取り専用で
スクレイピングし、Discord で情報を出す。チート検出・執行はしない。

コマンド:
  !ta online      - 現在オンラインのプレイヤー一覧 (全員)
  !ta status      - クラウド接続状態・統計 (全員)
  !ta raw         - CLOUD変数の生サマリ (管理者専用)
  /festival       - フェス登録 (管理者専用・スラッシュ)。URLを入れると
                    スクレイピング先をそのプロジェクトへ切り替える。
                    フェスは同時に1つだけ。入れるたびに前の登録と入れ替わる。
  /festival_clear - フェス登録を解除し、通常の TACT-AIM へ戻す (管理者専用)

トークンは環境変数 DISCORD_TOKEN (.env) から読む。
フェス登録は state.json に保存され、再起動しても維持される。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cloud import CLOUD_VARS, EXTRA_VARS, PROJECT_ID, TactAimCloud

cloud = TactAimCloud()

# 管理者 (Discord ユーザーID)。特定コマンド (raw 等) はこのIDだけが実行できる。
ADMIN_IDS = {
    1320705325686587408,
    1498868054904406029,
    831369295597928538,
}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def admin_only():
    async def predicate(ctx):
        return is_admin(ctx.author.id)

    return commands.check(predicate)


# --- フェス登録 (同時に1つだけ。登録し直すと入れ替わる) ---
STATE_PATH = Path(__file__).with_name("state.json")


def load_festival() -> str | None:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        pid = str(data.get("festival_project_id") or "")
        return pid if pid.isdigit() else None
    except Exception:
        return None


def save_festival(project_id: str | None) -> None:
    if project_id is None:
        STATE_PATH.unlink(missing_ok=True)
    else:
        STATE_PATH.write_text(
            json.dumps({"festival_project_id": project_id}), encoding="utf-8"
        )


def extract_project_id(text: str) -> str | None:
    """URL または ID 文字列からプロジェクトIDを取り出す。

    対応形式: turbowarp.org/<id>[/editor], scratch.mit.edu/projects/<id>,
    素の数字ID。
    """
    text = text.strip()
    if text.isdigit():
        return text
    m = re.search(r"(?:projects/|turbowarp\.org/)(\d+)", text)
    if m:
        return m.group(1)
    m = re.search(r"/(\d{6,})(?:[/?#]|$)", text)
    return m.group(1) if m else None

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!ta ", intents=intents)


def fmt_players() -> str:
    players = sorted(cloud.online_players(), key=lambda r: r.player_id)
    if not players:
        if cloud.parse_failures:
            lengths = ", ".join(str(n) for n in sorted(cloud.seen_lengths))
            return (
                "レコードは届いていますが、デコードできていません"
                f"（失敗 {cloud.parse_failures}件、レコード長: {lengths}）。"
                "\n16.9形式 (246文字) と違う版の可能性があります。"
            )
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
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("このコマンドは管理者専用です。")
        return
    raise error


_tree_synced = False


@bot.event
async def on_ready():
    global _tree_synced
    print(f"Logged in as {bot.user}")
    if not _tree_synced:
        await bot.tree.sync()
        _tree_synced = True
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
    target = (
        f"フェス (プロジェクト {cloud.project_id})"
        if cloud.is_festival
        else f"通常 TACT-AIM (プロジェクト {PROJECT_ID})"
    )
    await ctx.send(
        "**TACT-AIM Bot 状態**\n"
        f"- スクレイピング先: {target}\n"
        f"- クラウド接続: {'接続中' if cloud.connected else '切断 (再接続中)'} "
        f"(接続回数 {cloud.connect_count})\n"
        f"- 受信した更新: {cloud.update_count}件 / 最終受信 {last}\n"
        f"- 追跡中プレイヤー: {cloud.tracked_count()}人 "
        f"(オンライン判定 {len(cloud.online_players())}人)\n"
        f"- 稼働時間: {uptime}秒"
        + (
            f"\n- デコード失敗: {cloud.parse_failures}件 "
            f"(レコード長: {', '.join(str(n) for n in sorted(cloud.seen_lengths))})"
            " ※16.9形式 (246文字) と違う可能性"
            if cloud.parse_failures
            else ""
        )
    )


@bot.command(name="raw")
@admin_only()
async def raw(ctx):
    lines = ["**CLOUD変数サマリ**"]
    for name in CLOUD_VARS + EXTRA_VARS:
        v = cloud.values.get(name)
        if v is None:
            lines.append(f"- {name}: (未受信)")
        else:
            lines.append(f"- {name}: {len(v)}文字, 先頭 {v[:24]}…")
    await ctx.send("\n".join(lines))


@bot.tree.command(
    name="festival",
    description="フェス登録 (管理者専用): スクレイピング先をフェスのプロジェクトへ切り替える",
)
@app_commands.describe(url="フェスプロジェクトのURL (TurboWarp / Scratch) またはプロジェクトID")
async def festival(interaction: discord.Interaction, url: str):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            "このコマンドは管理者専用です。", ephemeral=True
        )
        return
    pid = extract_project_id(url)
    if pid is None:
        await interaction.response.send_message(
            "URLからプロジェクトIDを読み取れませんでした。", ephemeral=True
        )
        return
    if pid == PROJECT_ID:
        await cloud.set_project(PROJECT_ID)
        save_festival(None)
        await interaction.response.send_message(
            "通常の TACT-AIM が指定されたため、フェス登録を解除しました。"
        )
        return
    await cloud.set_project(pid)
    save_festival(pid)
    await interaction.response.send_message(
        f"フェス登録: プロジェクト {pid} に切り替えました（前の登録は置き換え）。"
        "\n解除は /festival_clear。"
    )


@bot.tree.command(
    name="festival_clear",
    description="フェス登録を解除し、通常の TACT-AIM へ戻す (管理者専用)",
)
async def festival_clear(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            "このコマンドは管理者専用です。", ephemeral=True
        )
        return
    await cloud.set_project(PROJECT_ID)
    save_festival(None)
    await interaction.response.send_message(
        f"フェス登録を解除しました。通常の TACT-AIM ({PROJECT_ID}) に戻ります。"
    )


async def main():
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("DISCORD_TOKEN が未設定です (.env を確認)")
    festival_pid = load_festival()
    if festival_pid:
        await cloud.set_project(festival_pid)
        print(f"Festival project restored: {festival_pid}")
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
