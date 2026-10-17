#!/usr/bin/env python3
"""launch.sh 的退出码约定和成功路径通知的回归测试。

retry.sh 完全靠 launch.sh 的退出码决策: 0 成功 / 10 容量不足 / 11 被限流 / 其他 = 未知错误。
这里用假的 oci 和假的 notify.sh，不联网、不创建任何资源。
跑法: python3 test_launch.py
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BASH = shutil.which("bash")

# 假 oci: 按环境变量 FAKE_LAUNCH 决定 launch 的结果；instance list 只有在"已创建且可见"
# (marker 文件存在) 时才返回实例 ID；每次调用都记到 calls.log。
FAKE_OCI = r'''#!/usr/bin/env bash
echo "$*" >> "$FAKE_DIR/calls.log"
case "$1 $2 $3" in
  "compute instance list")
    [ -f "$FAKE_DIR/visible" ] && echo "ocid1.instance.fake"
    exit 0 ;;
  "compute instance launch")
    case "$FAKE_LAUNCH" in
      capacity) echo '{"code": "InternalError", "message": "Out of host capacity for shape"}'; exit 1 ;;
      toomany)  echo '{"code": "TooManyRequests", "status": 429}'; exit 1 ;;
      other)    echo 'NotAuthenticated'; exit 1 ;;
      ok_visible)   touch "$FAKE_DIR/visible"; echo '{}'; exit 0 ;;
      ok_invisible) echo '{}'; exit 0 ;;
    esac ;;
  "compute instance get") exit 0 ;;
  "compute instance list-vnics") echo "1.2.3.4"; exit 0 ;;
esac
exit 0
'''
FAKE_NOTIFY = '#!/usr/bin/env bash\nprintf "%s\\n" "$1" >> "$FAKE_DIR/notify.log"\n'


def make_exec(path, text):
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


@unittest.skipUnless(BASH, "需要 bash")
class LaunchContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(self.proj)
        for name in ("launch.sh", "common.sh"):
            shutil.copy(os.path.join(HERE, name), self.proj)
        make_exec(os.path.join(self.proj, "notify.sh"), FAKE_NOTIFY)
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        make_exec(os.path.join(bindir, "oci"), FAKE_OCI)
        key = os.path.join(self.tmp, "key.pub")
        open(key, "w").write("ssh-ed25519 AAAA test\n")
        cfg = os.path.join(self.tmp, "config.env")
        with open(cfg, "w") as f:
            f.write('COMPARTMENT_ID=c\nAVAILABILITY_DOMAIN=ad\nSUBNET_ID=s\nIMAGE_ID=i\n'
                    'SSH_PUBLIC_KEY_FILE="%s"\nINSTANCE_NAME=test-arm\n' % key)
        self.env = dict(os.environ, CONFIG_FILE=cfg, FAKE_DIR=self.tmp,
                        PATH=bindir + os.pathsep + os.environ["PATH"])

    def launch(self, mode):
        env = dict(self.env, FAKE_LAUNCH=mode)
        return subprocess.run([BASH, os.path.join(self.proj, "launch.sh")], env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True)

    def read(self, name):
        path = os.path.join(self.tmp, name)
        return open(path).read() if os.path.exists(path) else ""

    def test_capacity_error_is_exit_10(self):
        self.assertEqual(self.launch("capacity").returncode, 10)
        self.assertEqual(self.read("notify.log"), "")

    def test_rate_limit_is_exit_11(self):
        self.assertEqual(self.launch("toomany").returncode, 11)

    def test_other_errors_are_exit_1_and_shown(self):
        r = self.launch("other")
        self.assertEqual(r.returncode, 1)
        self.assertIn("NotAuthenticated", r.stderr)

    def test_success_notifies_with_ip(self):
        r = self.launch("ok_visible")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("已创建", self.read("notify.log"))
        self.assertIn("1.2.3.4", self.read("notify.log"))

    def test_success_but_not_yet_listed_still_notifies(self):
        # 创建请求被接受、但 instance list 暂时看不到它: 退出码仍是 0(别让 retry.sh 重复创建)，
        # 但这是整个项目最重要的事件，绝不能静默结束
        r = self.launch("ok_invisible")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotEqual(self.read("notify.log").strip(), "", "成功却没有任何通知")

    def test_existing_instance_is_not_created_again(self):
        open(os.path.join(self.tmp, "visible"), "w").close()
        r = self.launch("ok_visible")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("compute instance launch", self.read("calls.log"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
