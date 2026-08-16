import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "release"
if str(SCRIPTS) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPTS))
CANDIDATE_FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-candidate-spec.json"
RELEASE_FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-release.json"
VALIDATOR = ROOT / "scripts" / "release" / "validate_release_manifest.py"
ARTIFACT_VERIFIER = ROOT / "scripts" / "release" / "verify_release_artifacts.py"
SEALER = ROOT / "scripts" / "release" / "seal_release_manifest.py"
SCHEMA = ROOT / "release-manifests" / "schema-v1.json"
VALIDATION_WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-validate.yml"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Et13EvidenceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_module(VALIDATOR, "et13_release_validator")
        cls.artifacts = load_module(ARTIFACT_VERIFIER, "et13_artifact_verifier")
        cls.sealer = load_module(SEALER, "et13_release_sealer")
        cls.candidate = json.loads(CANDIDATE_FIXTURE.read_text(encoding="utf-8"))
        cls.release = json.loads(RELEASE_FIXTURE.read_text(encoding="utf-8"))
        cls.candidate_sha = hashlib.sha256(CANDIDATE_FIXTURE.read_bytes()).hexdigest()

    def test_candidate_prebinds_exact_catalogs_and_signed_mobile_builds(self):
        inputs = self.candidate["quality_evidence_inputs"]
        self.assertEqual(set(inputs), {"catalogs", "mobile_test_artifacts"})
        self.assertEqual(set(inputs["catalogs"]), set(self.validator.QUALITY_EVIDENCE_LABELS))
        self.assertGreater(inputs["catalogs"]["frontend-visual"]["case_count"], 80)
        home = inputs["catalogs"]["home-visual"]
        self.assertEqual(
            home["source_sha"],
            "6821ab90b6625a15752f831cdce183dc0ffaa86f",
        )
        self.assertEqual(
            home["rendered_product_tree_sha256"],
            "9f7f2c06c7caa9e77a155163654cc8107670fe8c9d9cc059d1f4a6ca427bcf25",
        )
        self.assertNotEqual(home["rendered_product_sha"], home["source_sha"])
        for field in ("rendered_product_tree_sha256", "font_manifest_sha256"):
            self.assertRegex(home[field], r"^[0-9a-f]{64}$")
        self.assertEqual(
            inputs["mobile_test_artifacts"]["source_sha"],
            self.candidate["frontend"]["source_sha"],
        )
        for field in ("build_provenance_sha256", "signed_apk_sha256", "signed_ipa_sha256"):
            self.assertRegex(inputs["mobile_test_artifacts"][field], r"^[0-9a-f]{64}$")
        self.validator.validate_candidate_spec(copy.deepcopy(self.candidate), CANDIDATE_FIXTURE)

        invalid = copy.deepcopy(self.candidate)
        invalid["quality_evidence_inputs"]["catalogs"]["home-axe-browser-a11y"][
            "provenance_sha256"
        ] = "0" * 64
        with self.assertRaisesRegex(ValueError, "same combined catalog and render provenance"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

        invalid = copy.deepcopy(self.candidate)
        mobile = invalid["quality_evidence_inputs"]["mobile_test_artifacts"]
        mobile["signed_apk_sha256"] = mobile["build_provenance_sha256"]
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

    def test_schema_uses_exact_catalog_counts_without_a_screenshot_cap(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        count_schema = schema["$defs"]["qualityCatalog"]["properties"]["case_count"]
        self.assertEqual(count_schema, {"type": "integer", "minimum": 1})
        self.assertNotIn("screenshot_count", json.dumps(schema, sort_keys=True))
        self.assertEqual(schema["$defs"]["artifact"]["properties"]["event"], {"const": "workflow_dispatch"})
        self.assertEqual(schema["$defs"]["artifact"]["properties"]["evidence_file"], {"const": "evidence.json"})
        self.assertEqual(
            schema["$defs"]["homeQualityArtifact"]["properties"]["event"],
            {"const": "workflow_dispatch"},
        )

    def test_final_manifest_has_seven_distinct_source_pinned_artifacts(self):
        quality = self.release["quality_evidence"]
        self.assertEqual(set(quality), set(self.validator.QUALITY_EVIDENCE_KEYS))
        self.assertEqual(len(quality), 7)
        self.assertEqual(
            quality["home_visual"]["artifact_id"],
            quality["home_axe_browser_a11y"]["artifact_id"],
        )
        self.assertEqual(
            quality["home_visual"]["artifact_name"],
            "ms-20990101-contract-fixture-home-visual-a11y-attempt-1",
        )
        self.assertEqual(
            quality["home_axe_browser_a11y"]["artifact_name"],
            "ms-20990101-contract-fixture-home-visual-a11y-attempt-1",
        )
        self.assertEqual(quality["home_visual"]["evidence_file"], "visual-evidence.v2.json")
        self.assertEqual(
            quality["home_axe_browser_a11y"]["evidence_file"],
            "a11y-evidence.v2.json",
        )
        for key in ("home_visual", "home_axe_browser_a11y"):
            self.assertEqual(quality[key]["repository"], "DevPathAi/devpath-gitops")
            self.assertEqual(
                quality[key]["workflow_run_id"],
                self.release["validation_attestation"]["validator_run_id"],
            )
            self.assertEqual(
                quality[key]["run_attempt"],
                self.release["validation_attestation"]["validator_run_attempt"],
            )
        self.validator.validate_release_manifest(
            copy.deepcopy(self.release),
            copy.deepcopy(self.candidate),
            self.candidate_sha,
            RELEASE_FIXTURE,
        )

    def test_home_two_manifest_refs_must_share_one_exact_physical_artifact(self):
        shared_fields = (
            "repository",
            "event",
            "head_sha",
            "run_attempt",
            "workflow_path",
            "workflow_sha256",
            "workflow_run_id",
            "artifact_id",
            "artifact_name",
        )
        for field in shared_fields:
            with self.subTest(field=field):
                release = copy.deepcopy(self.release)
                current = release["quality_evidence"]["home_axe_browser_a11y"][field]
                if isinstance(current, int):
                    release["quality_evidence"]["home_axe_browser_a11y"][field] = current + 1
                elif field == "event":
                    release["quality_evidence"]["home_axe_browser_a11y"][field] = "push"
                elif field == "repository":
                    release["quality_evidence"]["home_axe_browser_a11y"][field] = "DevPathAi/devpath-frontend"
                elif field == "head_sha":
                    release["quality_evidence"]["home_axe_browser_a11y"][field] = "0" * 40
                elif field == "workflow_path":
                    release["quality_evidence"]["home_axe_browser_a11y"][field] = (
                        ".github/workflows/mission-spine-visual-evidence.yml"
                    )
                elif field == "workflow_sha256":
                    release["quality_evidence"]["home_axe_browser_a11y"][field] = "0" * 64
                else:
                    release["quality_evidence"]["home_axe_browser_a11y"][field] = "wrong-evidence"
                with self.assertRaisesRegex(ValueError, "must|mismatch|combined artifact"):
                    self.validator.validate_release_manifest(
                        release,
                        copy.deepcopy(self.candidate),
                        self.candidate_sha,
                        RELEASE_FIXTURE,
                    )

    def test_artifact_ids_are_scoped_by_repository(self):
        release = copy.deepcopy(self.release)
        release["quality_evidence"]["frontend_visual"]["artifact_id"] = release[
            "quality_evidence"
        ]["home_visual"]["artifact_id"]
        self.validator.validate_release_manifest(
            release,
            copy.deepcopy(self.candidate),
            self.candidate_sha,
            RELEASE_FIXTURE,
        )

    def test_manual_artifact_names_and_workflow_path_are_exact(self):
        mutations = (
            ("manual_nvda", "artifact_name", "ms-20990101-fixture-nvda-evidence"),
            ("manual_voiceover", "workflow_path", ".github/workflows/ci.yml"),
            ("manual_talkback", "artifact_name", "talkback-evidence-copy"),
        )
        for key, field, value in mutations:
            with self.subTest(key=key, field=field):
                release = copy.deepcopy(self.release)
                release["quality_evidence"][key][field] = value
                with self.assertRaisesRegex(ValueError, "must be"):
                    self.validator.validate_release_manifest(
                        release,
                        copy.deepcopy(self.candidate),
                        self.candidate_sha,
                        RELEASE_FIXTURE,
                    )

    def _base_payload(self, label):
        catalog = self.candidate["quality_evidence_inputs"]["catalogs"][label]
        payload = {
            "candidate_spec_sha256": self.candidate_sha,
            "status": "passed",
            "producer_run_id": 501,
            "producer_run_attempt": 3,
            "repository": catalog["repository"],
            "source_sha": catalog["source_sha"],
            "case_catalog_sha256": catalog["sha256"],
            "case_count": catalog["case_count"],
            "passed_case_count": catalog["case_count"],
            "failed_case_count": 0,
        }
        if label == "frontend-visual":
            payload.update({
                "surface_case_counts": {
                    "web": catalog["case_count"] - 48,
                    "admin": 16,
                    "mobile": 16,
                    "dp_design": 16,
                },
                "render_provenance_sha256": catalog["provenance_sha256"],
                "pixel_diff_percent": 0,
            })
        elif label == "home-visual":
            return self._home_payload("visual", catalog)
        elif label in {"frontend-automated-a11y", "home-axe-browser-a11y"}:
            if label == "home-axe-browser-a11y":
                return self._home_payload("a11y", catalog)
            payload.update({
                "test_provenance_sha256": catalog["provenance_sha256"],
                "standard": "WCAG2.2AA",
                "critical_violations": 0,
                "serious_violations": 0,
            })
            if label == "frontend-automated-a11y":
                payload["surface_case_counts"] = {
                    "web": catalog["case_count"] - 12,
                    "admin": 4,
                    "mobile": 4,
                    "dp_design": 4,
                }
        else:
            payload.update({
                "assistive_technology": {
                    "manual-nvda": "NVDA+Chromium",
                    "manual-voiceover": "VoiceOver+Safari+iOS",
                    "manual-talkback": "TalkBack+Android",
                }[label],
                "test_provenance_sha256": catalog["provenance_sha256"],
            })
            if label in {"manual-voiceover", "manual-talkback"}:
                mobile = self.candidate["quality_evidence_inputs"]["mobile_test_artifacts"]
                payload["build_provenance_sha256"] = mobile["build_provenance_sha256"]
                payload[
                    "signed_ipa_sha256" if label == "manual-voiceover" else "signed_apk_sha256"
                ] = mobile[
                    "signed_ipa_sha256" if label == "manual-voiceover" else "signed_apk_sha256"
                ]
        return payload

    def _home_payload(self, kind, catalog):
        cases = []
        for index in range(catalog["case_count"]):
            case = {
                "case_id": f"home-{kind}-{index + 1}",
                "status": "passed",
                "theme": "light",
                "viewport": {"width": 320 if index == 0 else 1240, "height": 900},
                "check_count": 3,
                "failed_check_count": 0,
            }
            if kind == "visual":
                case["artifact_sha256"] = f"{index + 1:064x}"
            else:
                case["violation_counts"] = {
                    "critical": 0,
                    "serious": 0,
                    "moderate": 0,
                    "minor": 0,
                    "total": 0,
                }
            cases.append(case)
        return {
            "$schema": "https://leva.ai.kr/schemas/home-visual-a11y-evidence-v2.json",
            "schema_version": 2,
            "document_type": f"home-{'visual' if kind == 'visual' else 'a11y'}-evidence",
            "evidence_mode": "release_ready",
            "binding": {
                "repository": catalog["repository"],
                "rendered_product_sha": catalog["rendered_product_sha"],
                "rendered_product_tree_sha256": catalog["rendered_product_tree_sha256"],
                "evidence_producer_sha": catalog["source_sha"],
                "candidate_spec_sha256": self.candidate_sha,
                "case_catalog_sha256": catalog["sha256"],
                "font_manifest_sha256": catalog["font_manifest_sha256"],
            },
            "runtime": {
                "browser": "chromium",
                "playwright_version": "1.61.1",
                "locale": "ko-KR",
                "timezone_id": "UTC",
                "device_scale_factor": 1,
                "color_scheme": "light",
                "reduced_motion": "reduce",
                "animations": "disabled",
                "clock": "2026-08-16T00:00:00.000Z",
                "network_policy": "loopback-only",
                "workers": 1,
            },
            "theme_coverage": {
                "light": "covered",
                "dark": {
                    "status": "not_applicable",
                    "reason": "No approved production dark-theme activation policy.",
                    "approval": {
                        "required": True,
                        "status": "pending",
                        "owner": "product-design",
                        "artifact": None,
                    },
                },
            },
            "baseline_review": {
                "status": "approved",
                "review_id": "design-review-2099",
            },
            "summary": {
                "required": catalog["case_count"],
                "passed": catalog["case_count"],
                "failed": 0,
            },
            "cases": cases,
            "privacy": {
                "classification": "sanitized-aggregate-only",
                "contains_raw_content": False,
            },
        }

    def test_all_seven_sanitized_payload_shapes_pass(self):
        for label in self.validator.QUALITY_EVIDENCE_LABELS:
            with self.subTest(label=label):
                self.artifacts.validate_evidence_payload(
                    label,
                    self._base_payload(label),
                    self.candidate_sha,
                    self.candidate,
                    501,
                    3,
                )

    def test_frontend_visual_supports_more_than_80_exact_catalog_cases(self):
        candidate = copy.deepcopy(self.candidate)
        catalog = candidate["quality_evidence_inputs"]["catalogs"]["frontend-visual"]
        catalog["case_count"] = 128
        payload = self._base_payload("frontend-visual")
        payload["case_count"] = 128
        payload["passed_case_count"] = 128
        payload["surface_case_counts"]["web"] += 128 - sum(payload["surface_case_counts"].values())
        self.artifacts.validate_evidence_payload(
            "frontend-visual", payload, self.candidate_sha, candidate, 501, 3
        )

        payload["passed_case_count"] = 127
        payload["failed_case_count"] = 1
        with self.assertRaisesRegex(ValueError, "all catalog cases|passed"):
            self.artifacts.validate_evidence_payload(
                "frontend-visual", payload, self.candidate_sha, candidate, 501, 3
            )

    def test_catalog_source_provenance_and_raw_content_fail_closed(self):
        cases = (
            ("case_catalog_sha256", "0" * 64, "catalog"),
            ("source_sha", "0" * 40, "source"),
            ("raw_output", "private learner output", "raw_output|key set"),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                payload = self._base_payload("frontend-visual")
                payload[field] = value
                with self.assertRaisesRegex(ValueError, message):
                    self.artifacts.validate_evidence_payload(
                        "frontend-visual", payload, self.candidate_sha, self.candidate
                    )

    def test_accessibility_requires_zero_critical_and_serious_violations(self):
        payload = self._base_payload("frontend-automated-a11y")
        payload["critical_violations"] = 1
        with self.assertRaisesRegex(ValueError, "critical_violations"):
            self.artifacts.validate_evidence_payload(
                "frontend-automated-a11y", payload, self.candidate_sha, self.candidate
            )
        payload = self._base_payload("home-axe-browser-a11y")
        payload["cases"][0]["violation_counts"]["serious"] = 1
        payload["cases"][0]["violation_counts"]["total"] = 1
        with self.assertRaisesRegex(ValueError, "serious"):
            self.artifacts.validate_evidence_payload(
                "home-axe-browser-a11y", payload, self.candidate_sha, self.candidate
            )

    def test_home_v2_binds_rendered_tree_font_runtime_and_producer_separately(self):
        mutations = (
            ("binding", "rendered_product_sha", "0" * 40, "rendered_product_sha"),
            ("binding", "rendered_product_tree_sha256", "0" * 64, "tree"),
            ("binding", "evidence_producer_sha", "0" * 40, "producer"),
            ("binding", "font_manifest_sha256", "0" * 64, "font"),
            ("runtime", "playwright_version", "latest", "provenance"),
        )
        for section, field, value, message in mutations:
            with self.subTest(field=field):
                payload = self._base_payload("home-visual")
                payload[section][field] = value
                with self.assertRaisesRegex(ValueError, message):
                    self.artifacts.validate_evidence_payload(
                        "home-visual", payload, self.candidate_sha, self.candidate
                    )

        payload = self._base_payload("home-visual")
        payload["runtime"]["platform"] = "unbound-container"
        with self.assertRaisesRegex(ValueError, "key set"):
            self.artifacts.validate_evidence_payload(
                "home-visual", payload, self.candidate_sha, self.candidate
            )

    def test_mobile_manual_evidence_binds_exact_signed_artifact_and_build(self):
        mutations = (
            ("manual-voiceover", "signed_ipa_sha256"),
            ("manual-voiceover", "build_provenance_sha256"),
            ("manual-talkback", "signed_apk_sha256"),
            ("manual-talkback", "build_provenance_sha256"),
        )
        for label, field in mutations:
            with self.subTest(label=label, field=field):
                payload = self._base_payload(label)
                payload[field] = "0" * 64
                with self.assertRaisesRegex(ValueError, field):
                    self.artifacts.validate_evidence_payload(
                        label, payload, self.candidate_sha, self.candidate
                    )

    def test_producer_workflow_allowlist_is_exact_per_artifact(self):
        expected = {
            "frontend-visual": ".github/workflows/mission-spine-visual-evidence.yml",
            "home-visual": ".github/workflows/mission-spine-validate.yml",
            "frontend-automated-a11y": ".github/workflows/mission-spine-accessibility-evidence.yml",
            "home-axe-browser-a11y": ".github/workflows/mission-spine-validate.yml",
            "manual-nvda": ".github/workflows/mission-spine-manual-at-evidence.yml",
            "manual-voiceover": ".github/workflows/mission-spine-manual-at-evidence.yml",
            "manual-talkback": ".github/workflows/mission-spine-manual-at-evidence.yml",
        }
        self.assertEqual(
            {label: self.validator.PRODUCER_WORKFLOWS[label] for label in expected},
            expected,
        )

    def test_home_release_evidence_runs_inside_candidate_validator(self):
        workflow = VALIDATION_WORKFLOW.read_text(encoding="utf-8")
        required = (
            "npm run visual:evidence:docker",
            "MISSION_CANDIDATE_SPEC_PATH: ${{ steps.candidate.outputs.candidate_spec_path }}",
            "MISSION_CANDIDATE_SPEC_SHA256: ${{ steps.candidate.outputs.candidate_spec_sha256 }}",
            "id: upload_home_quality",
            "-home-visual-a11y-attempt-${{ github.run_attempt }}",
            "home_quality_artifact_id: ${{ steps.upload_home_quality.outputs.artifact-id }}",
            "--home-quality-artifact-id",
            "--home-visual-evidence",
            "--home-a11y-evidence",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, workflow)
        self.assertGreaterEqual(
            workflow.count('sha256sum "$MISSION_CANDIDATE_SPEC_PATH"'),
            2,
        )
        ordered = (
            "--candidate-id",
            "npm run visual:evidence:docker",
            "id: upload_home_quality",
            "npm run test:release",
            "seal_release_manifest.py",
        )
        positions = [workflow.index(needle) for needle in ordered]
        self.assertEqual(positions, sorted(positions))

    def test_sealer_accepts_only_two_regular_home_manifest_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact = Path(temp_dir) / "artifact"
            artifact.mkdir()
            visual = artifact / "visual-evidence.v2.json"
            a11y = artifact / "a11y-evidence.v2.json"
            visual.write_text(
                json.dumps(self._base_payload("home-visual")),
                encoding="utf-8",
            )
            a11y.write_text(
                json.dumps(self._base_payload("home-axe-browser-a11y")),
                encoding="utf-8",
            )
            loaded = self.sealer.load_home_quality_manifests(
                visual,
                a11y,
                self.candidate_sha,
                self.candidate,
            )
            self.assertEqual(set(loaded), {"home-visual", "home-axe-browser-a11y"})

            extra = artifact / "raw-output.txt"
            extra.write_text("forbidden", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "file set is not canonical"):
                self.sealer.load_home_quality_manifests(
                    visual,
                    a11y,
                    self.candidate_sha,
                    self.candidate,
                )
            extra.unlink()

            target = Path(temp_dir) / "target"
            links = Path(temp_dir) / "links"
            target.mkdir()
            links.mkdir()
            target_visual = target / visual.name
            target_a11y = target / a11y.name
            target_visual.write_bytes(visual.read_bytes())
            target_a11y.write_bytes(a11y.read_bytes())
            try:
                (links / visual.name).symlink_to(target_visual)
                (links / a11y.name).symlink_to(target_a11y)
            except OSError:
                return
            with self.assertRaisesRegex(ValueError, "regular manifest"):
                self.sealer.load_home_quality_manifests(
                    links / visual.name,
                    links / a11y.name,
                    self.candidate_sha,
                    self.candidate,
                )


if __name__ == "__main__":
    unittest.main()
