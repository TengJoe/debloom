"""Local package operations. This module never asks for or handles passwords."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import tempfile
import uuid
from typing import Callable
from install_guard import expected_actions


class InstallerError(RuntimeError):
    pass


class AuthorizationCancelled(InstallerError):
    pass


@dataclass
class Package:
    name: str
    version: str
    architecture: str
    filename: str
    size: int
    description: str
    installedVersion: str


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def system_stamp() -> tuple:
    """Read metadata only; never expose APT configuration values."""
    paths = [Path("/var/lib/dpkg/status")]
    for directory in ("/var/lib/dpkg/updates", "/var/lib/apt/lists", "/etc/apt"):
        root = Path(directory)
        paths.extend(p for p in root.rglob("*") if p.is_file())
    result = []
    for path in sorted(set(paths)):
        try:
            info = path.stat()
        except FileNotFoundError:
            continue
        # APT's persistent lock file does not describe package state.
        if path.name == "lock":
            continue
        result.append((str(path), info.st_ino, info.st_size, info.st_mtime_ns))
    return tuple(result)


def stream_command(args: list[str], log: Callable[[str], None],
                   on_started: Callable[[], None] | None = None) -> tuple[int, str]:
    env = dict(os.environ, LC_ALL="C.UTF-8", LANGUAGE="C", TERM="dumb")
    with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors="replace", env=env) as proc:
        if on_started:
            on_started()
        output = []
        assert proc.stdout is not None
        for line in proc.stdout:
            output.append(line)
            log(line)
        return proc.wait(), "".join(output)


def plan_operations(output: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in output.splitlines()
                 if line.startswith(("Inst ", "Conf ", "Remv ")))


def readable_plan(operations: tuple[str, ...]) -> str:
    if not operations:
        return "所选版本已安装，当前无需更改。\n\n安装 / 升级：0 个软件包\n卸载：0 个软件包"
    changes, installed = [], set()
    for line in operations:
        match = re.match(r"Inst (\S+)(?: \[([^\]]+)\])? \((\S+)", line)
        if match:
            name, old, new = match.groups()
            installed.add(name)
            changes.append(f"升级  {name}：{old} → {new}" if old else f"安装  {name}：{new}")
        elif line.startswith("Inst "):
            changes.append(line)
    for line in operations:
        if line.startswith("Conf ") and line.split()[1] not in installed:
            changes.append("完成配置  " + line.split()[1])
    return "将执行以下变更：\n\n" + "\n".join(changes) + "\n\n卸载：0 个软件包\n完整检查输出可在详细日志中查看。"


class PackageSession:
    def __init__(self, runner=stream_command, stamp=system_stamp):
        self.runner = runner
        self.stamp = stamp
        self.temp: tempfile.TemporaryDirectory | None = None
        self.paths: list[Path] = []
        self.hashes: list[str] = []
        self.packages: list[Package] = []
        self.plan_text = ""
        self.operations: tuple[str, ...] = ()
        self.checked_stamp: tuple | None = None
        self.can_install = False

    def cleanup(self):
        if self.temp is not None:
            self.temp.cleanup()
        self.temp = None
        self.paths = []
        self.hashes = []
        self.packages = []
        self.plan_text = ""
        self.operations = ()
        self.checked_stamp = None
        self.can_install = False

    def package_data(self):
        return [asdict(pkg) for pkg in self.packages]

    def load(self, filenames: list[str]):
        self.cleanup()
        if not filenames:
            raise InstallerError("请选择至少一个 DEB 文件。")
        self.temp = tempfile.TemporaryDirectory(prefix="local-deb-install.", dir="/tmp")
        folder = Path(self.temp.name)
        names = set()
        try:
            for index, filename in enumerate(filenames):
                source = Path(filename).expanduser().absolute()
                if source.suffix.lower() != ".deb" or not source.is_file():
                    raise InstallerError(f"不是可读取的 DEB 文件：{source.name}")
                target = folder / f"package-{index + 1}.deb"
                shutil.copyfile(source, target)
                info = subprocess.run(["/usr/bin/dpkg-deb", "--field", str(target)],
                                      capture_output=True, text=True, errors="replace")
                if info.returncode:
                    raise InstallerError(f"无效的 DEB 软件包：{source.name}\n{info.stderr.strip()}")
                fields = Parser().parsestr(info.stdout)
                name = str(fields.get("Package", ""))
                version = str(fields.get("Version", ""))
                architecture = str(fields.get("Architecture", ""))
                if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", name) or not version or not architecture:
                    raise InstallerError(f"软件包元数据不完整：{source.name}")
                if name in names:
                    raise InstallerError(f"同一软件包只能选择一个版本：{name}")
                names.add(name)
                current = subprocess.run(
                    ["/usr/bin/dpkg-query", "--show", "--showformat=${Version}\t${db:Status-Status}", "--", f"{name}:{architecture}"],
                    capture_output=True, text=True, errors="replace")
                installed = ""
                if current.returncode == 0 and current.stdout.endswith("\tinstalled"):
                    installed = current.stdout.split("\t", 1)[0]
                self.packages.append(Package(name, version, architecture, source.name,
                                             target.stat().st_size,
                                             (str(fields.get("Description", "")).splitlines() or [""])[0], installed))
                target.chmod(0o644)
                self.paths.append(target)
                self.hashes.append(sha256(target))
            folder.chmod(0o755)
        except Exception:
            self.cleanup()
            raise

    def check(self, log: Callable[[str], None]):
        self.can_install = False
        self.checked_stamp = None
        if not self.paths:
            raise InstallerError("请先选择软件包。")
        before = self.stamp()
        code, output = self.runner(
            ["/usr/bin/apt-get", "--simulate", "--no-remove", "-o", "APT::Color=0",
             "install", *map(str, self.paths)], log)
        self.plan_text = output
        if code:
            raise InstallerError(f"安装计划检查失败（APT 退出码 {code}）。请查看详细日志。")
        if before != self.stamp():
            raise InstallerError("检查期间系统软件状态发生了变化，请重新检查。")
        self.operations = plan_operations(output)
        if any(line.startswith("Remv ") for line in self.operations):
            raise InstallerError("安装计划需要卸载现有软件，已中止。")
        self.checked_stamp = before
        self.can_install = bool(self.operations)
        self.plan_text = readable_plan(self.operations)

    def install(self, log: Callable[[str], None], on_started=None):
        if not self.can_install or self.checked_stamp is None:
            raise InstallerError("请先检查安装计划，然后确认安装。")
        # Consume confirmation before doing anything, so a failed attempt cannot be replayed.
        self.can_install = False
        if self.checked_stamp != self.stamp():
            raise InstallerError("系统软件状态已发生变化，请重新检查安装计划。")
        if [sha256(path) for path in self.paths] != self.hashes:
            raise InstallerError("临时软件包内容发生变化，请重新选择文件。")
        code, output = self.runner(
            ["/usr/bin/apt-get", "--simulate", "--no-remove", "-o", "APT::Color=0",
             "install", *map(str, self.paths)], log)
        if code or plan_operations(output) != self.operations or self.checked_stamp != self.stamp():
            raise InstallerError("安装计划已变化或无法确认，请重新检查。")
        # APT's actual transaction must also match after authentication, before dpkg runs.
        manifest = self.paths[0].parent / f"approval-{uuid.uuid4().hex}.json"
        approval = {"actions": expected_actions(self.operations), "stamp": self.checked_stamp,
                    "files": [{"path": str(path), "sha256": checksum}
                              for path, checksum in zip(self.paths, self.hashes)]}
        manifest.write_text(json.dumps(approval, ensure_ascii=True, sort_keys=True))
        manifest.chmod(0o644)
        guard = str(Path(__file__).with_name("install_guard.py"))
        guard_command = shlex.quote(guard)
        hook = guard_command + " " + shlex.quote(str(manifest)) + " " + sha256(manifest)
        preflight = guard_command + " --preflight " + shlex.quote(str(manifest)) + " " + sha256(manifest)
        # The executable token is exactly the token APT uses to select its hook protocol.
        hook_options = ["-o", "DPkg::Pre-Invoke::=" + preflight,
                        "-o", "DPkg::Pre-Install-Pkgs::=" + hook,
                        "-o", "DPkg::Tools::Options::" + guard_command + "::Version=3"]
        # pkexec displays the system authentication dialog. No password goes through this app.
        command = ["/usr/bin/pkexec", "/usr/bin/env", "DEBIAN_FRONTEND=noninteractive",
                   "LC_ALL=C.UTF-8", "/usr/bin/apt-get", "--yes", "--no-remove",
                   "-o", "Dpkg::Use-Pty=0", "-o", "APT::Color=0",
                   "-o", "Dpkg::Options::=--force-confold", *hook_options,
                   "install", *map(str, self.paths)]
        started = False
        def install_log(line):
            nonlocal started
            if line.strip() == "LOCAL_DEB_GUARD_OK":
                if not started:
                    started = True
                    if on_started:
                        on_started()
                log("最终安装计划已核对，开始安装。\n")
            elif line.strip() == "LOCAL_DEB_GUARD_PREFLIGHT_OK":
                log("系统授权已完成，授权后的安装计划已核对。\n")
            else:
                log(line)
        code, output = self.runner(command, install_log)
        if code in (126, 127):
            raise AuthorizationCancelled("系统授权未完成。软件可能尚未开始安装，请查看日志后重新检查。")
        if code:
            raise InstallerError(f"安装未成功完成（退出码 {code}）。请查看详细日志；这不代表已自动回滚。")
        # Independently read back the requested package versions and configured status.
        for package in self.packages:
            result = subprocess.run(
                ["/usr/bin/dpkg-query", "--show", "--showformat=${Version}\t${db:Status-Status}", "--", f"{package.name}:{package.architecture}"],
                capture_output=True, text=True)
            if result.returncode or result.stdout != f"{package.version}\tinstalled":
                raise InstallerError(f"APT 已结束，但 {package.name} 的版本或配置状态未通过核对，请查看日志。")
        # Refresh only after every package passed independent readback.
        for package in self.packages:
            package.installedVersion = package.version
