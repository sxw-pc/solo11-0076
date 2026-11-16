"""Deterministic JSON rendering for read-only repair rehearsals."""
from __future__ import annotations
from typing import Any, Sequence
from .model import InputFile, RepairRehearsalResult
from .util import canonical_json, pretty_json, sha256_text


def build_rehearsal_report(
    result: RepairRehearsalResult,
    *,
    run_label: str,
    input_files: Sequence[InputFile],
) -> dict[str, Any]:
    """Build a stable, JSON-serializable rehearsal report.

    The report contains only proposed in-memory edits and review evidence;
    it never embeds a write-back instruction.
    """
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "report_kind": "endpoint_repair_rehearsal",
        "run_label": run_label,
        "read_only": True,
        "write_back": "never",
        "input_files": [item.as_dict() for item in input_files],
        "rehearsal": result.as_dict(),
    }
    payload["rehearsal_digest"] = sha256_text(
        canonical_json(
            {
                "input_files": payload["input_files"],
                "rehearsal": payload["rehearsal"],
            }
        )
    )
    return payload


def write_rehearsal_report(path: str, report: dict[str, Any]) -> None:
    from pathlib import Path

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(pretty_json(report), encoding="utf-8")
