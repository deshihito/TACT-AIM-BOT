"""Discordなしのスクレイピング単体テスト。

使い方: python scrape_test.py [観測秒数=20]
クラウドに接続し、観測時間のあいだにデコードできたプレイヤーを表示する。
"""
import asyncio
import sys

from cloud import TactAimCloud


async def main(seconds: float) -> None:
    cloud = TactAimCloud()
    task = asyncio.create_task(cloud.run_forever())
    print(f"{seconds:.0f}秒観測します…")
    await asyncio.sleep(seconds)
    task.cancel()
    print(f"接続={'OK' if cloud.connect_count else 'NG'} / 更新 {cloud.update_count}件")
    print(f"CLOUD変数: {len(cloud.values)}件受信")
    players = cloud.online_players()
    print(f"デコードできたプレイヤー: {len(players)}人 (追跡総数 {cloud.tracked_count()})")
    for r in sorted(players, key=lambda r: r.player_id):
        print(
            f"- ID {r.player_id} seq={r.seq} ver={r.version} {r.character} "
            f"HP {r.hp}/{r.max_hp} 座標({r.x},{r.y}) チーム {r.team_id} "
            f"攻撃係数 {r.attack} 名前片 '{r.name_chunk}' 発射 {r.active_shots}件 "
            f"被弾 {len(r.hits)}件"
        )


if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    asyncio.run(main(secs))
