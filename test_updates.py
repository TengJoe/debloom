import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from updates import UpdateError, check_for_update


class UpdateTests(unittest.TestCase):
    def opener(self, tag="v0.1.0", **extra):
        data = {
            "tag_name": tag,
            "html_url": "https://malicious.invalid/execute",
            **extra,
        }
        return lambda request, timeout: io.BytesIO(json.dumps(data).encode())

    def test_current_newer_and_older_versions(self):
        self.assertEqual(check_for_update(self.opener())["status"], "current")
        self.assertEqual(check_for_update(self.opener("v0.2.0"))["status"], "available")
        self.assertFalse(check_for_update(self.opener("v0.0.9"))["available"])

    def test_release_link_is_constructed_from_fixed_repository(self):
        self.assertEqual(
            check_for_update(self.opener("v0.2.0"))["url"],
            "https://github.com/TengJoe/debloom/releases/tag/v0.2.0",
        )

    def test_unpublished_repository_has_friendly_status(self):
        def opener(*args, **kwargs):
            raise HTTPError("url", 404, "not found", {}, None)

        self.assertEqual(check_for_update(opener)["status"], "unpublished")

    def test_network_invalid_and_oversized_responses(self):
        for opener in [
            lambda *a, **k: io.BytesIO(b"bad json"),
            lambda *a, **k: io.BytesIO(b"x" * 262145),
            self.opener("invalid"),
            self.opener(prerelease=True),
        ]:
            with self.assertRaises(UpdateError):
                check_for_update(opener)

        def offline(*args, **kwargs):
            raise URLError("offline")

        with self.assertRaisesRegex(UpdateError, "网络"):
            check_for_update(offline)

    def test_check_is_explicit_and_does_not_change_package_task(self):
        import tempfile

        import gui

        with tempfile.TemporaryDirectory() as d:
            app = gui.InstallerApp(smoke_dir=d)
            app.busy = True
            app.phase = "installing"
            app.event = Mock()
            self.assertFalse(app.update_busy)

            def immediate(function, *args):
                return function(*args)

            with (
                patch(
                    "gui.check_for_update",
                    return_value={
                        "status": "current",
                        "current": "0.1.0",
                        "available": False,
                    },
                ),
                patch("gui.GLib.idle_add", side_effect=immediate),
                patch("gui.threading.Thread") as thread,
            ):
                thread.return_value.start.side_effect = lambda: thread.call_args.kwargs[
                    "target"
                ]()
                app.check_updates()
            self.assertTrue(app.busy)
            self.assertEqual(app.phase, "installing")
            self.assertEqual(app.update_state["status"], "current")
            self.assertFalse(app.update_busy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
