"""TACT-AIM 独立 Discord Bot (テスト版)。

役割は1つだけ: TurboWarp クラウド (TACT-AIM 16.9) を読み取り専用で
スクレイピングし、Discord で情報を出す。チート検出・執行はしない。

コマンド:
  !ta online      - 現在オンラインのプレイヤー一覧 (全員)
  !ta status      - クラウド接続状態・統計 (全員)
  !ta raw         - CLOUD変数の生サマリ (管理者専用)
  /festival       - フェス登録 (管理者専用・スラッシュ)。登録したプロジェクトへは
                    フェス時間に自動で切り替わる (原作のフェス判定に従う)。
                    フェスは同時に1つだけ。入れるたびに前の登録と入れ替わる。
  /festival_clear - フェス登録を解除し、通常の TACT-AIM へ戻す (管理者専用)
  /map            - プレイヤー配置マップ (PNG、Components V2)
  /tracker        - プレイヤー戦績トラッカー (ページ切替、Components V2)

トークンは環境変数 DISCORD_TOKEN (.env) から読む。
フェス登録は state.json に保存され、再起動しても維持される。
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cloud import CLOUD_VARS, EXTRA_VARS, PROJECT_ID, TactAimCloud
from festival import is_festival
from graph_render import render_trend_png
from history import History, data_dir
from map_data import tiles_for
from map_render import render_map_png
from record import TEAM_COLOR_NAMES

# 履歴はリポジトリ外の永続領域に置く (デプロイで消えないように)。
# 旧配置 (リポジトリ内 history.json) があれば初回だけ引き継ぐ。
_history_path = data_dir() / "history.json"
_legacy_path = Path(__file__).with_name("history.json")
if not _history_path.exists() and _legacy_path.exists():
    try:
        _history_path.write_bytes(_legacy_path.read_bytes())
    except Exception:
        pass
history = History(_history_path)

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


# 登録中のフェスプロジェクト。フェス時間 (原作の式) の間だけ監視先になる
festival_pid: str | None = None


def desired_target() -> str:
    if festival_pid and is_festival():
        return festival_pid
    return PROJECT_ID


async def apply_target() -> None:
    target = desired_target()
    if cloud.project_id != target:
        await cloud.set_project(target)
        print(f"Auto switch: scraping target -> {target}")


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
_activity_started = False


async def _start_activity_server() -> None:
    # 内蔵サーバーは tree.sync() 等の Discord API の完了を待たずに起動する。
    # (2026-11-28 の障害: sync が長時間戻らず、後段のサーバー起動に永遠に
    # 到達しなかった)。失敗してもリトライし、Bot 本体は止めない。
    import activity_server

    for attempt in range(10):
        try:
            await activity_server.start_server(cloud, str(bot.user.id))
            return
        except Exception as e:
            print(
                f"activity server failed to start ({attempt + 1}/10): {e}",
                flush=True,
            )
            await asyncio.sleep(15)


async def _sync_tree() -> None:
    # グローバルコマンドの sync はレート制限 (429 の retry_after) で長時間
    # 戻らないことがあるため、背景でタイムアウト付きで実行する。
    try:
        await asyncio.wait_for(bot.tree.sync(), timeout=120)
        print("tree synced", flush=True)
    except Exception as e:
        print(f"tree sync failed/timed out: {e}", flush=True)


@bot.event
async def on_ready():
    global _tree_synced, _activity_started
    print(f"Logged in as {bot.user}", flush=True)
    if not _activity_started:
        _activity_started = True
        asyncio.create_task(_start_activity_server())
    if not _tree_synced:
        _tree_synced = True
        asyncio.create_task(_sync_tree())
    if not update_presence.is_running():
        update_presence.start()
    if not sample_history.is_running():
        sample_history.start()
    if not auto_switch.is_running():
        auto_switch.start()


@tasks.loop(seconds=30)
async def update_presence():
    n = len(cloud.online_players())
    state = "接続中" if cloud.connected else "再接続中"
    await bot.change_presence(
        activity=discord.Game(name=f"TACT-AIM {n}人オンライン ({state})")
    )


@tasks.loop(seconds=60)
async def sample_history():
    history.add(len(cloud.online_players()))


@tasks.loop(seconds=60)
async def auto_switch():
    await apply_target()


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
    fest_line = ""
    if festival_pid:
        fest_line = (
            f"\n- フェス登録: {festival_pid} / 自動切替オン "
            f"({'フェス時間中' if is_festival() else '通常時間帯'})"
        )
    await ctx.send(
        "**TACT-AIM Bot 状態**\n"
        f"- スクレイピング先: {target}{fest_line}\n"
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
    description="フェス登録 (管理者専用): フェス時間に自動で切り替わるよう登録する",
)
@app_commands.describe(url="フェスプロジェクトのURL (TurboWarp / Scratch) またはプロジェクトID")
async def festival(interaction: discord.Interaction, url: str):
    global festival_pid
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
        festival_pid = None
        save_festival(None)
        await apply_target()
        await interaction.response.send_message(
            "通常の TACT-AIM が指定されたため、フェス登録を解除しました。"
        )
        return
    festival_pid = pid
    save_festival(pid)
    await apply_target()
    state = "フェス時間中のため切り替えました" if is_festival() else "通常時間帯のため、フェス時間に自動で切り替わります"
    await interaction.response.send_message(
        f"フェス登録: プロジェクト {pid}（前の登録は置き換え）。{state}。"
        "\n解除は /festival_clear。"
    )


@bot.tree.command(
    name="festival_clear",
    description="フェス登録を解除し、通常の TACT-AIM へ戻す (管理者専用)",
)
async def festival_clear(interaction: discord.Interaction):
    global festival_pid
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            "このコマンドは管理者専用です。", ephemeral=True
        )
        return
    festival_pid = None
    save_festival(None)
    await apply_target()
    await interaction.response.send_message(
        f"フェス登録を解除しました。通常の TACT-AIM ({PROJECT_ID}) に戻ります。"
    )


# --- /map と /tracker (スラッシュ、Components V2) ---

def _sorted_tracked():
    online_ids = {r.player_id for r in cloud.online_players()}
    recs = sorted(
        cloud.players.values(),
        key=lambda r: (
            r.player_id not in online_ids,
            -cloud.stats.get(r.player_id).kills,
            -cloud.stats.get(r.player_id).damage_dealt,
            r.player_id,
        ),
    )
    return recs, online_ids


def _player_block(r, online: bool) -> str:
    st = cloud.stats.get(r.player_id)
    name = cloud.full_name(r.player_id)
    title = f"**{name}** (ID {r.player_id})" if name else f"**ID {r.player_id}**"
    team = (
        f"Team: {TEAM_COLOR_NAMES.get(r.team_color, str(r.team_color))}"
        if r.team_color
        else f"チーム {r.team_id}"
    )
    lines = [
        f"{title} {r.character}" + ("" if online else " (オフライン)"),
        f"HP {r.hp}/{r.max_hp} | 座標 ({r.x}, {r.y}) | {team}",
        f"攻撃 x{r.attack:.2f} | スキルpt {r.skill_point} | 速度 ({r.vel_x}, {r.vel_y})"
        f" | 発射 {r.active_shots}件",
        f"キル {st.kills} / デス {st.deaths} (K/D {st.kd:.2f})"
        f" | 与ダメ {st.damage_dealt} / 被ダメ {st.damage_taken}"
        f" | 命中 {st.hits_landed} / 被弾 {st.hits_taken}",
    ]
    if r.hits:
        last = r.hits[-1]
        lines.append(
            f"直近の被弾: {last.attacker_id}から{last.damage}"
            + (" (キル)" if last.is_kill else "")
        )
    return "\n".join(lines)


class TrackerView(discord.ui.LayoutView):
    PER_PAGE = 4

    def __init__(self, author_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.page = 0
        self._build()

    def _pages(self):
        recs, online_ids = _sorted_tracked()
        chunks = [
            recs[i : i + self.PER_PAGE] for i in range(0, len(recs), self.PER_PAGE)
        ]
        return chunks or [[]], online_ids

    def _build(self):
        self.clear_items()
        chunks, online_ids = self._pages()
        self.page = max(0, min(self.page, len(chunks) - 1))
        items = []
        page_recs = chunks[self.page]
        if page_recs:
            for r in page_recs:
                items.append(
                    discord.ui.TextDisplay(_player_block(r, r.player_id in online_ids))
                )
        else:
            items.append(discord.ui.TextDisplay("追跡中のプレイヤーはいません。"))
        prev_btn = discord.ui.Button(
            label="◀", style=discord.ButtonStyle.secondary,
            custom_id="tracker_prev", disabled=self.page <= 0,
        )
        prev_btn.callback = self._prev
        page_btn = discord.ui.Button(
            label=f"{self.page + 1} / {len(chunks)}",
            style=discord.ButtonStyle.secondary, disabled=True,
        )
        refresh_btn = discord.ui.Button(
            label="更新", style=discord.ButtonStyle.secondary,
            custom_id="tracker_refresh",
        )
        refresh_btn.callback = self._refresh
        next_btn = discord.ui.Button(
            label="▶", style=discord.ButtonStyle.secondary,
            custom_id="tracker_next", disabled=self.page >= len(chunks) - 1,
        )
        next_btn.callback = self._next
        items.append(discord.ui.ActionRow(prev_btn, page_btn, refresh_btn, next_btn))
        self.add_item(discord.ui.Container(*items, accent_colour=0x2F81F7))

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "このパネルはコマンド実行者だけが操作できます。", ephemeral=True
            )
            return False
        return True

    async def _prev(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.page -= 1
        self._build()
        await interaction.response.edit_message(view=self)

    async def _next(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.page += 1
        self._build()
        await interaction.response.edit_message(view=self)

    async def _refresh(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self._build()
        await interaction.response.edit_message(view=self)


@bot.tree.command(name="tracker", description="プレイヤーの戦績トラッカー")
async def tracker(interaction: discord.Interaction):
    await interaction.response.send_message(view=TrackerView(interaction.user.id))


class MapView(discord.ui.LayoutView):
    """マップ表示 (更新ボタン付き)。操作は実行者のみ。"""

    def __init__(self, author_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.player_count = 0
        self._build()

    def _build(self):
        self.clear_items()
        refresh_btn = discord.ui.Button(
            label="更新", style=discord.ButtonStyle.secondary,
            custom_id="map_refresh",
        )
        refresh_btn.callback = self._refresh
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(f"**オンライン: {self.player_count}人**"),
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(
                        media=discord.UnfurledMediaItem(url="attachment://map.png")
                    )
                ),
                discord.ui.ActionRow(refresh_btn),
                accent_colour=0x2F81F7,
            )
        )

    async def render(self) -> discord.File:
        players = sorted(cloud.online_players(), key=lambda r: r.player_id)
        self.player_count = len(players)
        tiles = tiles_for(cloud.project_id)
        names = {r.player_id: cloud.full_name(r.player_id) for r in players}
        png = await asyncio.to_thread(
            render_map_png, players, tiles, cloud.project_id, names
        )
        self._build()
        return discord.File(io.BytesIO(png), filename="map.png")

    async def _refresh(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "このパネルはコマンド実行者だけが操作できます。", ephemeral=True
            )
            return
        await interaction.response.defer()
        file = await self.render()
        await interaction.edit_original_response(attachments=[file], view=self)


@bot.tree.command(name="map", description="プレイヤー配置マップ")
async def map_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    view = MapView(interaction.user.id)
    file = await view.render()
    await interaction.followup.send(view=view, file=file)


@bot.tree.command(name="watch", description="アクティビティでリアルタイム観戦")
async def watch_cmd(interaction: discord.Interaction):
    if interaction.channel is None:
        await interaction.response.send_message(
            "チャンネル内で実行してください。", ephemeral=True
        )
        return
    try:
        invite = await interaction.channel.create_invite(
            target_type=discord.InviteTarget.embedded_application,
            target_application_id=bot.user.id,
            reason="TACT-AIM リアルタイム観戦",
        )
    except (discord.Forbidden, discord.HTTPException) as e:
        await interaction.response.send_message(
            f"招待を作成できませんでした ({e})。Developer Portal で "
            "Activities の有効化と URL マッピングが必要です。",
            ephemeral=True,
        )
        return
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(
                f"**リアルタイム観戦**\n[アクティビティを開く]({invite.url})"
            ),
            accent_colour=0x2F81F7,
        )
    )
    await interaction.response.send_message(view=view)


@bot.tree.command(name="player", description="プレイヤー数の推移グラフ")
async def player_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    points = list(history.points)
    current = len(cloud.online_players())
    if not points or points[-1][1] != current:
        points.append((time.time(), current))
    png = await asyncio.to_thread(render_trend_png, points)
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"**オンライン: {current}人**"),
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(
                    media=discord.UnfurledMediaItem(url="attachment://players.png")
                )
            ),
            accent_colour=0x2F81F7,
        )
    )
    await interaction.followup.send(
        view=view, file=discord.File(io.BytesIO(png), filename="players.png")
    )


async def main():
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("DISCORD_TOKEN が未設定です (.env を確認)")
    global festival_pid
    festival_pid = load_festival()
    if festival_pid:
        print(f"Festival project registered: {festival_pid}")
    await apply_target()
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
