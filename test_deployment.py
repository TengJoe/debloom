import json
import subprocess
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from deployment import deploy, rollback


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old = self.root / "existing"
        self.new = self.root / "added"
        self.old.write_text("original")
        self.old.chmod(0o640)
        self.files = {self.old: (b"release", 0o644), self.new: (b"new", 0o755)}

    def tearDown(self):
        self.temp.cleanup()

    def test_restore_and_archive_are_idempotent(self):
        receipt = deploy(self.files, self.root / "state")
        self.assertEqual(rollback(receipt), 2)
        self.assertEqual(self.old.read_bytes(), b"release")
        self.assertEqual(rollback(receipt, True), 2)
        self.assertEqual(self.old.read_text(), "original")
        self.assertEqual(self.old.stat().st_mode & 0o777, 0o640)
        self.assertFalse(self.new.exists())
        self.assertEqual(rollback(receipt, True), 0)
        self.assertTrue(
            any(
                p.read_bytes() == b"new" for p in (receipt.parent / "removed").iterdir()
            )
        )

    def test_later_edits_stop_all_writes(self):
        receipt = deploy(self.files, self.root / "state")
        self.new.write_text("later user edit")
        with self.assertRaisesRegex(RuntimeError, "Later edits"):
            rollback(receipt, True)
        self.assertEqual(self.old.read_bytes(), b"release")
        self.assertEqual(self.new.read_text(), "later user edit")

    def test_backup_tampering_is_rejected(self):
        receipt = deploy(self.files, self.root / "state")
        (receipt.parent / "000.backup").write_text("tampered")
        with self.assertRaisesRegex(RuntimeError, "Backup changed"):
            rollback(receipt, True)
        self.assertEqual(self.old.read_bytes(), b"release")

    def test_symlink_target_is_rejected_without_touching_other_files(self):
        self.new.symlink_to(self.old)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            deploy(self.files, self.root / "state")
        self.assertEqual(self.old.read_text(), "original")

    def test_spaces_and_shell_metacharacters_in_launcher(self):
        import gi

        from install import build_files

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        # Validate behavior through GIO and the actual shell wrappers; a string
        # containing quotes or a successful desktop-file-validate is insufficient.
        homes = [
            "normal",
            "中文 space",
            "dollar$",
            "back`tick",
            "per%cent",
            'slash\\and"quote',
            "single'quote",
            "equals=sign",
        ]
        for name in homes:
            with self.subTest(home=name):
                home = self.root / name
                home.mkdir()
                with patch("install.render_icons", return_value={}):
                    files = build_files(home)
                script = home / ".local/lib/local-deb-installer/gui.py"
                script.parent.mkdir(parents=True)
                trace = home / "argv.json"
                script.write_text(
                    "import json,sys\nfrom pathlib import Path\n"
                    f"Path({str(trace)!r}).write_text(json.dumps(sys.argv))\n"
                )
                package_paths = [home / "中文 app '$`%.deb", home / "second %F.deb"]
                for path in package_paths:
                    path.write_bytes(b"argument fixture only")
                desktop = home / "test.desktop"
                entry = files[
                    home / ".local/share/applications/local-deb-installer.desktop"
                ][0]
                desktop.write_bytes(entry)
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore", message="Gio.DesktopAppInfo is deprecated.*"
                    )
                    app = Gio.DesktopAppInfo.new_from_filename(str(desktop))
                self.assertTrue(
                    app.launch(
                        [Gio.File.new_for_path(str(p)) for p in package_paths], None
                    )
                )
                deadline = time.monotonic() + 5
                while not trace.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                expected = [str(script), *map(str, package_paths)]
                self.assertTrue(trace.exists(), name)
                self.assertEqual(json.loads(trace.read_text()), expected)
                for alias in ["debloom", "deb-install-gui"]:
                    launcher = home / ".local/bin" / alias
                    launcher.parent.mkdir(parents=True, exist_ok=True)
                    command, mode = files[launcher]
                    launcher.write_bytes(command)
                    launcher.chmod(mode)
                    trace.unlink()
                    subprocess.run(
                        [str(launcher), *map(str, package_paths)], check=True
                    )
                    self.assertEqual(json.loads(trace.read_text()), expected)

    def test_relative_deployment_paths_are_rejected(self):
        from install import build_files

        with self.assertRaisesRegex(ValueError, "absolute"):
            build_files(self.root, Path(""))
        with self.assertRaisesRegex(ValueError, "absolute"):
            build_files(Path("relative-home"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
