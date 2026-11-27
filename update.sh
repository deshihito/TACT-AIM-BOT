#!/usr/bin/env bash
# TACT-AIM-BOT 自動更新: cron から毎分呼ばれる。
# GitHub の main に新しいコミットがあれば pull して Bot を再起動する。
# 更新がなければ何もしない (ログも残さない)。
exec 9>/tmp/tact-aim-bot-update.lock
flock -n 9 || exit 0
set -u
cd "$(dirname "$0")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

git fetch -q origin main || { log "update: fetch failed"; exit 1; }
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main)"
[ "$LOCAL" = "$REMOTE" ] && exit 0

CHANGED="$(git diff --name-only "$LOCAL" "$REMOTE")"
if ! git pull -q --ff-only origin main; then
  log "update: pull failed (local changes on Pi?)"
  exit 1
fi
log "update: $LOCAL -> $REMOTE"

if echo "$CHANGED" | grep -qx "requirements.txt"; then
  venv/bin/pip install -q -r requirements.txt || { log "update: pip failed"; exit 1; }
  log "update: requirements updated"
fi

sudo -n systemctl restart tact-aim-bot || { log "update: restart failed (sudoers?)"; exit 1; }
log "update: restarted tact-aim-bot"
