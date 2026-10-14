#!/usr/bin/env python3
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from appearance import AppearancePreferences


class AppearanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='deb-appearance-test-')
        self.path = Path(self.temp.name)/'app/appearance.json'

    def tearDown(self):
        self.temp.cleanup()

    def test_defaults_to_system_without_creating_file(self):
        prefs = AppearancePreferences(self.path)
        self.assertEqual(prefs.mode,'system')
        self.assertFalse(self.path.exists())

    def test_modes_survive_restart_and_private_permissions(self):
        prefs = AppearancePreferences(self.path)
        for mode in ('light','dark','system'):
            prefs.save(mode)
            self.assertEqual(AppearancePreferences(self.path).mode,mode)
            self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777,0o700)

    def test_invalid_file_falls_back_to_system(self):
        self.path.parent.mkdir()
        for data in ('bad-json','[]','{"mode":"invalid"}'):
            self.path.write_text(data)
            self.assertEqual(AppearancePreferences(self.path).mode,'system')

    def test_unknown_mode_cannot_write(self):
        prefs = AppearancePreferences(self.path)
        with self.assertRaises(ValueError): prefs.save('invalid')
        self.assertFalse(self.path.exists())

    def test_failed_save_preserves_previous_preference(self):
        prefs = AppearancePreferences(self.path)
        prefs.save('light')
        before = self.path.read_bytes()
        with patch('appearance.os.replace',side_effect=OSError('cannot write')):
            with self.assertRaises(OSError): prefs.save('dark')
        self.assertEqual(prefs.mode,'light')
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(list(self.path.parent.iterdir()),[self.path])

    def test_system_notification_updates_effective_theme_without_saving(self):
        import gui
        app = gui.InstallerApp(smoke_dir=self.temp.name)
        events = []
        app.event = events.append
        app.style_manager.set_color_scheme(gui.Adw.ColorScheme.FORCE_LIGHT)
        app.style_manager.set_color_scheme(gui.Adw.ColorScheme.FORCE_DARK)
        appearance_events = [e for e in events if e.get('type') == 'appearance']
        self.assertTrue(any(e['effective']=='light' and e['mode']=='system' for e in appearance_events))
        self.assertTrue(any(e['effective']=='dark' and e['mode']=='system' for e in appearance_events))
        self.assertFalse(app.appearance.path.exists())
        app.apply_appearance()

    def test_appearance_can_change_during_busy_operation(self):
        import gui
        app = gui.InstallerApp(smoke_dir=self.temp.name)
        app.busy = True
        value = Mock()
        value.to_string.return_value = '{"action":"appearance","mode":"dark"}'
        app.on_message(None,value)
        self.assertEqual(app.appearance.mode,'dark')
        self.assertTrue(app.style_manager.get_dark())
        self.assertEqual(AppearancePreferences(app.appearance.path).mode,'dark')
        app.appearance.mode='system'
        app.apply_appearance()


if __name__ == '__main__':
    unittest.main(verbosity=2)
