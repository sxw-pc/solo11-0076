"""Atomic, recoverable publication of audit results into an output directory.

Reviewers only ever look at the *current* result.  The publish pipeline
guarantees that the current result is always one complete, verified
version:

* every version is content-addressed by its ``result_digest`` and stored
  under ``versions/<digest>/`` together with a ``manifest.json`` recording
  the SHA-256 of each artifact (see :mod:`topoaudit.publish_layout`);
* new results are staged under ``.staging/<digest>/`` where reviewers never
  look, then verified (file hashes, ledger cross-check, per-file identity,
  see :mod:`topoaudit.publish_verify`) before anything becomes visible;
* the ledger rows and the ``ledger_committed`` publish intent are written
  in one SQLite transaction (see :mod:`topoaudit.ledger`), so the durable
  publish decision and the ledger content commit or roll back together;
* the only publish boundary is the ``current`` symlink, swapped atomically
  after every verification has passed;
* every publish decision is appended to the ledger ``publish_log`` table,
  which makes interrupted runs recoverable and the recovery route
  (roll-forward vs rollback) traceable (see :mod:`topoaudit.publish_recover`).

Fault-injection cut points used by the recovery drills (env var
``TOPOAUDIT_FAULT_AT`` or :func:`set_fault_hook`):

* ``staging.partial``   - some report files written (crash during staging)
* ``staging.complete``  - all files + manifest written, ledger not committed
* ``ledger.committed``  - ledger rows + intent committed, pointer not switched
* ``publish.verified``  - verification done, version not yet promoted
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .errors import ReportError
from .ledger import (
    EVENT_PUBLISHED,
    EVENT_VERIFICATION_FAILED,
    EVENT_VERIFIED,
    commit_report_with_intent,
    record_publish_event,
)
from .ledger_query import get_publish_log, run_exists
from .publish_layout import (  # noqa: F401  (re-exported public surface)
    CURRENT_LINK,
    FAULT_ENV_VAR,
    FAULT_LEDGER_COMMITTED,
    FAULT_POINTS,
    FAULT_PUBLISH_VERIFIED,
    FAULT_STAGING_COMPLETE,
    FAULT_STAGING_PARTIAL,
    LEDGER_DB_NAME,
    MANIFEST_NAME,
    POLICY_ENV_VAR,
    POLICY_ROLL_FORWARD,
    POLICY_ROLLBACK,
    QUARANTINE_DIR,
    REPORT_FILE_NAMES,
    REPORT_HTML_NAME,
    REPORT_JSON_NAME,
    REPORT_MD_NAME,
    INPUTS_JSON_NAME,
    SUMMARY_TXT_NAME,
    STAGING_DIR,
    VERSIONS_DIR,
    PublishOutcome,
    RecoveryAction,
    current_digest,
    ensure_compat_links,
    fsync_dir,
    promote_staging,
    read_manifest,
    remove_tree,
    resolve_recovery_policy,
    set_fault_hook,
    staging_dir,
    switch_current,
    version_dir,
    version_intact,
    write_file_atomic,
    _fault,
)
from .publish_recover import recover_pending  # noqa: F401
from .publish_verify import verify_candidate  # noqa: F401
from .util import pretty_json, sha256_bytes


def stage_report_files(
    directory: Path, file_contents: dict[str, str], report: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Write all artifacts plus the manifest into a private staging dir."""
    directory.mkdir(parents=True, exist_ok=False)
    files_meta: dict[str, Any] = {}
    written = 0
    for name in REPORT_FILE_NAMES:
        data = file_contents[name].encode("utf-8")
        write_file_atomic(directory / name, data)
        files_meta[name] = {"sha256": sha256_bytes(data), "size_bytes": len(data)}
        written += 1
        if written == 2:
            _fault(FAULT_STAGING_PARTIAL)
    manifest = {
        "schema_version": report.get("schema_version", ""),
        "result_digest": report.get("result_digest", ""),
        "input_digest": report.get("input_digest", ""),
        "config_digest": report.get("config_digest", ""),
        "run_label": report.get("run_label", ""),
        "files": files_meta,
    }
    manifest_bytes = pretty_json(manifest).encode("utf-8")
    write_file_atomic(directory / MANIFEST_NAME, manifest_bytes)
    fsync_dir(directory)
    _fault(FAULT_STAGING_COMPLETE)
    return manifest, sha256_bytes(manifest_bytes)


def publish_run(
    out_dir: Path,
    report: dict[str, Any],
    file_contents: dict[str, str],
    ledger_name: str = LEDGER_DB_NAME,
) -> PublishOutcome:
    """Stage, verify and atomically publish one audit result.

    Idempotent: re-running with the same content is a no-op once that
    version is current, and never duplicates ledger rows or publishes a
    mixed set of artifacts.
    """
    out_dir = Path(out_dir)
    digest = str(report["result_digest"])
    ledger_path = out_dir / ledger_name
    previous = current_digest(out_dir)
    if (
        previous == digest
        and version_intact(out_dir, digest)
        and run_exists(ledger_path, digest)
        and _last_event(ledger_path, digest) == EVENT_PUBLISHED
    ):
        ensure_compat_links(out_dir)
        return PublishOutcome(digest, previous, True, version_dir(out_dir, digest))

    staging = staging_dir(out_dir, digest)
    if staging.exists():
        remove_tree(staging)
    manifest, manifest_sha = stage_report_files(staging, file_contents, report)

    intent = {
        "manifest_sha256": manifest_sha,
        "files": manifest["files"],
        "input_digest": report.get("input_digest", ""),
    }
    commit_report_with_intent(ledger_path, report, intent)
    _fault(FAULT_LEDGER_COMMITTED)

    issues = verify_candidate(staging, manifest, ledger_path, digest, intent["files"])
    if issues:
        record_publish_event(
            ledger_path, digest, EVENT_VERIFICATION_FAILED, {"issues": issues}
        )
        raise ReportError(
            "TOPO-RPT-005",
            "发布前校验失败，当前结果保持不变: " + "; ".join(issues),
            str(staging),
        )
    record_publish_event(
        ledger_path, digest, EVENT_VERIFIED, {"files": sorted(manifest["files"])}
    )
    _fault(FAULT_PUBLISH_VERIFIED)

    reused, quarantined = promote_staging(out_dir, digest)
    switch_current(out_dir, digest)
    ensure_compat_links(out_dir)
    record_publish_event(
        ledger_path,
        digest,
        EVENT_PUBLISHED,
        {
            "files": manifest["files"],
            "manifest_sha256": manifest_sha,
            "previous_current": previous,
            "via": "run",
            "version_reused": reused,
            "conflicting_version_quarantined": str(quarantined) if quarantined else None,
        },
    )
    return PublishOutcome(digest, previous, False, version_dir(out_dir, digest))


def _last_event(ledger_path: Path, digest: str) -> Optional[str]:
    events = get_publish_log(ledger_path, digest)
    return events[-1]["event"] if events else None
