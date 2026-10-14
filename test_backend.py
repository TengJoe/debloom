#!/usr/bin/env python3
"""Real DEB fixtures; fake APT/pkexec runner, no privileged commands."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backend import AuthorizationCancelled, InstallerError, PackageSession, readable_plan

PLAN = "Inst local-deb-gui-test (1.0 local-deb-gui-test [all])\nConf local-deb-gui-test (1.0 local-deb-gui-test [all])\n"


class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = tempfile.TemporaryDirectory(prefix="deb-gui-tests-")
        cls.root = Path(cls.context.name)
        tree = cls.root / "tree"
        (tree / "DEBIAN").mkdir(parents=True)
        (tree / "DEBIAN/control").write_text(
            "Package: local-deb-gui-test\nVersion: 1.0\nArchitecture: all\n"
            "Maintainer: Test <test@example.invalid>\nDescription: <script>unsafe HTML</script>\n")
        cls.file = cls.root / "中文 空格 '$`包.deb"
        subprocess.run(["/usr/bin/dpkg-deb", "--build", "--root-owner-group", str(tree), str(cls.file)],
                       check=True, capture_output=True)
        cls.original = cls.file.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.context.cleanup()

    def setUp(self):
        self.calls = []
        self.plan = PLAN
        self.sim_status = 0
        self.install_status = 0
        self.current_stamp = ("initial",)
        self.session = PackageSession(runner=self.runner, stamp=lambda: self.current_stamp)
        self.session.load([str(self.file)])

    def tearDown(self):
        path = Path(self.session.temp.name) if self.session.temp else None
        self.session.cleanup()
        if path:
            self.assertFalse(path.exists())
        self.assertEqual(self.file.read_bytes(), self.original)

    def runner(self, args, log, on_started=None):
        self.calls.append(args)
        if args[0] == "/usr/bin/pkexec":
            if on_started:
                on_started()
            log("installer output\n")
            return self.install_status, "installer output\n"
        self.assertEqual(args[0], "/usr/bin/apt-get")
        self.assertIn("--simulate", args)
        log(self.plan)
        return self.sim_status, self.plan

    def check(self):
        self.session.check(lambda _: None)

    def install(self):
        return self.session.install(lambda _: None)

    def test_real_metadata_and_permissions(self):
        data = self.session.package_data()[0]
        self.assertEqual(data["name"], "local-deb-gui-test")
        self.assertEqual(data["version"], "1.0")
        self.assertEqual(data["description"], "<script>unsafe HTML</script>")
        target = self.session.paths[0]
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(target.read_bytes(), self.original)

    def test_invalid_archive_cleans_up(self):
        bad = self.root / "bad.deb"
        bad.write_text("invalid")
        with self.assertRaisesRegex(InstallerError, "无效的 DEB"):
            self.session.load([str(bad)])
        self.assertIsNone(self.session.temp)

    def test_duplicate_package_rejected(self):
        with self.assertRaisesRegex(InstallerError, "只能选择一个版本"):
            self.session.load([str(self.file), str(self.file)])
        self.assertIsNone(self.session.temp)

    def test_successful_check_never_calls_pkexec(self):
        self.check()
        self.assertTrue(self.session.can_install)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("--yes", self.calls[0])

    def test_nothing_to_install(self):
        self.plan = "0 upgraded, 0 newly installed, 0 to remove\n"
        self.check()
        self.assertFalse(self.session.can_install)
        with self.assertRaisesRegex(InstallerError, "先检查"):
            self.install()

    def test_simulation_failure(self):
        self.sim_status = 100
        with self.assertRaisesRegex(InstallerError, "检查失败"):
            self.check()
        self.assertFalse(self.session.can_install)

    def test_removal_rejected(self):
        self.plan = "Remv existing-package [1.0]\n" + PLAN
        with self.assertRaisesRegex(InstallerError, "卸载现有软件"):
            self.check()
        self.assertFalse(self.session.can_install)

    def test_changed_system_blocks_install(self):
        self.check()
        self.current_stamp = ("changed",)
        with self.assertRaisesRegex(InstallerError, "软件状态已发生变化"):
            self.install()
        self.assertEqual(len(self.calls), 1)

    def test_changed_archive_blocks_install(self):
        self.check()
        self.session.paths[0].write_bytes(b"changed")
        with self.assertRaisesRegex(InstallerError, "内容发生变化"):
            self.install()
        self.assertEqual(len(self.calls), 1)

    def test_changed_plan_blocks_authorization(self):
        self.check()
        self.plan += "Inst other-package (2.0 Ubuntu [amd64])\n"
        with self.assertRaisesRegex(InstallerError, "安装计划已变化"):
            self.install()
        self.assertFalse(any(c[0] == "/usr/bin/pkexec" for c in self.calls))

    def test_system_changes_during_check(self):
        self.session.stamp = iter([("before",), ("after",)]).__next__
        with self.assertRaisesRegex(InstallerError, "检查期间"):
            self.check()

    def test_authorization_cancel(self):
        self.check()
        self.install_status = 126
        with self.assertRaises(AuthorizationCancelled):
            self.install()
        self.assertFalse(self.session.can_install)

    def test_apt_install_failure(self):
        self.check()
        self.install_status = 100
        with self.assertRaisesRegex(InstallerError, "不代表已自动回滚"):
            self.install()
        self.assertFalse(self.session.can_install)

    def test_exit_zero_requires_readback(self):
        self.check()
        with self.assertRaisesRegex(InstallerError, "未通过核对"):
            self.install()

    def test_install_success_command_and_readback(self):
        self.check()
        readback = subprocess.CompletedProcess([], 0, "1.0\tinstalled", "")
        with patch("backend.subprocess.run", return_value=readback) as query:
            self.install()
        query.assert_called_once()
        self.assertEqual(query.call_args.args[0][-1], 'local-deb-gui-test:all')
        self.assertEqual(self.session.package_data()[0]['installedVersion'], '1.0')
        command = self.calls[-1]
        self.assertEqual(command[:5], ["/usr/bin/pkexec", "/usr/bin/env", "DEBIAN_FRONTEND=noninteractive", "LC_ALL=C.UTF-8", "/usr/bin/apt-get"])
        self.assertIn("--no-remove", command)
        self.assertIn("Dpkg::Options::=--force-confold", command)
        self.assertTrue(any(arg.startswith('DPkg::Pre-Invoke::=') for arg in command))
        self.assertTrue(any(arg.startswith('DPkg::Pre-Install-Pkgs::=') for arg in command))
        self.assertNotIn("--allow-downgrades", command)
        self.assertFalse(self.session.can_install)
        self.assertEqual(len(self.calls), 3)  # initial simulation, recheck, authorized install

    def test_readable_plan_shows_actual_changes(self):
        text = readable_plan(('Inst fresh (1.0 Local [all])', 'Conf fresh (1.0 Local [all])',
                              'Inst existing [1.0] (2.0 Ubuntu [amd64])',
                              'Conf unfinished (1.0 Ubuntu [all])'))
        self.assertIn('安装  fresh：1.0', text)
        self.assertIn('升级  existing：1.0 → 2.0', text)
        self.assertIn('完成配置  unfinished', text)
        self.assertEqual(text.count('fresh'), 1)

    def test_unknown_plan_format_is_not_hidden(self):
        self.assertIn('Inst future-package unknown-format',
                      readable_plan(('Inst future-package unknown-format',)))

    def test_waiting_for_authentication_does_not_mark_installing(self):
        self.check()
        self.install_status = 126
        started = []
        with self.assertRaises(AuthorizationCancelled):
            self.session.install(lambda _: None, lambda: started.append(True))
        self.assertFalse(started)

    def test_final_guard_signal_marks_installing(self):
        original = self.runner
        def runner(args, log, on_started=None):
            if args[0] == '/usr/bin/pkexec':
                log('LOCAL_DEB_GUARD_PREFLIGHT_OK\n')
                log('LOCAL_DEB_GUARD_OK\n')
            return original(args, log, on_started)
        self.session.runner = runner
        self.check()
        started, lines = [], []
        with patch('backend.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '1.0\tinstalled', '')):
            self.session.install(lines.append, lambda: started.append(True))
        self.assertEqual(started, [True])
        self.assertIn('最终安装计划已核对，开始安装。\n', lines)

    def test_metadata_query_is_architecture_qualified(self):
        real_run = subprocess.run
        queries = []
        def run(args, **kwargs):
            if args[0] == '/usr/bin/dpkg-query':
                queries.append(args[-1])
                return subprocess.CompletedProcess(args, 0, '0.9\tinstalled', '')
            return real_run(args, **kwargs)
        with patch('backend.subprocess.run', side_effect=run):
            self.session.load([str(self.file)])
        self.assertEqual(queries, ['local-deb-gui-test:all'])
        self.assertEqual(self.session.package_data()[0]['installedVersion'], '0.9')

    def test_failed_readback_does_not_refresh_installed_metadata(self):
        self.session.packages[0].installedVersion = '0.9'
        self.check()
        with patch('backend.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '0.8\tinstalled', '')):
            with self.assertRaisesRegex(InstallerError, '未通过核对'):
                self.install()
        self.assertEqual(self.session.package_data()[0]['installedVersion'], '0.9')


if __name__ == "__main__":
    unittest.main(verbosity=2)
