"""Bounded per-user deployment and guarded rollback. No elevated commands."""

import hashlib
import json
import os
import tempfile
import time
import uuid
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(path):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError("Refusing a symlink target: " + str(path))
    if not path.exists():
        return None
    if not path.is_file():
        raise RuntimeError("Not a regular file: " + str(path))
    return {"sha256": digest(path), "mode": path.stat().st_mode & 0o777}


def atomic_write(path, data, mode):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def deploy(files, state_root):
    # Freeze the entire target set before the first write.
    before = {str(p): snapshot(p) for p in files}
    folder = Path(state_root) / (
        time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    )
    folder.mkdir(parents=True, mode=0o700)
    records = []
    for index, (p, (data, mode)) in enumerate(files.items()):
        p = Path(p)
        backup = folder / f"{index:03d}.backup"
        if before[str(p)] is not None:
            backup.write_bytes(p.read_bytes())
            backup.chmod(0o600)
            if digest(backup) != before[str(p)]["sha256"]:
                raise RuntimeError("Target changed while backing up: " + str(p))
        records.append(
            {
                "target": str(p),
                "backup": str(backup) if before[str(p)] else None,
                "before": before[str(p)],
                "after": {"sha256": hashlib.sha256(data).hexdigest(), "mode": mode},
                "applied": False,
            }
        )
    receipt = folder / "receipt.json"

    def save():
        atomic_write(
            receipt,
            json.dumps({"version": 1, "files": records}, indent=2).encode() + b"\n",
            0o600,
        )

    save()
    for record, (p, (data, mode)) in zip(records, files.items()):
        if snapshot(p) != before[str(p)]:
            raise RuntimeError("Target changed before deployment: " + str(p))
        atomic_write(p, data, mode)
        record["applied"] = True
        save()
    return receipt


def rollback(receipt, apply=False):
    receipt = Path(receipt)
    records = json.loads(receipt.read_text())["files"]
    pending = []
    for record in records:
        if not record["applied"]:
            continue
        target = Path(record["target"])
        current = snapshot(target)
        if current == record["before"]:
            continue
        if current != record["after"]:
            raise RuntimeError(
                "Later edits detected; refusing rollback: " + str(target)
            )
        if record["backup"] and digest(record["backup"]) != record["before"]["sha256"]:
            raise RuntimeError("Backup changed: " + record["backup"])
        pending.append(record)
    if apply:
        for index, record in enumerate(pending):
            target = Path(record["target"])
            if snapshot(target) != record["after"]:
                raise RuntimeError("Target changed before restore: " + str(target))
            if record["backup"]:
                atomic_write(
                    target,
                    Path(record["backup"]).read_bytes(),
                    record["before"]["mode"],
                )
            else:
                archive = receipt.parent / "removed"
                archive.mkdir(exist_ok=True, mode=0o700)
                os.replace(target, archive / f"{index:03d}-{target.name}")
    return len(pending)
