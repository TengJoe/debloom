#!/usr/bin/env python3
"""Test native control paths without opening or changing the user's window."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from gi.repository import Gio
import gui


class GUIControlTests(unittest.TestCase):
    def test_smoke_is_separate_from_user_instance(self):
        app = gui.InstallerApp(smoke_dir='/tmp/unused-deb-smoke')
        self.assertTrue(app.get_flags() & Gio.ApplicationFlags.NON_UNIQUE)

    def test_smoke_failure_returns_failure(self):
        app = gui.InstallerApp()
        app.quit = Mock()
        app.smoke_failed()
        self.assertEqual(app.exit_status, 1)
        app.quit.assert_called_once()

    def test_finished_callback_failure_becomes_error(self):
        app = gui.InstallerApp()
        app.log, app.send_state = Mock(), Mock()
        app.busy = True
        def fail():
            raise OSError('temporary cleanup failed')
        app.finished(fail)
        self.assertEqual(app.phase, 'error')
        self.assertFalse(app.busy)
        self.assertIn('temporary cleanup failed', app.message)

    def test_file_arguments_reach_gapplication(self):
        app = Mock()
        app.run.return_value = 0
        app.exit_status = 0
        with patch.object(sys, 'argv', ['deb-install-gui', '中文 空格.deb']), patch.object(gui, 'InstallerApp', return_value=app):
            self.assertEqual(gui.main(), 0)
        app.run.assert_called_once_with(['deb-install-gui', str(Path('中文 空格.deb').absolute())])

    def test_smoke_failure_replaces_stale_success_report(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory)/'native-smoke.json'
            report.write_text('{"status":"success"}')
            app = gui.InstallerApp(smoke_dir=directory)
            app.quit = Mock()
            app.smoke_failed()
            result = json.loads(report.read_text())
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['exit_status'],1)
            self.assertFalse(result['check_triggered_by_html_button'])

    def test_filepicker_survives_missing_xdg_user_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            app=gui.InstallerApp(smoke_dir=directory)
            dialog=Mock()
            with patch('gui.Gtk.FileDialog',return_value=dialog), patch('gui.subprocess.run',side_effect=FileNotFoundError('missing tool')):
                app.choose()
            folder=dialog.set_initial_folder.call_args.args[0].get_path()
            self.assertTrue(Path(folder).is_dir())
            dialog.open_multiple.assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
