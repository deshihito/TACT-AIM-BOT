# TACT-AIM Bot (テスト版)

TACT-AIM (uk-400、Scratch project 831274817) の TurboWarp クラウドを
読み取り専用でスクレイピングし、Discord で情報を出す独立 Bot。
AGO BOT とは無関係の別プロセスとして Raspberry Pi に常駐させる想定。

当面の範囲: **スクレイピングして情報を出すだけ**。
チート検出・BAN 等の執行はしない (書き込みもしない)。

## 出せる情報

- `!ta online` — オンライン中のプレイヤー (ID / キャラ / HP / 座標 / チーム / 名前片 / 直近の被弾)
- `!ta status` — クラウド接続状態、受信更新数、追跡プレイヤー数
- `!ta raw` — CLOUD1〜10・Member1・Chat1〜5 の生サマリ (**管理者専用**)
- `/festival url:<URL>` — フェス登録 (**管理者専用**・スラッシュコマンド)。
  スクレイピング先をそのプロジェクトへ切り替える。フェスは同時に1つだけで、
  登録し直すと前の登録と入れ替わる。登録は state.json に保存され再起動後も維持
- `/festival_clear` — フェス登録を解除し、通常の TACT-AIM へ戻す (**管理者専用**)

管理者は bot.py の `ADMIN_IDS` (DiscordユーザーID 3件) で判定する。
管理者以外が管理者専用コマンドを実行すると「このコマンドは管理者専用です。」と返す。
- プレゼンスにオンライン人数を 30 秒ごとに反映

## 仕組み

- `cloud.py` — TurboWarp クラウド (wss://clouddata.turbowarp.org) へ
  handshake して set を購読。切断時は自動再接続。
  各プレイヤーは自分のレコードを CLOUD1〜10 のランダムな1本へ書く方式の
  ため、ID 単位で蓄積し、seq (World_timer) の鮮度で最新を保つ。
  オンライン判定は最終観測から 45 秒以内 (仮置き)。
- `record.py` — 246 文字固定長レコードのパーサ。形式は
  TACT-AIM-distilled の Load cloud data 手順 (16.9) で確定したもの。
  名前は 3 文字チャンクで回転送信されるため、表示はチャンク単位。

## セットアップ

### 1. Discord Bot アカウント (ユーザーの作業)

1. Discord Developer Portal で New Application → 名前「TACT-AIM」
2. Bot タブでトークンを発行
3. Privileged Gateway Intents で **MESSAGE CONTENT INTENT** を ON
4. OAuth2 → URL Generator で scope=bot、権限は Send Messages /
   Read Message History 程度で招待 URL を作り、テストサーバーへ招待

トークンはチャットに貼らず、デプロイ先の `.env` に直接書くこと。

### 2. ローカルテスト (Discord 不要)

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python scrape_test.py 25   # 25秒観測してデコード結果を表示
```

### 3. Pi 常駐

```bash
# Pi 上 (SSH / ローカルターミナルで実行。トークンが発言として残る
# Discord 擬似ターミナルでは実行しないこと)
cd ~ && git clone https://github.com/deshihito/TACT-AIM-BOT.git
cd ~/TACT-AIM-BOT
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python scrape_test.py 15   # Pi から直接クラウドへ繋がるか先に確認
read -rsp 'Bot token: ' T; echo
printf 'DISCORD_TOKEN=%s\n' "$T" > .env; unset T; chmod 600 .env
sudo cp tact-aim-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tact-aim-bot
systemctl --no-pager status tact-aim-bot | head -12
```

## 自動更新

GitHub の main へ push されると、Pi の cron が毎分 `update.sh` を呼び、
新しいコミットがある時だけ pull + Bot再起動する。初回だけ Pi 上で:

```bash
cd ~/TACT-AIM-BOT && bash setup-cron.sh
```

crontab への登録、luna が `tact-aim-bot` の再起動だけをパスワードなしで
実行できる sudoers の追加、旧 systemd timer 方式があればその無効化まで行う。
以後の更新は push するだけで反映される。更新ログは `update.log` に残る
(更新がない分は無言)。

## 未決事項

- オンライン判定の窓 (現在 45 秒仮置き) — 実プレイで調整
- Chat1〜5 / Member1 のデコード (現在は生サマリのみ)
- 名前チャンクの連結 (回転チャンクを名前全体へ組み立てる処理)
