#!/usr/bin/env python3
"""procs / runner 的回归测试。

不需要真的启动服务，用构造出来的进程表验证匹配规则。
跑法: python3 test_procs.py
"""
import contextlib
import io
import os
import unittest

import ctl
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


class EtimeTest(unittest.TestCase):
    def test_under_one_minute_shows_seconds(self):
        self.assertEqual(procs.etime_to_text("00:12"), "12秒")

    def test_minutes_hours_days(self):
        self.assertEqual(procs.etime_to_text("01:30"), "1分钟")
        self.assertEqual(procs.etime_to_text("01:02:03"), "1小时2分钟")
        self.assertEqual(procs.etime_to_text("2-03:04:05"), "2天3小时")


class DescribeTest(unittest.TestCase):
    """Telegram 回复用的渲染(带 emoji)。"""

    def test_running_status(self):
        text = runner.describe({
            "kind": "status", "label": "抢机脚本", "running": True,
            "pids": [60042], "etime": "12分钟", "log": "[20:19:11] 尝试创建 qdii-arm",
            "note": "",
        })
        self.assertIn("🟢 抢机脚本 运行中", text)
        self.assertIn("pid 60042 · 已运行 12分钟", text)
        self.assertIn("📄 最近日志", text)

    def test_stopped_status_keeps_note(self):
        text = runner.describe({
            "kind": "status", "label": "遥控机器人", "running": False,
            "pids": [], "etime": "", "log": "", "note": "存在残留的锁",
        })
        self.assertIn("⚪️ 遥控机器人 未运行", text)
        self.assertIn("存在残留的锁", text)

    def test_log_lines_are_trimmed_for_chat(self):
        # 手机聊天窗口窄: 去掉日期、并把每行压到 TG_LOG_WIDTH 以内，免得自动折行
        long_line = "[2026-10-16 20:31:07] " + "这是一条很长的日志 " * 10
        text = runner.describe({
            "kind": "status", "label": "抢机脚本", "running": True,
            "pids": [60042], "etime": "19秒", "log": long_line, "note": "",
        })
        log_line = text.splitlines()[-1]
        self.assertTrue(log_line.startswith("[20:31:07] "), log_line)
        self.assertLessEqual(runner.display_width(log_line), runner.TG_LOG_WIDTH)
        self.assertTrue(log_line.endswith("…"), log_line)

    def test_common_log_lines_are_compacted(self):
        # 常见日志压成手机上放得下的短句，原文(retry.log)不动
        self.assertEqual(
            runner._short_log_line(
                "[2026-10-16 20:31:09] 尝试创建 qdii-arm (1 OCPU / 6GB, 启动卷 50GB)"),
            "[20:31:09] 尝试创建 qdii-arm（1C/6GB）")
        self.assertEqual(
            runner._short_log_line(
                "[2026-10-16 20:31:07] 开始重试: 间隔约 90s (+随机 1~30s)，最多 0 次 (0=不限)"),
            "[20:31:07] 开始重试: 间隔 90s，不限次数")
        self.assertEqual(
            runner._short_log_line("[2026-10-16 20:32:53] 第 3 次: 未知错误 (连续 2/5)"),
            "[20:32:53] 第 3 次: 未知错误 (2/5)")

    def test_common_lines_fit_in_one_chat_line(self):
        # 真实 retry.log 里最常见的几种行，压缩后都必须一行放得下
        common = [
            "尝试创建 qdii-arm (1 OCPU / 6GB, 启动卷 50GB)",
            "开始重试: 间隔约 90s (+随机 1~30s)，最多 0 次 (0=不限)",
            "开始重试: 间隔约 90s (+随机 1~30s)，最多 5 次 (0=不限)",
            "第 12 次: 被限流，延长等待",
            "第 3 次: 未知错误 (连续 2/5)",
            "第 7 次: 容量不足",
            "373 秒后重试",
            "完成，共尝试 42 次",
            "收到中断信号，退出",
        ]
        for raw in common:
            line = runner._short_log_line("[2026-10-16 20:31:09] " + raw)
            self.assertLessEqual(runner.display_width(line), runner.TG_LOG_WIDTH, line)
            self.assertFalse(line.endswith("…"), "不该被截断: %s" % line)

    def test_action_levels(self):
        ok = runner.describe({"kind": "action", "label": "抢机脚本",
                              "level": "ok", "message": "已启动", "output": ""})
        self.assertEqual(ok, "✅ 抢机脚本 已启动")
        bad = runner.describe({"kind": "action", "label": "遥控机器人",
                               "level": "error", "message": "启动后立即退出 (退出码 1)",
                               "output": "已有另一个 tg_control 在运行"})
        self.assertIn("❌ 遥控机器人 启动后立即退出 (退出码 1)", bad)
        self.assertIn("本次输出:", bad)
        self.assertIn("已有另一个 tg_control 在运行", bad)


class TerminalRenderTest(unittest.TestCase):
    """终端排版(方案 A: 分隔线 + 缩进)，颜色默认关闭便于断言。"""

    def render(self, func, *args):
        buf = io.StringIO()
        ctl.COLOR = False
        with contextlib.redirect_stdout(buf):
            func(*args)
        return buf.getvalue()

    def test_status_block(self):
        out = self.render(ctl.show_status, {
            "kind": "status", "label": "抢机脚本", "running": True, "pids": [60042],
            "etime": "12分钟", "log": "[20:19:11] 尝试创建 qdii-arm", "note": ""})
        self.assertIn("── 抢机脚本", out)
        self.assertIn("    运行中 · pid 60042 · 已运行 12分钟", out)
        self.assertIn("    最近日志:", out)
        self.assertIn("      [20:19:11] 尝试创建 qdii-arm", out)

    def test_action_lines_are_aligned(self):
        out = self.render(ctl.show_actions, [
            {"kind": "action", "label": "抢机脚本", "level": "ok",
             "message": "已启动", "output": ""},
            {"kind": "action", "label": "遥控机器人", "level": "error",
             "message": "启动后立即退出 (退出码 1)", "output": "已有另一个 tg_control 在运行"},
        ])
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("  ✓ 抢机脚本"))
        self.assertTrue(lines[1].startswith("  ✗ 遥控机器人"))
        self.assertIn("      已有另一个 tg_control 在运行", out)

    def test_color_can_be_forced(self):
        buf = io.StringIO()
        ctl.COLOR = True
        with contextlib.redirect_stdout(buf):
            ctl.show_actions([{"kind": "action", "label": "抢机脚本", "level": "ok",
                               "message": "已启动", "output": ""}])
        ctl.COLOR = False
        self.assertIn("\033[32m", buf.getvalue())

    def test_clip_counts_cjk_as_double_width(self):
        self.assertEqual(ctl._dwidth("抢机abc"), 7)
        self.assertEqual(ctl._clip("abcdefghij", 5), "abcd…")
        self.assertEqual(ctl._clip("抢机脚本测试", 5), "抢机…")


if __name__ == "__main__":
    unittest.main(verbosity=2)
