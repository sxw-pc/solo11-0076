import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from topoaudit.config import load_config
from topoaudit.engine import run_audit
from topoaudit.ledger import write_report
from topoaudit.ledger_query import get_network_audit, get_network_reachability
from topoaudit.ledger_verify import verify_report_and_ledger
from topoaudit.loaders import load_layers
from topoaudit.model import LoadedLayer
from topoaudit.pipeline import execute
from topoaudit.report_json import build_report
from topoaudit.rule_topology import audit_line_network
from topoaudit.rules import run_rules, run_rules_with_network

ROOT = Path(__file__).resolve().parent.parent
NETWORK_DIR = ROOT / "fixtures" / "network"


def _config_dict(geojson_name, layer_id, entries, grade_field=None,
                 crossing_policy=None, near_miss_distance=2.0):
    network = {"entry_feature_ids": entries}
    if grade_field is not None:
        network["grade_field"] = grade_field
    if crossing_policy is not None:
        network["crossing_policy"] = crossing_policy
    return {
        "schema_version": "1.0",
        "run_label": f"network-{geojson_name}",
        "tolerance": {
            "coordinate_epsilon": 1e-6,
            "near_miss_distance": near_miss_distance,
            "sliver_ratio": 0.1,
        },
        "crs": {
            "name": "LOCAL_XY",
            "type": "planar",
            "bounds": {"min_x": -100, "min_y": -100, "max_x": 1000, "max_y": 1000},
        },
        "layers": [
            {
                "id": layer_id,
                "path": str((NETWORK_DIR / geojson_name).resolve()),
                "format": "geojson",
                "geometry_types": ["LineString", "MultiLineString"],
                "id_field": "line_id",
                "schema": {
                    "line_id": {"type": "string", "required": True},
                    "grade": {"type": "string", "required": False},
                },
                "network_audit": network,
            }
        ],
    }


def _write_config(tmp: Path, name: str, data: dict) -> Path:
    path = tmp / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _audit(tmp: Path, geojson: str, entries, grade_field=None,
           crossing_policy=None, near_miss_distance=2.0):
    layer_id = "net"
    data = _config_dict(
        geojson, layer_id, entries, grade_field, crossing_policy, near_miss_distance
    )
    config_path = _write_config(tmp, f"config-{geojson}.json", data)
    config = load_config(config_path)
    result = run_audit(config)
    report = build_report(result)
    return config, result, report


def _by_rule(result, rule_id):
    return [f for f in result.findings if f.rule_id == rule_id]


class GeometryCoreNetworkTest(unittest.TestCase):
    def test_real_crossing_returns_intersection_point(self):
        from topoaudit.geometry_core import segment_relation
        relation = segment_relation((0, 10), (20, 10), (10, 0), (10, 20), 1e-6)
        self.assertEqual(relation["relation"], "crossing")
        self.assertEqual(relation["points"], [(10.0, 10.0)])
        self.assertTrue(relation["interior_to_ab"])
        self.assertTrue(relation["interior_to_cd"])

    def test_near_but_not_touching_is_disjoint(self):
        from topoaudit.geometry_core import segment_relation
        relation = segment_relation((0, 10), (20, 10), (20.5, 10), (40, 10), 1e-6)
        self.assertEqual(relation["relation"], "disjoint")
        self.assertEqual(relation["points"], [])

    def test_t_junction_is_crossing_with_one_interior(self):
        from topoaudit.geometry_core import segment_relation
        # branch start (10,10) is an endpoint of c-d but interior to a-b
        relation = segment_relation((0, 10), (30, 10), (10, 10), (10, 30), 1e-6)
        self.assertEqual(relation["relation"], "crossing")
        self.assertTrue(relation["interior_to_ab"])
        self.assertFalse(relation["interior_to_cd"])

    def test_collinear_overlap_returns_interval(self):
        from topoaudit.geometry_core import segment_relation
        relation = segment_relation((0, 0), (10, 0), (5, 0), (15, 0), 1e-6)
        self.assertEqual(relation["relation"], "overlap")
        self.assertEqual(relation["points"], [(5.0, 0.0), (10.0, 0.0)])

    def test_grade_decisions_are_conservative(self):
        from topoaudit.geometry_core import grade_connection_decision
        same = grade_connection_decision("L1", "L1")
        self.assertTrue(same["connected"])
        separated = grade_connection_decision("L1", "L2")
        self.assertFalse(separated["connected"])
        self.assertEqual(separated["decision"], "grade_separated")
        unknown = grade_connection_decision("L1", None)
        self.assertFalse(unknown["connected"])
        self.assertEqual(unknown["decision"], "grade_undecidable")
        both_unknown = grade_connection_decision(None, None)
        self.assertFalse(both_unknown["connected"])


class PlanarCrossTest(unittest.TestCase):
    def test_planar_junction_connects_all_four_arms(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _, result, _ = _audit(
                tmp, "planar_cross.geojson", ["PX-H"],
                crossing_policy="planar_junction",
            )
            network = result.network_audits[0]
            self.assertEqual(network["node_count"], 5)
            self.assertEqual(network["edge_count"], 4)
            self.assertEqual(len(network["connected_components"]), 1)
            entry = network["entry_reachability"][0]
            reachable = set(entry["reachable_terminal_ids"])
            self.assertEqual(len(reachable), 4)
            self.assertEqual(entry["unreachable_terminal_ids"], [])
            self.assertEqual(network["unreachable_terminals"], [])

    def test_grade_unknown_keeps_crossing_lines_separate(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _, result, report = _audit(
                tmp, "planar_cross.geojson", ["PX-H"],
                crossing_policy="grade_unknown",
            )
            network = result.network_audits[0]
            self.assertEqual(len(network["connected_components"]), 2)
            entry = network["entry_reachability"][0]
            self.assertEqual(len(entry["reachable_terminal_ids"]), 2)
            unreachable_ids = set(entry["unreachable_terminal_ids"])
            self.assertEqual(len(unreachable_ids), 2)
            blocked = network["blocked_contacts"]
            self.assertEqual(len(blocked), 1)
            self.assertEqual(blocked[0]["decision"], "unknown_grade_crossing")
            unknown_findings = _by_rule(result, "NETWORK_UNKNOWN_CROSSING")
            self.assertEqual(len(unknown_findings), 1)
            self.assertTrue(
                any(
                    rule["rule_id"] == "NETWORK_UNKNOWN_CROSSING"
                    for rule in report["rules"]
                )
            )

    def test_two_policies_emit_different_reachable_sets(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _, planar, _ = _audit(
                tmp, "planar_cross.geojson", ["PX-H"],
                crossing_policy="planar_junction",
            )
            _, unknown, _ = _audit(
                tmp, "planar_cross.geojson", ["PX-H"],
                crossing_policy="grade_unknown",
            )
            planar_north = planar.network_audits[0]["reachable_terminal_ids"]
            unknown_north = unknown.network_audits[0]["reachable_terminal_ids"]
            self.assertNotEqual(planar_north, unknown_north)
            self.assertGreater(len(planar_north), len(unknown_north))

    def test_grade_unknown_crossing_is_two_separate_nodes(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _, planar, _ = _audit(
                tmp, "planar_cross.geojson", ["PX-H"],
                crossing_policy="planar_junction",
            )
            _, unknown, _ = _audit(
                tmp, "planar_cross.geojson", ["PX-H"],
                crossing_policy="grade_unknown",
            )
            planar_network = planar.network_audits[0]
            unknown_network = unknown.network_audits[0]
            # planar policy: one four-way junction node
            self.assertEqual(len(planar_network["junctions"]), 1)
            self.assertEqual(
                planar_network["junctions"][0]["degree"], 4
            )
            # grade_unknown: no multi-part graph node, but a reviewable block
            self.assertEqual(unknown_network["junctions"], [])
            self.assertEqual(
                unknown_network["node_count"],
                planar_network["node_count"] + 1,
            )


class GradeBridgeTest(unittest.TestCase):
    def test_cross_grade_bridge_does_not_connect(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _, result, _ = _audit(
                tmp, "grade_bridge.geojson", ["BR-GROUND"], grade_field="grade"
            )
            network = result.network_audits[0]
            self.assertEqual(len(network["connected_components"]), 2)
            entry = network["entry_reachability"][0]
            self.assertEqual(len(entry["reachable_terminal_ids"]), 2)
            self.assertEqual(len(entry["unreachable_terminal_ids"]), 2)
            blocked = network["blocked_contacts"]
            self.assertEqual(len(blocked), 1)
            self.assertEqual(blocked[0]["decision"], "grade_separated")
            separated = _by_rule(result, "NETWORK_GRADE_SEPARATED")
            self.assertEqual(len(separated), 1)
            evidence = separated[0].evidence
            self.assertEqual(evidence["grade_a"], "G0")
            self.assertEqual(evidence["grade_b"], "G2")

    def test_same_grade_crossing_is_connected(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _, result, _ = _audit(
                tmp, "planar_cross.geojson", ["PX-H"], grade_field="grade"
            )
            network = result.network_audits[0]
            self.assertEqual(len(network["connected_components"]), 1)
            self.assertEqual(len(network["reachable_terminal_ids"]), 4)
            self.assertEqual(_by_rule(result, "NETWORK_GRADE_SEPARATED"), [])

    def test_missing_grade_is_undecidable_never_connected(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _, result, _ = _audit(
                tmp, "missing_grade.geojson", ["MG-KNOWN"], grade_field="grade"
            )
            network = result.network_audits[0]
            self.assertEqual(len(network["connected_components"]), 2)
            blocked = network["blocked_contacts"]
            self.assertEqual(blocked[0]["decision"], "grade_undecidable")
            undecidable = _by_rule(result, "NETWORK_GRADE_UNDUCIDABLE")
            self.assertEqual(len(undecidable), 1)
            self.assertIsNone(undecidable[0].evidence["grade_b"])


class TJunctionTest(unittest.TestCase):
    def _run(self, policy):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            return _audit(
                tmp, "t_junction.geojson", ["TJ-TRUNK"],
                crossing_policy=policy,
            )

    def test_t_junction_connected_under_both_policies(self):
        for policy in ("planar_junction", "grade_unknown"):
            _, result, _ = self._run(policy)
            network = result.network_audits[0]
            self.assertEqual(len(network["connected_components"]), 1, policy)
            entry = network["entry_reachability"][0]
            # trunk start/end (2) + branch tip (1) + branch base shares the
            # trunk's interior node and is itself an endpoint (1) => 4 terminals
            self.assertEqual(len(entry["reachable_terminal_ids"]), 4, policy)
            self.assertEqual(network["unreachable_terminals"], [], policy)
            self.assertEqual(network["blocked_contacts"], [], policy)

    def test_t_junction_evidence_points_at_real_endpoint(self):
        _, result, _ = self._run("grade_unknown")
        network = result.network_audits[0]
        junction = network["junctions"][0]
        self.assertEqual(junction["coordinate"], [10.0, 10.0])
        self.assertEqual(
            sorted(junction["feature_ids"]), ["TJ-BRANCH", "TJ-TRUNK"]
        )


class NearMissTest(unittest.TestCase):
    def test_near_lines_are_not_connected(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _, result, _ = _audit(
                tmp, "near_miss.geojson", ["NM-LEFT"],
                crossing_policy="planar_junction",
            )
            network = result.network_audits[0]
            # three genuinely separate lines despite bbox overlap/proximity
            self.assertEqual(len(network["connected_components"]), 3)
            entry = network["entry_reachability"][0]
            self.assertEqual(len(entry["reachable_terminal_ids"]), 2)
            unreachable_features = {
                item["feature_id"] for item in network["unreachable_terminals"]
            }
            self.assertEqual(unreachable_features, {"NM-RIGHT", "NM-STUB"})
            near_findings = _by_rule(result, "NEAR_MISS_ENDPOINT")
            self.assertTrue(near_findings)
            # proximity never fabricates a junction
            self.assertEqual(network["junctions"], [])

    def test_grade_unknown_also_refuses_near_connection(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _, result, _ = _audit(
                tmp, "near_miss.geojson", ["NM-LEFT"],
                crossing_policy="grade_unknown",
            )
            network = result.network_audits[0]
            self.assertEqual(len(network["connected_components"]), 3)
            self.assertEqual(network["blocked_contacts"], [])


class InvalidSegmentTest(unittest.TestCase):
    def test_invalid_geometry_never_bridges_disconnected_sides(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _, result, _ = _audit(
                tmp, "invalid_segments.geojson", ["IV-WEST"],
                crossing_policy="planar_junction",
            )
            network = result.network_audits[0]
            invalid = network["invalid_parts"]
            reasons = {
                item["feature_id"]: item["reasons"][0] for item in invalid
            }
            self.assertEqual(reasons["IV-BOWTIE"], "self_intersection")
            self.assertEqual(reasons["IV-POINT"], "too_few_points")
            self.assertEqual(reasons["IV-NAN"], "non_finite_or_missing_coordinate")
            invalid_findings = _by_rule(result, "NETWORK_INVALID_SEGMENT")
            self.assertEqual(
                sorted(f.feature_id for f in invalid_findings),
                ["IV-BOWTIE", "IV-NAN", "IV-POINT"],
            )
            # valid graph contains only the two short end lines, not joined
            self.assertEqual(len(network["connected_components"]), 2)
            entry = network["entry_reachability"][0]
            self.assertEqual(len(entry["reachable_terminal_ids"]), 2)
            east_terminals = [
                item for item in network["unreachable_terminals"]
                if item["feature_id"] == "IV-EAST"
            ]
            self.assertEqual(len(east_terminals), 2)

    def test_invalid_part_has_segment_pair_evidence(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _, result, _ = _audit(
                tmp, "invalid_segments.geojson", ["IV-WEST"],
                crossing_policy="planar_junction",
            )
            bowtie = next(
                f for f in _by_rule(result, "NETWORK_INVALID_SEGMENT")
                if f.feature_id == "IV-BOWTIE"
            )
            self.assertTrue(bowtie.evidence["segment_pairs"])


class RingAndMultiEntryTest(unittest.TestCase):
    def test_ring_spur_and_multi_entry_reachability(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _, result, _ = _audit(
                tmp, "ring_multi_entry.geojson",
                ["RG-RING", "RG-MULTI"],
                crossing_policy="planar_junction",
            )
            network = result.network_audits[0]
            self.assertEqual(len(network["entry_reachability"]), 2)
            component_feature_sets = [
                set(component["feature_ids"])
                for component in network["connected_components"]
            ]
            self.assertIn({"RG-RING", "RG-SPUR"}, component_feature_sets)
            self.assertIn({"RG-MULTI"}, component_feature_sets)
            self.assertIn({"RG-ISLAND"}, component_feature_sets)
            # ring component: spur tip reachable from ring entry
            ring_entry = next(
                item for item in network["entry_reachability"]
                if item["entry_feature_id"] == "RG-RING"
            )
            spur_terminals = [
                terminal["terminal_id"]
                for terminal in network["terminals"]
                if terminal["feature_id"] == "RG-SPUR"
            ]
            self.assertTrue(
                set(spur_terminals).issubset(set(ring_entry["reachable_terminal_ids"]))
            )
            # multi-entry: island terminal unreachable from both entries
            island_terminals = {
                item["terminal_id"]
                for item in network["unreachable_terminals"]
                if item["feature_id"] == "RG-ISLAND"
            }
            self.assertEqual(len(island_terminals), 2)

    def test_multiline_string_split_at_t_junction(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _, result, _ = _audit(
                tmp, "ring_multi_entry.geojson", ["RG-MULTI"],
                crossing_policy="planar_junction",
            )
            network = result.network_audits[0]
            multi_edges = [
                edge for edge in network["edges"]
                if edge["feature_id"] == "RG-MULTI"
            ]
            # two parts joined at the shared (50,0) node: 3 edges total
            self.assertEqual(len(multi_edges), 3)


class EntryValidationTest(unittest.TestCase):
    def test_unknown_entry_emits_finding_and_skips_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _, result, _ = _audit(
                tmp, "t_junction.geojson", ["NO-SUCH-FEATURE"],
                crossing_policy="planar_junction",
            )
            not_found = _by_rule(result, "NETWORK_ENTRY_NOT_FOUND")
            self.assertEqual(len(not_found), 1)
            self.assertEqual(not_found[0].feature_id, "NO-SUCH-FEATURE")
            self.assertIn("TJ-TRUNK", not_found[0].evidence["known_feature_ids"])
            network = result.network_audits[0]
            self.assertEqual(network["entry_reachability"], [])
            self.assertEqual(
                network["missing_entry_feature_ids"], ["NO-SUCH-FEATURE"]
            )

    def test_config_rejects_unknown_crossing_policy(self):
        from topoaudit.errors import ConfigError
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            data = _config_dict(
                "t_junction.geojson", "net", ["TJ-TRUNK"],
                crossing_policy="flyover",
            )
            config_path = _write_config(tmp, "bad.json", data)
            with self.assertRaises(ConfigError) as caught:
                load_config(config_path)
            self.assertEqual(caught.exception.code, "TOPO-CFG-006")

    def test_config_rejects_grade_and_policy_together(self):
        from topoaudit.errors import ConfigError
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            data = _config_dict(
                "t_junction.geojson", "net", ["TJ-TRUNK"],
                grade_field="grade", crossing_policy="planar_junction",
            )
            config_path = _write_config(tmp, "bad.json", data)
            with self.assertRaises(ConfigError):
                load_config(config_path)

    def test_layer_without_network_audit_keeps_legacy_behaviour(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            data = _config_dict(
                "t_junction.geojson", "net", ["TJ-TRUNK"],
                crossing_policy="planar_junction",
            )
            data["layers"][0].pop("network_audit")
            config_path = _write_config(tmp, "legacy.json", data)
            config = load_config(config_path)
            layers = load_layers(config)
            findings = run_rules(config, layers)
            self.assertFalse(
                any(f.rule_id.startswith("NETWORK_") for f in findings)
            )
            findings_two, networks = run_rules_with_network(config, layers)
            self.assertEqual(networks, ())
            self.assertEqual(len(findings_two), len(findings))


class DeterminismAndLedgerTest(unittest.TestCase):
    def test_repeated_runs_are_identical(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            data = _config_dict(
                "ring_multi_entry.geojson", "net",
                ["RG-RING", "RG-MULTI"], crossing_policy="grade_unknown",
            )
            config_path = _write_config(tmp, "config.json", data)
            first = build_report(run_audit(load_config(config_path)))
            second = build_report(run_audit(load_config(config_path)))
            self.assertEqual(first, second)
            self.assertEqual(first["result_digest"], second["result_digest"])

    def test_feature_order_does_not_change_network(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            data = _config_dict(
                "t_junction.geojson", "net", ["TJ-TRUNK"],
                crossing_policy="planar_junction",
            )
            config_path = _write_config(tmp, "config.json", data)
            config = load_config(config_path)
            layers = load_layers(config)
            _, baseline = run_rules_with_network(config, layers)
            reversed_layers = tuple(
                LoadedLayer(
                    layer.config,
                    tuple(reversed(layer.features)),
                    layer.input_file,
                )
                for layer in layers
            )
            _, reversed_reports = run_rules_with_network(config, reversed_layers)
            self.assertEqual(
                json.loads(json.dumps(baseline, sort_keys=True)),
                json.loads(json.dumps(reversed_reports, sort_keys=True)),
            )

    def test_json_and_sqlite_are_consistent(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            data = _config_dict(
                "grade_bridge.geojson", "net", ["BR-GROUND"], grade_field="grade"
            )
            config_path = _write_config(tmp, "config.json", data)
            report, paths = execute(config_path, tmp / "out")
            verification = verify_report_and_ledger(
                paths["json"], paths["ledger"]
            )
            self.assertTrue(verification.ok, verification.issues)
            digest = report["result_digest"]
            stored = get_network_audit(paths["ledger"], digest)
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["layer_id"], "net")
            matrix = get_network_reachability(paths["ledger"], digest)
            self.assertEqual(len(matrix), 4)
            reachable = get_network_reachability(
                paths["ledger"], digest, reachable=True
            )
            self.assertEqual(len(reachable), 2)
            unreachable = get_network_reachability(
                paths["ledger"], digest, reachable=False
            )
            self.assertEqual(len(unreachable), 2)
            connection = sqlite3.connect(paths["ledger"])
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM network_audit_runs"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM network_audit_reachability"
                    ).fetchone()[0],
                    4,
                )
            finally:
                connection.close()
            # idempotent re-write
            before = report["result_digest"]
            write_report(paths["ledger"], report)
            self.assertEqual(before, report["result_digest"])

    def test_tampered_network_report_fails_verification(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            data = _config_dict(
                "grade_bridge.geojson", "net", ["BR-GROUND"], grade_field="grade"
            )
            config_path = _write_config(tmp, "config.json", data)
            report, paths = execute(config_path, tmp / "out")
            tampered = json.loads(
                Path(paths["json"]).read_text(encoding="utf-8")
            )
            tampered["network_audit"][0]["node_count"] = 999
            target = tmp / "tampered.json"
            target.write_text(json.dumps(tampered), encoding="utf-8")
            result = verify_report_and_ledger(target, paths["ledger"])
            self.assertFalse(result.ok)
            self.assertTrue(
                any("网络审计" in issue for issue in result.issues)
            )

    def test_source_files_are_not_modified(self):
        import hashlib
        source = NETWORK_DIR / "t_junction.geojson"
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _audit(
                tmp, "t_junction.geojson", ["TJ-TRUNK"],
                crossing_policy="planar_junction",
            )
        after = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_md_and_html_reports_contain_network_section(self):
        from topoaudit.report_html import render_html
        from topoaudit.report_md import render_markdown
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            _, _, report = _audit(
                tmp, "grade_bridge.geojson", ["BR-GROUND"], grade_field="grade"
            )
            markdown = render_markdown(report)
            html = render_html(report)
            self.assertIn("线网可达性审计", markdown)
            self.assertIn("BR-GROUND", markdown)
            self.assertIn("线网可达性审计", html)
            self.assertNotIn("<script", html.lower())


class CollinearOverlapTest(unittest.TestCase):
    def _overlap_config(self, tmp, policy):
        geojson = tmp / "overlap.geojson"
        geojson.write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {"line_id": "OL-A"},
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[0, 0], [10, 0]],
                            },
                        },
                        {
                            "type": "Feature",
                            "properties": {"line_id": "OL-B"},
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[5, 0], [15, 0]],
                            },
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        data = _config_dict(
            "overlap.geojson", "net", ["OL-A"], crossing_policy=policy
        )
        data["layers"][0]["path"] = str(geojson)
        return load_config(_write_config(tmp, f"c-{policy}.json", data))

    def test_overlap_connects_under_both_policies(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            for policy in ("planar_junction", "grade_unknown"):
                network = run_audit(
                    self._overlap_config(tmp, policy)
                ).network_audits[0]
                self.assertEqual(
                    len(network["connected_components"]), 1, policy
                )
                self.assertEqual(network["blocked_contacts"], [], policy)
                self.assertEqual(len(network["reachable_terminal_ids"]), 4, policy)
                overlap_junctions = [
                    junction
                    for junction in network["junctions"]
                    if set(junction["feature_ids"]) == {"OL-A", "OL-B"}
                ]
                self.assertTrue(overlap_junctions, policy)


class DisabledRuleTest(unittest.TestCase):
    def test_network_findings_respect_rule_switches(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            data = _config_dict(
                "grade_bridge.geojson", "net", ["BR-GROUND"], grade_field="grade"
            )
            config_path = _write_config(tmp, "config.json", data)
            config = load_config(config_path)
            layer = config.layers[0]
            rules = dict(layer.rules)
            rules["NETWORK_GRADE_SEPARATED"] = False
            rules["NETWORK_UNREACHABLE_TERMINAL"] = False
            patched_layer = replace(layer, rules=rules)
            patched_config = replace(config, layers=(patched_layer,))
            result = run_audit(patched_config)
            self.assertEqual(_by_rule(result, "NETWORK_GRADE_SEPARATED"), [])
            self.assertEqual(_by_rule(result, "NETWORK_UNREACHABLE_TERMINAL"), [])
            # graph itself is unchanged; only findings are suppressed
            network = result.network_audits[0]
            self.assertEqual(len(network["connected_components"]), 2)


if __name__ == "__main__":
    unittest.main()
