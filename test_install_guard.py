#!/usr/bin/env python3
"""Guard tests plus real APT in isolated state, with dpkg replaced by a stub."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from install_guard import GuardError, actual_actions, digest, expected_actions, verify

PROTOCOL = "VERSION 3\nPrivate::Secret=DO_NOT_LOG_THIS\n\npkg - - none < 1.0 all none /tmp/pkg.deb\npkg - - none < 1.0 all none **CONFIGURE**\n"
EXPECTED = [["configure", "pkg", "all", "1.0", None], ["install", "pkg", "all", "1.0", "-"]]


class GuardUnitTests(unittest.TestCase):
    def test_version_architecture_and_actions(self):
        self.assertEqual(actual_actions(io.StringIO(PROTOCOL)), EXPECTED)

    def test_unsupported_protocol_stops(self):
        with self.assertRaisesRegex(GuardError, "不受支持"):
            actual_actions(io.StringIO(PROTOCOL.replace("VERSION 3", "VERSION 2")))

    def test_remove_stops(self):
        with self.assertRaisesRegex(GuardError, "卸载或降级"):
            actual_actions(io.StringIO(PROTOCOL.replace("/tmp/pkg.deb", "**REMOVE**")))

    def test_downgrade_stops(self):
        with self.assertRaisesRegex(GuardError, "卸载或降级"):
            actual_actions(io.StringIO(PROTOCOL.replace(" < ", " > ")))

    def test_malformed_payload_stops(self):
        with self.assertRaisesRegex(GuardError, "格式错误"):
            actual_actions(io.StringIO(PROTOCOL + "garbage\n"))

    def test_architecture_mismatch_stops(self):
        with self.assertRaisesRegex(GuardError, "计划不一致"):
            verify({"actions": EXPECTED, "files": [], "stamp": []},
                   actual_actions(io.StringIO(PROTOCOL.replace("1.0 all", "1.0 amd64"))))

    def test_foreign_architecture_approval(self):
        self.assertEqual(expected_actions(("Inst pkg:i386 [1.0] (2.0 Repo [i386])",
                                           "Conf pkg:i386 (2.0 Repo [i386])")),
                         [["configure", "pkg", "i386", "2.0", None],
                          ["install", "pkg", "i386", "2.0", "1.0"]])


class RealAPTHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = tempfile.TemporaryDirectory(prefix="deb-guard-apt-")
        cls.root = Path(cls.context.name)
        cls.root.chmod(0o755)
        tree = cls.root / "package"
        (tree / "DEBIAN").mkdir(parents=True)
        (tree / "DEBIAN/control").write_text(
            "Package: local-deb-guard-test\nVersion: 1.0\nArchitecture: all\n"
            "Maintainer: Test <test@example.invalid>\nDescription: isolated guard test\n")
        cls.package = cls.root / "test.deb"
        subprocess.run(["/usr/bin/dpkg-deb", "--build", "--root-owner-group", str(tree), str(cls.package)],
                       check=True, capture_output=True)
        cls.guard = cls.root / "install_guard.py"
        shutil.copy2(Path(__file__).with_name("install_guard.py"), cls.guard)
        cls.guard.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        cls.context.cleanup()

    def setUp(self):
        self.work = self.root / self.id().rsplit(".", 1)[-1]
        self.work.mkdir()
        for name in ("lists/partial", "archives/partial", "log", "dpkg-updates"):
            (self.work / name).mkdir(parents=True)
        self.status = self.work / "status"
        self.status.write_text("")
        self.called = self.work / "dpkg-called.jsonl"
        self.fake = self.work / "fake-dpkg"
        self.fake.write_text("#!/usr/bin/python3\nimport json,sys\n"
                             "if sys.argv[1:] == ['--print-architecture']: print('amd64')\n"
                             "elif sys.argv[1:] == ['--print-foreign-architectures']: pass\n"
                             "elif '--unpack' in sys.argv or '--configure' in sys.argv:\n"
                             f"    with open({str(self.called)!r},'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n")
        self.fake.chmod(0o755)
        self.options = ["-o", "Debug::NoLocking=1", "-o", "Dir::State::status=" + str(self.status),
                        "-o", "Dir::State::extended_states=" + str(self.work / "extended_states"),
                        "-o", "Dir::State::lists=" + str(self.work / "lists"),
                        "-o", "Dir::Cache::archives=" + str(self.work / "archives"),
                        "-o", "Dir::Cache::pkgcache=", "-o", "Dir::Cache::srcpkgcache=",
                        "-o", "Dir::Log=" + str(self.work / "log"),
                        "-o", "Dir::Etc::sourcelist=/dev/null", "-o", "Dir::Etc::sourceparts=-",
                        "-o", "Dir::Etc::parts=-", "-o", "Dir::Etc::main=/dev/null",
                        "-o", "Dir::Bin::dpkg=" + str(self.fake), "-o", "Dpkg::Use-Pty=0"]
        result = self.run_apt(["--simulate", "--no-remove"])
        self.assertEqual(result.returncode, 0, result.stdout)
        operations = tuple(line for line in result.stdout.splitlines() if line.startswith(("Inst ", "Conf ")))
        info = self.status.stat()
        self.manifest = {"actions": expected_actions(operations),
                         "stamp": [[str(self.status), info.st_ino, info.st_size, info.st_mtime_ns]],
                         "files": [{"path": str(self.package), "sha256": digest(self.package)}]}
        self.approval = self.work / "approval.json"

    def run_apt(self, more):
        import os
        return subprocess.run(["/usr/bin/apt-get", *self.options, *more, "install", str(self.package)],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, env=dict(os.environ, LC_ALL="C.UTF-8", DEBIAN_FRONTEND="noninteractive"),
                              timeout=20)

    def hook_install(self, preflight=False):
        self.approval.write_text(json.dumps(self.manifest))
        token = digest(self.approval)
        hook = f"{self.guard} {self.approval} {token}"
        options = ["--yes", "--no-remove", "-o", "DPkg::Pre-Install-Pkgs::=" + hook,
                   "-o", f"DPkg::Tools::Options::{self.guard}::Version=3"]
        if preflight:
            options += ["-o", "DPkg::Pre-Invoke::=" + f"{self.guard} --preflight {self.approval} {token}"]
        return self.run_apt(options)

    def test_real_apt_accepts_exact_transaction(self):
        result = self.hook_install(preflight=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("LOCAL_DEB_GUARD_PREFLIGHT_OK", result.stdout)
        self.assertIn("LOCAL_DEB_GUARD_OK", result.stdout)
        self.assertTrue(self.called.exists(), result.stdout)
        self.assertEqual(self.status.read_text(), "")  # The fake dpkg did not install anything.

    def test_real_apt_blocks_changed_transaction_before_dpkg(self):
        self.manifest["actions"][0][3] = "unexpected-version"
        result = self.hook_install()
        self.assertEqual(result.returncode, 100, result.stdout)
        self.assertIn("实际安装计划", result.stdout)
        self.assertFalse(self.called.exists(), result.stdout)

    def test_real_apt_blocks_changed_state_before_dpkg(self):
        self.status.touch()
        result = self.hook_install()
        self.assertEqual(result.returncode, 100, result.stdout)
        self.assertIn("系统软件状态发生变化", result.stdout)
        self.assertFalse(self.called.exists(), result.stdout)

    def test_real_apt_blocks_changed_hash_before_dpkg(self):
        self.manifest["files"][0]["sha256"] = "wrong-hash"
        result = self.hook_install()
        self.assertEqual(result.returncode, 100, result.stdout)
        self.assertIn("软件包内容发生变化", result.stdout)
        self.assertFalse(self.called.exists(), result.stdout)

    def test_preflight_blocks_before_existing_preinstall_hook(self):
        self.manifest["actions"][0][3] = "changed-before-authentication"
        self.approval.write_text(json.dumps(self.manifest))
        token = digest(self.approval)
        marker = self.work / "package-config-was-run"
        options = ["--yes", "--no-remove", "-o", "DPkg::Pre-Install-Pkgs::=/usr/bin/touch " + str(marker),
                   "-o", "DPkg::Pre-Invoke::=" + f"{self.guard} --preflight {self.approval} {token}"]
        result = self.run_apt(options)
        self.assertEqual(result.returncode, 100, result.stdout)
        self.assertIn("安装计划发生变化", result.stdout)
        self.assertFalse(marker.exists(), result.stdout)
        self.assertFalse(self.called.exists(), result.stdout)

    def test_manifest_tampering_is_rejected_without_secret_log(self):
        self.approval.write_text(json.dumps(self.manifest))
        before = digest(self.approval)
        self.approval.write_text('{}')
        result = subprocess.run([str(self.guard), str(self.approval), before], input=PROTOCOL,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("清单发生变化", result.stderr)
        self.assertNotIn("DO_NOT_LOG_THIS", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
