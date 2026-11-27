#!/usr/bin/env bash
# 自動デプロイの初回セットアップ (Pi 上で1回だけ、sudo で実行する)。
#   sudo bash install-autodeploy.sh
# やること:
#   1. deploy 用の systemd service/timer をインストールして有効化
#   2. luna が tact-aim-bot の再起動だけをパスワードなしで実行できる
#      sudoers を追加 (deploy.sh の再起動に必要)
set -eu
cd "$(dirname "$0")"

install -m 644 tact-aim-bot-deploy.service /etc/systemd/system/
install -m 644 tact-aim-bot-deploy.timer /etc/systemd/system/
printf 'luna ALL=(root) NOPASSWD: /usr/bin/systemctl restart tact-aim-bot\n' \
  > /etc/sudoers.d/tact-aim-bot
chmod 440 /etc/sudoers.d/tact-aim-bot
visudo -cf /etc/sudoers.d/tact-aim-bot

systemctl daemon-reload
systemctl enable --now tact-aim-bot-deploy.timer
systemctl list-timers tact-aim-bot-deploy.timer --no-pager | head -5
echo "auto deploy: enabled (poll every 2 min)"
