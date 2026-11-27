#!/usr/bin/env bash
# TACT-AIM-BOT 自動デプロイ: GitHub の main をポーリングし、新しい
# コミットがあれば pull して Bot を再起動する。AGO BOT の自動デプロイと同方式。
# systemd timer (tact-aim-bot-deploy.timer) から定期実行される想定。
set -u
cd "$(dirname "$0")"

SERVICE="${TACTAIM_SERVICE:-tact-aim-bot}"
RESTART_CMD="${TACTAIM_RESTART_CMD:-sudo -n systemctl restart ${SERVICE}}"

git fetch -q origin main || { echo "deploy: fetch failed"; exit 1; }
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main)"
if [ "$LOCAL" = "$REMOTE" ]; then
  exit 0
fi

CHANGED="$(git diff --name-only "$LOCAL" "$REMOTE")"
if ! git pull -q --ff-only origin main; then
  echo "deploy: pull failed (local changes on Pi?)"
  exit 1
fi
echo "deploy: $LOCAL -> $REMOTE"

# 依存が変わった時だけ入れ直す
if echo "$CHANGED" | grep -qx "requirements.txt"; then
  venv/bin/pip install -q -r requirements.txt || { echo "deploy: pip failed"; exit 1; }
  echo "deploy: requirements updated"
fi

$RESTART_CMD || { echo "deploy: restart failed (sudoers 未設定?)"; exit 1; }
echo "deploy: restarted $SERVICE"
