"""Per-application appearance preference; never changes desktop settings."""
import json
import os
from pathlib import Path
import tempfile

MODES = ("system", "light", "dark")


class AppearancePreferences:
    modes = MODES
    default_mode = 'system'
    filename = 'appearance.json'

    def __init__(self, path=None):
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        self.path = Path(path) if path else config_home / 'local-deb-installer' / self.filename
        self.mode = self.default_mode
        try:
            data = json.loads(self.path.read_text())
            if isinstance(data, dict) and data.get("mode") in self.modes:
                self.mode = data["mode"]
        except (OSError, ValueError):
            pass

    def save(self, mode):
        if mode not in self.modes:
            raise ValueError("不支持的外观模式")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             delete=False) as stream:
                temporary = Path(stream.name)
                os.fchmod(stream.fileno(), 0o600)
                json.dump({"version": 1, "mode": mode}, stream)
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        self.mode = mode
