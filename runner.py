"""启动 / 停止 / 查询本项目的两个后台服务: 抢机脚本 (retry.sh) 和 Telegram 遥控 (tg_control)。

命令行 (ctl.sh) 和 Telegram 机器人共用这一份逻辑，所以两边行为完全一致。
这里只会执行本目录下固定的脚本，函数不接收任何外部文本作为命令参数。

start / stop / status 返回的是结构化结果(dict)，不是给人看的字符串:
  终端由 ctl.py 渲染成分隔线+颜色的区块，Telegram 由 describe() 渲染成带 emoji 的纯文本。
"""
import os
import re
import shutil
import subprocess
import time
import unicodedata

import procs

DIR = procs.DIR

# Telegram 回复里每行日志的目标显示宽度: 手机聊天窗口大约只有 40 列，
# 不截断就会自动折行、看起来乱。终端不用这个值(按终端宽度渲染)。
# 手机上还是折行就调小，想多看点内容就调大(在 config.env 里加 TG_LOG_WIDTH=...)。
TG_LOG_WIDTH = int(os.environ.get("TG_LOG_WIDTH", "40"))
# retry.log 的时间戳 [2026-10-16 20:31:07] → [20:31:07]，一行省 11 列
_STAMP = re.compile(r"^\[\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2})\] ")
# 聊天窗口窄，顺手把最常见的啰嗦日志压短。只影响 Telegram 回复，retry.log 原文不动
_COMPACT = (
    # 尝试创建 qdii-arm (1 OCPU / 6GB, 启动卷 50GB) → 尝试创建 qdii-arm（1C/6GB）
    (re.compile(r"\s*\((\d+)\s*OCPU\s*/\s*(\d+)\s*GB,\s*启动卷\s*\d+\s*GB\)"), r"（\1C/\2GB）"),
    # 开始重试: 间隔约 90s (+随机 1~30s)，最多 0 次 (0=不限) → 开始重试: 间隔 90s，不限次数
    (re.compile(r"间隔约 (\d+)s"), r"间隔 \1s"),
    (re.compile(r"\s*\(\+随机 1~\d+s\)"), ""),
    (re.compile(r"，最多 0 次 \(0=不限\)"), "，不限次数"),
    (re.compile(r"，最多 (\d+) 次 \(0=不限\)"), r"，最多 \1 次"),
    # 第 3 次: 未知错误 (连续 2/5) → 第 3 次: 未知错误 (2/5)
    (re.compile(r"\(连续 (\d+/\d+)\)"), r"(\1)"),
)


def _tail(path, n=3):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4096))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return "(读不到日志)"
    return "\n".join(lines[-n:])


def _tail_since(path, offset, n=3, limit=2000):
    """只读 offset 之后新增的日志，取最后 n 行。

    启动失败时用它，避免把日志里历史的失败当成本次启动的问题。
    """
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(65536)
    except OSError:
        return ""
    text = data.decode("utf-8", "replace").strip()
    if not text:
        return ""
    return "\n".join(text.splitlines()[-n:])[-limit:]


# 结果等级: 终端里用符号+颜色(见 ctl.py)，Telegram 回复里用 emoji
LEVEL_ICON = {"ok": "✓", "info": "·", "warn": "!", "error": "✗"}
LEVEL_EMOJI = {"ok": "✅", "info": "ℹ️", "warn": "⚠️", "error": "❌"}


def display_width(text):
    """终端/聊天窗口里的显示宽度: 中文和全角字符算 2 列。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def clip(text, limit):
    """按显示宽度截断，超出部分用 … 收尾(结果宽度不超过 limit)。"""
    if display_width(text) <= limit:
        return text
    out, used = "", 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if used + w > limit - 1:
            break
        out += ch
        used += w
    return out + "…"


def _compact(line):
    """把常见的啰嗦日志压短(见 _COMPACT)，只用于聊天回复。"""
    for pattern, repl in _COMPACT:
        line = pattern.sub(repl, line)
    return line


def _short_log_line(line):
    """Telegram 用的日志行: 压掉啰嗦部分、时间戳只留时分秒，再截到放得下的宽度。"""
    return clip(_compact(_STAMP.sub(r"[\1] ", line)), TG_LOG_WIDTH)


def describe(result):
    """把 start / stop / status 返回的结构化结果转成 Telegram 回复用的纯文本(带 emoji)。

    终端那套分隔线/颜色在 ctl.py 里，两边共用同一份数据，互不影响。
    """
    if result.get("kind") == "status":
        return _describe_status(result)
    icon = result.get("icon") or LEVEL_EMOJI.get(result.get("level"), "ℹ️")
    lines = ["%s %s %s" % (icon, result["label"], result["message"])]
    out = (result.get("output") or "").strip()
    if out:
        lines += ["", "本次输出:"]
        lines += [_short_log_line(ln) for ln in out.splitlines()]
    return "\n".join(lines)


def _describe_status(info):
    if not info["running"]:
        line = "⚪️ %s 未运行" % info["label"]
        if info.get("note"):
            line += "\n（%s）" % info["note"]
        return line
    lines = [
        "🟢 %s 运行中" % info["label"],
        "pid %s · 已运行 %s" % (",".join(str(p) for p in info["pids"]), info["etime"]),
    ]
    if info.get("log"):
        lines += ["", "📄 最近日志"]
        lines += [_short_log_line(ln) for ln in info["log"].splitlines()]
    return "\n".join(lines)


class Service(object):
    def __init__(self, label, patterns, script, log_file,
                 lock_dir=None, kill_children=False, show_log=False):
        self.label = label
        self.patterns = patterns              # [("shell"|"python", 脚本名)]
        self.script = script                  # 要启动的 shell 脚本的绝对路径
        self.script_name = os.path.basename(script)
        self.log_file = log_file
        self.lock_dir = lock_dir
        self.kill_children = kill_children    # 停止时是否连同子进程一起终止
        self.show_log = show_log
        self._started = []                    # 本进程启动的子进程，用来回收僵尸进程

    def _reap(self):
        self._started = [p for p in self._started if p.poll() is None]

    def _matched(self, rows, primary=True):
        # primary=True: 一个服务只算一个 pid —— caffeinate 会派生一个命令行里仍带脚本名的
        #   子进程，先去掉它，免得服务把自己算成"另一个实例"(见 procs.primaries)。
        #   注意必须先去掉派生子进程、再排除自己/祖先：否则自己的 pid 被剔掉后，就认不出
        #   "这个 caffeinate 子进程的父进程是我"了。
        # primary=False: 返回全部匹配进程，停止时用它把 caffeinate 子进程也一起收掉。
        found = procs.find(self.patterns, rows)
        if primary:
            found = procs.primaries(found)
        protected = procs.self_and_ancestors(rows)
        return [r for r in found if r["pid"] not in protected]

    def pids(self):
        self._reap()
        return [r["pid"] for r in self._matched(procs.snapshot())]

    def _clear_lock(self):
        if self.lock_dir and os.path.isdir(self.lock_dir):
            try:
                os.rmdir(self.lock_dir)
                return True
            except OSError:
                pass
        return False

    def _result(self, level, message, output="", icon=None):
        """start / stop 的结构化结果: 终端和 Telegram 各自渲染(见 describe)。

        icon 只在 Telegram 回复里用得上(比如"停止"用 🛑 比 ✅ 更直观)，不填就按 level 取。
        """
        return {"kind": "action", "label": self.label, "level": level,
                "message": message, "output": output, "icon": icon}

    def start(self):
        self._reap()
        rows = procs.snapshot()
        if self._matched(rows):
            return self._result("info", "已经在运行，无需重复启动")

        if self.lock_dir and os.path.isdir(self.lock_dir):
            others = procs.mentions(self.script_name, rows)
            if others:
                return self._result(
                    "warn", "检测到别处有同名脚本在运行 (pid %s)，为避免冲突没有启动，请先确认"
                    % ",".join(map(str, others)))
            self._clear_lock()  # 没有任何进程持有它，说明是残留的锁，自动清理

        cmd = ["bash", self.script]
        if shutil.which("caffeinate"):  # macOS: 防止休眠
            cmd = ["caffeinate", "-is"] + cmd

        try:
            log_size = os.path.getsize(self.log_file)  # 记住启动前的日志长度
        except OSError:
            log_size = 0
        with open(self.log_file, "ab") as log:
            # stdin 固定为 /dev/null: 关闭终端后 oci 不会因标准输入失效而崩溃；
            # start_new_session: 独立会话，不受启动它的终端影响
            proc = subprocess.Popen(
                cmd, cwd=DIR, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        self._started.append(proc)

        time.sleep(3)
        code = proc.poll()
        if code is None and self._matched(procs.snapshot()):
            return self._result("ok", "已启动")
        if code is not None:
            level, message = "error", "启动后立即退出 (退出码 %s)" % code
        else:
            level, message = "warn", "进程还活着，但在进程列表里找不到它"
        out = _tail_since(self.log_file, log_size)  # 只看这次启动新写进去的内容
        return self._result(level, message, out or "(没有新的输出)")

    def stop(self):
        self._reap()
        rows = procs.snapshot()
        found = self._matched(rows, primary=False)  # 连 caffeinate 派生的子进程一起收
        if not found:
            cleaned = self._clear_lock()
            return self._result(
                "info", "当前没有在运行%s" % ("（已清理残留的锁）" if cleaned else ""))

        victims = [r["pid"] for r in found]
        if self.kill_children:
            protected = procs.self_and_ancestors(rows)
            victims = [p for p in procs.with_descendants(victims, rows) if p not in protected]
        procs.kill(victims)
        self._reap()
        if not procs.wait_gone(victims, timeout=10):
            return self._result("error", "已发送终止信号，但进程仍未退出，请到电脑上检查")
        cleaned = self._clear_lock()  # SIGKILL 不会触发脚本自己的清理，这里补上
        return self._result("ok", "已强制停止%s" % ("，并清理了锁" if cleaned else ""),
                            icon="🛑")

    def status(self):
        """结构化状态: {kind, label, running, pids, etime, log, note}。

        终端(ctl.py)和 Telegram 回复(runner.describe)各自渲染这份数据。
        """
        self._reap()
        found = self._matched(procs.snapshot())
        info = {"kind": "status", "label": self.label, "running": bool(found),
                "pids": [r["pid"] for r in found], "etime": "", "log": "", "note": ""}
        if found:
            main = min(found, key=lambda r: r["pid"])
            info["etime"] = procs.etime_to_text(main["etime"])
            if self.show_log:
                info["log"] = _tail(self.log_file)
        elif self.lock_dir and os.path.isdir(self.lock_dir):
            info["note"] = "存在残留的锁，下次启动会自动清理"
        return info


# 与 retry.sh 里的锁目录保持一致: ${TMPDIR:-/tmp}/oci-a1-retry.lock
LOCK_DIR = os.path.join(os.environ.get("TMPDIR") or "/tmp", "oci-a1-retry.lock")

RETRY = Service(
    "抢机脚本", [("shell", "retry.sh")], os.path.join(DIR, "retry.sh"),
    os.path.join(DIR, "retry.log"), lock_dir=LOCK_DIR, kill_children=True, show_log=True,
)
# 停止遥控时不能连带终止它启动的抢机脚本，所以 kill_children=False
BOT = Service(
    "遥控机器人", [("shell", "tg_control.sh"), ("python", "tg_control.py")],
    os.path.join(DIR, "tg_control.sh"), os.path.join(DIR, "tg_control.log"),
)

# 机器人用到的三个动作，针对抢机脚本
start, stop, status = RETRY.start, RETRY.stop, RETRY.status
