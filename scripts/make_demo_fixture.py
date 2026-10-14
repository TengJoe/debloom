#!/usr/bin/python3
"""Create an inert tiny DEB for read-only APT simulation and screenshots."""

import argparse
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        tree = Path(d)
        (tree / "DEBIAN").mkdir()
        (tree / "DEBIAN/control").write_text(
            "Package: debloom-demo\nVersion: 1.0\nArchitecture: all\nMaintainer: Demo <demo@example.invalid>\nDescription: A local demo package for Debloom UI previews\n"
        )
        subprocess.run(
            [
                "/usr/bin/dpkg-deb",
                "--build",
                "--root-owner-group",
                str(tree),
                str(args.output.absolute()),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
