"""启动 / 停止 / 查询本项目的两个后台服务: 抢机脚本 (retry.sh) 和 Telegram 遥控 (tg_control)。

命令行 (ctl.sh) 和 Telegram 机器人共用这一份逻辑，所以两边行为完全一致。
这里只会执行本目录下固定的脚本，函数不接收任何外部文本作为命令参数。
"""
import os
import shutil
import subprocess
import time

import procs

DIR = procs.DIR


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

    def start(self):
        self._reap()
        rows = procs.snapshot()
        if self._matched(rows):
            return "%s: 已经在运行，无需重复启动" % self.label

        if self.lock_dir and os.path.isdir(self.lock_dir):
            others = procs.mentions(self.script_name, rows)
            if others:
                return ("%s: 检测到别处有同名脚本在运行 (pid %s)，"
                        "为避免冲突没有启动，请先确认" % (self.label, ",".join(map(str, others))))
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
            return "%s: 已启动" % self.label
        if code is not None:
            head = "%s: 启动后立即退出 (退出码 %s)" % (self.label, code)
        else:
            head = "%s: 进程还活着，但在进程列表里找不到它" % self.label
        out = _tail_since(self.log_file, log_size)  # 只看这次启动新写进去的内容
        return head + "，本次输出:\n" + (out or "(没有新的输出)")

    def stop(self):
        self._reap()
        rows = procs.snapshot()
        found = self._matched(rows, primary=False)  # 连 caffeinate 派生的子进程一起收
        if not found:
            cleaned = self._clear_lock()
            return "%s: 当前没有在运行%s" % (self.label, "（已清理残留的锁）" if cleaned else "")

        victims = [r["pid"] for r in found]
        if self.kill_children:
            protected = procs.self_and_ancestors(rows)
            victims = [p for p in procs.with_descendants(victims, rows) if p not in protected]
        procs.kill(victims)
        self._reap()
        if not procs.wait_gone(victims, timeout=10):
            return "%s: 已发送终止信号，但进程仍未退出，请到电脑上检查" % self.label
        cleaned = self._clear_lock()  # SIGKILL 不会触发脚本自己的清理，这里补上
        return "%s: 已强制停止%s" % (self.label, "，并清理了锁" if cleaned else "")

    def status(self):
        self._reap()
        found = self._matched(procs.snapshot())
        if not found:
            note = ""
            if self.lock_dir and os.path.isdir(self.lock_dir):
                note = "（存在残留的锁，下次启动会自动清理）"
            return "%s: 未运行%s" % (self.label, note)
        main = min(found, key=lambda r: r["pid"])
        text = "%s: 运行中 (pid %s，已运行 %s)" % (
            self.label, ",".join(str(r["pid"]) for r in found),
            procs.etime_to_text(main["etime"]))
        if self.show_log:
            text += "\n最近日志:\n" + _tail(self.log_file)
        return text


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
