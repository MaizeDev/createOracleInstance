#!/usr/bin/env python3
"""procs / runner 的回归测试。

不需要真的启动服务，用构造出来的进程表验证匹配规则。
跑法: python3 test_procs.py
"""
import os
import unittest

import procs
import runner

ME = os.getpid()
SCRIPT = os.path.join(procs.DIR, "retry.sh")


def row(pid, ppid, cmd, stat="S", etime="00:10"):
    return {"pid": pid, "ppid": ppid, "stat": stat, "etime": etime, "cmd": cmd}


class ParsePsTest(unittest.TestCase):
    def test_parses_normal_rows(self):
        # 真机上 ps 每列是右对齐带空格的
        text = ("  100     1 S    00:10 bash /x/retry.sh\n"
                "  200     1 S 1-02:03:04 bash /x/retry.sh\n")
        self.assertEqual([r["pid"] for r in procs.parse_ps(text)], [100, 200])

    def test_drops_continuation_lines_of_multiline_argv(self):
        # argv 里含换行的进程会被 ps 拆成多行，续行不是进程，必须丢掉
        text = ("  100     1 S    00:10 bash /x/retry.sh\n"
                " 1234  5678 S hello tg_control.sh\n"
                "  200     1 S    01:02 bash /x/retry.sh\n")
        self.assertEqual([r["pid"] for r in procs.parse_ps(text)], [100, 200])


class FindTest(unittest.TestCase):
    def test_matches_shell_and_caffeinate_wrapper(self):
        rows = [row(100, 1, "bash %s" % SCRIPT),
                row(101, 100, "caffeinate -is bash %s" % SCRIPT)]
        self.assertEqual([r["pid"] for r in procs.find([("shell", "retry.sh")], rows)],
                         [100, 101])

    def test_ignores_other_directories(self):
        rows = [row(100, 1, "bash /somewhere/else/retry.sh"),
                row(101, 1, "python3 /somewhere/else/retry.py")]
        self.assertEqual(procs.find([("shell", "retry.sh"), ("python", "retry.py")], rows), [])

    def test_ignores_zombies(self):
        rows = [row(100, 1, "bash %s" % SCRIPT, stat="Z")]
        self.assertEqual(procs.find([("shell", "retry.sh")], rows), [])


class PrimariesTest(unittest.TestCase):
    def test_drops_caffeinate_child(self):
        rows = [row(100, 1, "bash %s" % SCRIPT),
                row(101, 100, "caffeinate -is bash %s" % SCRIPT)]
        self.assertEqual([r["pid"] for r in procs.primaries(rows)], [100])

    def test_drops_subshell_of_same_script(self):
        # retry.sh 里 $(date ...) 之类 fork 出来的子 shell 会继承父进程的命令行
        rows = [row(100, 1, "bash %s" % SCRIPT),
                row(101, 100, "bash %s" % SCRIPT)]
        self.assertEqual([r["pid"] for r in procs.primaries(rows)], [100])

    def test_keeps_unrelated_instances(self):
        rows = [row(100, 1, "bash %s" % SCRIPT),
                row(200, 1, "bash %s" % SCRIPT)]
        self.assertEqual([r["pid"] for r in procs.primaries(rows)], [100, 200])


class MatchedTest(unittest.TestCase):
    def service(self):
        return runner.Service("测试服务", [("shell", "retry.sh")],
                              SCRIPT, os.path.join(procs.DIR, "retry.log"))

    def bot_service(self):
        return runner.Service(
            "测试机器人", [("shell", "tg_control.sh"), ("python", "tg_control.py")],
            os.path.join(procs.DIR, "tg_control.sh"),
            os.path.join(procs.DIR, "tg_control.log"))

    def bot_rows(self, main_pid, main_ppid):
        """真实结构: 主进程(python3 tg_control.py) + 它 fork 的 caffeinate 子进程。"""
        return [
            row(main_pid, main_ppid,
                "python3 %s" % os.path.join(procs.DIR, "tg_control.py")),
            row(main_pid + 1, main_pid,
                "caffeinate -is bash %s" % os.path.join(procs.DIR, "tg_control.sh")),
        ]

    def test_bot_does_not_see_its_own_caffeinate_child(self):
        # 回归: 机器人自己启动时(ME = 主进程), caffeinate 子进程 ME+1 命令行里仍带
        # tg_control.sh。修复前这里返回 [ME+1], 机器人误判"已有另一个 tg_control 在运行"后自杀。
        self.assertEqual(self.bot_service()._matched(self.bot_rows(ME, 1)), [])

    def test_launcher_still_sees_the_service_it_just_started(self):
        # 启动方(ME)做完 3 秒自检时, 服务正是 ME 的子进程, 必须能找到
        found = self.bot_service()._matched(self.bot_rows(ME + 10, ME))
        self.assertEqual([r["pid"] for r in found], [ME + 10])

    def test_own_caffeinate_child_is_not_another_instance(self):
        rows = [row(ME, 1, "caffeinate -is bash %s" % SCRIPT),
                row(ME + 1, ME, "caffeinate -is bash %s" % SCRIPT),
                row(ME + 2, 1, "bash %s" % SCRIPT)]  # 这才是真的另一个实例
        self.assertEqual([r["pid"] for r in self.service()._matched(rows)], [ME + 2])

    def test_primary_false_keeps_child_for_stop(self):
        # stop() 用 primary=False, 要把 caffeinate 派生的子进程也一起收掉
        rows = [row(100, 1, "bash %s" % SCRIPT),
                row(101, 100, "caffeinate -is bash %s" % SCRIPT)]
        self.assertEqual(
            [r["pid"] for r in self.service()._matched(rows, primary=False)], [100, 101])
        self.assertEqual([r["pid"] for r in self.service()._matched(rows)], [100])


if __name__ == "__main__":
    unittest.main(verbosity=2)
