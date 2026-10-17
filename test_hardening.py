#!/usr/bin/env python3
"""审查后加固项的回归测试(和 test_procs.py 分开，互不影响)。

覆盖: ps 读不到/读空必须报错、项目路径含空格仍能识别、notify.sh 不把 token 放进命令行、
TELEGRAM_CHAT_ID 的校验、遥控机器人的入口和配置加载不依赖 oci、.sh 里 $VAR 后不能紧跟全角字符。
跑法: python3 test_hardening.py
"""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import procs

HERE = os.path.dirname(os.path.abspath(__file__))


def row(pid, ppid, cmd, stat_="S", etime="00:10"):
    return {"pid": pid, "ppid": ppid, "stat": stat_, "etime": etime, "cmd": cmd}


class SnapshotTest(unittest.TestCase):
    def _fake_run(self, stdout):
        return mock.Mock(return_value=mock.Mock(stdout=stdout))

    def test_empty_ps_output_is_an_error_not_no_processes(self):
        with mock.patch.object(procs.subprocess, "run", self._fake_run("")):
            with self.assertRaises(RuntimeError):
                procs.snapshot()

    def test_unparseable_ps_output_is_an_error(self):
        with mock.patch.object(procs.subprocess, "run", self._fake_run("garbage\n")):
            with self.assertRaises(RuntimeError):
                procs.snapshot()

    def test_missing_ps_is_an_error(self):
        with mock.patch.object(procs.subprocess, "run", side_effect=OSError("no ps")):
            with self.assertRaises(RuntimeError):
                procs.snapshot()

    def test_normal_output_still_works(self):
        out = "  100     1 S    00:10 bash /x/retry.sh\n"
        with mock.patch.object(procs.subprocess, "run", self._fake_run(out)):
            self.assertEqual([r["pid"] for r in procs.snapshot()], [100])


class PathWithSpacesTest(unittest.TestCase):
    PROJECT = "/tmp/my proj"

    def setUp(self):
        for name in ("DIR", "REAL_DIR"):
            p = mock.patch.object(procs, name, self.PROJECT)
            p.start()
            self.addCleanup(p.stop)

    def test_matches_script_in_project_dir_with_spaces(self):
        rows = [row(100, 1, "bash %s/retry.sh" % self.PROJECT),
                row(101, 100, "caffeinate -is bash %s/retry.sh" % self.PROJECT)]
        found = procs.find([("shell", "retry.sh")], rows)
        self.assertEqual([r["pid"] for r in found], [100, 101])

    def test_python_script_with_spaces(self):
        rows = [row(100, 1, "python3 %s/tg_control.py" % self.PROJECT)]
        self.assertEqual(len(procs.find([("python", "tg_control.py")], rows)), 1)

    def test_does_not_match_other_directory_with_spaces(self):
        rows = [row(100, 1, "bash /elsewhere dir/retry.sh"),
                row(101, 1, "bash /tmp/other my proj/retry.sh")]
        self.assertEqual(procs.find([("shell", "retry.sh")], rows), [])

    def test_does_not_match_when_project_path_is_only_a_suffix(self):
        # "/x/tmp/my proj/retry.sh" 以 "/tmp/my proj/retry.sh" 结尾，但前面没有空格分隔，
        # 不是同一个目录，不能误认
        rows = [row(100, 1, "bash /x/tmp/my proj/retry.sh")]
        self.assertEqual(procs.find([("shell", "retry.sh")], rows), [])


@unittest.skipUnless(shutil.which("bash"), "需要 bash")
class NotifyTokenTest(unittest.TestCase):
    TOKEN = "123456:SECRETTOKEN"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        fake = os.path.join(self.tmp, "curl")
        with open(fake, "w") as f:
            f.write('#!/usr/bin/env bash\n'
                    'printf "%%s\\n" "$@" > "%s/argv.txt"\n'
                    'cat > "%s/stdin.txt"\n' % (self.tmp, self.tmp))
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)

    def run_notify(self, env_extra):
        env = dict(os.environ, PATH=self.tmp + os.pathsep + os.environ["PATH"])
        env.pop("TELEGRAM_BOT_TOKEN", None)
        env.pop("TELEGRAM_CHAT_ID", None)
        env.update(env_extra)
        return subprocess.run(["bash", os.path.join(HERE, "notify.sh"), "你好 hello"],
                              env=env, stdin=subprocess.DEVNULL, capture_output=True)

    def read(self, name):
        with open(os.path.join(self.tmp, name)) as f:
            return f.read()

    def test_token_is_not_in_curl_argv(self):
        r = self.run_notify({"TELEGRAM_BOT_TOKEN": self.TOKEN, "TELEGRAM_CHAT_ID": "42"})
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("SECRETTOKEN", self.read("argv.txt"))
        # token 走 stdin 的 curl 配置
        self.assertIn("url = \"https://api.telegram.org/bot%s/sendMessage\"" % self.TOKEN,
                      self.read("stdin.txt"))
        self.assertIn("chat_id=42", self.read("argv.txt"))

    def test_skips_silently_without_config(self):
        r = self.run_notify({})
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "argv.txt")))

    def test_curl_failure_does_not_fail_the_script(self):
        with open(os.path.join(self.tmp, "curl"), "w") as f:
            f.write("#!/usr/bin/env bash\ncat >/dev/null\nexit 22\n")
        r = self.run_notify({"TELEGRAM_BOT_TOKEN": self.TOKEN, "TELEGRAM_CHAT_ID": "42"})
        self.assertEqual(r.returncode, 0)


class AllowedIdTest(unittest.TestCase):
    def import_with(self, chat_id):
        env = dict(os.environ, TELEGRAM_CHAT_ID=chat_id)
        return subprocess.run([sys.executable, "-c", "import tg_control"],
                              cwd=HERE, env=env, capture_output=True, text=True)

    def test_accepts_plain_positive_integer(self):
        self.assertEqual(self.import_with("123456789").returncode, 0)

    def test_rejects_bad_values_with_a_message_not_a_traceback(self):
        for bad in ("", "abc", "-5", "0", "12 34", "²", "１２３"):
            r = self.import_with(bad)
            self.assertNotEqual(r.returncode, 0, repr(bad))
            self.assertIn("TELEGRAM_CHAT_ID", r.stderr, repr(bad))
            self.assertNotIn("Traceback", r.stderr, repr(bad))


def path_without_oci():
    """去掉 PATH 里所有带 oci 的目录: 不管本机装没装 oci，都能模拟"没有 oci"的环境。
    /usr/bin、/bin 保底保留(common.sh 要用 dirname 等基础命令)。"""
    keep = [d for d in os.environ.get("PATH", "").split(os.pathsep)
            if d and not os.path.exists(os.path.join(d, "oci"))]
    for d in ("/usr/bin", "/bin"):
        if d not in keep:
            keep.append(d)
    return os.pathsep.join(keep)


BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "需要 bash")
class LoadEnvTest(unittest.TestCase):
    def test_load_env_works_without_oci_and_other_settings(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "config.env")
            with open(cfg, "w") as f:
                f.write("TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=42\n")
            script = '. "%s/common.sh"; load_env; echo "$TELEGRAM_CHAT_ID"' % HERE
            env = dict(os.environ, CONFIG_FILE=cfg, PATH=path_without_oci())
            r = subprocess.run([BASH, "-c", script], env=env,
                               capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "42", r.stderr)

    def test_load_env_reports_missing_config(self):
        script = '. "%s/common.sh"; load_env' % HERE
        r = subprocess.run([BASH, "-c", script],
                           env=dict(os.environ, CONFIG_FILE="/nonexistent/config.env",
                                    PATH=path_without_oci()),
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("config.env", r.stdout + r.stderr)

    def test_load_config_still_requires_oci(self):
        script = '. "%s/common.sh"; load_config' % HERE
        r = subprocess.run([BASH, "-c", script], env=dict(os.environ, PATH=path_without_oci()),
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("oci", r.stdout + r.stderr)


@unittest.skipUnless(BASH, "需要 bash")
class BotEntryTest(unittest.TestCase):
    """tg_control.sh 这个入口本身必须不依赖 oci 和抢机参数。

    只测 load_env 这个函数不够: 入口脚本改回 load_config 时，函数测试照样通过。
    这里用一个假的 python3 顶替真的机器人，只要入口能走到 exec python3 就算过关。
    """

    def test_entry_starts_with_only_token_and_user_id(self):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "python3")
            with open(fake, "w") as f:
                f.write("#!/bin/sh\necho BOT_STARTED\n")
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
            cfg = os.path.join(d, "config.env")
            with open(cfg, "w") as f:
                f.write("TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=42\n")  # 没有任何抢机参数
            env = dict(os.environ, CONFIG_FILE=cfg,
                       PATH=d + os.pathsep + path_without_oci())
            r = subprocess.run([BASH, os.path.join(HERE, "tg_control.sh")], env=env,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True)
            self.assertIn("BOT_STARTED", r.stdout, r.stdout + r.stderr)

    def test_entry_still_requires_token(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "config.env")
            with open(cfg, "w") as f:
                f.write("TELEGRAM_CHAT_ID=42\n")
            env = dict(os.environ, CONFIG_FILE=cfg, PATH=path_without_oci())
            env.pop("TELEGRAM_BOT_TOKEN", None)
            r = subprocess.run([BASH, os.path.join(HERE, "tg_control.sh")], env=env,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("TELEGRAM_BOT_TOKEN", r.stdout + r.stderr)


class ShellVarBraceTest(unittest.TestCase):
    """macOS 自带 bash 3.2: "$VAR，" 这种变量名后紧跟全角字符的写法会把变量吞成空串
    (报错信息里的值直接消失)。所有 .sh 里必须写成 ${VAR}。"""

    PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)(?=[^\x00-\x7f])")

    def test_no_unbraced_var_before_non_ascii(self):
        bad = []
        for name in sorted(os.listdir(HERE)):
            if not name.endswith(".sh"):
                continue
            with open(os.path.join(HERE, name), encoding="utf-8") as f:
                for no, line in enumerate(f, 1):
                    if line.lstrip().startswith("#"):
                        continue
                    for m in self.PATTERN.finditer(line):
                        bad.append("%s:%d  $%s 后面紧跟 %r，请改成 ${%s}"
                                   % (name, no, m.group(1), line[m.end()], m.group(1)))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
