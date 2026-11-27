#!/usr/bin/env bash
# 自動更新 (update.sh + cron) の初回セットアップ。Pi 上で1回だけ実行:
#   bash setup-cron.sh
# やること:
#   1. luna の crontab に update.sh の毎分実行を追加 (重複登録しない)
#   2. luna が tact-aim-bot の再起動だけをパスワードなしで実行できる
#      sudoers を追加 (update.sh の再起動に必要、sudo で1回だけ聞かれる)
#   3. 旧方式の systemd timer があれば無効化 (二重ポーリング防止)
set -eu
cd "$(dirname "$0")"

CRON_LINE='* * * * * /home/luna/TACT-AIM-BOT/update.sh >> /home/luna/TACT-AIM-BOT/update.log 2>&1'
if crontab -l 2>/dev/null | grep -qF 'TACT-AIM-BOT/update.sh'; then
  echo "cron: already registered"
else
  (crontab -l 2>/dev/null || true; echo "$CRON_LINE") | crontab -
  echo "cron: registered (every minute)"
fi

if [ ! -f /etc/sudoers.d/tact-aim-bot ]; then
  printf 'luna ALL=(root) NOPASSWD: /usr/bin/systemctl restart tact-aim-bot\n' \
    | sudo tee /etc/sudoers.d/tact-aim-bot >/dev/null
  sudo chmod 440 /etc/sudoers.d/tact-aim-bot
  sudo visudo -cf /etc/sudoers.d/tact-aim-bot
  echo "sudoers: installed"
else
  echo "sudoers: already installed"
fi

if systemctl list-unit-files tact-aim-bot-deploy.timer >/dev/null 2>&1 \
   && systemctl is-enabled tact-aim-bot-deploy.timer >/dev/null 2>&1; then
  sudo systemctl disable --now tact-aim-bot-deploy.timer
  echo "old systemd timer: disabled"
fi

echo "auto update: ready (update.sh + cron, every minute)"
