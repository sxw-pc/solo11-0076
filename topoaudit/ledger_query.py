"""Read-only query helpers for the SQLite audit ledger."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Optional
from .ledger import connect_ledger
from .model import SEVERITIES

def list_runs(path: str | Path) -> list[dict[str, Any]]:
    connection = connect_ledger(path, read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT r.result_digest, r.run_label, r.input_digest, r.config_digest,
                   r.schema_version,
                   (SELECT COUNT(*) FROM findings f
                    WHERE f.result_digest = r.result_digest) AS finding_count,
                   (SELECT COUNT(*) FROM layers l
                    WHERE l.result_digest = r.result_digest) AS layer_count
            FROM runs r
            ORDER BY r.run_label, r.result_digest
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()

def run_exists(path: str | Path, digest: str) -> bool:
    connection = connect_ledger(path, read_only=True)
    try:
        row = connection.execute(
            "SELECT 1 FROM runs WHERE result_digest = ?", (digest,)
        ).fetchone()
        return row is not None
    finally:
        connection.close()

def get_run(path: str | Path, digest: str) -> Optional[dict[str, Any]]:
    connection = connect_ledger(path, read_only=True)
    try:
        row = connection.execute(
            "SELECT * FROM runs WHERE result_digest = ?", (digest,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["summary"] = json.loads(result.pop("summary_json"))
        result["limitations"] = json.loads(result.pop("limitations_json"))
        result["layers"] = _layers(connection, digest)
        result["findings_total"] = _findings_count(connection, digest)
        return result
    finally:
        connection.close()

def get_layers(path: str | Path, digest: str) -> list[dict[str, Any]]:
    connection = connect_ledger(path, read_only=True)
    try:
        return _layers(connection, digest)
    finally:
        connection.close()
def _layers(connection: Any, digest: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT stats_json FROM layers WHERE result_digest = ?
        ORDER BY layer_id
        """,
        (digest,),
    ).fetchall()
    return [json.loads(row["stats_json"]) for row in rows]

def get_findings(
    path: str | Path,
    digest: str,
    severity: Optional[str] = None,
    layer_id: Optional[str] = None,
    rule_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    connection = connect_ledger(path, read_only=True)
    try:
        return _findings(connection, digest, severity, layer_id, rule_id)
    finally:
        connection.close()
def _findings(
    connection: Any,
    digest: str,
    severity: Optional[str],
    layer_id: Optional[str],
    rule_id: Optional[str],
) -> list[dict[str, Any]]:
    clauses = ["result_digest = ?"]
    params: list[Any] = [digest]
    if severity is not None:
        if severity not in SEVERITIES:
            raise ValueError(f"invalid severity: {severity}")
        clauses.append("severity = ?")
        params.append(severity)
    if layer_id is not None:
        clauses.append("layer_id = ?")
        params.append(layer_id)
    if rule_id is not None:
        clauses.append("rule_id = ?")
        params.append(rule_id)
    query = (
        "SELECT finding_id, rule_id, severity, layer_id, feature_id, "
        "related_json, evidence_json FROM findings WHERE "
        + " AND ".join(clauses)
        + " ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 "
        "WHEN 'info' THEN 2 ELSE 9 END, rule_id, layer_id, "
        "COALESCE(feature_id, ''), finding_id"
    )
    rows = connection.execute(query, tuple(params)).fetchall()
    return [
        {
            "finding_id": row["finding_id"],
            "rule_id": row["rule_id"],
            "severity": row["severity"],
            "layer_id": row["layer_id"],
            "feature_id": row["feature_id"],
            "related_feature_ids": json.loads(row["related_json"]),
            "evidence": json.loads(row["evidence_json"]),
        }
        for row in rows
    ]
def _findings_count(connection: Any, digest: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS count FROM findings WHERE result_digest = ?", (digest,)
    ).fetchone()
    return int(row["count"])

def get_input_files(path: str | Path, digest: str) -> list[dict[str, Any]]:
    connection = connect_ledger(path, read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT layer_id, path, format, sha256, size_bytes
            FROM input_files WHERE result_digest = ? ORDER BY layer_id
            """,
            (digest,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()

def get_rule_catalog(path: str | Path) -> list[dict[str, Any]]:
    connection = connect_ledger(path, read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT rule_id, severity, title, description, evidence_fields_json
            FROM rule_catalog ORDER BY rule_id
            """
        ).fetchall()
        return [
            {
                "rule_id": row["rule_id"],
                "severity": row["severity"],
                "title": row["title"],
                "description": row["description"],
                "evidence_fields": json.loads(row["evidence_fields_json"]),
            }
            for row in rows
        ]
    finally:
        connection.close()

def get_publish_log(path: str | Path, digest: Optional[str] = None) -> list[dict[str, Any]]:
    """Return publish-log events in commit order.

    Tolerates ledgers written before the publish log existed and missing
    ledger files, so recovery can always ask "what was decided so far?".
    """
    ledger_path = Path(path)
    if not ledger_path.is_file():
        return []
    connection = connect_ledger(ledger_path, read_only=True)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'publish_log'"
        ).fetchone()
        if table is None:
            return []
        if digest is None:
            rows = connection.execute(
                "SELECT seq, result_digest, event, detail_json FROM publish_log ORDER BY seq"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT seq, result_digest, event, detail_json FROM publish_log "
                "WHERE result_digest = ? ORDER BY seq",
                (digest,),
            ).fetchall()
        return [
            {
                "seq": int(row["seq"]),
                "result_digest": row["result_digest"],
                "event": row["event"],
                "detail": json.loads(row["detail_json"]),
            }
            for row in rows
        ]
    finally:
        connection.close()

def latest_published_digest(path: str | Path) -> Optional[str]:
    """Digest of the most recent ``published`` event, if any."""
    latest: Optional[str] = None
    for row in get_publish_log(path):
        if row["event"] == "published":
            latest = row["result_digest"]
    return latest

def was_published(path: str | Path, digest: str) -> bool:
    """True when the digest has any ``published`` event in the log."""
    return any(row["event"] == "published" for row in get_publish_log(path, digest))

def committed_intent_files(path: str | Path, digest: str) -> Optional[dict[str, Any]]:
    """Per-file SHA-256 map recorded by the latest ``ledger_committed`` event."""
    for row in reversed(get_publish_log(path, digest)):
        if row["event"] == "ledger_committed":
            files = row["detail"].get("files")
            if isinstance(files, dict):
                return files
    return None

def events_by_digest(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """All publish-log events grouped by digest, each list in commit order."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in get_publish_log(path):
        grouped.setdefault(row["result_digest"], []).append(row)
    return grouped

def session_state(events: list[dict[str, Any]]) -> str:
    """Reduce a digest's publish-log history to its current session state."""
    state = "none"
    for row in events:
        event = row["event"]
        if event == "ledger_committed":
            state = "ledger_committed"
        elif event == "verified" and state == "ledger_committed":
            state = "verified"
        elif event == "verification_failed" and state in ("ledger_committed", "verified"):
            state = "verification_failed"
        elif event == "published":
            state = "published"
        elif event == "recovery_rollback":
            state = "recovery_rollback"
    return state
