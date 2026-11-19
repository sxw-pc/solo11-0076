"""Crash recovery for interrupted publish sessions.

Recovery settles every incomplete publish session found in an output
directory before any new work starts:

* staging without a committed ledger intent (cut points ``staging.partial``
  / ``staging.complete``) is rolled back - the previously published version
  stays current;
* a committed-but-unpublished session (cut points ``ledger.committed`` /
  ``publish.verified``) is either rolled forward after full verification
  (default) or rolled back, per the recovery policy; the choice is appended
  to the ledger ``publish_log`` so the route stays traceable;
* the ``current`` pointer is reconciled with the publish log, and
  pre-versioning flat output layouts are adopted without data loss.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .ledger import (
    EVENT_LEDGER_COMMITTED,
    EVENT_PUBLISHED,
    EVENT_VERIFICATION_FAILED,
    EVENT_VERIFIED,
    record_publish_event,
    record_recovery_rollback,
)
from .ledger_query import (
    committed_intent_files,
    events_by_digest,
    get_publish_log,
    session_state,
    was_published,
)
from .publish_layout import (
    CURRENT_LINK,
    LEDGER_DB_NAME,
    POLICY_ROLLBACK,
    STAGING_DIR,
    RecoveryAction,
    cleanup_staging_root,
    current_digest,
    ensure_compat_links,
    is_digest,
    promote_staging,
    quarantine,
    read_manifest,
    remove_tree,
    resolve_recovery_policy,
    switch_current,
    version_dir,
    version_intact,
)
from .publish_legacy import adopt_legacy_layout
from .publish_verify import verify_candidate


def recover_pending(out_dir: Path, policy: Optional[str] = None) -> list[RecoveryAction]:
    """Settle every interrupted publish session in ``out_dir``."""
    out_dir = Path(out_dir)
    chosen = resolve_recovery_policy(policy)
    actions: list[RecoveryAction] = []
    if not out_dir.is_dir():
        return actions
    ledger_path = out_dir / LEDGER_DB_NAME

    adopt_legacy_layout(out_dir, ledger_path, actions)

    staging_root = out_dir / STAGING_DIR
    staged: dict[str, Path] = {}
    if staging_root.is_dir():
        for child in sorted(staging_root.iterdir()):
            if child.is_dir():
                staged[child.name] = child

    grouped = events_by_digest(ledger_path)
    for digest, directory in staged.items():
        if not is_digest(digest):
            remove_tree(directory)
            actions.append(
                RecoveryAction("cleanup", digest, "invalid_staging_name", {"removed": str(directory)})
            )
            continue
        state = session_state(grouped.get(digest, []))
        if state == EVENT_PUBLISHED:
            remove_tree(directory)
            actions.append(RecoveryAction("cleanup", digest, "staging_of_published_version", {}))
        elif state in (EVENT_LEDGER_COMMITTED, EVENT_VERIFIED, EVENT_VERIFICATION_FAILED):
            actions.append(
                _settle_committed_session(
                    out_dir, ledger_path, digest, chosen, staging=directory
                )
            )
        else:
            reason = (
                "staging_incomplete"
                if read_manifest(directory) is None
                else "ledger_not_committed"
            )
            remove_tree(directory)
            record_recovery_rollback(
                ledger_path,
                digest,
                {
                    "reason": reason,
                    "policy": chosen,
                    "staging_removed": True,
                    "preserved_current": current_digest(out_dir),
                },
            )
            actions.append(RecoveryAction("rollback", digest, reason, {"staging_removed": True}))
    cleanup_staging_root(out_dir)

    grouped = events_by_digest(ledger_path)
    for digest, events in sorted(grouped.items()):
        state = session_state(events)
        if state not in (EVENT_LEDGER_COMMITTED, EVENT_VERIFIED, EVENT_VERIFICATION_FAILED):
            continue
        if digest in staged:
            continue
        directory = version_dir(out_dir, digest)
        if directory.is_dir():
            actions.append(
                _settle_committed_session(
                    out_dir, ledger_path, digest, chosen, staging=None
                )
            )
        else:
            record_recovery_rollback(
                ledger_path,
                digest,
                {
                    "reason": "artifacts_missing",
                    "policy": chosen,
                    "preserved_current": current_digest(out_dir),
                },
            )
            actions.append(RecoveryAction("rollback", digest, "artifacts_missing", {}))

    _reconcile_current(out_dir, ledger_path, actions)
    if current_digest(out_dir) is not None:
        ensure_compat_links(out_dir)
    return actions


def _settle_committed_session(
    out_dir: Path,
    ledger_path: Path,
    digest: str,
    policy: str,
    staging: Optional[Path],
) -> RecoveryAction:
    """Resolve a cut-point-3 session: ledger committed, pointer not switched."""
    directory = staging if staging is not None else version_dir(out_dir, digest)
    previous = current_digest(out_dir)
    if policy == POLICY_ROLLBACK:
        issues: list[str] = []
        reason = "policy"
    else:
        manifest = read_manifest(directory)
        issues = verify_candidate(
            directory,
            manifest,
            ledger_path,
            digest,
            committed_intent_files(ledger_path, digest),
        )
        reason = "verification_failed"
    if policy == POLICY_ROLLBACK or issues:
        quarantined = False
        if staging is not None and staging.exists():
            remove_tree(staging)
        target = version_dir(out_dir, digest)
        if (
            target.is_dir()
            and current_digest(out_dir) != digest
            and not was_published(ledger_path, digest)
        ):
            # Only stray artifacts of the interrupted session are moved aside;
            # a version that was published earlier stays reviewable forever.
            quarantine(out_dir, target, digest)
            quarantined = True
        detail = {
            "reason": reason,
            "policy": policy,
            "issues": issues,
            "staging_removed": staging is not None,
            "version_quarantined": quarantined,
            "preserved_current": previous,
        }
        record_recovery_rollback(ledger_path, digest, detail)
        return RecoveryAction("rollback", digest, reason, detail)

    if staging is not None:
        promote_staging(out_dir, digest)
    switch_current(out_dir, digest)
    ensure_compat_links(out_dir)
    manifest = read_manifest(version_dir(out_dir, digest)) or {}
    record_publish_event(
        ledger_path,
        digest,
        EVENT_PUBLISHED,
        {
            "files": manifest.get("files", {}),
            "previous_current": previous,
            "via": "recovery_rollforward",
            "policy": policy,
        },
    )
    return RecoveryAction(
        "rollforward",
        digest,
        "committed_session_verified",
        {"previous_current": previous, "policy": policy},
    )


def _reconcile_current(
    out_dir: Path,
    ledger_path: Path,
    actions: list[RecoveryAction],
) -> None:
    """Make the ``current`` pointer and the publish log agree."""
    current = current_digest(out_dir)
    latest_published: Optional[str] = None
    for row in get_publish_log(ledger_path):
        if row["event"] == EVENT_PUBLISHED:
            latest_published = row["result_digest"]
    if current is None:
        if latest_published is not None and version_intact(out_dir, latest_published):
            switch_current(out_dir, latest_published)
            actions.append(
                RecoveryAction("heal", latest_published, "current_pointer_restored", {})
            )
        return
    if not version_dir(out_dir, current).is_dir():
        # Dangling pointer: fall back to the latest fully published version.
        try:
            (out_dir / CURRENT_LINK).unlink()
        except OSError:
            pass
        if latest_published is not None and version_intact(out_dir, latest_published):
            switch_current(out_dir, latest_published)
        actions.append(
            RecoveryAction("heal", current, "dangling_current_removed", {"fallback": latest_published})
        )
        return
    if latest_published is None or current == latest_published:
        return
    # The pointer moved but the ``published`` event was never logged (crash
    # between the atomic swap and the log append).  Fully re-verify the
    # candidate before backfilling the missing publish record.
    manifest = read_manifest(version_dir(out_dir, current))
    issues = verify_candidate(
        version_dir(out_dir, current),
        manifest,
        ledger_path,
        current,
        committed_intent_files(ledger_path, current),
    )
    if not issues:
        record_publish_event(
            ledger_path,
            current,
            EVENT_PUBLISHED,
            {
                "files": (manifest or {}).get("files", {}),
                "previous_current": latest_published,
                "via": "recovery_heal",
            },
        )
        actions.append(
            RecoveryAction("heal", current, "publish_event_backfilled", {"pointer_kept": True})
        )
    elif version_intact(out_dir, latest_published):
        switch_current(out_dir, latest_published)
        record_recovery_rollback(
            ledger_path,
            current,
            {
                "reason": "pointer_ahead_of_log",
                "issues": issues,
                "preserved_current": latest_published,
            },
        )
        actions.append(
            RecoveryAction(
                "rollback", current, "pointer_ahead_of_log", {"restored": latest_published}
            )
        )
