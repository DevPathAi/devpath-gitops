import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "release"
if str(SCRIPTS) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPTS))
CANDIDATE_FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-candidate-spec.json"
RELEASE_FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-release.json"
VALIDATOR = SCRIPTS / "validate_release_manifest.py"
VERIFIER = SCRIPTS / "verify_release_artifacts.py"
SEALER = SCRIPTS / "seal_release_manifest.py"
SCHEMA = ROOT / "release-manifests" / "schema-v1.json"

FIXTURE_IDS = [
    "web-today-available",
    "web-path-current-week",
    "web-content-reading",
    "web-workspace-idle",
    "web-review-loaded",
    "web-mentor-context-preview",
    "admin-kpi-dashboard",
    "admin-support-long-wire",
    "mobile-today-available",
    "mobile-content-reading",
    "dp-design-mission-ledger",
    "dp-design-context-payload-preview",
]
CATALOG_VERSION = "leva.et13.catalog.v1"
PROJECTION_CONTRACT_VERSION = "leva.et13.projection-contract.v1"
PROJECTION_MATRIX = [
    {
        "fixture_id": "web-today-available",
        "capture_scope": "body_projection",
        "source_widget": "TodayMissionSection",
        "substitutions": [
            "controller/provider state replaced by approved deterministic fixture"
        ],
    },
    {
        "fixture_id": "web-path-current-week",
        "capture_scope": "body_projection",
        "source_widget": "MissionPathPlanView",
        "substitutions": [
            "controller/provider state replaced by approved deterministic fixture"
        ],
    },
    {
        "fixture_id": "web-content-reading",
        "capture_scope": "body_projection",
        "source_widget": "WebContentProjection",
        "substitutions": [
            "AdSense slot replaced by explicit offline blocked-network evidence slot"
        ],
    },
    {
        "fixture_id": "web-workspace-idle",
        "capture_scope": "body_projection",
        "source_widget": "SandboxLayout",
        "substitutions": [
            "controller state replaced by approved deterministic fixture",
            "Monaco callbacks disabled except production readiness",
        ],
    },
    {
        "fixture_id": "web-review-loaded",
        "capture_scope": "component_projection",
        "source_widget": "WebReviewProjection",
        "substitutions": [
            "controller state replaced by approved deterministic fixture"
        ],
    },
    {
        "fixture_id": "web-mentor-context-preview",
        "capture_scope": "body_projection",
        "source_widget": "WebMentorContextProjection",
        "substitutions": [
            "controller/provider state replaced by approved deterministic fixture"
        ],
    },
    {
        "fixture_id": "admin-kpi-dashboard",
        "capture_scope": "body_projection",
        "source_widget": "AdminKpiDashboardProjection",
        "substitutions": [
            "controller/provider state replaced by approved deterministic fixture"
        ],
    },
    {
        "fixture_id": "admin-support-long-wire",
        "capture_scope": "component_projection",
        "source_widget": "AdminSupportDetailProjection",
        "substitutions": [
            "live provider and dialog shell replaced by deterministic AlertDialog host"
        ],
    },
    {
        "fixture_id": "mobile-today-available",
        "capture_scope": "body_projection",
        "source_widget": "MobileTodayProjection",
        "substitutions": [
            "native AppBar and route shell omitted",
            "controller/provider state replaced by approved deterministic fixture",
        ],
    },
    {
        "fixture_id": "mobile-content-reading",
        "capture_scope": "body_projection",
        "source_widget": "MobileContentProjection",
        "substitutions": [
            "native AppBar and route shell omitted",
            "periodic dwell timer frozen",
            "controller/provider state replaced by approved deterministic fixture",
        ],
    },
    {
        "fixture_id": "dp-design-mission-ledger",
        "capture_scope": "component_projection",
        "source_widget": "DpEt13MissionLedgerFixture",
        "substitutions": ["hosted by the Flutter Web production distribution"],
    },
    {
        "fixture_id": "dp-design-context-payload-preview",
        "capture_scope": "component_projection",
        "source_widget": "DpEt13ContextPayloadPreviewFixture",
        "substitutions": ["hosted by the Flutter Web production distribution"],
    },
]
PROJECTION_SHA256 = "c66d08b6425628a06b27d07e08d648cfb3568d9db7c8d8aca2371172ccf4bde3"
LANES = {
    "frontend-visual": {
        "kind": "visual",
        "schema_version": "leva.et13.visual-cases.v1",
        "manifest_schema": "leva.et13.visual-manifest.v1",
        "catalog_file": "evidence/et13/generated/visual-cases.v1.json",
        "manifest_file": "artifacts/et13/visual-manifest.v1.json",
        "count": 96,
        "surfaces": {"web": 48, "admin": 16, "mobile": 16, "dp_design": 16},
    },
    "frontend-automated-a11y": {
        "kind": "a11y",
        "schema_version": "leva.et13.a11y-cases.v1",
        "manifest_schema": "leva.et13.a11y-manifest.v1",
        "catalog_file": "evidence/et13/generated/a11y-cases.v1.json",
        "manifest_file": "artifacts/et13/a11y-manifest.v1.json",
        "count": 24,
        "surfaces": {"web": 12, "admin": 4, "mobile": 4, "dp_design": 4},
    },
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def raw_json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_sha(value):
    raw = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(raw)


def surface_for(fixture_id):
    if fixture_id.startswith("web-"):
        return "web"
    if fixture_id.startswith("admin-"):
        return "admin"
    if fixture_id.startswith("mobile-"):
        return "mobile"
    return "dp_design"


def generated_cases(label):
    lane = LANES[label]
    profiles = (
        [(width, theme, 100) for width in (320, 600, 840, 1240) for theme in ("light", "dark")]
        if lane["kind"] == "visual"
        else [(320, "light", 200), (1240, "dark", 200)]
    )
    cases = []
    projection_by_fixture = {row["fixture_id"]: row for row in PROJECTION_MATRIX}
    for fixture_id in FIXTURE_IDS:
        surface = surface_for(fixture_id)
        projection = projection_by_fixture[fixture_id]
        for width, theme, text_scale in profiles:
            suffix = (
                f"visual--w{width}--{theme}"
                if lane["kind"] == "visual"
                else f"a11y--w{width}--{theme}--text200"
            )
            extension = "png" if lane["kind"] == "visual" else "json"
            case_id = f"{fixture_id}--{suffix}"
            cases.append({
                "case_id": case_id,
                "fixture_id": fixture_id,
                "owner": surface,
                "distribution": "web" if surface == "dp_design" else surface,
                "route": f"/?fixture={fixture_id}",
                "ready_semantics_label": f"ET13_READY:{fixture_id}",
                "surface_label": fixture_id,
                "capture_scope": projection["capture_scope"],
                "source_widget": projection["source_widget"],
                "substitutions": projection["substitutions"],
                "width": width,
                "height": 900,
                "device_pixel_ratio": 1,
                "theme": theme,
                "text_scale_percent": text_scale,
                "locale": "ko-KR",
                "timezone": "UTC",
                "reduced_motion": True,
                "artifact_path": f"{lane['kind']}/{surface}/{case_id}.{extension}",
            })
    return cases


class Et13AtomicEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_module(VALIDATOR, "atomic_release_validator")
        cls.verifier = load_module(VERIFIER, "atomic_artifact_verifier")
        cls.sealer = load_module(SEALER, "atomic_release_sealer")
        cls.candidate = json.loads(CANDIDATE_FIXTURE.read_text(encoding="utf-8"))
        cls.release = json.loads(RELEASE_FIXTURE.read_text(encoding="utf-8"))
        cls.candidate_sha = sha256(CANDIDATE_FIXTURE.read_bytes())

    def test_candidate_distinguishes_catalog_schema_and_prebinds_only_inputs(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        for label, definition in (
            ("frontend-visual", "frontendVisualCatalog"),
            ("frontend-automated-a11y", "frontendAutomatedA11yCatalog"),
        ):
            lane = LANES[label]
            catalog = self.candidate["quality_evidence_inputs"]["catalogs"][label]
            self.assertEqual(catalog["projection_contract_sha256"], PROJECTION_SHA256)
            self.assertEqual(catalog["case_catalog_version"], CATALOG_VERSION)
            self.assertEqual(catalog["case_catalog_schema_version"], lane["schema_version"])
            self.assertRegex(catalog["input_provenance_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(catalog["input_provenance_file_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(catalog["evidence_mode"], "release_ready")
            self.assertNotIn("provenance_sha256", catalog)
            self.assertNotIn("result_manifest_sha256", catalog)

            properties = schema["$defs"][definition]["properties"]
            self.assertEqual(
                properties["projection_contract_sha256"],
                {"const": PROJECTION_SHA256},
            )
            self.assertEqual(properties["case_catalog_version"], {"const": CATALOG_VERSION})
            self.assertEqual(
                properties["case_catalog_schema_version"],
                {"const": lane["schema_version"]},
            )
            self.assertNotIn("result_manifest_sha256", properties)

        projection = self.candidate["quality_evidence_inputs"][
            "frontend_projection_contract"
        ]
        self.assertEqual(
            projection,
            {
                "schema_version": PROJECTION_CONTRACT_VERSION,
                "projection_contract_sha256": PROJECTION_SHA256,
                "projection_matrix": PROJECTION_MATRIX,
            },
        )
        self.assertEqual(canonical_sha(PROJECTION_MATRIX), PROJECTION_SHA256)
        projection_schema = schema["$defs"]["frontendProjectionContract"]
        self.assertFalse(projection_schema["additionalProperties"])
        self.assertEqual(
            projection_schema["properties"]["projection_matrix"],
            {"const": PROJECTION_MATRIX},
        )

        for mutation in ("order", "source_widget", "substitution", "digest"):
            with self.subTest(projection_mutation=mutation):
                invalid = copy.deepcopy(self.candidate)
                contract = invalid["quality_evidence_inputs"][
                    "frontend_projection_contract"
                ]
                if mutation == "order":
                    contract["projection_matrix"][0], contract["projection_matrix"][1] = (
                        contract["projection_matrix"][1],
                        contract["projection_matrix"][0],
                    )
                elif mutation == "source_widget":
                    contract["projection_matrix"][0]["source_widget"] = "WrongWidget"
                elif mutation == "substitution":
                    contract["projection_matrix"][0]["substitutions"] = ["wrong"]
                else:
                    contract["projection_contract_sha256"] = "f" * 64
                with self.assertRaisesRegex(ValueError, "projection contract"):
                    self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

        visual = self.candidate["quality_evidence_inputs"]["catalogs"]["frontend-visual"]
        self.assertEqual(visual["baseline_status"], "approved")
        self.assertRegex(visual["baseline_set_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(visual["baseline_approval_sha256"], r"^[0-9a-f]{64}$")

        invalid = copy.deepcopy(self.candidate)
        invalid["quality_evidence_inputs"]["catalogs"]["frontend-visual"][
            "baseline_status"
        ] = "pending_external_review"
        with self.assertRaisesRegex(ValueError, "approved baseline|release-ready"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

        invalid = copy.deepcopy(self.candidate)
        invalid["quality_evidence_inputs"]["catalogs"]["frontend-visual"][
            "result_manifest_sha256"
        ] = "f" * 64
        with self.assertRaisesRegex(ValueError, "unknown fields|result"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

    def test_frontend_prebound_digests_are_pairwise_distinct(self):
        catalogs = self.candidate["quality_evidence_inputs"]["catalogs"]
        digests = [
            catalogs["frontend-visual"]["sha256"],
            catalogs["frontend-visual"]["input_provenance_sha256"],
            catalogs["frontend-visual"]["input_provenance_file_sha256"],
            catalogs["frontend-visual"]["baseline_set_sha256"],
            catalogs["frontend-visual"]["baseline_approval_sha256"],
            catalogs["frontend-automated-a11y"]["sha256"],
            catalogs["frontend-automated-a11y"]["input_provenance_sha256"],
            catalogs["frontend-automated-a11y"]["input_provenance_file_sha256"],
        ]
        self.assertEqual(len(set(digests)), len(digests))

        for source_field, target_label, target_field in (
            ("sha256", "frontend-automated-a11y", "sha256"),
            ("input_provenance_sha256", "frontend-automated-a11y", "sha256"),
            (
                "input_provenance_file_sha256",
                "frontend-automated-a11y",
                "input_provenance_sha256",
            ),
        ):
            with self.subTest(source=source_field, target=target_field):
                invalid = copy.deepcopy(self.candidate)
                invalid["quality_evidence_inputs"]["catalogs"]["frontend-visual"][
                    source_field
                ] = invalid["quality_evidence_inputs"]["catalogs"][target_label][target_field]
                with self.assertRaisesRegex(ValueError, "distinct"):
                    self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

    def test_release_frontend_pair_is_atomic_and_rerun_scoped(self):
        visual = self.release["quality_evidence"]["frontend_visual"]
        a11y = self.release["quality_evidence"]["frontend_automated_a11y"]
        shared = (
            "repository",
            "event",
            "head_sha",
            "run_attempt",
            "workflow_path",
            "workflow_sha256",
            "workflow_run_id",
        )
        for field in shared:
            self.assertEqual(visual[field], a11y[field])
        self.assertEqual(visual["workflow_path"], ".github/workflows/et13-evidence.yml")
        for key, label in (
            ("frontend_visual", "frontend-visual"),
            ("frontend_automated_a11y", "frontend-automated-a11y"),
        ):
            artifact = self.release["quality_evidence"][key]
            self.assertEqual(
                artifact["artifact_name"],
                (
                    f"{self.release['release_id']}-{label}-run-"
                    f"{artifact['workflow_run_id']}-attempt-{artifact['run_attempt']}"
                ),
            )

        for field in ("workflow_run_id", "run_attempt", "head_sha", "workflow_sha256"):
            with self.subTest(field=field):
                invalid = copy.deepcopy(self.release)
                artifact = invalid["quality_evidence"]["frontend_automated_a11y"]
                artifact[field] = artifact[field] + 1 if isinstance(artifact[field], int) else "f" * len(artifact[field])
                if field in {"workflow_run_id", "run_attempt"}:
                    artifact["artifact_name"] = (
                        f"{invalid['release_id']}-frontend-automated-a11y-run-"
                        f"{artifact['workflow_run_id']}-attempt-{artifact['run_attempt']}"
                    )
                expected_error = (
                    "exact producer source SHA"
                    if field == "head_sha"
                    else "atomic frontend evidence pair"
                )
                with self.assertRaisesRegex(ValueError, expected_error):
                    self.validator.validate_release_manifest(
                        invalid,
                        copy.deepcopy(self.candidate),
                        self.candidate_sha,
                        RELEASE_FIXTURE,
                    )

    def test_rerun_selection_uses_one_run_id_and_highest_attempt(self):
        base = {
            "id": 501,
            "status": "completed",
            "conclusion": "success",
            "event": "workflow_dispatch",
            "head_sha": self.candidate["frontend"]["source_sha"],
            "path": ".github/workflows/et13-evidence.yml",
        }
        selected = self.sealer.select_frontend_evidence_run([
            {**base, "run_attempt": 1},
            {**base, "run_attempt": 2},
        ], self.candidate["frontend"]["source_sha"])
        self.assertEqual((selected["id"], selected["run_attempt"]), (501, 2))

        with self.assertRaisesRegex(ValueError, "exactly one frontend producer run"):
            self.sealer.select_frontend_evidence_run(
                [{**base, "run_attempt": 2}, {**base, "id": 502, "run_attempt": 1}],
                self.candidate["frontend"]["source_sha"],
            )

    def test_run_discovery_paginates_and_detects_page_two_competing_run(self):
        expected_head = self.candidate["frontend"]["source_sha"]
        base = {
            "id": 501,
            "status": "completed",
            "conclusion": "success",
            "event": "workflow_dispatch",
            "head_sha": expected_head,
            "path": ".github/workflows/et13-evidence.yml",
            "run_attempt": 2,
        }
        page_one = [base] + [
            {**base, "id": 600 + index, "path": ".github/workflows/other.yml"}
            for index in range(99)
        ]
        page_two = [{**base, "id": 502, "run_attempt": 1}]

        def fake_gh_json(arguments, _env):
            endpoint = arguments[-1]
            if endpoint.endswith("&page=1"):
                return {"workflow_runs": page_one}
            if endpoint.endswith("&page=2"):
                return {"workflow_runs": page_two}
            self.fail(f"unexpected pagination request: {endpoint}")

        with mock.patch.object(self.sealer, "_gh_json", side_effect=fake_gh_json):
            runs = self.sealer.list_frontend_evidence_runs(
                {}, self.candidate["frontend"]["repository"], expected_head
            )
        self.assertEqual(len(runs), 101)
        with self.assertRaisesRegex(ValueError, "exactly one frontend producer run"):
            self.sealer.select_frontend_evidence_run(runs, expected_head)

        page_two = [{**base, "id": 501, "run_attempt": 3}]
        with mock.patch.object(self.sealer, "_gh_json", side_effect=fake_gh_json):
            runs = self.sealer.list_frontend_evidence_runs(
                {}, self.candidate["frontend"]["repository"], expected_head
            )
        selected = self.sealer.select_frontend_evidence_run(runs, expected_head)
        self.assertEqual((selected["id"], selected["run_attempt"]), (501, 3))

    def _write_bundle(self, root, label):
        lane = LANES[label]
        candidate = copy.deepcopy(self.candidate)
        catalog_binding = candidate["quality_evidence_inputs"]["catalogs"][label]
        cases = generated_cases(label)
        generated = {
            "schema_version": lane["schema_version"],
            "case_catalog_version": CATALOG_VERSION,
            "catalog_sha256": "7" * 64,
            "projection_contract_sha256": PROJECTION_SHA256,
            "projection_matrix": PROJECTION_MATRIX,
            "fixture_ids": FIXTURE_IDS,
            "case_count": lane["count"],
            "surface_case_counts": lane["surfaces"],
            "cases": cases,
        }
        generated_raw = raw_json(generated)
        catalog_path = root / lane["catalog_file"]
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_bytes(generated_raw)
        catalog_binding["sha256"] = sha256(generated_raw)

        provenance_inputs = {
            "schema_version": "leva.et13.input-provenance.v1",
            "kind": lane["kind"],
            "source_sha": candidate["frontend"]["source_sha"],
            "catalog_sha256": generated["catalog_sha256"],
            "case_catalog_sha256": catalog_binding["sha256"],
            "projection_contract_sha256": PROJECTION_SHA256,
            "assets_lock_sha256": "8" * 64,
            "renderer_lock_sha256": "9" * 64,
            "renderer_image_digest": "sha256:" + "a" * 64,
            "build_marker_sha256": "b" * 64,
        }
        provenance = {
            **provenance_inputs,
            "input_provenance_sha256": canonical_sha(provenance_inputs),
        }
        provenance_raw = raw_json(provenance)
        provenance_path = root / "artifacts/et13/provenance.v1.json"
        provenance_path.parent.mkdir(parents=True, exist_ok=True)
        provenance_path.write_bytes(provenance_raw)
        catalog_binding["input_provenance_sha256"] = provenance[
            "input_provenance_sha256"
        ]
        catalog_binding["input_provenance_file_sha256"] = sha256(provenance_raw)

        result_cases = []
        for index, case in enumerate(cases):
            result = {
                "case_id": case["case_id"],
                "status": "passed",
                "artifact_path": case["artifact_path"],
                "sha256": f"{index + 1000:064x}",
                "bytes": index + 1,
            }
            if lane["kind"] == "a11y":
                result.update({
                    "standard": "WCAG 2.2 AA",
                    "critical_violations": 0,
                    "serious_violations": 0,
                    "other_violations": 0,
                    "passes": 1,
                    "incomplete": 0,
                })
            result_cases.append(result)
        manifest = {
            "schema_version": lane["manifest_schema"],
            "case_catalog_version": CATALOG_VERSION,
            "case_catalog_schema_version": lane["schema_version"],
            "fixture_ids": FIXTURE_IDS,
            "source_sha": candidate["frontend"]["source_sha"],
            "catalog_sha256": generated["catalog_sha256"],
            "case_catalog_sha256": catalog_binding["sha256"],
            "projection_contract_sha256": PROJECTION_SHA256,
            "assets_lock_sha256": provenance["assets_lock_sha256"],
            "renderer_lock_sha256": provenance["renderer_lock_sha256"],
            "input_provenance_sha256": provenance["input_provenance_sha256"],
            "renderer_image": "renderer@example@" + provenance["renderer_image_digest"],
            "renderer_image_digest": provenance["renderer_image_digest"],
            "capture_network": "none",
            "unexpected_request_policy": "fail",
            "capture_surface": "flutter_web_release_projection",
            "device_evidence": False,
            "external_accessibility_status": "not_satisfied",
            "evidence_mode": "release_ready",
            "case_count": lane["count"],
            "surface_case_counts": lane["surfaces"],
            "cases": result_cases,
        }
        if lane["kind"] == "visual":
            manifest.update({
                "baseline_status": "approved",
                "baseline_set_sha256": catalog_binding["baseline_set_sha256"],
                "baseline_approval_sha256": catalog_binding["baseline_approval_sha256"],
            })
        manifest_raw = raw_json(manifest)
        (root / lane["manifest_file"]).write_bytes(manifest_raw)

        payload = {
            "candidate_spec_sha256": "d" * 64,
            "status": "passed",
            "producer_run_id": 501,
            "producer_run_attempt": 2,
            "repository": candidate["frontend"]["repository"],
            "source_sha": candidate["frontend"]["source_sha"],
            "case_catalog_sha256": catalog_binding["sha256"],
            "projection_contract_sha256": PROJECTION_SHA256,
            "case_catalog_version": CATALOG_VERSION,
            "case_catalog_schema_version": lane["schema_version"],
            "fixture_ids": FIXTURE_IDS,
            "case_count": lane["count"],
            "passed_case_count": lane["count"],
            "failed_case_count": 0,
            "surface_case_counts": lane["surfaces"],
            "capture_surface": "flutter_web_release_projection",
            "device_evidence": False,
            "input_provenance_sha256": provenance["input_provenance_sha256"],
            "input_provenance_file_sha256": sha256(provenance_raw),
            "result_manifest_sha256": sha256(manifest_raw),
            "evidence_mode": "release_ready",
        }
        if lane["kind"] == "visual":
            payload.update({
                "baseline_status": "approved",
                "baseline_set_sha256": catalog_binding["baseline_set_sha256"],
                "baseline_approval_sha256": catalog_binding["baseline_approval_sha256"],
                "pixel_diff_percent": 0,
            })
        else:
            payload.update({
                "standard": "WCAG 2.2 AA",
                "critical_violations": 0,
                "serious_violations": 0,
            })
        (root / "evidence.json").write_bytes(raw_json(payload))
        return candidate, payload, generated, provenance, manifest

    def test_exact_four_file_bundle_verifies_raw_bytes_and_ordered_results(self):
        for label, lane in LANES.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                candidate, payload, _, _, manifest = self._write_bundle(root, label)
                self.validator.validate_candidate_spec(candidate, CANDIDATE_FIXTURE)
                self.verifier.validate_evidence_payload(
                    label, payload, "d" * 64, candidate, 501, 2
                )
                self.verifier.validate_frontend_evidence_bundle(
                    label, root, payload, candidate
                )
                self.assertEqual(
                    sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()),
                    sorted(self.validator.FRONTEND_EVIDENCE_FILES[label]),
                )
                manifest["cases"][1]["sha256"] = manifest["cases"][0]["sha256"]
                manifest_raw = raw_json(manifest)
                (root / lane["manifest_file"]).write_bytes(manifest_raw)
                payload["result_manifest_sha256"] = sha256(manifest_raw)
                (root / "evidence.json").write_bytes(raw_json(payload))
                self.verifier.validate_frontend_evidence_bundle(
                    label, root, payload, candidate
                )

    def test_bundle_rejects_raw_drift_pending_baseline_and_case_tampering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate, payload, _, _, _ = self._write_bundle(root, "frontend-visual")

            catalog_path = root / "evidence/et13/generated/visual-cases.v1.json"
            catalog_path.write_bytes(catalog_path.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "catalog raw SHA"):
                self.verifier.validate_frontend_evidence_bundle(
                    "frontend-visual", root, payload, candidate
                )

        for field, value in (
            ("device_pixel_ratio", 2),
            ("distribution", "admin"),
            ("route", "/arbitrary"),
            ("ready_semantics_label", "READY"),
            ("surface_label", "wrong-surface"),
            ("capture_scope", "native_device"),
            ("source_widget", "WrongWidget"),
            ("substitutions", ["wrong"]),
        ):
            with self.subTest(generated_field=field), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                candidate, payload, generated, _, _ = self._write_bundle(
                    root, "frontend-visual"
                )
                generated["cases"][0][field] = value
                raw = raw_json(generated)
                (root / "evidence/et13/generated/visual-cases.v1.json").write_bytes(raw)
                candidate["quality_evidence_inputs"]["catalogs"]["frontend-visual"][
                    "sha256"
                ] = sha256(raw)
                with self.assertRaisesRegex(ValueError, "exact ordered cases"):
                    self.verifier.validate_frontend_evidence_bundle(
                        "frontend-visual", root, payload, candidate
                    )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate, payload, generated, _, _ = self._write_bundle(root, "frontend-visual")
            generated["cases"][0], generated["cases"][1] = (
                generated["cases"][1],
                generated["cases"][0],
            )
            raw = raw_json(generated)
            (root / "evidence/et13/generated/visual-cases.v1.json").write_bytes(raw)
            candidate["quality_evidence_inputs"]["catalogs"]["frontend-visual"][
                "sha256"
            ] = sha256(raw)
            with self.assertRaisesRegex(ValueError, "exact ordered cases"):
                self.verifier.validate_frontend_evidence_bundle(
                    "frontend-visual", root, payload, candidate
                )

        for field, value in (
            ("projection_contract_sha256", "f" * 64),
            (
                "projection_matrix",
                list(reversed(PROJECTION_MATRIX)),
            ),
        ):
            with self.subTest(generated_projection_field=field), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                candidate, payload, generated, _, _ = self._write_bundle(
                    root, "frontend-visual"
                )
                generated[field] = value
                raw = raw_json(generated)
                (root / "evidence/et13/generated/visual-cases.v1.json").write_bytes(raw)
                candidate["quality_evidence_inputs"]["catalogs"]["frontend-visual"][
                    "sha256"
                ] = sha256(raw)
                with self.assertRaisesRegex(ValueError, "identity or exact matrix"):
                    self.verifier.validate_frontend_evidence_bundle(
                        "frontend-visual", root, payload, candidate
                    )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate, payload, _, provenance, _ = self._write_bundle(root, "frontend-visual")
            provenance["build_marker_sha256"] = "c" * 64
            raw = raw_json(provenance)
            (root / "artifacts/et13/provenance.v1.json").write_bytes(raw)
            candidate["quality_evidence_inputs"]["catalogs"]["frontend-visual"][
                "input_provenance_file_sha256"
            ] = sha256(raw)
            payload["input_provenance_file_sha256"] = sha256(raw)
            (root / "evidence.json").write_bytes(raw_json(payload))
            with self.assertRaisesRegex(ValueError, "canonical or raw input provenance"):
                self.verifier.validate_frontend_evidence_bundle(
                    "frontend-visual", root, payload, candidate
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate, payload, _, _, _ = self._write_bundle(root, "frontend-visual")
            manifest_path = root / "artifacts/et13/visual-manifest.v1.json"
            manifest_path.write_bytes(manifest_path.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "result manifest raw SHA"):
                self.verifier.validate_frontend_evidence_bundle(
                    "frontend-visual", root, payload, candidate
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate, payload, _, _, manifest = self._write_bundle(root, "frontend-visual")
            manifest["cases"][0]["status"] = "pending"
            raw = raw_json(manifest)
            (root / "artifacts/et13/visual-manifest.v1.json").write_bytes(raw)
            payload["result_manifest_sha256"] = sha256(raw)
            (root / "evidence.json").write_bytes(raw_json(payload))
            with self.assertRaisesRegex(ValueError, "ordered passed cases"):
                self.verifier.validate_frontend_evidence_bundle(
                    "frontend-visual", root, payload, candidate
                )

        for field, value in (
            ("case_catalog_schema_version", "leva.et13.wrong-cases.v1"),
            ("projection_contract_sha256", "f" * 64),
        ):
            with self.subTest(manifest_field=field), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                candidate, payload, _, _, manifest = self._write_bundle(
                    root, "frontend-visual"
                )
                manifest[field] = value
                raw = raw_json(manifest)
                (root / "artifacts/et13/visual-manifest.v1.json").write_bytes(raw)
                payload["result_manifest_sha256"] = sha256(raw)
                (root / "evidence.json").write_bytes(raw_json(payload))
                with self.assertRaisesRegex(ValueError, f"result manifest {field} mismatch"):
                    self.verifier.validate_frontend_evidence_bundle(
                        "frontend-visual", root, payload, candidate
                    )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate, payload, _, _, _ = self._write_bundle(root, "frontend-visual")
            (root / "review.zip").write_bytes(b"raw-review")
            with self.assertRaisesRegex(ValueError, "exactly four sanitized files"):
                self.verifier.validate_frontend_evidence_bundle(
                    "frontend-visual", root, payload, candidate
                )

        for field, value in (
            ("evidence_mode", "diagnostic"),
            ("baseline_status", "pending_external_review"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                candidate, payload, _, _, _ = self._write_bundle(root, "frontend-visual")
                payload[field] = value
                with self.assertRaisesRegex(ValueError, "catalog order|approved baseline"):
                    self.verifier.validate_evidence_payload(
                        "frontend-visual", payload, "d" * 64, candidate, 501, 2
                    )


if __name__ == "__main__":
    unittest.main()
