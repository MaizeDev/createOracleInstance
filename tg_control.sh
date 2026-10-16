#!/usr/bin/env bash
# Telegram 遥控入口: 加载 config.env 后启动 tg_control.py。
# 平时不用手动跑: 用 ./ctl.sh bot-start 启动、./ctl.sh bot-stop 停止。
# ctl.sh 会自动带上 caffeinate -is 防休眠（注意 caffeinate 会多出一个持有电源断言的
# 子进程，识别进程时要认主进程，见 procs.primaries）。
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
