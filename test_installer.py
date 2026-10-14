#!/usr/bin/env python3
"""隔离测试：不运行真实 sudo 或实际 APT 安装。"""
import hashlib
import json
import os
from pathlib import Path
import pty
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("deb-install")

STUB = r'''#!/usr/bin/python3
import hashlib, json, os, signal, stat, subprocess, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
record = {"tool": name, "args": args}
if name == "apt-get":
    paths = [Path(p) for p in args if p.endswith(".deb")]
    record["packages"] = [{"path": str(p), "mode": stat.S_IMODE(p.stat().st_mode),
        "parent_mode": stat.S_IMODE(p.parent.stat().st_mode),
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]
if name == "mktemp":
    result = subprocess.run(["/usr/bin/mktemp", *args], capture_output=True, text=True)
    record["directory"] = result.stdout.strip()
with open(os.environ["TEST_TRACE"], "a") as f:
    f.write(json.dumps(record) + "\n")
if name == "sudo":
    if os.environ.get("TEST_SUDO_FAIL"):
        sys.exit(int(os.environ["TEST_SUDO_FAIL"]))
    assert args.pop(0) == "--"
    os.execvp(args[0], args)
if name == "apt-get":
    if os.environ.get("TEST_SIGNAL"):
        os.kill(os.getppid(), signal.SIGTERM)
    sys.exit(int(os.environ.get("TEST_APT_STATUS", "0")))
if name == "mktemp":
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    sys.exit(result.returncode)
if name == "cp":
    if os.environ.get("TEST_COPY_FAIL"):
        sys.exit(73)
    os.execv("/usr/bin/cp", ["cp", *args])
if name == "zenity":
    code = int(os.environ.get("TEST_PICK_STATUS", "0"))
    if code == 0:
        print(os.environ["TEST_PICK_FILE"])
    sys.exit(code)
if name == "xdg-user-dir":
    print(os.environ["TEST_DOWNLOAD"])
'''


class InstallerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root_context = tempfile.TemporaryDirectory(prefix="deb-installer-test-")
        cls.root = Path(cls.root_context.name)
        cls.bin = cls.root / "bin"
        cls.bin.mkdir()
        stub = cls.bin / "stub"
        stub.write_text(STUB)
        stub.chmod(0o755)
        for name in ("sudo", "apt-get", "mktemp", "cp", "zenity", "xdg-user-dir"):
            (cls.bin / name).symlink_to(stub)
        pkg = cls.root / "pkg"
        (pkg / "DEBIAN").mkdir(parents=True)
        (pkg / "DEBIAN/control").write_text(
            "Package: local-deb-installer-test\nVersion: 1.0\nArchitecture: all\n"
            "Maintainer: Test <test@example.invalid>\nDescription: isolated test fixture\n"
        )
        (pkg / "payload.txt").write_text("test fixture\n")
        cls.package = cls.root / "中文 空格 '$`包.deb"
        subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(pkg), str(cls.package)],
                       check=True, capture_output=True)
        cls.hash = hashlib.sha256(cls.package.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.root_context.cleanup()

    def run_script(self, args, tty=False, **extra_env):
        trace = self.root / (self.id().rsplit(".", 1)[-1] + ".jsonl")
        trace.unlink(missing_ok=True)
        env = dict(os.environ, PATH=f"{self.bin}:/usr/bin:/bin", TEST_TRACE=str(trace),
                   TEST_DOWNLOAD=str(self.root), TEST_PICK_FILE=str(self.package),
                   DISPLAY=":test", WAYLAND_DISPLAY="")
        env.update(extra_env)
        master = slave = None
        if tty:
            master, slave = pty.openpty()
        try:
            result = subprocess.run(["bash", str(SCRIPT), *map(str, args)], env=env,
                                    stdin=slave if tty else subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, timeout=15)
        finally:
            if master is not None:
                os.close(master)
                os.close(slave)
        records = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        for record in records:
            if record["tool"] == "mktemp":
                self.assertFalse(Path(record["directory"]).exists(), result.stdout)
        self.assertEqual(hashlib.sha256(self.package.read_bytes()).hexdigest(), self.hash)
        return result, records

    def assert_staged(self, records, count):
        apt = [r for r in records if r["tool"] == "apt-get"]
        self.assertEqual(len(apt), 1)
        self.assertEqual(len(apt[0]["packages"]), count)
        for pkg in apt[0]["packages"]:
            self.assertTrue(pkg["path"].startswith("/tmp/local-deb-install."))
            self.assertEqual(pkg["mode"], 0o644)
            self.assertEqual(pkg["parent_mode"], 0o755)
            self.assertEqual(pkg["sha256"], self.hash)
        return apt[0]

    def test_check_spaces_and_metacharacters(self):
        result, records = self.run_script(["--check", self.package])
        self.assertEqual(result.returncode, 0, result.stdout)
        apt = self.assert_staged(records, 1)
        self.assertEqual(apt["args"][:3], ["--simulate", "--no-remove", "install"])
        self.assertFalse(any(r["tool"] == "sudo" for r in records))

    def test_install_multiple_packages(self):
        result, records = self.run_script([self.package, self.package], tty=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        apt = self.assert_staged(records, 2)
        self.assertEqual(apt["args"][:2], ["--no-remove", "install"])
        sudo = [r for r in records if r["tool"] == "sudo"]
        self.assertEqual(len(sudo), 1)
        self.assertEqual(sudo[0]["args"][:4], ["--", "apt-get", "--no-remove", "install"])
        self.assertNotIn("-y", apt["args"])

    def test_apt_failure_preserves_status(self):
        result, records = self.run_script([self.package], tty=True, TEST_APT_STATUS="100")
        self.assertEqual(result.returncode, 100, result.stdout)
        self.assert_staged(records, 1)
        self.assertIn("安装未成功完成", result.stdout)
        self.assertNotIn("APT 已成功完成", result.stdout)

    def test_simulation_failure_preserves_status(self):
        result, records = self.run_script(["--check", self.package], TEST_APT_STATUS="100")
        self.assertEqual(result.returncode, 100, result.stdout)
        self.assertIn("APT 模拟失败", result.stdout)
        self.assert_staged(records, 1)

    def test_sudo_cancel(self):
        result, records = self.run_script([self.package], tty=True, TEST_SUDO_FAIL="1")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertFalse(any(r["tool"] == "apt-get" for r in records))
        self.assertIn("安装未成功完成", result.stdout)

    def test_invalid_deb(self):
        invalid = self.root / "invalid.deb"
        invalid.write_text("not a Debian archive")
        result, records = self.run_script(["--check", invalid])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("不是有效的 DEB", result.stdout)
        self.assertFalse(any(r["tool"] in ("sudo", "apt-get") for r in records))

    def test_missing_file(self):
        result, records = self.run_script(["--check", self.root / "missing.deb"])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("不是可读取的普通文件", result.stdout)
        self.assertFalse(any(r["tool"] in ("sudo", "apt-get") for r in records))

    def test_copy_failure(self):
        result, records = self.run_script(["--check", self.package], TEST_COPY_FAIL="1")
        self.assertEqual(result.returncode, 73, result.stdout)
        self.assertFalse(any(r["tool"] in ("sudo", "apt-get") for r in records))

    def test_picker_cancel(self):
        result, records = self.run_script([], TEST_PICK_STATUS="1")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(any(r["tool"] in ("mktemp", "sudo", "apt-get") for r in records))

    def test_picker_error(self):
        result, records = self.run_script([], TEST_PICK_STATUS="2")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("文件选择器启动失败", result.stdout)
        self.assertFalse(any(r["tool"] in ("mktemp", "sudo", "apt-get") for r in records))

    def test_picker_selection(self):
        result, records = self.run_script(["--check"])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_staged(records, 1)

    def test_requires_terminal(self):
        result, records = self.run_script([self.package])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("安装需要交互终端", result.stdout)
        self.assertFalse(any(r["tool"] in ("mktemp", "sudo", "apt-get") for r in records))

    def test_sigterm_cleanup(self):
        result, records = self.run_script(["--check", self.package], TEST_SIGNAL="1")
        self.assertEqual(result.returncode, 143, result.stdout)
        self.assert_staged(records, 1)

    def test_unknown_flag(self):
        result, records = self.run_script(["--assume-yes"])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("未知选项", result.stdout)
        self.assertFalse(records)


if __name__ == "__main__":
    unittest.main(verbosity=2)
