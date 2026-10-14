import tempfile
import unittest
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
        from install import build_files

        home = self.root / "中文 space $`"
        home.mkdir()
        with patch("install.render_icons", return_value={16: b"png"}):
            files = build_files(home)
        command = files[home / ".local/bin/debloom"][0].decode()
        self.assertIn("'", command)
        self.assertIn('"$@"', command)
        desktop = files[home / ".local/share/applications/local-deb-installer.desktop"][
            0
        ].decode()
        self.assertIn("Icon=local-deb-installer", desktop)
        self.assertNotIn("@GUI_LAUNCHER@", desktop)
        self.assertIn("\nTryExec=" + str(home / ".local/bin/debloom") + "\n", desktop)


if __name__ == "__main__":
    unittest.main(verbosity=2)
