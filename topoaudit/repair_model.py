"""Typed models for the read-only endpoint repair rehearsal."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from .model import Finding
from .util import stable_round


@dataclass(frozen=True)
class EndpointRef:
    """Stable reference to one line endpoint inside one feature."""

    feature_id: str
    part_index: str
    side: str

    def as_dict(self) -> dict[str, str]:
        return {
            "feature_id": self.feature_id,
            "part_index": self.part_index,
            "side": self.side,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "EndpointRef":
        if not isinstance(raw, dict):
            raise TypeError("endpoint reference must be an object")
        return cls(
            feature_id=str(raw["feature_id"]),
            part_index=str(raw["part_index"]),
            side=str(raw["side"]),
        )

    def key(self) -> tuple[str, str, str]:
        return (self.feature_id, self.part_index, self.side)


@dataclass(frozen=True)
class EndpointMove:
    """One candidate coordinate change for a single endpoint."""

    endpoint: EndpointRef
    original_coordinate: tuple[float, float]
    proposed_coordinate: tuple[float, float]
    displacement: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint.as_dict(),
            "original_coordinate": list(self.original_coordinate),
            "proposed_coordinate": list(self.proposed_coordinate),
            "displacement": stable_round(self.displacement),
        }


@dataclass(frozen=True)
class FindingChange:
    """How one baseline/repair finding changes between the two audits."""

    change_type: str  # "added" | "removed" | "retained"
    finding: Finding

    def as_dict(self) -> dict[str, Any]:
        return {"change_type": self.change_type, "finding": self.finding.as_dict()}


@dataclass(frozen=True)
class RepairConflict:
    """Structured reason why a candidate edit cannot be executed."""

    code: str
    message: str
    endpoints: tuple[EndpointRef, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "endpoints": [endpoint.as_dict() for endpoint in self.endpoints],
            "details": self.details,
        }


@dataclass(frozen=True)
class RepairCandidate:
    """One complete, fully reviewed candidate edit plan."""

    candidate_id: str
    moves: tuple[EndpointMove, ...]
    moved_endpoint_count: int
    max_displacement: float
    total_displacement: float
    feasible: bool
    is_pareto: bool
    conflicts: tuple[RepairConflict, ...]
    finding_changes: tuple[FindingChange, ...]
    affected_endpoints: tuple[EndpointRef, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "moves": [move.as_dict() for move in self.moves],
            "moved_endpoint_count": self.moved_endpoint_count,
            "max_displacement": stable_round(self.max_displacement),
            "total_displacement": stable_round(self.total_displacement),
            "feasible": self.feasible,
            "is_pareto": self.is_pareto,
            "conflicts": [conflict.as_dict() for conflict in self.conflicts],
            "finding_changes": [change.as_dict() for change in self.finding_changes],
            "affected_endpoints": [endpoint.as_dict() for endpoint in self.affected_endpoints],
        }


@dataclass(frozen=True)
class RepairRehearsalResult:
    """Read-only endpoint repair rehearsal for one layer request."""

    layer_id: str
    status: str  # "rejected" | "infeasible" | "feasible"
    coordinate_epsilon: float
    near_miss_distance: float
    requested_targets: tuple[tuple[EndpointRef, EndpointRef], ...]
    max_displacement_limit: float
    candidates: tuple[RepairCandidate, ...]
    pareto_front: tuple[str, ...]
    conflicts: tuple[RepairConflict, ...]
    review_required: bool
    review_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "layer_id": self.layer_id,
            "status": self.status,
            "coordinate_epsilon": self.coordinate_epsilon,
            "near_miss_distance": self.near_miss_distance,
            "requested_targets": [
                [first.as_dict(), second.as_dict()]
                for first, second in self.requested_targets
            ],
            "max_displacement_limit": self.max_displacement_limit,
            "candidate_count": len(self.candidates),
            "pareto_front": list(self.pareto_front),
            "review_required": self.review_required,
            "review_reasons": list(self.review_reasons),
            "conflicts": [conflict.as_dict() for conflict in self.conflicts],
            "candidates": [candidate.as_dict() for candidate in self.candidates],
        }
