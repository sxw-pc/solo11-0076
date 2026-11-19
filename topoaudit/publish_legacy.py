"""Adoption of pre-versioning flat output layouts without data loss.

Older runs wrote ``report.json`` and friends directly into the output
directory.  Those files are the previously published result and must not
be lost, so they are adopted as a content-addressed version before any
new publication replaces the top-level names with compat links.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .ledger import EVENT_PUBLISHED, record_publish_event
from .ledger_query import run_exists
from .publish_layout import (
    MANIFEST_NAME,
    REPORT_FILE_NAMES,
    REPORT_JSON_NAME,
    RecoveryAction,
    current_digest,
    fsync_dir,
    is_digest,
    quarantine,
    switch_current,
    version_dir,
    write_file_atomic,
)
from .util import pretty_json, sha256_bytes


def adopt_legacy_layout(
    out_dir: Path, ledger_path: Path, actions: list[RecoveryAction]
) -> None:
    """Move a flat legacy output set into ``versions/<digest>/`` untouched."""
    out_dir = Path(out_dir)
    report_file = out_dir / REPORT_JSON_NAME
    if not report_file.is_file() or report_file.is_symlink():
        return
    digest: Optional[str] = None
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
        candidate = str(report.get("result_digest", ""))
        if is_digest(candidate):
            digest = candidate
    except (OSError, ValueError):
        digest = None
    present = [name for name in REPORT_FILE_NAMES if (out_dir / name).is_file()]
    if digest is None:
        moved = quarantine(out_dir, report_file, "legacy-unidentified")
        for name in present:
            if name == REPORT_JSON_NAME:
                continue
            quarantine(out_dir, out_dir / name, "legacy-unidentified")
        actions.append(
            RecoveryAction("quarantine", "-", "legacy_layout_unidentified", {"moved": str(moved)})
        )
        return
    target = version_dir(out_dir, digest)
    if target.exists():
        for name in present:
            quarantine(out_dir, out_dir / name, f"legacy-{digest}")
        actions.append(
            RecoveryAction("quarantine", digest, "legacy_layout_conflicts_with_version", {})
        )
        return
    target.mkdir(parents=True)
    files_meta: dict[str, Any] = {}
    for name in present:
        data = (out_dir / name).read_bytes()
        write_file_atomic(target / name, data)
        files_meta[name] = {"sha256": sha256_bytes(data), "size_bytes": len(data)}
        (out_dir / name).unlink()
    manifest = {
        "schema_version": report.get("schema_version", ""),
        "result_digest": digest,
        "input_digest": report.get("input_digest", ""),
        "config_digest": report.get("config_digest", ""),
        "run_label": report.get("run_label", ""),
        "adopted_from_legacy_layout": True,
        "files": files_meta,
    }
    write_file_atomic(target / MANIFEST_NAME, pretty_json(manifest).encode("utf-8"))
    fsync_dir(target)
    fsync_dir(target.parent)
    if Path(ledger_path).is_file() and run_exists(ledger_path, digest):
        record_publish_event(
            ledger_path,
            digest,
            EVENT_PUBLISHED,
            {
                "files": files_meta,
                "previous_current": None,
                "via": "legacy_adopted",
            },
        )
    if current_digest(out_dir) is None:
        # Adopt the legacy set as the visible result only when no newer
        # pointer exists; otherwise it is archived for review only.
        switch_current(out_dir, digest)
    actions.append(RecoveryAction("adopt", digest, "legacy_layout_adopted", {"files": present}))
