"""Atomic, recoverable publication of audit results into an output directory.

The output directory layout produced by this module is::

    <out>/
      report.json / report.md / report.html / inputs.json / summary.txt
      audit.sqlite            # append-only ledger (written by ledger.py)
      .topoaudit/
        current.json          # durable pointer to the published version
        journal.jsonl         # append-only publish/recovery phase log
        versions/<digest>/    # complete artifact set per version
          report.json ... summary.txt
          manifest.json       # artifact identities (sha256 + size per file)

Publish protocol (every step is journaled; the journal is the replayable
evidence of each phase and of every artifact identity):

1. ``stage_begin``  -> write files into ``versions/<digest>.partial`` and
   rename it to ``versions/<digest>`` (single atomic directory rename),
   then journal ``staged`` with the manifest (per-file SHA-256).
2. ``ledger_committed`` -> persist the report rows in one SQLite
   transaction (idempotent, keyed by ``result_digest``).
3. ``verified`` -> re-check the staged files against the manifest and
   cross-check the staged report against the ledger.
4. ``publish_begin`` -> durable record of the publish decision, then
   project the staged files onto the visible top-level names
   (atomic rename per file), journal ``projected``.
5. Flip ``current.json`` to the new digest (single atomic rename: this is
   the publish boundary) and journal ``published``.

A crash anywhere before the pointer flip leaves the previously published
version fully visible.  ``Publisher.recover`` converges the directory back
to a consistent state on the next run:

* incomplete staging (``.partial``) is discarded (cut point: partial
  reports written);
* a fully staged version whose ledger rows were never committed is
  discarded (cut point: reports complete, ledger not committed);
* a fully staged version with committed ledger rows but no publish
  decision is either rolled forward (verified, then published) or rolled
  back (staged files removed), according to the configured recovery
  policy; the chosen route is journaled as a ``recovery`` record;
* a visible projection that does not match the current pointer manifest
  is rebuilt from the version store.

The ledger itself stays append-only: rolling back an unpublished version
removes its staged files but never deletes committed ledger rows.
"""
from __future__ import annotations
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from .errors import PublishError
from .util import canonical_json, pretty_json, sha256_bytes, sha256_text

STATE_DIR = ".topoaudit"
VERSIONS_DIR = "versions"
JOURNAL_FILE = "journal.jsonl"
CURRENT_FILE = "current.json"
MANIFEST_FILE = "manifest.json"
PARTIAL_SUFFIX = ".partial"
TMP_GLOB = ".*.tmp-*"

# Visible projection file names.  These mirror the output constants in
# pipeline.py; the set is part of the publish contract.
VISIBLE_FILES = ("report.json", "report.md", "report.html", "inputs.json", "summary.txt")

RECOVER_ROLL_FORWARD = "roll-forward"
RECOVER_ROLL_BACK = "roll-back"
RECOVER_POLICIES = (RECOVER_ROLL_FORWARD, RECOVER_ROLL_BACK)

# Named fault-injection cut points (testing only).  The first three are the
# designated audit cut points; the last two harden the projection window.
FAILPOINT_STAGE_PARTIAL = "stage.partial"        # 写完部分报告
FAILPOINT_STAGE_COMPLETE = "stage.complete"      # 全部报告已写版本库，台账尚未提交
FAILPOINT_LEDGER_COMMITTED = "ledger.committed"  # 台账已记录，当前版本尚未切换
FAILPOINT_PUBLISH_PROJECTING = "publish.projecting"
FAILPOINT_PUBLISH_FLIPPED = "publish.flipped"
FAILPOINTS = (
    FAILPOINT_STAGE_PARTIAL,
    FAILPOINT_STAGE_COMPLETE,
    FAILPOINT_LEDGER_COMMITTED,
    FAILPOINT_PUBLISH_PROJECTING,
    FAILPOINT_PUBLISH_FLIPPED,
)
FAILPOINT_ENV = "TOPOAUDIT_FAIL_AT"


class FaultInjectionError(Exception):
    """Simulated crash raised at a named failpoint (testing only)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _atomic_write_text(path: Path, content: str) -> None:
    _atomic_write_bytes(path, content.encode("utf-8"))


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class Publisher:
    """Orchestrates staged publication and recovery for one output dir."""

    def __init__(self, out_dir: str | Path, fail_at: Optional[str] = None) -> None:
        self.out_dir = Path(out_dir)
        self.state_dir = self.out_dir / STATE_DIR
        self.versions_dir = self.state_dir / VERSIONS_DIR
        self.journal_path = self.state_dir / JOURNAL_FILE
        self.current_path = self.state_dir / CURRENT_FILE
        self.ledger_path = self.out_dir / "audit.sqlite"
        self.fail_at = fail_at
        self._seq: Optional[int] = None

    # ------------------------------------------------------------------
    # fault injection (testing only)
    # ------------------------------------------------------------------
    def _failpoint(self, name: str) -> None:
        requested = self.fail_at or os.environ.get(FAILPOINT_ENV, "").strip()
        if requested == name:
            raise FaultInjectionError(f"injected fault at failpoint: {name}")

    # ------------------------------------------------------------------
    # journal
    # ------------------------------------------------------------------
    def read_journal(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            text = self.journal_path.read_text(encoding="utf-8")
        except OSError:
            return records
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # A crash may leave a half-written trailing line; ignore it.
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    def _journal(self, event: str, **fields: Any) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self._seq is None:
            self._seq = len(self.read_journal())
        self._seq += 1
        record = {"seq": self._seq, "ts": _now_iso(), "event": event}
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with open(self.journal_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    # ------------------------------------------------------------------
    # current pointer
    # ------------------------------------------------------------------
    def current(self) -> Optional[dict[str, Any]]:
        try:
            raw = _load_json(self.current_path)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict) or not raw.get("result_digest"):
            return None
        return raw

    def _write_pointer(self, digest: str, manifest: dict[str, Any], previous: Optional[str]) -> None:
        pointer = {
            "result_digest": digest,
            "previous": previous,
            "published_at": _now_iso(),
            "manifest": manifest,
        }
        _atomic_write_text(self.current_path, pretty_json(pointer))
        _fsync_dir(self.state_dir)

    # ------------------------------------------------------------------
    # publish phases
    # ------------------------------------------------------------------
    def stage(self, digest: str, files: dict[str, str], meta: dict[str, Any]) -> Path:
        """Write the full artifact set into the version store (not visible)."""
        self._journal("stage_begin", result_digest=digest, **meta)
        expected = {name: sha256_text(files[name]) for name in VISIBLE_FILES}
        final = self.versions_dir / digest
        if final.is_dir():
            issues = self._check_version_files(final)
            if not issues:
                manifest = _load_json(final / MANIFEST_FILE)
                hashes = {name: manifest["files"][name]["sha256"] for name in VISIBLE_FILES}
                if hashes == expected:
                    self._journal("staged", result_digest=digest, manifest=manifest, reused=True)
                    self._failpoint(FAILPOINT_STAGE_COMPLETE)
                    return final
            shutil.rmtree(final, ignore_errors=True)
        partial = self.versions_dir / f"{digest}{PARTIAL_SUFFIX}"
        if partial.exists():
            shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        for index, name in enumerate(VISIBLE_FILES):
            _atomic_write_text(partial / name, files[name])
            if index == 0:
                self._failpoint(FAILPOINT_STAGE_PARTIAL)
        manifest = {
            "result_digest": digest,
            "schema": 1,
            "files": {
                name: {"sha256": expected[name], "size": len(files[name].encode("utf-8"))}
                for name in VISIBLE_FILES
            },
        }
        _atomic_write_text(partial / MANIFEST_FILE, pretty_json(manifest))
        _fsync_dir(partial)
        os.replace(partial, final)
        _fsync_dir(self.versions_dir)
        self._journal("staged", result_digest=digest, manifest=manifest)
        self._failpoint(FAILPOINT_STAGE_COMPLETE)
        return final

    def commit_ledger(self, report: dict[str, Any], digest: str) -> None:
        """Persist the report rows in one idempotent SQLite transaction."""
        from .ledger import write_report
        write_report(self.ledger_path, report)
        self._journal("ledger_committed", result_digest=digest)
        self._failpoint(FAILPOINT_LEDGER_COMMITTED)

    def verify_staged(self, digest: str):
        """Re-check staged files against the manifest and against the ledger."""
        from .ledger_verify import verify_report_and_ledger
        version_dir = self.versions_dir / digest
        issues = self._check_version_files(version_dir)
        if issues:
            raise PublishError(
                "TOPO-PUB-002",
                "待发布产物与清单不一致: " + "; ".join(issues),
                str(version_dir),
            )
        result = verify_report_and_ledger(version_dir / "report.json", self.ledger_path)
        if not result.ok:
            raise PublishError(
                "TOPO-PUB-003",
                "待发布版本未通过台账交叉校验: " + "; ".join(result.issues),
                str(self.ledger_path),
            )
        self._journal(
            "verified",
            result_digest=digest,
            findings=result.finding_count,
            layers=result.layer_count,
        )
        return result

    def publish(self, digest: str, previous: Optional[str]) -> None:
        """Publish a verified version: project files, then flip the pointer."""
        version_dir = self.versions_dir / digest
        manifest = _load_json(version_dir / MANIFEST_FILE)
        self._journal(
            "publish_begin",
            result_digest=digest,
            previous=previous,
            manifest_sha256=sha256_text(canonical_json(manifest)),
        )
        self._project(digest, manifest)
        self._journal("projected", result_digest=digest)
        self._write_pointer(digest, manifest, previous)
        self._failpoint(FAILPOINT_PUBLISH_FLIPPED)
        self._journal("published", result_digest=digest, previous=previous)

    def _project(self, digest: str, manifest: dict[str, Any]) -> None:
        version_dir = self.versions_dir / digest
        for index, name in enumerate(VISIBLE_FILES):
            _atomic_write_bytes(self.out_dir / name, (version_dir / name).read_bytes())
            if index == 0:
                self._failpoint(FAILPOINT_PUBLISH_PROJECTING)
        _fsync_dir(self.out_dir)

    # ------------------------------------------------------------------
    # status helpers
    # ------------------------------------------------------------------
    def is_current(self, digest: str) -> bool:
        pointer = self.current()
        if not pointer or str(pointer.get("result_digest")) != digest:
            return False
        if self.projection_issues():
            return False
        from .ledger import has_run
        return has_run(self.ledger_path, digest)

    def note_noop(self, digest: str) -> None:
        self._journal("run_noop", result_digest=digest)

    def projection_issues(self) -> list[str]:
        """Differences between the visible files and the pointer manifest."""
        pointer = self.current()
        if pointer is None:
            return []
        manifest = pointer.get("manifest")
        if not isinstance(manifest, dict):
            return ["当前指针缺少发布清单"]
        files = manifest.get("files")
        if not isinstance(files, dict):
            return ["当前指针清单缺少 files 段"]
        issues: list[str] = []
        for name in VISIBLE_FILES:
            entry = files.get(name)
            if not isinstance(entry, dict):
                issues.append(f"发布清单缺少条目: {name}")
                continue
            target = self.out_dir / name
            if not target.is_file():
                issues.append(f"可见文件缺失: {name}")
                continue
            if sha256_bytes(target.read_bytes()) != entry.get("sha256"):
                issues.append(f"可见文件与发布清单不一致: {name}")
        return issues

    def _check_version_files(self, version_dir: Path) -> list[str]:
        issues: list[str] = []
        try:
            manifest = _load_json(version_dir / MANIFEST_FILE)
        except (OSError, json.JSONDecodeError):
            return ["清单缺失或不可解析"]
        if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
            return ["清单缺少 files 段"]
        if str(manifest.get("result_digest", "")) != version_dir.name:
            issues.append("清单 result_digest 与版本目录名不一致")
        files = manifest["files"]
        for name in VISIBLE_FILES:
            entry = files.get(name)
            target = version_dir / name
            if not isinstance(entry, dict):
                issues.append(f"清单缺少条目: {name}")
                continue
            if not target.is_file():
                issues.append(f"版本库缺少文件: {name}")
                continue
            if sha256_bytes(target.read_bytes()) != entry.get("sha256"):
                issues.append(f"版本库文件摘要与清单不符: {name}")
        return issues

    def _ledger_complete(self, version_dir: Path) -> bool:
        from .ledger_verify import verify_report_and_ledger
        try:
            result = verify_report_and_ledger(version_dir / "report.json", self.ledger_path)
        except Exception:
            # 台账缺失或不可读：视为该版本从未提交
            return False
        return result.ok

    # ------------------------------------------------------------------
    # recovery
    # ------------------------------------------------------------------
    def recover(self, policy: str = RECOVER_ROLL_FORWARD) -> list[dict[str, Any]]:
        """Converge the output directory back to a consistent state.

        Returns the list of recovery actions; every action is also
        journaled as a ``recovery`` record so the chosen route stays
        traceable after restart.
        """
        if policy not in RECOVER_POLICIES:
            raise PublishError("TOPO-PUB-001", f"未知恢复策略: {policy}", str(self.out_dir))
        actions: list[dict[str, Any]] = []
        if not self.state_dir.exists():
            return actions
        self._clean_stale_tmp()
        if not self.journal_path.is_file():
            if self.current_path.exists():
                raise PublishError(
                    "TOPO-PUB-005",
                    "发布日志缺失，无法安全恢复发布状态",
                    str(self.journal_path),
                )
            return actions
        journal = self.read_journal()
        pointer = self.current()
        current_digest = str(pointer["result_digest"]) if pointer else None
        published_digests = {
            str(record.get("result_digest"))
            for record in journal
            if record.get("event") == "published" and record.get("result_digest")
        }

        if self.versions_dir.is_dir():
            # Cut point 1: partial staging must never become visible.
            for entry in sorted(self.versions_dir.iterdir()):
                if entry.is_dir() and entry.name.endswith(PARTIAL_SUFFIX):
                    shutil.rmtree(entry, ignore_errors=True)
                    digest = entry.name[: -len(PARTIAL_SUFFIX)]
                    actions.append(
                        self._record_recovery(
                            "discard_partial",
                            digest,
                            "暂存目录不完整，已清理；当前发布版本不受影响",
                        )
                    )
            # Complete but never-published versions.
            for entry in sorted(self.versions_dir.iterdir()):
                if not entry.is_dir() or entry.name.endswith(PARTIAL_SUFFIX):
                    continue
                digest = entry.name
                if digest == current_digest or digest in published_digests:
                    continue  # 当前版本或历史已发布版本：保留
                issues = self._check_version_files(entry)
                if issues:
                    shutil.rmtree(entry, ignore_errors=True)
                    actions.append(
                        self._record_recovery(
                            "discard_corrupt",
                            digest,
                            "版本库产物不完整: " + "; ".join(issues),
                        )
                    )
                    continue
                if not self._ledger_complete(entry):
                    # Cut point 2: ledger transaction never committed.
                    shutil.rmtree(entry, ignore_errors=True)
                    actions.append(
                        self._record_recovery(
                            "roll_back",
                            digest,
                            "台账未提交该版本，清理未发布产物，保留当前发布版本",
                        )
                    )
                    continue
                # Cut point 3: ledger committed, pointer never switched.
                if policy == RECOVER_ROLL_FORWARD:
                    try:
                        self.verify_staged(digest)
                    except PublishError as exc:
                        shutil.rmtree(entry, ignore_errors=True)
                        actions.append(
                            self._record_recovery(
                                "roll_back",
                                digest,
                                f"前滚校验失败，回退并清理未发布产物: {exc.message}",
                            )
                        )
                        continue
                    actions.append(
                        self._record_recovery(
                            "roll_forward",
                            digest,
                            "台账已提交且产物校验通过，前滚发布为当前版本",
                        )
                    )
                    self.publish(digest, previous=current_digest)
                    current_digest = digest
                    published_digests.add(digest)
                else:
                    shutil.rmtree(entry, ignore_errors=True)
                    actions.append(
                        self._record_recovery(
                            "roll_back",
                            digest,
                            "台账已提交但恢复策略选择回退，清理未发布产物",
                        )
                    )

        # Pointer lost but the journal still proves a publish happened.
        pointer = self.current()
        if pointer is None:
            for record in reversed(self.read_journal()):
                if record.get("event") != "published" or not record.get("result_digest"):
                    continue
                digest = str(record["result_digest"])
                version_dir = self.versions_dir / digest
                if not version_dir.is_dir() or self._check_version_files(version_dir):
                    continue
                manifest = _load_json(version_dir / MANIFEST_FILE)
                self._project(digest, manifest)
                self._write_pointer(digest, manifest, previous=record.get("previous"))
                actions.append(
                    self._record_recovery(
                        "restore_pointer",
                        digest,
                        "当前指针缺失，根据发布日志重建",
                    )
                )
                pointer = self.current()
                break

        # The visible projection must always match the current pointer.
        if pointer is not None:
            digest = str(pointer["result_digest"])
            issues = self.projection_issues()
            if issues:
                version_dir = self.versions_dir / digest
                if not version_dir.is_dir():
                    raise PublishError(
                        "TOPO-PUB-004",
                        "当前发布版本的产物库缺失，无法修复可见投影",
                        str(version_dir),
                    )
                manifest = _load_json(version_dir / MANIFEST_FILE)
                self._project(digest, manifest)
                actions.append(
                    self._record_recovery(
                        "repair_projection",
                        digest,
                        "可见投影与发布清单不一致，已按版本库重建: " + "; ".join(issues),
                    )
                )
            journal = self.read_journal()
            if not any(
                record.get("event") == "published" and record.get("result_digest") == digest
                for record in journal
            ):
                self._journal(
                    "published",
                    result_digest=digest,
                    previous=pointer.get("previous"),
                    reconciled=True,
                )
                actions.append(
                    self._record_recovery(
                        "complete_publish",
                        digest,
                        "补记发布完成记录",
                    )
                )
        return actions

    def _record_recovery(self, route: str, digest: str, reason: str) -> dict[str, Any]:
        self._journal("recovery", route=route, result_digest=digest, reason=reason)
        return {"route": route, "result_digest": digest, "reason": reason}

    def _clean_stale_tmp(self) -> None:
        for directory in (self.out_dir, self.state_dir, self.versions_dir):
            if not directory.is_dir():
                continue
            for entry in directory.glob(TMP_GLOB):
                try:
                    if entry.is_file():
                        entry.unlink()
                except OSError:
                    pass


def projection_issues_for_report(report_path: str | Path, digest: str) -> list[str]:
    """Check a report against the publish state of its output directory.

    Soft check: directories without publish state (``.topoaudit``) are
    ignored so historical or copied reports verify exactly as before.
    """
    path = Path(report_path)
    publisher = Publisher(path.parent)
    if not (publisher.state_dir / CURRENT_FILE).is_file():
        return []
    pointer = publisher.current()
    if pointer is None:
        return []
    issues: list[str] = []
    current_digest = str(pointer.get("result_digest", ""))
    if digest and current_digest and digest != current_digest:
        issues.append(f"报告版本 {digest} 不是当前发布版本 {current_digest}")
    issues.extend(publisher.projection_issues())
    return issues
