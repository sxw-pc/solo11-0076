"""Small deterministic helpers shared by the audit pipeline."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
from typing import Any

def canonical_json(value: Any) -> str:
    """Serialize a value deterministically.

    Non-finite numbers are rejected because they have no portable JSON
    representation and would make audit digests unstable.
    """
    return json.dumps(
        _normalize_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )

def pretty_json(value: Any) -> str:
    """Pretty JSON with a trailing newline, using stable key order."""
    text = json.dumps(
        _normalize_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    )
    return text + "\n"

def _normalize_value(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float cannot be serialized")
        if value == 0.0:
            return 0.0
        return value
    if isinstance(value, dict):
        return {str(k): _normalize_value(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(v) for v in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def sha256_prefix(value: Any, length: int = 16) -> str:
    return sha256_text(canonical_json(value))[:length]

def stable_round(value: float, digits: int = 9) -> float:
    """Round a finite float without introducing negative zero."""
    if not math.isfinite(value):
        raise ValueError("cannot round non-finite float")
    rounded = round(float(value), digits)
    return 0.0 if rounded == 0 else rounded

def truncate_text(value: Any, limit: int = 80) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
