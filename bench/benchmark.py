#!/usr/bin/python3
"""Read-only DEB preflight measurements; no extrapolated rewrite-speed claims."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import resource
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend import PackageSession


def run(args):
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        env=dict(os.environ, LC_ALL="C.UTF-8", LANGUAGE="C"),
    )
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} exited with code {result.returncode}")
    return result.stdout


def measure(function):
    child_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_before, wall_before = time.process_time(), time.perf_counter()
    function()
    wall = time.perf_counter() - wall_before
    cpu = time.process_time() - cpu_before
    child_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    child = (
        child_after.ru_utime
        - child_before.ru_utime
        + (child_after.ru_stime - child_before.ru_stime)
    )
    return {
        "wall_ms": wall * 1000,
        "parent_cpu_ms": cpu * 1000,
        "child_cpu_ms": child * 1000,
    }


def summarize(samples):
    return {
        metric: {
            "median": statistics.median(row[metric] for row in samples),
            "min": min(row[metric] for row in samples),
            "max": max(row[metric] for row in samples),
        }
        for metric in ["wall_ms", "parent_cpu_ms", "child_cpu_ms"]
    }


def unique_output(name):
    folder = (
        ROOT
        / "bench/runs"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    )
    return folder / name


def write_results(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def benchmark(package, repeats):
    session = PackageSession()
    session.load([str(package)])
    apt_command = [
        "/usr/bin/apt-get",
        "--simulate",
        "--no-remove",
        "-o",
        "APT::Color=0",
        "install",
        *map(str, session.paths),
    ]
    data = package.read_bytes()

    def load():
        loaded = PackageSession()
        try:
            loaded.load([str(package)])
        finally:
            loaded.cleanup()

    functions = {
        "apt_simulation": lambda: run(apt_command),
        "session_check": lambda: session.check(lambda _: None),
        "package_load_and_cleanup": load,
        "sha256_in_memory": lambda: hashlib.sha256(data).hexdigest(),
    }
    stages = {}
    try:
        for name, function in functions.items():
            samples = [measure(function) for _ in range(repeats)]
            stages[name] = {"summary": summarize(samples), "samples": samples}
    finally:
        session.cleanup()
    # Every condition gets the same interpreter flags and path setup. Importing
    # in a fresh process is not the same as clearing filesystem/library caches.
    prefix = "import sys;sys.path.insert(0," + repr(str(ROOT)) + ");"
    py = "import backend,appearance,localization,updates"
    gtk = "import gi;gi.require_version('Gtk','4.0');gi.require_version('Adw','1');gi.require_version('WebKit','6.0');from gi.repository import Gtk,Adw,WebKit"
    conditions = {
        "interpreter": "pass",
        "application_imports": py,
        "gtk_imports": gtk,
        "combined_imports": py + ";" + gtk,
    }
    samples = {name: [] for name in conditions}
    rng = random.Random(0)
    for _ in range(repeats):
        names = list(conditions)
        rng.shuffle(names)
        for name in names:
            samples[name].append(
                measure(
                    lambda name=name: run(
                        [sys.executable, "-I", "-c", prefix + conditions[name]]
                    )
                )
            )
    imports = {
        name: {"summary": summarize(rows), "samples": rows}
        for name, rows in samples.items()
    }
    os_release = platform.freedesktop_os_release()
    return {
        "schema": 2,
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "environment": {
            "os": os_release.get("PRETTY_NAME", "unknown"),
            "python": platform.python_version(),
            "architecture": platform.machine(),
            "apt": run(["/usr/bin/apt-get", "--version"]).splitlines()[0],
        },
        "package": {
            "name": package.name,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        "repeats": repeats,
        "stages": stages,
        "fresh_process_imports": imports,
        "scope": {
            "apt_mode": "simulate only",
            "cache_state": "not cleared",
            "imports_are_window_ready": False,
            "compiled_comparison_measured": False,
            "rewrite_speedup_estimate": None,
        },
        "method": "CPU is split into parent and waited-for children. Separate import timings are not added. No rewrite-speed ceiling is inferred.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--repeats", type=int, default=9)
    parser.add_argument(
        "--package", type=Path, help="optional real DEB; otherwise build an inert demo"
    )
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    output = args.output or unique_output("benchmark.json")
    if output.exists():
        parser.error("refusing to overwrite existing evidence: " + str(output))
    with tempfile.TemporaryDirectory(prefix="debloom-benchmark-") as folder:
        package = args.package
        if package is None:
            package = Path(folder) / "debloom-demo.deb"
            run(
                [
                    sys.executable,
                    str(ROOT / "scripts/make_demo_fixture.py"),
                    str(package),
                ]
            )
        result = benchmark(package.absolute(), args.repeats)
    write_results(output, result)
    print(result["environment"]["os"])
    for group in ["stages", "fresh_process_imports"]:
        for name, row in result[group].items():
            summary = row["summary"]
            print(
                f"{name:28s} wall={summary['wall_ms']['median']:.3f}ms parent={summary['parent_cpu_ms']['median']:.3f}ms child={summary['child_cpu_ms']['median']:.3f}ms"
            )
    print("Saved unique evidence:", output)
    print("No compiled-language speedup was measured or inferred.")


if __name__ == "__main__":
    main()
