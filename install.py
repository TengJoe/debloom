#!/usr/bin/python3
"""Install Debloom for the current user; --apply writes, default only checks."""

import argparse
import io
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from deployment import deploy, rollback

ROOT = Path(__file__).resolve().parent
RUNTIME = (
    "gui.py",
    "backend.py",
    "install_guard.py",
    "appearance.py",
    "localization.py",
    "translations.json",
    "updates.py",
    "version.py",
    "index.html",
    "local-deb-installer.svg",
)


def render_icons():
    import cairo
    import gi

    gi.require_version("Rsvg", "2.0")
    from gi.repository import Rsvg

    handle = Rsvg.Handle.new_from_data((ROOT / "local-deb-installer.svg").read_bytes())
    result = {}
    for size in (16, 24, 32, 48, 64, 96, 128, 256, 512):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        rect = Rsvg.Rectangle()
        rect.x = rect.y = 0
        rect.width = rect.height = size
        if not handle.render_document(cairo.Context(surface), rect):
            raise RuntimeError("Icon rendering failed")
        out = io.BytesIO()
        surface.write_to_png(out)
        result[size] = out.getvalue()
    return result


def build_files(home, data_home=None):
    home = Path(home)
    data = Path(data_home) if data_home else home / ".local/share"
    files = {}
    for name in RUNTIME:
        files[home / ".local/lib/local-deb-installer" / name] = (
            (ROOT / name).read_bytes(),
            0o755 if name == "install_guard.py" else 0o644,
        )
    command = (
        "#!/bin/sh\nexec /usr/bin/python3 "
        + shlex.quote(str(home / ".local/lib/local-deb-installer/gui.py"))
        + ' "$@"\n'
    )
    for name in ("debloom", "deb-install-gui"):
        files[home / ".local/bin" / name] = (command.encode(), 0o755)
    files[home / ".local/bin/deb-install"] = (
        (ROOT / "deb-install").read_bytes(),
        0o755,
    )
    launcher = str(home / ".local/bin/debloom")
    # Desktop Exec follows its own quoting rules, not shell quoting.
    quoted = (
        '"'
        + launcher.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
        .replace("%", "%%")
        + '"'
    )
    desktop = (
        (ROOT / "local-deb-installer.desktop")
        .read_text()
        .replace("\nExec=@GUI_LAUNCHER@", "\nExec=" + quoted)
        .replace("\nTryExec=@GUI_LAUNCHER@", "\nTryExec=" + launcher)
    )
    files[data / "applications/local-deb-installer.desktop"] = (desktop.encode(), 0o644)
    icons = render_icons()
    themes = ["hicolor"]
    if (data / "icons/DockNormalized/index.theme").exists():
        themes.append("DockNormalized")
    for theme in themes:
        for size, png in icons.items():
            files[
                data / f"icons/{theme}/{size}x{size}/apps/local-deb-installer.png"
            ] = (png, 0o644)
    files[data / "icons/hicolor/scalable/apps/local-deb-installer.svg"] = (
        (ROOT / "local-deb-installer.svg").read_bytes(),
        0o644,
    )
    return files


def refresh_caches(data):
    for command in (
        ["update-desktop-database", str(data / "applications")],
        *[
            [
                "gtk-update-icon-cache",
                "--force",
                "--ignore-theme-index",
                str(data / "icons" / t),
            ]
            for t in ["hicolor", "DockNormalized"]
            if (data / "icons" / t / "index.theme").exists()
        ],
    ):
        executable = shutil.which(command[0])
        if executable:
            result = subprocess.run(
                [executable, *command[1:]], capture_output=True, text=True, check=False
            )
            if result.returncode:
                print("Cache refresh warning:", command[0], result.stderr.strip())


def check_dependencies():
    import gi

    for module, version in [
        ("Gtk", "4.0"),
        ("Adw", "1"),
        ("WebKit", "6.0"),
        ("Rsvg", "2.0"),
    ]:
        gi.require_version(module, version)
    for tool in ["apt-get", "dpkg-deb", "dpkg-query", "pkexec"]:
        if not shutil.which(tool):
            raise RuntimeError("Missing runtime dependency: " + tool)


def main():
    parser = argparse.ArgumentParser(
        description="Install Debloom for the current user, without sudo."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the installation or execute guarded rollback",
    )
    parser.add_argument(
        "--rollback",
        type=Path,
        metavar="RECEIPT",
        help="check a deployment rollback receipt",
    )
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("Run as your ordinary desktop user, without sudo.")
    data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    if args.rollback:
        count = rollback(args.rollback, args.apply)
        if args.apply:
            refresh_caches(data)
        print(
            "Rollback "
            + ("completed" if args.apply else "check passed")
            + f": {count} files."
        )
        return
    check_dependencies()
    files = build_files(Path.home(), data)
    if not args.apply:
        print(
            f"Dependencies OK. {len(files)} files prepared for the current user. Add --apply to install."
        )
        return
    state = (
        Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        / "debloom/deployments"
    )
    receipt = deploy(files, state)
    refresh_caches(data)
    print("Debloom installed. Launch: " + str(Path.home() / ".local/bin/debloom"))
    print("Guarded rollback receipt: " + str(receipt))


if __name__ == "__main__":
    main()
