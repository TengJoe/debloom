import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from localization import LanguagePreferences, translate


class LocalizationTests(unittest.TestCase):
    def test_system_default_and_restart(self):
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict("os.environ", {"LC_ALL": "zh_CN.UTF-8"}),
        ):
            path = Path(d) / "app/language.json"
            prefs = LanguagePreferences(path)
            self.assertEqual(prefs.mode, "zh")
            self.assertFalse(path.exists())
            prefs.save("en")
            self.assertEqual(LanguagePreferences(path).mode, "en")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_language_never_writes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "language.json"
            prefs = LanguagePreferences(path)
            with self.assertRaises(ValueError):
                prefs.save("invalid")
            self.assertFalse(path.exists())

    def test_dynamic_errors_and_plans_are_translated(self):
        self.assertEqual(
            translate("安装  sample：1.0\n卸载：0 个软件包", "en"),
            "Install  sample: 1.0\nRemove: 0 packages",
        )
        self.assertIn(
            "exit code 100",
            translate(
                "安装未成功完成（退出码 100）。请查看详细日志；这不代表已自动回滚。",
                "en",
            ),
        )
        self.assertEqual(
            translate("Invalid <script> & original text", "en"),
            "Invalid <script> & original text",
        )

    def test_busy_language_change_and_failed_save(self):
        import gui

        with tempfile.TemporaryDirectory() as d:
            app = gui.InstallerApp(smoke_dir=d)
            app.busy = True
            app.event = Mock()
            value = Mock()
            value.to_string.return_value = json.dumps(
                {"action": "locale", "language": "en"}
            )
            app.on_message(None, value)
            self.assertEqual(app.language.mode, "en")
            self.assertTrue(app.busy)
            before = app.language.path.read_bytes()
            with patch("appearance.os.replace", side_effect=OSError("cannot write")):
                app.set_language("zh")
            self.assertEqual(app.language.mode, "en")
            self.assertEqual(app.language.path.read_bytes(), before)
            self.assertIn(
                "Could not save language", app.event.call_args.args[0]["error"]
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
