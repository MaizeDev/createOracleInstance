"""进程查找与终止，只针对本项目的脚本。

通过 `ps` 扫描进程列表，识别规则缺一不可，识别不了的一律不碰:
  1. 可执行文件符合预期: bash/sh/zsh/caffeinate/nohup，或 python
  2. 命令行的最后一个参数就是目标脚本名
  3. 脚本位置校验: 绝对路径则所在目录必须是本项目目录；
     相对路径（如 ./retry.sh）则按进程的工作目录解析后，所在目录必须是本项目目录
"""
import os
import signal
import subprocess
import time

DIR = os.path.dirname(os.path.abspath(__file__))
REAL_DIR = os.path.realpath(DIR)
SHELL_EXES = {"bash", "sh", "zsh", "caffeinate", "nohup"}


def snapshot():
    """当前所有进程: [{pid, ppid, stat, etime, cmd}]。"""
    out = subprocess.run(
        ["ps", "-axww", "-o", "pid=,ppid=,stat=,etime=,command="],
        capture_output=True, text=True, timeout=10,
    ).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        rows.append({"pid": pid, "ppid": ppid, "stat": parts[2],
                     "etime": parts[3], "cmd": parts[4]})
    return rows


def cwd_of(pid):
    """进程的工作目录，查不到返回 None。Linux 读 /proc，macOS 用 lsof。"""
    try:
        return os.path.realpath(os.readlink("/proc/%d/cwd" % pid))
    except OSError:
        pass
    try:
        out = subprocess.run(
            ["lsof", "-a", "-d", "cwd", "-p", str(pid), "-Fn"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if line.startswith("n"):
            return os.path.realpath(line[1:])
    return None


def _in_project(row, script_arg):
    if os.path.isabs(script_arg):
        path = script_arg
    else:
        cwd = cwd_of(row["pid"])
        if cwd is None:
            return False
        path = os.path.join(cwd, script_arg)
    return os.path.dirname(os.path.realpath(path)) == REAL_DIR


def find(patterns, rows):
    """patterns: [("shell"|"python", 脚本名)]，返回匹配的进程行。"""
    found = []
    for row in rows:
        if row["stat"].startswith("Z"):  # 僵尸进程不算在运行
            continue
        tokens = row["cmd"].split()
        if len(tokens) < 2:
            continue
        exe = os.path.basename(tokens[0])
        last = tokens[-1]
        for kind, script in patterns:
            if os.path.basename(last) != script:
                continue
            if kind == "shell" and exe not in SHELL_EXES:
                continue
            if kind == "python" and not exe.lower().startswith("python"):
                continue
            if _in_project(row, last):
                found.append(row)
                break
    return found


def mentions(script, rows):
    """不校验目录: 有没有任何 shell 在运行名为 script 的脚本。用来识别别处的同名脚本。"""
    pids = []
    for row in rows:
        if row["stat"].startswith("Z"):
            continue
        tokens = row["cmd"].split()
        if (len(tokens) >= 2 and os.path.basename(tokens[0]) in SHELL_EXES
                and os.path.basename(tokens[-1]) == script):
            pids.append(row["pid"])
    return pids


def self_and_ancestors(rows):
    """当前进程及其所有祖先进程，永远不会被当作目标。"""
    parent = {r["pid"]: r["ppid"] for r in rows}
    chain, pid = set(), os.getpid()
    while pid and pid not in chain:
        chain.add(pid)
        pid = parent.get(pid, 0)
    return chain


def with_descendants(roots, rows):
    """roots 以及它们所有的子孙进程。"""
    children = {}
    for r in rows:
        children.setdefault(r["ppid"], []).append(r["pid"])
    result, queue = list(roots), list(roots)
    while queue:
        for child in children.get(queue.pop(), []):
            if child not in result:
                result.append(child)
                queue.append(child)
    return result


def kill(pids):
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def wait_gone(pids, timeout=10):
    wanted = set(pids)
    end = time.time() + timeout
    while time.time() < end:
        alive = {r["pid"] for r in snapshot() if not r["stat"].startswith("Z")}
        if not (wanted & alive):
            return True
        time.sleep(0.2)
    return False


def etime_to_text(etime):
    """ps 的 etime ([[dd-]hh:]mm:ss) 转成中文。"""
    days = 0
    if "-" in etime:
        d, etime = etime.split("-", 1)
        days = int(d)
    nums = [int(x) for x in etime.split(":")]
    while len(nums) < 3:
        nums.insert(0, 0)
    hours, minutes = nums[0], nums[1]
    if days:
        return "%d天%d小时" % (days, hours)
    if hours:
        return "%d小时%d分钟" % (hours, minutes)
    return "%d分钟" % minutes
