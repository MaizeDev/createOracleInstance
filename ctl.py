#!/usr/bin/env python3
"""一键启停命令行。用法见 USAGE。

终端的排版在这里: 分隔线 + 缩进层级 + 颜色(只在终端里上色)。
Telegram 的回复由 runner.describe() 渲染，两套风格互不影响。
"""
import os
import shutil
import sys

import runner

USAGE = """coi / ctl.sh —— 抢机脚本与 Telegram 遥控的启停入口

用法: coi <命令>          (等价于 ./ctl.sh <命令>)

启停:
  up          一键启动: 抢机脚本 + Telegram 遥控
  down        一键停止: 抢机脚本 + Telegram 遥控
  restart     先 down 再 up
  status      查看两者状态 (抢机脚本会附带最近几行日志)

只操作其中一个:
  start       只启动抢机脚本
  stop        只停止抢机脚本
  bot-start   只启动 Telegram 遥控 (不动抢机脚本)
  bot-stop    只停止 Telegram 遥控
  bot-restart 重启 Telegram 遥控 (= bot-stop + bot-start，改完代码后用它载入新代码)

看日志:
  log         实时查看抢机日志 (Ctrl+C 退出，不影响脚本)
  botlog      实时查看遥控日志 (Ctrl+C 退出，不影响脚本)

其它:
  -h, --help  显示这份帮助
  --no-color  关闭颜色 (默认只在终端里上色；也可以用 NO_COLOR=1)"""

RESET = "\033[0m"
BOLD = "\033[1m"
LEVEL_COLOR = {"ok": "\033[32m", "info": "\033[2m", "warn": "\033[33m", "error": "\033[31m"}

COLOR = False  # 由 main() 按"是否终端 + 参数"决定


def follow(path):
    if not os.path.exists(path):
        open(path, "a").close()
    os.execvp("tail", ["tail", "-n", "30", "-f", path])


def _paint(text, code):
    return "%s%s%s" % (code, text, RESET) if COLOR and code else text


def _dwidth(text):
    return runner.display_width(text)


def _clip(text, limit):
    return runner.clip(text, limit)


def _terminal_width():
    return max(40, min(shutil.get_terminal_size((80, 24)).columns, 110))


def show_status(info):
    """status: 一个服务一个区块，分隔线 + 缩进层级。"""
    width = _terminal_width()
    head = "  ── %s " % info["label"]
    print(_paint(head + "─" * max(3, width - _dwidth(head)), BOLD))
    if info["running"]:
        line = "运行中 · pid %s · 已运行 %s" % (
            ",".join(str(p) for p in info["pids"]), info["etime"])
        print("    " + _paint(_clip(line, width - 4), LEVEL_COLOR["ok"]))
        if info["log"]:
            print("    " + _paint("最近日志:", BOLD))
            for line in info["log"].splitlines():
                print("      " + _clip(line, width - 8))
    else:
        note = "（%s）" % info["note"] if info["note"] else ""
        print("    " + _paint(_clip("未运行" + note, width - 4), LEVEL_COLOR["info"]))
    print()


def show_actions(results):
    """up / down / restart / start / stop: 一个服务一行，标签对齐，可带本次输出。"""
    width = _terminal_width()
    pad = max(_dwidth(r["label"]) for r in results)
    for r in results:
        icon = runner.LEVEL_ICON.get(r["level"], "·")
        gap = " " * (pad - _dwidth(r["label"]))
        line = "%s %s%s  %s" % (icon, r["label"], gap, r["message"])
        print("  " + _paint(_clip(line, width - 2), LEVEL_COLOR.get(r["level"], "")))
        for out_line in (r.get("output") or "").strip().splitlines():
            print("      " + _clip(out_line, width - 8))


def main(argv):
    global COLOR
    raw = argv[1:]
    flags = [a for a in raw if a.startswith("--")]
    args = [a for a in raw if not a.startswith("--")]
    COLOR = ("--color" in flags
             or (sys.stdout.isatty() and "--no-color" not in flags
                 and not os.environ.get("NO_COLOR")))
    cmd = args[0] if args else ""
    retry, bot = runner.RETRY, runner.BOT

    if cmd == "up":
        show_actions([retry.start(), bot.start()])
    elif cmd == "down":
        show_actions([retry.stop(), bot.stop()])
    elif cmd == "restart":
        show_actions([retry.stop(), bot.stop()])
        print()
        show_actions([retry.start(), bot.start()])
    elif cmd == "status":
        print()
        show_status(retry.status())
        show_status(bot.status())
    elif cmd == "bot-restart":
        # 只重启遥控: 改完代码后用这条载入新代码，抢机脚本不受影响
        show_actions([bot.stop()])
        print()
        show_actions([bot.start()])
    elif cmd in ("start", "stop", "bot-start", "bot-stop"):
        svc = bot if cmd.startswith("bot-") else retry
        result = svc.start() if cmd.endswith("start") else svc.stop()
        show_actions([result])
    elif cmd == "log":
        follow(retry.log_file)
    elif cmd == "botlog":
        follow(bot.log_file)
    else:
        print(USAGE)
        return 0 if cmd in ("", "help", "-h", "--help") else 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        # 例如 ps 读不到进程列表: 给一句人话，而不是一屏栈
        print("出错了: %s" % (e,), file=sys.stderr)
        sys.exit(1)
