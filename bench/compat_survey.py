#!/usr/bin/python3
"""Survey how many real .deb packages Debloom can plan to install.

Read-only: every check runs `apt-get --simulate` and the app's own metadata
reader. Nothing is installed, no pkexec, no dpkg write. The point is to
measure the app's *own* gates (uninstall refusal, arch mismatch, parse
failures) against a realistic corpus.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import InstallerError, PackageSession
from bench.benchmark import unique_output, write_results

DEFAULT_CORPUS = [
    Path("/var/cache/apt/archives"),
]


def classify(package_path: Path) -> dict:
    """Run the app's real load() + check() path against one archive."""
    record = {"file": package_path.name, "size": package_path.stat().st_size}

    session = PackageSession()
    try:
        session.load([str(package_path)])
    except InstallerError as error:
        record.update(outcome="reject_load", reason=str(error).split("\n")[0])
        session.cleanup()
        return record
    except Exception as error:  # noqa: BLE001 - survey must not abort
        record.update(outcome="error_load", reason=f"{type(error).__name__}: {error}")
        session.cleanup()
        return record

    if session.packages:
        package = session.packages[0]
        record["name"] = package.name
        record["version"] = package.version
        record["arch"] = package.architecture
        record["already_installed"] = bool(package.installedVersion)

    try:
        session.check(lambda _line: None)
    except InstallerError as error:
        message = str(error)
        if "卸载" in message:
            record.update(outcome="reject_uninstall", reason=message)
        elif "退出码" in message:
            record.update(outcome="reject_apt", reason=message)
        else:
            record.update(outcome="reject_check", reason=message)
        session.cleanup()
        return record
    except Exception as error:  # noqa: BLE001
        record.update(outcome="error_check", reason=f"{type(error).__name__}: {error}")
        session.cleanup()
        return record

    record["operations"] = len(session.operations)
    if not session.can_install:
        record.update(outcome="no_change", reason="已安装且无变更")
    else:
        record.update(
            outcome="installable", reason=f"计划 {len(session.operations)} 步"
        )
    session.cleanup()
    return record


def apt_architectures() -> list[str]:
    out = subprocess.run(
        ["/usr/bin/dpkg", "--print-foreign-architectures"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    foreign = [a for a in out.split() if a]
    native = subprocess.run(
        ["/usr/bin/dpkg", "--print-architecture"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return [native, *foreign]


def main():
    parser = argparse.ArgumentParser(description="真实 DEB 兼容性普查（只读）")
    parser.add_argument("paths", nargs="*", type=Path, help=".deb 文件或目录")
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0, help="最多测试多少个（0=全部）")
    args = parser.parse_args()
    output = args.output or unique_output("compatibility.json")
    if output.exists():
        parser.error("refusing to overwrite existing evidence: " + str(output))

    targets: list[Path] = []
    roots = args.paths or DEFAULT_CORPUS
    for root in roots:
        if root.is_dir():
            targets.extend(sorted(root.glob("*.deb")))
        elif root.suffix == ".deb" and root.is_file():
            targets.append(root)
    if args.limit:
        targets = targets[: args.limit]

    if not targets:
        raise SystemExit("没有找到 .deb 样本")

    print(f"样本数：{len(targets)}")
    print(f"本机架构：{apt_architectures()}")
    print()

    results = []
    for path in targets:
        record = classify(path)
        results.append(record)
        size_mb = record["size"] / 1048576
        name = record.get("name", path.name)
        print(
            f"  {record['outcome']:18s} {size_mb:7.1f} MB  {name[:38]:38s} {record['reason'][:44]}"
        )

    summary: dict[str, int] = {}
    for record in results:
        summary[record["outcome"]] = summary.get(record["outcome"], 0) + 1

    installable = summary.get("installable", 0)
    no_change = summary.get("no_change", 0)
    total = len(results)
    acceptable = installable + no_change

    print("\n—— 汇总 ——")
    for key in sorted(summary):
        print(f"  {key:18s} {summary[key]:3d}")
    print(
        f"\n  可正常处理（可安装 + 无需变更）: {acceptable}/{total} = {acceptable / total * 100:.1f}%"
    )
    print(
        f"  被拒绝                        : {total - acceptable}/{total} = {(total - acceptable) / total * 100:.1f}%"
    )

    payload = {
        "corpus_size": total,
        "architectures": apt_architectures(),
        "summary": summary,
        "acceptable_percent": acceptable / total * 100 if total else None,
        "results": results,
    }
    payload["scope"] = (
        "metadata and APT simulation only; no real installation or maintainer-script validation"
    )
    write_results(output, payload)
    print(f"\n已写入 {output}")


if __name__ == "__main__":
    main()
