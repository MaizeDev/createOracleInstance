#!/usr/bin/env python3
"""一键启停命令行。用法见 USAGE。"""
import os
import sys

import runner

USAGE = """用法: ./ctl.sh <命令>

  up         一键启动: 抢机脚本 + Telegram 遥控
  down       一键停止: 抢机脚本 + Telegram 遥控
  restart    先 down 再 up
  status     查看两者状态
  start      只启动抢机脚本
  stop       只停止抢机脚本
  bot-start  只启动 Telegram 遥控
  bot-stop   只停止 Telegram 遥控
  log        实时查看抢机日志 (Ctrl+C 退出，不影响脚本)
  botlog     实时查看遥控日志"""


def follow(path):
    if not os.path.exists(path):
        open(path, "a").close()
    os.execvp("tail", ["tail", "-n", "30", "-f", path])


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    retry, bot = runner.RETRY, runner.BOT

    if cmd == "up":
        print(retry.start())
        print(bot.start())
    elif cmd == "down":
        # 先停抢机再停遥控，避免遥控在中间又把抢机拉起来
        print(retry.stop())
        print(bot.stop())
    elif cmd == "restart":
        print(retry.stop())
        print(bot.stop())
        print(retry.start())
        print(bot.start())
    elif cmd == "status":
        print(retry.status())
        print(bot.status())
    elif cmd == "start":
        print(retry.start())
    elif cmd == "stop":
        print(retry.stop())
    elif cmd == "bot-start":
        print(bot.start())
    elif cmd == "bot-stop":
        print(bot.stop())
    elif cmd == "log":
        follow(retry.log_file)
    elif cmd == "botlog":
        follow(bot.log_file)
    else:
        print(USAGE)
        return 0 if cmd in ("", "help", "-h", "--help") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
