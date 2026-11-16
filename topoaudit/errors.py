"""Structured errors used by configuration parsing and data loading.

Every user-facing failure carries a stable machine readable code and a
JSON-path-like location so that CLI output and tests can assert on it.
"""
from __future__ import annotations
from typing import Optional

class AuditError(Exception):
    """Base class for expected, structured audit failures."""
    code = "TOPO-ERR-000"
    def __init__(self, message: str, path: Optional[str] = None) -> None:
        self.message = message
        self.path = path
        super().__init__(self.render())
    def render(self) -> str:
        location = f" @ {self.path}" if self.path else ""
        return f"[{self.code}] {self.message}{location}"

class ConfigError(AuditError):
    """Configuration is missing, malformed, or violates the schema."""
    def __init__(self, code: str, message: str, path: Optional[str] = None) -> None:
        self.code = code
        super().__init__(message, path)

class InputError(AuditError):
    """A local input file is missing or cannot be interpreted safely."""
    def __init__(self, code: str, message: str, path: Optional[str] = None) -> None:
        self.code = code
        super().__init__(message, path)

class ReportError(AuditError):
    """A report file is malformed or unusable."""
    def __init__(self, code: str, message: str, path: Optional[str] = None) -> None:
        self.code = code
        super().__init__(message, path)

class LedgerError(AuditError):
    """The SQLite ledger is missing or has an incompatible shape."""
    def __init__(self, code: str, message: str, path: Optional[str] = None) -> None:
        self.code = code
        super().__init__(message, path)

class ServerError(AuditError):
    """The local review server cannot be configured or started safely."""
    def __init__(self, code: str, message: str, path: Optional[str] = None) -> None:
        self.code = code
        super().__init__(message, path)

class RepairError(AuditError):
    """A repair rehearsal request is malformed or references unknown data."""
    def __init__(self, code: str, message: str, path: Optional[str] = None) -> None:
        self.code = code
        super().__init__(message, path)
