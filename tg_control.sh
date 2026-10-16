#!/usr/bin/env bash
# Telegram 遥控入口: 加载 config.env 后启动 tg_control.py。
# 用法: nohup caffeinate -is ./tg_control.sh < /dev/null >> tg_control.log 2>&1 &
set -u

# 与 retry.sh 一样，固定标准输入，避免关闭终端后子进程因 stdin 失效而崩溃
exec < /dev/null

# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"
load_config

: "${TELEGRAM_BOT_TOKEN:?请在 config.env 填写 TELEGRAM_BOT_TOKEN}"
: "${TELEGRAM_CHAT_ID:?请在 config.env 填写 TELEGRAM_CHAT_ID (你自己的数字用户 ID)}"
command -v python3 >/dev/null 2>&1 || die "找不到 python3"

exec python3 "$DIR/tg_control.py"
