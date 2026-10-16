import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import install


class IconThemeOwnershipTests(unittest.TestCase):
    def test_existing_generated_theme_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            data = home / ".local/share"
            overlay = data / "icons/DockNormalized"
            overlay.mkdir(parents=True)
            marker = overlay / "index.theme"
            marker.write_bytes(b"existing generated theme")
            with patch.object(
                install, "render_icons", return_value={16: b"source png"}
            ):
                files = install.build_files(home, data)
            self.assertEqual(
                files[data / "icons/hicolor/16x16/apps/local-deb-installer.png"][0],
                b"source png",
            )
            self.assertFalse(
                any(path == overlay or overlay in path.parents for path in files)
            )
            self.assertEqual(marker.read_bytes(), b"existing generated theme")

    def test_refresh_leaves_generated_theme_cache_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            for theme in ("hicolor", "DockNormalized"):
                theme_root = data / "icons" / theme
                theme_root.mkdir(parents=True)
                (theme_root / "index.theme").write_bytes(b"theme")
            with (
                patch.object(
                    install.shutil, "which", side_effect=lambda name: "/usr/bin/" + name
                ),
                patch.object(
                    install.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=0, stderr=""),
                ) as run,
            ):
                install.refresh_caches(data)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertTrue(
                any(str(data / "icons/hicolor") in command for command in commands)
            )
            self.assertFalse(
                any(
                    str(data / "icons/DockNormalized") in command
                    for command in commands
                )
            )


if __name__ == "__main__":
    unittest.main()
