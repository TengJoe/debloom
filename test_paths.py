import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from appearance import AppearancePreferences
from localization import LanguagePreferences
from paths import DEFAULTS, xdg_home


class XDGPathTests(unittest.TestCase):
    def test_unset_empty_relative_and_absolute_directories(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            for kind, (variable, fallback) in DEFAULTS.items():
                for value in [None, "", "relative/path", "~/relative"]:
                    with (
                        self.subTest(kind=kind, value=value),
                        patch.dict(os.environ, {"HOME": d}),
                    ):
                        if value is None:
                            os.environ.pop(variable, None)
                        else:
                            os.environ[variable] = value
                        self.assertEqual(xdg_home(kind), home / fallback)
                custom = home / "custom 中文 $%"
                with patch.dict(os.environ, {variable: str(custom)}):
                    self.assertEqual(xdg_home(kind), custom)

    def test_both_preferences_save_under_default_config(self):
        with tempfile.TemporaryDirectory() as d:
            for value in ["", "relative"]:
                with (
                    self.subTest(value=value),
                    patch.dict(os.environ, {"HOME": d, "XDG_CONFIG_HOME": value}),
                ):
                    for cls, mode in [
                        (AppearancePreferences, "dark"),
                        (LanguagePreferences, "en"),
                    ]:
                        prefs = cls()
                        prefs.save(mode)
                        self.assertEqual(
                            prefs.path.parent, Path(d) / ".config/local-deb-installer"
                        )
                        self.assertEqual(cls().mode, mode)
                        self.assertEqual(prefs.path.stat().st_mode & 0o777, 0o600)

    def test_installer_with_invalid_xdg_never_writes_in_working_directory(self):
        root = Path(__file__).resolve().parent
        for value in ["", "relative"]:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as d:
                base = Path(d)
                home = base / "home"
                home.mkdir()
                cwd = base / "cwd"
                cwd.mkdir()
                env = dict(
                    os.environ,
                    HOME=str(home),
                    XDG_CONFIG_HOME=value,
                    XDG_DATA_HOME=value,
                    XDG_STATE_HOME=value,
                )
                result = subprocess.run(
                    [sys.executable, str(root / "install.py"), "--apply"],
                    cwd=cwd,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(list(cwd.iterdir()))
                receipts = list(
                    (home / ".local/state/debloom/deployments").glob("*/receipt.json")
                )
                self.assertEqual(len(receipts), 1)
                records = json.loads(receipts[0].read_text())["files"]
                for record in records:
                    target = Path(record["target"])
                    self.assertTrue(target.is_absolute())
                    self.assertTrue(target.is_relative_to(home))
                self.assertTrue(
                    (
                        home / ".local/share/applications/local-deb-installer.desktop"
                    ).is_file()
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
