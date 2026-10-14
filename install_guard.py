#!/usr/bin/python3 -I
"""APT protocol-v3 hook. Check the actual transaction before dpkg starts.

Never log the protocol configuration section: it can contain credentials.
Uses only the standard library so root does not import user-installed modules.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


class GuardError(RuntimeError):
    pass


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def expected_actions(operations):
    import re
    actions = []
    for line in operations:
        match = re.match(r"^(Inst|Conf) (\S+)(?: \[([^\]]+)\])? \((\S+).*?\[([^\]]+)\]\)", line)
        if not match:
            raise GuardError("无法完整解析已确认的安装计划，请停止安装。")
        action, name, old, new, arch = match.groups()
        name = name.split(":", 1)[0]
        actions.append(["install" if action == "Inst" else "configure", name, arch, new,
                        (old or "-") if action == "Inst" else None])
    return sorted(actions)


def actual_actions(stream):
    if stream.readline().rstrip("\n") != "VERSION 3":
        raise GuardError("APT 最终事务协议不受支持，未执行安装。")
    # Deliberately discard all APT configuration values without logging or storing them.
    for line in stream:
        if not line.strip():
            break
    else:
        raise GuardError("APT 事务数据不完整，未执行安装。")
    actions = []
    for line in stream:
        if not line.strip():
            continue
        parts = line.rstrip("\n").split(" ", 8)
        if len(parts) != 9:
            raise GuardError("APT 事务数据格式错误，未执行安装。")
        name, old, _oldarch, _oldmulti, direction, new, arch, _multi, action = parts
        if action == "**REMOVE**" or new == "-" or direction == ">":
            raise GuardError("最终事务包含卸载或降级，已中止。")
        if action == "**CONFIGURE**":
            actions.append(["configure", name, arch, new, None])
        elif action.startswith("/"):
            actions.append(["install", name, arch, new, old])
        else:
            raise GuardError("APT 最终事务包含未知操作，已中止。")
    return sorted(actions)


def verify(manifest, actual):
    if actual != manifest["actions"]:
        raise GuardError("授权后的实际安装计划与确认的计划不一致，请重新检查。")
    for record in manifest["stamp"]:
        filename, inode, size, mtime = record
        try:
            info = Path(filename).stat()
        except OSError:
            raise GuardError("授权期间系统软件状态发生变化，请重新检查。") from None
        if (info.st_ino, info.st_size, info.st_mtime_ns) != (inode, size, mtime):
            raise GuardError("授权期间系统软件状态发生变化，请重新检查。")
    for record in manifest["files"]:
        if digest(record["path"]) != record["sha256"]:
            raise GuardError("授权期间临时软件包内容发生变化，请重新选择文件。")


def main():
    try:
        preflight = len(sys.argv) == 4 and sys.argv[1] == "--preflight"
        args = sys.argv[2:] if preflight else sys.argv[1:]
        if len(args) != 2:
            raise GuardError("安装校验参数错误，未执行安装。")
        path = Path(args[0])
        if digest(path) != args[1]:
            raise GuardError("已确认的安装清单发生变化，未执行安装。")
        manifest = json.loads(path.read_text())
        if preflight:
            verify(manifest, manifest["actions"])
            # Runs after authentication, before debconf's package config scripts.
            result = subprocess.run(
                ["/usr/bin/apt-get", "--simulate", "--no-remove", "-o", "APT::Color=0",
                 "install", *[item["path"] for item in manifest["files"]]],
                stdin=subprocess.DEVNULL, capture_output=True, text=True)
            operations = tuple(line.strip() for line in result.stdout.splitlines()
                               if line.startswith(("Inst ", "Conf ", "Remv ")))
            if result.returncode or expected_actions(operations) != manifest["actions"]:
                raise GuardError("授权后的安装计划发生变化，请重新检查。")
            verify(manifest, manifest["actions"])
        else:
            verify(manifest, actual_actions(sys.stdin))
    except (GuardError, OSError, ValueError, KeyError, TypeError):
        # No exception dump: protocol headers and root-only configuration must stay private.
        error = sys.exc_info()[1]
        message = str(error) if isinstance(error, GuardError) else "最终安装校验失败，未执行安装。"
        print("安装保护：" + message, file=sys.stderr, flush=True)
        return 1
    print("LOCAL_DEB_GUARD_PREFLIGHT_OK" if preflight else "LOCAL_DEB_GUARD_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
