#!/usr/bin/env bash
# 一键启停入口，可以在任意目录运行。用法: ./ctl.sh up | down | restart | status | start | stop | bot-start | bot-stop | bot-restart | log | botlog
# 完整说明见 ./ctl.sh --help
exec python3 "$(cd "$(dirname "$0")" && pwd)/ctl.py" "$@"
