"""Append-only, idempotent SQLite audit ledger storage."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any
from .errors import LedgerError
from .generation import source_generation_issues
from .report_json import compute_result_digest
from .rules_catalog import catalog_rows
from .util import canonical_json
SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS runs (
        result_digest TEXT PRIMARY KEY,
        schema_version TEXT NOT NULL,
        run_label TEXT NOT NULL,
        input_digest TEXT NOT NULL,
        config_digest TEXT NOT NULL,
        source_generation_json TEXT NOT NULL,
        generation_status TEXT NOT NULL,
        generation_id TEXT,
        quality_gate TEXT NOT NULL,
        manifest_sha256 TEXT,
        manifest_digest TEXT,
        summary_json TEXT NOT NULL,
        limitations_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS layers (
        result_digest TEXT NOT NULL,
        layer_id TEXT NOT NULL,
        feature_count INTEGER NOT NULL,
        stats_json TEXT NOT NULL,
        PRIMARY KEY (result_digest, layer_id),
        FOREIGN KEY (result_digest) REFERENCES runs(result_digest)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS findings (
        result_digest TEXT NOT NULL,
        finding_id TEXT NOT NULL,
        rule_id TEXT NOT NULL,
        severity TEXT NOT NULL,
        layer_id TEXT NOT NULL,
        feature_id TEXT,
        related_json TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        PRIMARY KEY (result_digest, finding_id),
        FOREIGN KEY (result_digest) REFERENCES runs(result_digest)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS input_files (
        result_digest TEXT NOT NULL,
        layer_id TEXT NOT NULL,
        path TEXT NOT NULL,
        format TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        manifest_path TEXT,
        expected_sha256 TEXT,
        generation_check_status TEXT NOT NULL,
        reread_consistent INTEGER,
        PRIMARY KEY (result_digest, layer_id),
        FOREIGN KEY (result_digest) REFERENCES runs(result_digest)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rule_catalog (
        rule_id TEXT PRIMARY KEY,
        severity TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        evidence_fields_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(result_digest, severity)",
    "CREATE INDEX IF NOT EXISTS idx_findings_layer ON findings(result_digest, layer_id)",
    "CREATE INDEX IF NOT EXISTS idx_findings_rule ON findings(result_digest, rule_id)",
)

def connect_ledger(path: str | Path, read_only: bool = False) -> sqlite3.Connection:
    ledger_path = Path(path)
    if read_only:
        if not ledger_path.is_file():
            raise LedgerError("TOPO-LED-001", "台账文件不存在", str(ledger_path))
        uri = ledger_path.resolve().as_uri() + "?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True)
        except sqlite3.Error as exc:
            raise LedgerError("TOPO-LED-002", f"无法以只读方式打开台账: {exc}", str(ledger_path)) from exc
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(str(ledger_path))
    except sqlite3.Error as exc:
        raise LedgerError("TOPO-LED-002", f"无法打开台账: {exc}", str(ledger_path)) from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection

def initialize_schema(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)
    _migrate_generation_columns(connection)
    connection.execute("PRAGMA user_version = 2")
    connection.commit()

def _migrate_generation_columns(connection: sqlite3.Connection) -> None:
    """Add source-generation columns to ledgers created before schema v2."""
    additions = {
        "runs": (
            ("source_generation_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("generation_status", "TEXT NOT NULL DEFAULT 'unverified'"),
            ("generation_id", "TEXT"),
            ("quality_gate", "TEXT NOT NULL DEFAULT 'blocked'"),
            ("manifest_sha256", "TEXT"),
            ("manifest_digest", "TEXT"),
        ),
        "input_files": (
            ("manifest_path", "TEXT"),
            ("expected_sha256", "TEXT"),
            ("generation_check_status", "TEXT NOT NULL DEFAULT 'not_applicable'"),
            ("reread_consistent", "INTEGER"),
        ),
    }
    for table, columns in additions.items():
        existing = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        for name, declaration in columns:
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"
                )

def write_report(path: str | Path, report: dict[str, Any]) -> None:
    """Persist a report exactly once per ``result_digest``."""
    digest = str(report.get("result_digest", ""))
    recomputed = compute_result_digest(report)
    if not digest or digest != recomputed:
        raise LedgerError("TOPO-LED-003", "报告 result_digest 无效，拒绝写入台账", str(path))
    generation_issues = source_generation_issues(report.get("source_generation"))
    if generation_issues:
        raise LedgerError(
            "TOPO-LED-003",
            "报告来源代证据不自洽，拒绝写入台账: " + "; ".join(generation_issues),
            str(path),
        )
    connection = connect_ledger(path, read_only=False)
    try:
        initialize_schema(connection)
        with connection:
            source_generation = report.get("source_generation", {})
            layer_checks = {
                str(item.get("layer_id")): item
                for item in source_generation.get("layer_checks", [])
                if isinstance(item, dict)
            }
            connection.execute(
                """
                INSERT OR IGNORE INTO runs
                (result_digest, schema_version, run_label, input_digest,
                 config_digest, source_generation_json, generation_status,
                 generation_id, quality_gate, manifest_sha256, manifest_digest,
                 summary_json, limitations_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest,
                    str(report.get("schema_version", "")),
                    str(report.get("run_label", "")),
                    str(report.get("input_digest", "")),
                    str(report.get("config_digest", "")),
                    canonical_json(source_generation),
                    str(source_generation.get("status", "unverified")),
                    source_generation.get("generation_id"),
                    str(source_generation.get("quality_gate", "blocked")),
                    source_generation.get("manifest_sha256"),
                    source_generation.get("manifest_digest"),
                    canonical_json(report.get("summary", {})),
                    canonical_json(report.get("limitations", [])),
                ),
            )
            for layer in report.get("layers", []):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO layers
                    (result_digest, layer_id, feature_count, stats_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        digest,
                        str(layer.get("layer_id", "")),
                        int(layer.get("feature_count", 0)),
                        canonical_json(layer),
                    ),
                )
            for finding in report.get("findings", []):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO findings
                    (result_digest, finding_id, rule_id, severity, layer_id,
                     feature_id, related_json, evidence_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        digest,
                        str(finding.get("finding_id", "")),
                        str(finding.get("rule_id", "")),
                        str(finding.get("severity", "")),
                        str(finding.get("layer_id", "")),
                        finding.get("feature_id"),
                        canonical_json(finding.get("related_feature_ids", [])),
                        canonical_json(finding.get("evidence", {})),
                    ),
                )
            for item in report.get("input_files", []):
                check = layer_checks.get(str(item.get("layer_id")), {})
                connection.execute(
                    """
                    INSERT OR IGNORE INTO input_files
                    (result_digest, layer_id, path, format, sha256, size_bytes,
                     manifest_path, expected_sha256, generation_check_status,
                     reread_consistent)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        digest,
                        str(item.get("layer_id", "")),
                        str(item.get("path", "")),
                        str(item.get("format", "")),
                        str(item.get("sha256", "")),
                        int(item.get("size_bytes", 0)),
                        check.get("manifest_path"),
                        check.get("expected_sha256"),
                        str(check.get("status", "not_applicable")),
                        _optional_bool(check.get("reread_consistent")),
                    ),
                )
            for row in catalog_rows():
                connection.execute(
                    """
                    INSERT INTO rule_catalog
                    (rule_id, severity, title, description, evidence_fields_json)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(rule_id) DO UPDATE SET
                      severity=excluded.severity,
                      title=excluded.title,
                      description=excluded.description,
                      evidence_fields_json=excluded.evidence_fields_json
                    """,
                    (
                        row["rule_id"],
                        row["severity"],
                        row["title"],
                        row["description"],
                        canonical_json(row["evidence_fields"]),
                    ),
                )
    except sqlite3.Error as exc:
        raise LedgerError("TOPO-LED-004", f"写入台账失败: {exc}", str(path)) from exc
    finally:
        connection.close()

def _optional_bool(value: Any) -> Any:
    if value is None:
        return None
    return 1 if bool(value) else 0
