"""Application translations. Package metadata and original tool logs stay verbatim."""

import json
import os
import re
from pathlib import Path

from appearance import AppearancePreferences

CATALOG = json.loads(Path(__file__).with_name("translations.json").read_text())


class LanguagePreferences(AppearancePreferences):
    modes = ("zh", "en")
    filename = "language.json"

    def __init__(self, path=None):
        locale = (
            os.environ.get("LC_ALL")
            or os.environ.get("LC_MESSAGES")
            or os.environ.get("LANG", "en")
        )
        self.default_mode = "zh" if locale.lower().startswith("zh") else "en"
        super().__init__(path)


def translate(text, language):
    if language != "en":
        return text
    lines = []
    for line in str(text).split("\n"):
        if line in CATALOG["strings"]:
            line = CATALOG["strings"][line]
        else:
            for pattern, replacement in CATALOG["patterns"]:
                match = re.fullmatch(pattern, line)
                if match:
                    line = re.sub(
                        r"\$(\d+)",
                        lambda m, hit=match: hit.group(int(m[1])),
                        replacement,
                    )
                    break
        lines.append(line)
    return "\n".join(lines)
