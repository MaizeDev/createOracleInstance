#!/usr/bin/env bash
# 一键启停入口，可以在任意目录运行。用法: ./ctl.sh up | down | restart | status | log
exec python3 "$(cd "$(dirname "$0")" && pwd)/ctl.py" "$@"
