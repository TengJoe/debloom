#!/usr/bin/python3
"""Capture real native WebKit previews using an inert DEB and APT simulation."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    raw = ROOT / "docs/raw"
    assets = ROOT / "docs/assets"
    raw.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    fixture = raw / "debloom-demo.deb"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/make_demo_fixture.py"), str(fixture)],
        check=True,
    )
    for width in (1040, 800):
        out = raw / str(width)
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "gui.py"),
                "--smoke-test",
                str(out),
                "--smoke-width",
                str(width),
                str(fixture),
            ],
            check=True,
            timeout=90,
        )
        receipt = json.loads((out / "native-smoke.json").read_text())
        assert (
            receipt["bridge_ready"]
            and receipt["phase"] == "checked"
            and not receipt["real_install"]
        )
        assert all(receipt["frontend_checks"].values())
        assert len(receipt["locale_checks"]) == 3
        assert all(
            not v["horizontal_overflow"] for v in receipt["appearance_checks"].values()
        )
        if width == 1040:
            for language in ("en", "zh"):
                for theme in ("light", "dark"):
                    shutil.copy2(
                        out / f"checked-{language}-{theme}.png",
                        assets / f"{language}-{theme}.png",
                    )
            shutil.copy2(out / "settings-zh.png", assets / "settings.png")
            shutil.copy2(out / "settings-en.png", assets / "settings-en.png")
        else:
            shutil.copy2(out / "checked-en-dark.png", assets / "compact.png")
    print("Verified previews captured. No package was installed.")


if __name__ == "__main__":
    main()
