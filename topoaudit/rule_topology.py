"""Layer-level topology checks: identities, overlaps, and line endpoints."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional
from .findings import FindingCollector
from .geometry import (
    bbox_intersection,
    bbox_of_points,
    distance,
    format_point,
    is_finite_point,
    line_components,
    points_close,
    polygon_bbox,
    polygon_components,
    polygons_overlap,
    valid_polygon,
)
from .geometry_core import (
    cluster_touching_points,
    grade_connection_decision,
    grade_value,
    line_self_intersections,
    point_is_interior,
    ring_closed,
    segment_relation,
    zero_length_segments,
)
from .model import Feature, LayerConfig, Tolerance
from .normalize import canonical_geometry
from .util import canonical_json, sha256_text, stable_round

def check_layer_topology(
    features: tuple[Feature, ...],
    layer: LayerConfig,
    tolerance: Tolerance,
    collector: FindingCollector,
) -> None:
    ordered = tuple(sorted(features, key=lambda item: item.source_index))
    _check_duplicate_ids(ordered, layer, collector)
    _check_duplicate_geometries(ordered, layer, tolerance, collector)
    _check_polygon_overlaps(ordered, layer, tolerance, collector)
    _check_line_endpoints(ordered, layer, tolerance, collector)

def _check_duplicate_ids(
    features: tuple[Feature, ...], layer: LayerConfig, collector: FindingCollector
) -> None:
    if not layer.rule_enabled("DUPLICATE_FEATURE_ID"):
        return
    groups: dict[str, list[Feature]] = {}
    for feature in features:
        value = _id_value(feature.properties.get(layer.id_field))
        if value is not None:
            groups.setdefault(value, []).append(feature)
    for value in sorted(groups):
        group = groups[value]
        if len(group) < 2:
            continue
        first = group[0]
        for duplicate in group[1:]:
            collector.add(
                "DUPLICATE_FEATURE_ID",
                layer.layer_id,
                duplicate.feature_id,
                related_feature_ids=(first.feature_id,),
                evidence={
                    "id_field": layer.id_field,
                    "value": value,
                    "first_source_index": first.source_index,
                    "duplicate_source_index": duplicate.source_index,
                },
            )

def _check_duplicate_geometries(
    features: tuple[Feature, ...],
    layer: LayerConfig,
    tolerance: Tolerance,
    collector: FindingCollector,
) -> None:
    if not layer.rule_enabled("DUPLICATE_GEOMETRY"):
        return
    seen: dict[str, Feature] = {}
    for feature in features:
        canonical = canonical_geometry(feature.geometry, tolerance.coordinate_epsilon)
        if canonical is None:
            continue
        signature = canonical_json(canonical)
        if signature not in seen:
            seen[signature] = feature
            continue
        first = seen[signature]
        collector.add(
            "DUPLICATE_GEOMETRY",
            layer.layer_id,
            feature.feature_id,
            related_feature_ids=(first.feature_id,),
            evidence={
                "geometry_digest": sha256_text(signature)[:16],
                "first_source_index": first.source_index,
                "duplicate_source_index": feature.source_index,
                "coordinate_epsilon": tolerance.coordinate_epsilon,
            },
        )

def _check_polygon_overlaps(
    features: tuple[Feature, ...],
    layer: LayerConfig,
    tolerance: Tolerance,
    collector: FindingCollector,
) -> None:
    if not layer.rule_enabled("POLYGON_OVERLAP"):
        return
    entries: list[tuple[Feature, str, Any, tuple[float, float, float, float]]] = []
    for feature in features:
        for label, polygon in polygon_components(feature.geometry):
            if not valid_polygon(polygon, tolerance.coordinate_epsilon):
                continue
            bbox = polygon_bbox(polygon)
            if bbox is not None:
                entries.append((feature, label, polygon, bbox))
    for left in range(len(entries)):
        feature_a, label_a, polygon_a, bbox_a = entries[left]
        for right in range(left + 1, len(entries)):
            feature_b, label_b, polygon_b, bbox_b = entries[right]
            if feature_a.source_index == feature_b.source_index:
                continue
            intersection = bbox_intersection(bbox_a, bbox_b)
            if intersection is None:
                continue
            if not polygons_overlap(polygon_a, polygon_b, tolerance.coordinate_epsilon):
                continue
            first, second = sorted(
                (feature_a, feature_b), key=lambda item: item.source_index
            )
            collector.add(
                "POLYGON_OVERLAP",
                layer.layer_id,
                first.feature_id,
                related_feature_ids=(second.feature_id,),
                evidence={
                    "pair": [first.feature_id, second.feature_id],
                    "component_indexes": [label_a, label_b],
                    "bbox_intersection": [stable_round(value) for value in intersection],
                },
            )

def _check_line_endpoints(
    features: tuple[Feature, ...],
    layer: LayerConfig,
    tolerance: Tolerance,
    collector: FindingCollector,
) -> None:
    dangling_enabled = layer.rule_enabled("DANGLING_ENDPOINT")
    near_enabled = layer.rule_enabled("NEAR_MISS_ENDPOINT")
    if not dangling_enabled and not near_enabled:
        return
    records = _endpoint_records(features, layer, tolerance)
    exact_connected = [False] * len(records)
    near_pairs: list[tuple[int, int, float]] = []
    for left in range(len(records)):
        first = records[left]
        for right in range(left + 1, len(records)):
            second = records[right]
            if first["source_index"] == second["source_index"]:
                continue
            gap = distance(first["point"], second["point"])
            if gap <= tolerance.coordinate_epsilon:
                exact_connected[left] = True
                exact_connected[right] = True
            elif gap <= tolerance.near_miss_distance:
                near_pairs.append((left, right, gap))
    if near_enabled:
        for left, right, gap in near_pairs:
            first, second = records[left], records[right]
            collector.add(
                "NEAR_MISS_ENDPOINT",
                layer.layer_id,
                first["feature_id"],
                related_feature_ids=(second["feature_id"],),
                evidence={
                    "endpoint_a": _endpoint_evidence(first),
                    "endpoint_b": _endpoint_evidence(second),
                    "distance": stable_round(gap),
                    "tolerance": tolerance.near_miss_distance,
                },
            )
    if dangling_enabled:
        for index, record in enumerate(records):
            if exact_connected[index]:
                continue
            nearest: Optional[float] = None
            nearest_feature: Optional[str] = None
            for other_index, other in enumerate(records):
                if other_index == index or other["source_index"] == record["source_index"]:
                    continue
                gap = distance(record["point"], other["point"])
                if nearest is None or gap < nearest:
                    nearest = gap
                    nearest_feature = other["feature_id"]
            collector.add(
                "DANGLING_ENDPOINT",
                layer.layer_id,
                record["feature_id"],
                related_feature_ids=(nearest_feature,) if nearest_feature else (),
                evidence={
                    "part_index": record["part"],
                    "endpoint": record["side"],
                    "coordinate": format_point(record["point"]),
                    "nearest_feature_id": nearest_feature,
                    "nearest_distance": (
                        stable_round(nearest) if nearest is not None else None
                    ),
                    "tolerance": tolerance.coordinate_epsilon,
                },
            )

def _endpoint_records(
    features: tuple[Feature, ...], layer: LayerConfig, tolerance: Tolerance
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for feature in features:
        if feature.geometry.kind not in layer.geometry_types:
            continue
        for label, line in line_components(feature.geometry):
            if len(line) < 2 or not all(is_finite_point(point) for point in line):
                continue
            if points_close(line[0], line[-1], tolerance.coordinate_epsilon):
                continue
            for side, point in (("start", line[0]), ("end", line[-1])):
                records.append(
                    {
                        "feature_id": feature.feature_id,
                        "source_index": feature.source_index,
                        "part": label,
                        "side": side,
                        "point": point,
                    }
                )
    return records

def _endpoint_evidence(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "feature_id": record["feature_id"],
        "source_index": record["source_index"],
        "part_index": record["part"],
        "endpoint": record["side"],
        "coordinate": format_point(record["point"]),
    }

def _id_value(value: Any) -> Optional[str]:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# Config-driven traversable line-network reachability audit.
#
# The builder turns the valid line parts of one layer into an undirected
# node/edge graph using only *real* contacts (coincident points within
# coordinate_epsilon, T-junctions, proper crossings, collinear overlaps).
# Bounding-box overlap or proximity above the epsilon never creates an edge.
# Grade attributes and the configured planar/grade_unknown policy decide
# whether a real contact is traversable; invalid geometry is excluded so it
# can never bridge two otherwise disconnected sides.
# ---------------------------------------------------------------------------

@dataclass
class _Part:
    part_id: str
    feature_id: str
    source_index: int
    part_index: int
    label: str
    points: tuple[tuple[float, float], ...]
    grade: Optional[str]
    bbox: tuple[float, float, float, float]
    closed: bool
    invalid_reasons: list[str] = field(default_factory=list)
    invalid_pairs: list[list[int]] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.invalid_reasons


@dataclass
class _Contact:
    contact_type: str
    coordinate: tuple[float, float]
    part_a: str
    part_b: str
    feature_a: str
    feature_b: str
    grade_a: Optional[str]
    grade_b: Optional[str]
    segment_pair: list[int]
    traversable: bool
    decision: str
    reason: str


@dataclass
class _Incidence:
    incidence_id: int
    part_id: str
    point: tuple[float, float]
    arc: float
    endpoint: bool
    segment_hint: int


def audit_line_network(
    features: tuple[Feature, ...],
    layer: LayerConfig,
    tolerance: Tolerance,
    collector: FindingCollector,
) -> Optional[dict[str, Any]]:
    """Build the traversable network and compute entry-to-terminal reach.

    Returns a reviewable report dictionary, or ``None`` when the layer does
    not declare a ``network_audit`` configuration block.
    """
    config = layer.network_audit
    if config is None:
        return None
    epsilon = tolerance.coordinate_epsilon
    ordered = tuple(sorted(features, key=lambda item: item.source_index))
    feature_by_id = {feature.feature_id: feature for feature in ordered}
    parts = _extract_parts(ordered, layer, tolerance, collector)
    contacts = _detect_contacts(parts, layer, tolerance, collector)
    graph = _build_graph(parts, contacts, epsilon)
    return _reachability_report(
        graph=graph,
        parts=parts,
        contacts=contacts,
        layer=layer,
        tolerance=tolerance,
        feature_by_id=feature_by_id,
        collector=collector,
    )


def _extract_parts(
    features: tuple[Feature, ...],
    layer: LayerConfig,
    tolerance: Tolerance,
    collector: FindingCollector,
) -> list[_Part]:
    config = layer.network_audit
    epsilon = tolerance.coordinate_epsilon
    parts: list[_Part] = []
    invalid_by_feature: dict[str, dict[str, Any]] = {}
    for feature in features:
        if feature.geometry.kind not in layer.geometry_types:
            continue
        grade = (
            grade_value(feature.properties.get(config.grade_field))
            if config.grade_field is not None
            else None
        )
        for part_index, (label, line) in enumerate(line_components(feature.geometry)):
            part_id = f"{feature.feature_id}#{part_index}"
            points = tuple((float(p[0]), float(p[1])) for p in line)
            bbox = bbox_of_points(points) or (0.0, 0.0, 0.0, 0.0)
            part = _Part(
                part_id=part_id,
                feature_id=feature.feature_id,
                source_index=feature.source_index,
                part_index=part_index,
                label=label,
                points=points,
                grade=grade,
                bbox=bbox,
                closed=False,
            )
            reasons: list[str] = []
            pairs: list[list[int]] = []
            if not points or any(not is_finite_point(point) for point in points):
                reasons.append("non_finite_or_missing_coordinate")
            if len(points) < 2:
                reasons.append("too_few_points")
            else:
                zero = zero_length_segments(points, epsilon)
                if zero:
                    reasons.append("zero_length_segment")
                closed = ring_closed(points, epsilon)
                part.closed = closed
                self_pairs = line_self_intersections(points, closed, epsilon)
                if self_pairs:
                    reasons.append("self_intersection")
                    pairs = [list(pair) for pair in self_pairs[:20]]
            part.invalid_reasons = reasons
            part.invalid_pairs = pairs
            if reasons:
                entry = invalid_by_feature.setdefault(
                    feature.feature_id,
                    {
                        "source_index": feature.source_index,
                        "parts": [],
                    },
                )
                entry["parts"].append(
                    {
                        "part_index": part_index,
                        "part_label": label,
                        "reasons": reasons,
                        "segment_pairs": pairs,
                    }
                )
            parts.append(part)
    if layer.rule_enabled("NETWORK_INVALID_SEGMENT"):
        for feature_id in sorted(invalid_by_feature):
            entry = invalid_by_feature[feature_id]
            first_part = entry["parts"][0]
            collector.add(
                "NETWORK_INVALID_SEGMENT",
                layer.layer_id,
                feature_id,
                evidence={
                    "feature_id": feature_id,
                    "part_index": first_part["part_index"],
                    "part_label": first_part["part_label"],
                    "reason": ",".join(first_part["reasons"]),
                    "segment_pairs": first_part["segment_pairs"],
                    "invalid_parts": entry["parts"],
                    "coordinate_epsilon": epsilon,
                },
            )
    return parts


def _detect_contacts(
    parts: list[_Part],
    layer: LayerConfig,
    tolerance: Tolerance,
    collector: FindingCollector,
) -> list[_Contact]:
    epsilon = tolerance.coordinate_epsilon
    valid_parts = [part for part in parts if part.valid]
    contacts: list[_Contact] = []
    emitted: set[str] = set()
    seen_contacts: set[tuple[str, str, tuple[float, float]]] = set()
    for left in range(len(valid_parts)):
        part_a = valid_parts[left]
        for right in range(left + 1, len(valid_parts)):
            part_b = valid_parts[right]
            if bbox_intersection(part_a.bbox, part_b.bbox) is None:
                continue
            for i in range(len(part_a.points) - 1):
                a0, a1 = part_a.points[i], part_a.points[i + 1]
                if distance(a0, a1) <= epsilon:
                    continue
                for j in range(len(part_b.points) - 1):
                    b0, b1 = part_b.points[j], part_b.points[j + 1]
                    if distance(b0, b1) <= epsilon:
                        continue
                    relation = segment_relation(a0, a1, b0, b1, epsilon)
                    if relation["relation"] == "disjoint":
                        continue
                    for coordinate in relation["points"]:
                        rounded = (
                            stable_round(coordinate[0]),
                            stable_round(coordinate[1]),
                        )
                        dedupe_key = (part_a.part_id, part_b.part_id, rounded)
                        if dedupe_key in seen_contacts:
                            continue
                        seen_contacts.add(dedupe_key)
                        contact = _make_contact(
                            relation, coordinate, part_a, part_b, i, j, layer, epsilon
                        )
                        contacts.append(contact)
                        _emit_contact_finding(contact, layer, collector, emitted)
    return contacts


def _make_contact(
    relation: dict[str, Any],
    coordinate: tuple[float, float],
    part_a: _Part,
    part_b: _Part,
    segment_a: int,
    segment_b: int,
    layer: LayerConfig,
    epsilon: float,
) -> _Contact:
    coordinate = (stable_round(coordinate[0]), stable_round(coordinate[1]))
    # Interior status is evaluated against the *whole polyline*, not just the
    # touching segment pair: a junction located at a mid-line vertex is still
    # interior to that feature even though it ends two adjacent segments.
    interior_a = _point_interior_to_part(coordinate, part_a, epsilon)
    interior_b = _point_interior_to_part(coordinate, part_b, epsilon)
    config = layer.network_audit
    contact_type = _contact_type_label(relation, interior_a, interior_b)
    if config.grade_field is not None:
        decision = grade_connection_decision(part_a.grade, part_b.grade)
        traversable = bool(decision["connected"])
        decision_code = str(decision["decision"])
        reason = str(decision["reason"])
    else:
        assert config.crossing_policy is not None
        # grade_unknown only suspends *mutual interior* crossings: a point in
        # the middle of both touching segments is an over/under-pass candidate.
        # T-contacts and endpoint touches are real, reviewable endpoint
        # evidence and stay traversable under both authorised interpretations.
        interior_cross = interior_a and interior_b
        if interior_cross and config.crossing_policy == "grade_unknown":
            traversable = False
            decision_code = "unknown_grade_crossing"
            reason = "grade_unknown_policy_keeps_lines_separate"
        else:
            traversable = True
            decision_code = "planar_junction"
            reason = "real_contact_under_" + config.crossing_policy
    return _Contact(
        contact_type=contact_type,
        coordinate=coordinate,
        part_a=part_a.part_id,
        part_b=part_b.part_id,
        feature_a=part_a.feature_id,
        feature_b=part_b.feature_id,
        grade_a=part_a.grade,
        grade_b=part_b.grade,
        segment_pair=[segment_a, segment_b],
        traversable=traversable,
        decision=decision_code,
        reason=reason,
    )


def _point_interior_to_part(
    coordinate: tuple[float, float], part: _Part, epsilon: float
) -> bool:
    """True when a contact lies strictly inside the part's full polyline."""
    if points_close(coordinate, part.points[0], epsilon):
        return False
    if points_close(coordinate, part.points[-1], epsilon):
        return False
    for index in range(len(part.points) - 1):
        start, end = part.points[index], part.points[index + 1]
        if distance(start, end) <= epsilon:
            continue
        if point_is_interior(coordinate, start, end, epsilon):
            return True
        # A mid-line vertex equals the point but is neither polyline end.
        if index > 0 and points_close(coordinate, start, epsilon):
            return True
    return False


def _contact_type_label(
    relation: dict[str, Any],
    interior_a: bool,
    interior_b: bool,
) -> str:
    if relation["relation"] == "overlap":
        return "collinear_overlap"
    if relation["relation"] == "touch" and not interior_a and not interior_b:
        return "endpoint_touch"
    if interior_a and interior_b:
        return "interior_crossing"
    return "t_junction"


def _emit_contact_finding(
    contact: _Contact,
    layer: LayerConfig,
    collector: FindingCollector,
    emitted: set[str],
) -> None:
    rule_id: Optional[str] = None
    evidence: dict[str, Any] = {}
    if contact.decision == "grade_undecidable" and layer.rule_enabled(
        "NETWORK_GRADE_UNDUCIDABLE"
    ):
        rule_id = "NETWORK_GRADE_UNDUCIDABLE"
        evidence = {
            "contact_type": contact.contact_type,
            "coordinate": list(format_point(contact.coordinate)),
            "feature_a": contact.feature_a,
            "feature_b": contact.feature_b,
            "part_a": contact.part_a,
            "part_b": contact.part_b,
            "segment_pair": contact.segment_pair,
            "grade_a": contact.grade_a,
            "grade_b": contact.grade_b,
            "reason": contact.reason,
        }
    elif contact.decision == "grade_separated" and layer.rule_enabled(
        "NETWORK_GRADE_SEPARATED"
    ):
        rule_id = "NETWORK_GRADE_SEPARATED"
        evidence = {
            "contact_type": contact.contact_type,
            "coordinate": list(format_point(contact.coordinate)),
            "feature_a": contact.feature_a,
            "feature_b": contact.feature_b,
            "part_a": contact.part_a,
            "part_b": contact.part_b,
            "segment_pair": contact.segment_pair,
            "grade_a": contact.grade_a,
            "grade_b": contact.grade_b,
        }
    elif contact.decision == "unknown_grade_crossing" and layer.rule_enabled(
        "NETWORK_UNKNOWN_CROSSING"
    ):
        rule_id = "NETWORK_UNKNOWN_CROSSING"
        evidence = {
            "contact_type": contact.contact_type,
            "coordinate": list(format_point(contact.coordinate)),
            "feature_a": contact.feature_a,
            "feature_b": contact.feature_b,
            "part_a": contact.part_a,
            "part_b": contact.part_b,
            "segment_pair": contact.segment_pair,
            "crossing_policy": layer.network_audit.crossing_policy,
        }
    if rule_id is None:
        return
    key = canonical_json(
        {
            "rule_id": rule_id,
            "coordinate": contact.coordinate,
            "features": sorted((contact.feature_a, contact.feature_b)),
        }
    )
    if key in emitted:
        return
    emitted.add(key)
    first, second = sorted(
        (contact.feature_a, contact.feature_b),
        key=lambda value: (value == "", value),
    )
    collector.add(
        rule_id,
        layer.layer_id,
        first,
        related_feature_ids=(second,),
        evidence=evidence,
    )


def _build_graph(
    parts: list[_Part],
    contacts: list[_Contact],
    epsilon: float,
) -> dict[str, Any]:
    valid_parts = [part for part in parts if part.valid]
    incidences: list[_Incidence] = []
    by_part: dict[str, list[_Incidence]] = {}

    def add_incidence(part: _Part, point: tuple[float, float], endpoint: bool) -> int:
        arc = _arc_position(part.points, point, epsilon)
        incidence = _Incidence(
            incidence_id=len(incidences),
            part_id=part.part_id,
            point=(stable_round(point[0]), stable_round(point[1])),
            arc=arc,
            endpoint=endpoint,
            segment_hint=_segment_hint(part.points, point, epsilon),
        )
        incidences.append(incidence)
        by_part.setdefault(part.part_id, []).append(incidence)
        return incidence.incidence_id

    for part in valid_parts:
        # Every finite, non-duplicate vertex is a node candidate; this keeps
        # edge chains (including closed rings) fully connected.  Endpoint flags
        # distinguish genuine line termini from interior/ring vertices.
        seen_on_part: list[tuple[float, float]] = []
        for vertex_index, point in enumerate(part.points):
            if any(points_close(point, earlier, epsilon) for earlier in seen_on_part):
                continue
            seen_on_part.append(point)
            is_endpoint = (
                not part.closed
                and (vertex_index == 0 or vertex_index == len(part.points) - 1)
            )
            add_incidence(part, point, is_endpoint)
    contact_index: dict[str, list[_Contact]] = {}
    for contact in contacts:
        contact_index.setdefault(contact.part_a, []).append(contact)
        contact_index.setdefault(contact.part_b, []).append(contact)
    for part in valid_parts:
        seen_points = [incidence.point for incidence in by_part[part.part_id]]
        for contact in contact_index.get(part.part_id, []):
            point = contact.coordinate
            if any(points_close(point, earlier, epsilon) for earlier in seen_points):
                continue
            add_incidence(part, point, False)
            seen_points.append(point)
    parent = list(range(len(incidences)))

    def find(index: int) -> int:
        root = index
        while parent[root] != root:
            root = parent[root]
        while parent[index] != root:
            parent[index], index = root, parent[index]
        return root

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[max(root_left, root_right)] = min(root_left, root_right)

    # Coincident incidences *within one part* describe the same local node.
    # Incidences from different parts are only merged through a traversable
    # contact below; blocked grade-separated or grade-unknown contacts must
    # keep their own nodes even though their coordinates are identical.
    for part_id, local in by_part.items():
        points = [incidence.point for incidence in local]
        for cluster in cluster_touching_points(points, epsilon):
            for member in cluster[1:]:
                union(local[cluster[0]].incidence_id, local[member].incidence_id)
    # Traversable real contacts merge the corresponding incidences; blocked
    # contacts deliberately keep distinct nodes so the sides stay disconnected.
    incidence_lookup = {
        (incidence.part_id, incidence.point): incidence.incidence_id
        for incidence in incidences
    }
    for contact in contacts:
        if not contact.traversable:
            continue
        id_a = _lookup_incidence(incidence_lookup, contact.part_a, contact.coordinate, epsilon)
        id_b = _lookup_incidence(incidence_lookup, contact.part_b, contact.coordinate, epsilon)
        if id_a is not None and id_b is not None:
            union(id_a, id_b)
    nodes = _materialize_nodes(incidences, find)
    edges = _materialize_edges(valid_parts, by_part, find, nodes, epsilon)
    return {
        "incidences": incidences,
        "nodes": nodes,
        "edges": edges,
        "parts": valid_parts,
        "contacts": contacts,
        "find": find,
    }


def _lookup_incidence(
    lookup: dict[tuple[str, tuple[float, float]], int],
    part_id: str,
    point: tuple[float, float],
    epsilon: float,
) -> Optional[int]:
    exact = lookup.get((part_id, point))
    if exact is not None:
        return exact
    for (candidate_part, candidate_point), incidence_id in lookup.items():
        if candidate_part == part_id and points_close(candidate_point, point, epsilon):
            return incidence_id
    return None


def _arc_position(
    points: tuple[tuple[float, float], ...],
    point: tuple[float, float],
    epsilon: float,
) -> float:
    """Accumulated polyline arc length at ``point`` from the first vertex."""
    total = 0.0
    for index in range(len(points) - 1):
        start, end = points[index], points[index + 1]
        length = distance(start, end)
        if length <= epsilon:
            continue
        if points_close(point, start, epsilon):
            return total
        if points_close(point, end, epsilon):
            total += length
            return total
        if point_is_interior(point, start, end, epsilon):
            ratio = distance(start, point) / length
            return total + max(0.0, min(1.0, ratio)) * length
        total += length
    return total


def _segment_hint(
    points: tuple[tuple[float, float], ...],
    point: tuple[float, float],
    epsilon: float,
) -> int:
    for index in range(len(points) - 1):
        start, end = points[index], points[index + 1]
        if points_close(point, start, epsilon) or point_is_interior(
            point, start, end, epsilon
        ) or points_close(point, end, epsilon):
            return index
    return max(0, len(points) - 2)


def _materialize_nodes(
    incidences: list[_Incidence], find: Any
) -> dict[int, dict[str, Any]]:
    groups: dict[int, list[_Incidence]] = {}
    for incidence in incidences:
        groups.setdefault(find(incidence.incidence_id), []).append(incidence)
    nodes: dict[int, dict[str, Any]] = {}
    for ordered, root in enumerate(sorted(groups)):
        members = sorted(
            groups[root],
            key=lambda item: (
                item.part_id,
                0 if item.endpoint else 1,
                item.arc,
                item.incidence_id,
            ),
        )
        representative = members[0]
        node_id = f"N{ordered:04d}"
        nodes[root] = {
            "node_id": node_id,
            "coordinate": list(format_point(representative.point)),
            "incidences": [
                {
                    "part_id": item.part_id,
                    "feature_id": item.part_id.split("#", 1)[0],
                    "endpoint": item.endpoint,
                    "arc": stable_round(item.arc),
                    "segment_hint": item.segment_hint,
                }
                for item in members
            ],
        }
    return nodes


def _materialize_edges(
    valid_parts: list[_Part],
    by_part: dict[str, list[_Incidence]],
    find: Any,
    nodes: dict[int, dict[str, Any]],
    epsilon: float,
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    seen_edge_keys: set[tuple[str, str, str]] = set()
    for part in sorted(valid_parts, key=lambda item: (item.source_index, item.part_index)):
        local = sorted(by_part[part.part_id], key=lambda item: item.arc)
        # Merge incidences that coincide on this part before chaining.
        chained: list[_Incidence] = []
        for incidence in local:
            if chained and points_close(chained[-1].point, incidence.point, epsilon):
                continue
            chained.append(incidence)
        if part.closed and len(chained) >= 2:
            # Close the ring by chaining its last node back to the first.
            chain = chained + [chained[0]]
        else:
            chain = chained
        for index in range(len(chain) - 1):
            first, second = chain[index], chain[index + 1]
            root_a, root_b = find(first.incidence_id), find(second.incidence_id)
            if root_a == root_b:
                continue
            node_a, node_b = nodes[root_a]["node_id"], nodes[root_b]["node_id"]
            edge_key = (part.part_id, node_a, node_b)
            if edge_key in seen_edge_keys:
                continue
            seen_edge_keys.add(edge_key)
            last_segment = (
                len(part.points) - 2
                if part.closed and index == len(chain) - 2
                else second.segment_hint
            )
            edges.append(
                {
                    "edge_id": f"E{len(edges):04d}",
                    "node_a": node_a,
                    "node_b": node_b,
                    "feature_id": part.feature_id,
                    "part_id": part.part_id,
                    "part_label": part.label,
                    "grade": part.grade,
                    "segment_range": [first.segment_hint, last_segment],
                    "length": stable_round(distance(first.point, second.point)),
                    "segment_evidence": {
                        "feature_id": part.feature_id,
                        "part_index": part.part_index,
                        "from_coordinate": list(format_point(first.point)),
                        "to_coordinate": list(format_point(second.point)),
                        "source_segment_indexes": [
                            number
                            for number in range(first.segment_hint, last_segment + 1)
                        ],
                    },
                }
            )
    return edges


def _reachability_report(
    graph: dict[str, Any],
    parts: list[_Part],
    contacts: list[_Contact],
    layer: LayerConfig,
    tolerance: Tolerance,
    feature_by_id: dict[str, Feature],
    collector: FindingCollector,
) -> dict[str, Any]:
    config = layer.network_audit
    epsilon = tolerance.coordinate_epsilon
    nodes = graph["nodes"]
    edges = graph["edges"]
    incidences = graph["incidences"]
    find = graph["find"]
    node_by_id = {node["node_id"]: node for node in nodes.values()}
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    for edge in edges:
        adjacency[edge["node_a"]].add(edge["node_b"])
        adjacency[edge["node_b"]].add(edge["node_a"])
    components = _connected_components(node_by_id, adjacency)
    component_of_node = {
        node_id: component["component_id"]
        for component in components
        for node_id in component["node_ids"]
    }
    terminals = _collect_terminals(nodes, adjacency)
    entry_records, missing_entries = _resolve_entries(
        config.entry_feature_ids, feature_by_id, incidences, find, nodes, parts
    )
    if layer.rule_enabled("NETWORK_ENTRY_NOT_FOUND"):
        known_ids = sorted(feature_by_id)
        for entry_id in missing_entries:
            collector.add(
                "NETWORK_ENTRY_NOT_FOUND",
                layer.layer_id,
                entry_id,
                evidence={
                    "entry_feature_id": entry_id,
                    "known_feature_ids": known_ids,
                },
            )
    reachable_nodes: set[str] = set()
    entry_reachability: list[dict[str, Any]] = []
    for entry in entry_records:
        reached = _bfs(adjacency, entry["node_id"])
        reachable_nodes.update(reached)
        terminal_records = []
        for terminal in terminals:
            terminal_records.append(
                {
                    "terminal_id": terminal["terminal_id"],
                    "feature_id": terminal["feature_id"],
                    "node_id": terminal["node_id"],
                    "coordinate": terminal["coordinate"],
                    "reachable": terminal["node_id"] in reached,
                }
            )
        entry_reachability.append(
            {
                "entry_feature_id": entry["entry_feature_id"],
                "node_id": entry["node_id"],
                "coordinate": entry["coordinate"],
                "reachable_node_count": len(reached),
                "reachable_terminal_ids": sorted(
                    item["terminal_id"]
                    for item in terminal_records
                    if item["reachable"]
                ),
                "unreachable_terminal_ids": sorted(
                    item["terminal_id"]
                    for item in terminal_records
                    if not item["reachable"]
                ),
                "terminals": terminal_records,
            }
        )
    reachable_terminal_ids = {
        terminal["terminal_id"]
        for entry in entry_reachability
        for terminal in entry["terminals"]
        if terminal["reachable"]
    }
    unreachable_terminals: list[dict[str, Any]] = []
    for terminal in terminals:
        if terminal["terminal_id"] in reachable_terminal_ids:
            continue
        detail = _break_evidence(terminal, reachable_nodes, nodes, edges, contacts, epsilon)
        record = {
            "terminal_id": terminal["terminal_id"],
            "feature_id": terminal["feature_id"],
            "part_id": terminal["part_id"],
            "node_id": terminal["node_id"],
            "coordinate": terminal["coordinate"],
            "component_id": component_of_node.get(terminal["node_id"]),
            "entry_feature_ids": sorted(config.entry_feature_ids),
            "nearest_reachable_node": detail["nearest_reachable_node"],
            "distance_to_network": detail["distance_to_network"],
            "blocked_contacts": detail["blocked_contacts"],
            "segment_evidence": detail["segment_evidence"],
        }
        unreachable_terminals.append(record)
        if layer.rule_enabled("NETWORK_UNREACHABLE_TERMINAL"):
            collector.add(
                "NETWORK_UNREACHABLE_TERMINAL",
                layer.layer_id,
                terminal["feature_id"],
                evidence={
                    "terminal": {
                        "terminal_id": terminal["terminal_id"],
                        "feature_id": terminal["feature_id"],
                        "part_id": terminal["part_id"],
                        "node_id": terminal["node_id"],
                        "coordinate": terminal["coordinate"],
                    },
                    "nearest_reachable_node": detail["nearest_reachable_node"],
                    "distance": detail["distance_to_network"],
                    "component_id": record["component_id"],
                    "entry_feature_ids": record["entry_feature_ids"],
                    "blocked_contacts": detail["blocked_contacts"],
                    "segment_evidence": detail["segment_evidence"],
                },
            )
    invalid_parts = [
        {
            "feature_id": part.feature_id,
            "part_index": part.part_index,
            "part_label": part.label,
            "reasons": part.invalid_reasons,
            "segment_pairs": part.invalid_pairs,
        }
        for part in sorted(parts, key=lambda item: (item.source_index, item.part_index))
        if not part.valid
    ]
    return {
        "layer_id": layer.layer_id,
        "grade_field": config.grade_field,
        "crossing_policy": config.crossing_policy,
        "coordinate_epsilon": epsilon,
        "entry_feature_ids": list(config.entry_feature_ids),
        "missing_entry_feature_ids": sorted(missing_entries),
        "valid_part_count": len(graph["parts"]),
        "invalid_part_count": len(invalid_parts),
        "invalid_parts": invalid_parts,
        "node_count": len(node_by_id),
        "edge_count": len(edges),
        "connected_components": components,
        "terminals": [
            {
                "terminal_id": terminal["terminal_id"],
                "feature_id": terminal["feature_id"],
                "part_id": terminal["part_id"],
                "node_id": terminal["node_id"],
                "coordinate": terminal["coordinate"],
                "component_id": component_of_node.get(terminal["node_id"]),
            }
            for terminal in terminals
        ],
        "junctions": _junction_records(nodes, adjacency, contacts),
        "edges": edges,
        "blocked_contacts": _blocked_contact_records(contacts),
        "entry_reachability": entry_reachability,
        "reachable_terminal_ids": sorted(reachable_terminal_ids),
        "unreachable_terminals": unreachable_terminals,
    }


def _connected_components(
    node_by_id: dict[str, dict[str, Any]], adjacency: dict[str, set[str]]
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    components: list[dict[str, Any]] = []
    for node_id in sorted(node_by_id):
        if node_id in seen:
            continue
        queue = [node_id]
        seen.add(node_id)
        members: list[str] = []
        while queue:
            current = queue.pop(0)
            members.append(current)
            for neighbor in sorted(adjacency[current]):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        members.sort()
        index = len(components)
        components.append(
            {
                "component_id": f"C{index:03d}",
                "node_ids": members,
                "node_count": len(members),
                "feature_ids": sorted(
                    {
                        incidence["feature_id"]
                        for member in members
                        for incidence in node_by_id[member]["incidences"]
                    }
                ),
            }
        )
    return components


def _collect_terminals(
    nodes: dict[int, dict[str, Any]], adjacency: dict[str, set[str]]
) -> list[dict[str, Any]]:
    terminals: list[dict[str, Any]] = []
    for root in sorted(nodes, key=lambda value: nodes[value]["node_id"]):
        node = nodes[root]
        endpoint_incidences = [
            incidence
            for incidence in node["incidences"]
            if incidence["endpoint"]
        ]
        if not endpoint_incidences:
            continue
        if len(adjacency[node["node_id"]]) == 0 and not endpoint_incidences:
            continue
        for incidence in sorted(
            endpoint_incidences, key=lambda item: (item["part_id"], item["arc"])
        ):
            terminals.append(
                {
                    "terminal_id": f"T{len(terminals):03d}",
                    "node_id": node["node_id"],
                    "feature_id": incidence["feature_id"],
                    "part_id": incidence["part_id"],
                    "coordinate": node["coordinate"],
                }
            )
    return terminals


def _resolve_entries(
    entry_ids: tuple[str, ...],
    feature_by_id: dict[str, Feature],
    incidences: list[_Incidence],
    find: Any,
    nodes: dict[int, dict[str, Any]],
    parts: list[_Part],
) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    missing: list[str] = []
    valid_part_ids = {part.part_id for part in parts if part.valid}
    for entry_id in entry_ids:
        if entry_id not in feature_by_id:
            missing.append(entry_id)
            continue
        matches = [
            incidence
            for incidence in incidences
            if incidence.part_id.split("#", 1)[0] == entry_id
            and incidence.part_id in valid_part_ids
        ]
        if not matches:
            missing.append(entry_id)
            continue
        # Prefer a genuine line endpoint; for closed rings, which have no
        # endpoints, fall back to the ring's first-vertex incidence.
        endpoints = [incidence for incidence in matches if incidence.endpoint]
        pool = endpoints if endpoints else matches
        incidence = sorted(
            pool, key=lambda item: (item.part_id, item.arc, item.incidence_id)
        )[0]
        node = nodes[find(incidence.incidence_id)]
        records.append(
            {
                "entry_feature_id": entry_id,
                "node_id": node["node_id"],
                "coordinate": node["coordinate"],
            }
        )
    return records, missing


def _bfs(adjacency: dict[str, set[str]], start: str) -> set[str]:
    seen = {start}
    queue = [start]
    while queue:
        current = queue.pop(0)
        for neighbor in sorted(adjacency[current]):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return seen


def _junction_records(
    nodes: dict[int, dict[str, Any]],
    adjacency: dict[str, set[str]],
    contacts: list[_Contact],
) -> list[dict[str, Any]]:
    # A junction is any node whose incidences span more than one line part:
    # that covers endpoint touches, T-junctions, interior crossings (whether
    # traversable or kept as separate grade-separated nodes) and overlaps.
    records: list[dict[str, Any]] = []
    for root in sorted(nodes, key=lambda value: nodes[value]["node_id"]):
        node = nodes[root]
        part_ids = {
            incidence["part_id"] for incidence in node["incidences"]
        }
        if len(part_ids) < 2:
            continue
        records.append(
            {
                "node_id": node["node_id"],
                "coordinate": node["coordinate"],
                "degree": len(adjacency[node["node_id"]]),
                "feature_ids": sorted(
                    {incidence["feature_id"] for incidence in node["incidences"]}
                ),
                "part_ids": sorted(part_ids),
            }
        )
    return records


def _blocked_contact_records(contacts: list[_Contact]) -> list[dict[str, Any]]:
    return [
        {
            "contact_type": contact.contact_type,
            "coordinate": list(format_point(contact.coordinate)),
            "feature_a": contact.feature_a,
            "feature_b": contact.feature_b,
            "part_a": contact.part_a,
            "part_b": contact.part_b,
            "segment_pair": contact.segment_pair,
            "grade_a": contact.grade_a,
            "grade_b": contact.grade_b,
            "decision": contact.decision,
            "reason": contact.reason,
        }
        for contact in sorted(
            (contact for contact in contacts if not contact.traversable),
            key=lambda contact: (
                contact.coordinate,
                contact.feature_a,
                contact.feature_b,
            ),
        )
    ]


def _break_evidence(
    terminal: dict[str, Any],
    reachable_nodes: set[str],
    nodes: dict[int, dict[str, Any]],
    edges: list[dict[str, Any]],
    contacts: list[_Contact],
    epsilon: float,
) -> dict[str, Any]:
    terminal_coordinate = tuple(terminal["coordinate"])
    nearest: Optional[dict[str, Any]] = None
    nearest_distance: Optional[float] = None
    for node in nodes.values():
        if node["node_id"] not in reachable_nodes:
            continue
        gap = distance(terminal_coordinate, tuple(node["coordinate"]))
        if nearest_distance is None or gap < nearest_distance:
            nearest_distance = gap
            nearest = node
    blocked = [
        record
        for record in _blocked_contact_records(contacts)
        if record["feature_a"] == terminal["feature_id"]
        or record["feature_b"] == terminal["feature_id"]
    ]
    segment_evidence = [
        edge["segment_evidence"]
        for edge in edges
        if edge["feature_id"] == terminal["feature_id"]
    ]
    return {
        "nearest_reachable_node": nearest["node_id"] if nearest is not None else None,
        "distance_to_network": (
            stable_round(nearest_distance) if nearest_distance is not None else None
        ),
        "blocked_contacts": blocked,
        "segment_evidence": segment_evidence,
    }
