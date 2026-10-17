#!/usr/bin/env bash
# 可选通知。用法: ./notify.sh "消息"
# 未配置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 时静默跳过；发送失败也不影响主流程。
msg="${1:-}"

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  # token 在 URL 里，而 URL 放在 curl 的命令行参数中会被 `ps` 看到(runner/procs 自己就是靠 ps 工作的)。
  # 所以 URL 改走 stdin 的 curl 配置(-K -)，printf 是 bash 内建命令，不会产生带 token 的进程。
  # chat_id 和消息不是机密，仍走命令行。
  printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$TELEGRAM_BOT_TOKEN" |
    curl -fsS -m 15 -K - \
      --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
      --data-urlencode "text=${msg}" >/dev/null 2>&1 || true
fi
exit 0
