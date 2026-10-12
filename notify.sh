#!/usr/bin/env bash
# 可选通知。用法: ./notify.sh "消息"
# 未配置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 时静默跳过；发送失败也不影响主流程。
msg="${1:-}"

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  curl -fsS -m 15 \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${msg}" >/dev/null 2>&1 || true
fi
exit 0
