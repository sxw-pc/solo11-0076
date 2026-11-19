"""Filesystem layout and atomic primitives for result publication.

An output directory managed by the publish pipeline contains the shared
``audit.sqlite`` ledger, immutable ``versions/<digest>/`` directories with
per-version ``manifest.json`` files, in-flight ``.staging/<digest>/``
directories, and the ``current`` symlink - the single atomic publish
boundary, swapped with ``os.replace`` only after staging, ledger commit
and verification have all succeeded.  The classic top-level names
(``report.json`` ...) are kept as relative symlinks through ``current/``.

Fault-injection cut points (env var ``TOPOAUDIT_FAULT_AT`` or
:func:`set_fault_hook`): ``staging.partial``, ``staging.complete``,
``ledger.committed``, ``publish.verified``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .errors import ReportError
from .util import sha256_bytes

REPORT_JSON_NAME = "report.json"
REPORT_MD_NAME = "report.md"
REPORT_HTML_NAME = "report.html"
INPUTS_JSON_NAME = "inputs.json"
SUMMARY_TXT_NAME = "summary.txt"
LEDGER_DB_NAME = "audit.sqlite"
REPORT_FILE_NAMES = (
    REPORT_JSON_NAME,
    REPORT_MD_NAME,
    REPORT_HTML_NAME,
    INPUTS_JSON_NAME,
    SUMMARY_TXT_NAME,
)
CURRENT_LINK = "current"
VERSIONS_DIR = "versions"
STAGING_DIR = ".staging"
QUARANTINE_DIR = ".quarantine"
MANIFEST_NAME = "manifest.json"

FAULT_STAGING_PARTIAL = "staging.partial"
FAULT_STAGING_COMPLETE = "staging.complete"
FAULT_LEDGER_COMMITTED = "ledger.committed"
FAULT_PUBLISH_VERIFIED = "publish.verified"
FAULT_POINTS = (
    FAULT_STAGING_PARTIAL,
    FAULT_STAGING_COMPLETE,
    FAULT_LEDGER_COMMITTED,
    FAULT_PUBLISH_VERIFIED,
)
FAULT_ENV_VAR = "TOPOAUDIT_FAULT_AT"

POLICY_ROLL_FORWARD = "roll-forward"
POLICY_ROLLBACK = "rollback"
POLICY_ENV_VAR = "TOPOAUDIT_RECOVERY_POLICY"

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

_fault_hook: Optional[Callable[[str], None]] = None


def set_fault_hook(hook: Optional[Callable[[str], None]]) -> None:
    """Install a test hook invoked at each named fault-injection point."""
    global _fault_hook
    _fault_hook = hook


def _fault(point: str) -> None:
    if _fault_hook is not None:
        _fault_hook(point)
    if os.environ.get(FAULT_ENV_VAR) == point:
        # Simulate a hard crash: no cleanup, no atexit handlers.
        os._exit(99)


def resolve_recovery_policy(policy: Optional[str] = None) -> str:
    chosen = policy or os.environ.get(POLICY_ENV_VAR) or POLICY_ROLL_FORWARD
    if chosen not in (POLICY_ROLL_FORWARD, POLICY_ROLLBACK):
        raise ReportError(
            "TOPO-RPT-006",
            f"未知恢复策略: {chosen}（可选 {POLICY_ROLL_FORWARD} / {POLICY_ROLLBACK}）",
            POLICY_ENV_VAR,
        )
    return chosen


@dataclass
class RecoveryAction:
    """One decision taken while settling an interrupted publish session."""

    action: str
    digest: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishOutcome:
    digest: str
    previous_current: Optional[str]
    already_current: bool
    version_dir: Path


def is_digest(value: str) -> bool:
    return bool(_DIGEST_RE.match(value))


def fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_file_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def remove_tree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def version_dir(out_dir: Path, digest: str) -> Path:
    return Path(out_dir) / VERSIONS_DIR / digest


def staging_dir(out_dir: Path, digest: str) -> Path:
    return Path(out_dir) / STAGING_DIR / digest


def current_digest(out_dir: Path) -> Optional[str]:
    """Digest the ``current`` symlink points at, even if the target is gone."""
    link = Path(out_dir) / CURRENT_LINK
    if not link.is_symlink():
        return None
    try:
        target = os.readlink(link)
    except OSError:
        return None
    name = Path(target).name
    return name if is_digest(name) else None


def read_manifest(directory: Path) -> Optional[dict[str, Any]]:
    try:
        raw = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("files"), dict):
        return None
    return raw


def verify_files_against_manifest(directory: Path, manifest: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for name, meta in sorted(manifest.get("files", {}).items()):
        target = directory / name
        if not target.is_file():
            issues.append(f"缺少产物文件 {name}")
            continue
        actual = sha256_bytes(target.read_bytes())
        if actual != meta.get("sha256"):
            issues.append(f"产物 {name} 的 SHA-256 与发布清单不一致")
    return issues


def version_intact(out_dir: Path, digest: str) -> bool:
    directory = version_dir(out_dir, digest)
    if not directory.is_dir():
        return False
    manifest = read_manifest(directory)
    if manifest is None or str(manifest.get("result_digest", "")) != digest:
        return False
    return not verify_files_against_manifest(directory, manifest)


def switch_current(out_dir: Path, digest: str) -> None:
    """Atomically point ``current`` at ``versions/<digest>``."""
    tmp = Path(out_dir) / f".{CURRENT_LINK}.tmp-{os.getpid()}"
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    os.symlink(f"{VERSIONS_DIR}/{digest}", tmp)
    os.replace(tmp, Path(out_dir) / CURRENT_LINK)
    fsync_dir(Path(out_dir))


def ensure_compat_links(out_dir: Path) -> None:
    """Keep the classic top-level names pointing through ``current/``.

    The historical layout (``<out>/report.json`` ...) stays readable via
    relative symlinks, so existing review commands keep working while the
    atomic unit of publication remains the ``current`` pointer.
    """
    out_dir = Path(out_dir)
    for name in REPORT_FILE_NAMES:
        link = out_dir / name
        desired = f"{CURRENT_LINK}/{name}"
        if link.is_symlink() and os.readlink(link) == desired:
            continue
        if link.is_dir() and not link.is_symlink():
            raise ReportError(
                "TOPO-RPT-007",
                f"无法创建兼容链接，目标已存在且是目录: {link}",
                str(link),
            )
        tmp = out_dir / f".{name}.tmp-{os.getpid()}"
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        os.symlink(desired, tmp)
        os.replace(tmp, link)
    fsync_dir(out_dir)


def cleanup_staging_root(out_dir: Path) -> None:
    root = Path(out_dir) / STAGING_DIR
    try:
        if root.is_dir() and not any(root.iterdir()):
            root.rmdir()
    except OSError:
        pass


def quarantine(out_dir: Path, path: Path, label: str) -> Path:
    """Move contradictory artifacts aside instead of destroying them."""
    root = Path(out_dir) / QUARANTINE_DIR
    root.mkdir(parents=True, exist_ok=True)
    index = 0
    while True:
        candidate = root / f"{label}.{index}"
        if not candidate.exists():
            break
        index += 1
    os.rename(path, candidate)
    fsync_dir(root)
    return candidate


def promote_staging(out_dir: Path, digest: str) -> tuple[bool, Optional[Path]]:
    """Move the staging dir to ``versions/<digest>``.

    Returns ``(reused, quarantined)``: ``reused`` is True when an identical
    version directory already existed, and ``quarantined`` is the path a
    contradictory pre-existing directory was moved to.
    """
    out_dir = Path(out_dir)
    staging = staging_dir(out_dir, digest)
    target = version_dir(out_dir, digest)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = read_manifest(target)
        incoming = read_manifest(staging)
        if (
            existing is not None
            and incoming is not None
            and existing.get("files") == incoming.get("files")
            and not verify_files_against_manifest(target, existing)
        ):
            remove_tree(staging)
            return True, None
        quarantined = quarantine(out_dir, target, digest)
        os.rename(staging, target)
        fsync_dir(target)
        fsync_dir(target.parent)
        cleanup_staging_root(out_dir)
        return False, quarantined
    os.rename(staging, target)
    fsync_dir(target)
    fsync_dir(target.parent)
    cleanup_staging_root(out_dir)
    return False, None
