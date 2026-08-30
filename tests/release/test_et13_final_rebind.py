import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "release"
if str(SCRIPTS) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPTS))
CANDIDATE = ROOT / "tests" / "release" / "fixtures" / "valid-candidate-spec.json"
RELEASE = ROOT / "tests" / "release" / "fixtures" / "valid-release.json"
CONTRACT = ROOT / "release-manifests" / "contracts" / "frontend-et13"
VERIFIER = ROOT / "scripts" / "release" / "verify_release_artifacts.py"

FRONTEND_SHA = "dbc1cc9010dea56471e8eec462a0c52cee946d15"
DIAGNOSTIC_FRONTEND_SHA = "dbc1cc9010dea56471e8eec462a0c52cee946d15"
CATALOG_SHA = "fa9067a499793499f67bf4db49184e58727706ee7013d624168fc7ef811d981f"
PROJECTION_SHA = "c66d08b6425628a06b27d07e08d648cfb3568d9db7c8d8aca2371172ccf4bde3"
ASSETS_LOCK_SHA = "a36c5b407ddd702778876b7a787b55aa20ebbbb720ddd46d19b0c238f7be9eeb"
RENDERER_LOCK_SHA = "af71b829ec7ba56a5a89ca2e11ea940136b70153cbd4f995c725d047d98cf20b"
AI_SOURCE_SHA = "b7203bcb000edfc0030f77a4c05dd8e1a83f7ce6"
AI_WORKFLOW_SHA = "08d4592c21abc5d314ef87f489c5a4d1bef651f7aa2892d6e669d9a7958f6ea9"
AI_PROMPT_SHA = "6f0e9042e3c5a37602ac08a632992f34563e80885684d5349dcc0c9f9e53fab5"
AI_FIXTURE_SHA = "e90cec1236e07f324d7348a7e9c9e72401c7f0a3b8de3909d79f96615121eb8c"
AI_RENDERED_CONFIG_SHA = (
    "697bd9595008b07d736df1d7166986feb48afb404372f19cbfc8fce84a3f1e40"
)
DOCUMENTS_SOURCE_SHA = "9dc2abb0cd0a1d3ba09c18884f3c5798bdf30de1"
DOCUMENTS_WORKFLOW_SHA = (
    "8c35b1d36c2f1c203a2ac02cec92ea928ebb26c217498d040d10b7aabe2d5e15"
)
LANES = {
    "frontend-visual": {
        "kind": "visual",
        "case_sha": "1f21427cec099ba1d5465d0207fd3f8261084d1e69d94827f02fa44341272e1b",
        "canonical_provenance_sha": "c084af31b89a178d906aa89afb4ce0a9b36b2758ad07321a0ee860de9bede17c",
        "raw_provenance_sha": "0a53222b8aabbf65839477a9d176b10a33b078b0632efe69469d0ed417839925",
        "manifest_sha": "b87b4c7639067118424ce9f3eb00aabfb04a6a08ee9b4af10ed947bd73fc71e4",
        "evidence_sha": "ce1a04d132c06064cb900ab9d2af545ecd692cf5ea0f7ec97285fcd27b0aa492",
        "local_candidate_sha": "d237c4ceb16852d3e3f370d06fda75398138046ed3604e5bfe5b1bbdaed3c0a6",
    },
    "frontend-automated-a11y": {
        "kind": "a11y",
        "case_sha": "2a76815be997178a6eeb9d54eb609a998fec33538803fd94e44127bdf390897c",
        "canonical_provenance_sha": "1496c1eeaef7f5e99ba6b5c2e61a6e3573d34b32e1cf84375ff1b7d83583ffee",
        "raw_provenance_sha": "8eeae50c59575c6e77c5d913e0456e5ec868c65d7f0a2b579a2c0d106c2df478",
        "manifest_sha": "96566b941df23c08950c7da0cc655e77abed462646d0bbbddadcb125515852eb",
        "evidence_sha": "4d76b8abcb0640cabff1553d8cc58975e06bcf41ec43b1ae67701d15e573c199",
        "local_candidate_sha": "ee6b09a246cd1d2cd231f68d5aa813dab4f8d53a4fdbaca597e75675312af04c",
    },
}
GOLDEN_HASHES = {
    "catalog.schema.json": "c2d03fa6a1d2dd7d07ff1e8fe485da761f8f0bbef5cfef6d64099a377a5a15a9",
    "generated-cases.schema.json": "2443100f54598e862158535dca855e68c6443c2e0951b6bfed95da61e0419162",
    "manifest.schema.json": "fe9f63be423da0e027120bf120e2435da193587786250ea0d2848caf674a01b1",
    "evidence.schema.json": "b34616cf6ddcac50ecf05e87484298fb00ee6d45db6dc58b1d470917d3257e3e",
    "release-bundle.v1.json": "b46cbc3903924267dfa9d44ea66d138ecceee22195c96c0f4fc29b0db6cfd411",
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Et13FinalRebindTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
        cls.release = json.loads(RELEASE.read_text(encoding="utf-8"))
        cls.verifier = load_module(VERIFIER, "et13_final_rebind_verifier")

    def test_candidate_binds_final_frontend_input_contract(self):
        self.assertEqual(self.candidate["frontend"]["source_sha"], FRONTEND_SHA)
        self.assertEqual(
            self.candidate["services"]["devpath-admin"]["source_sha"], FRONTEND_SHA
        )
        for label, expected in LANES.items():
            lane = self.candidate["quality_evidence_inputs"]["catalogs"][label]
            common_keys = {
                "repository",
                "source_sha",
                "path",
                "sha256",
                "case_catalog_version",
                "case_catalog_schema_version",
                "projection_contract_sha256",
                "fixture_ids",
                "case_count",
                "surface_case_counts",
                "capture_surface",
                "device_evidence",
                "evidence_mode",
                "input_provenance_sha256",
                "input_provenance_file_sha256",
            }
            if label == "frontend-visual":
                common_keys |= {
                    "baseline_status",
                    "baseline_set_sha256",
                    "baseline_approval_sha256",
                }
            self.assertEqual(set(lane), common_keys)
            self.assertEqual(lane["source_sha"], FRONTEND_SHA)
            self.assertEqual(lane["sha256"], expected["case_sha"])
            self.assertEqual(lane["projection_contract_sha256"], PROJECTION_SHA)
            # A release-ready provenance contains protected baseline authentication;
            # the observed diagnostic provenance must never be substituted for it.
            self.assertNotEqual(
                lane["input_provenance_sha256"],
                expected["canonical_provenance_sha"],
            )
            self.assertNotEqual(
                lane["input_provenance_file_sha256"],
                expected["raw_provenance_sha"],
            )

    def test_committed_frontend_golden_contract_is_byte_pinned(self):
        for name, expected_hash in GOLDEN_HASHES.items():
            path = CONTRACT / name
            self.assertTrue(path.is_file(), name)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected_hash)
        bundle = json.loads((CONTRACT / "release-bundle.v1.json").read_text())
        self.assertEqual(bundle["schema_version"], "leva.et13.release-bundle.v1")
        self.assertEqual(bundle["capture_surface"], "flutter_web_release_projection")
        self.assertIs(bundle["device_evidence"], False)
        self.assertEqual([lane["kind"] for lane in bundle["lanes"]], ["visual", "a11y"])

    def test_ai_and_privacy_producer_coordinates_are_final_and_static_only(self):
        ai = self.candidate["services"]["devpath-ai-svc"]
        config = self.candidate["ai_release_eval_config"]
        self.assertEqual(ai["source_sha"], AI_SOURCE_SHA)
        self.assertEqual(config["runtime_primary_model"], "qwen2.5:3b")
        self.assertEqual(config["runtime_fallback_models"], ["claude-sonnet-4-6"])
        self.assertEqual(
            config["development_model"],
            "devpath-mentor-eval:mentor-development-tuning-v1",
        )
        self.assertEqual(config["tuning_revision"], "mentor-development-tuning-v1")
        self.assertEqual(
            config["tuning_sha256"],
            "325d43fadad64dcf43c0b74c60e124c95c8b5b10ef15979aed34552aba1b7bef",
        )
        self.assertEqual(config["prompt_sha256"], AI_PROMPT_SHA)
        self.assertEqual(config["fixture_revision"], "mentor-golden-v2")
        self.assertEqual(config["fixture_sha256"], AI_FIXTURE_SHA)
        self.assertEqual(config["rendered_config_sha256"], AI_RENDERED_CONFIG_SHA)
        ai_evidence = self.release["ai_release_eval"]["evidence"]
        self.assertEqual(ai_evidence["head_sha"], AI_SOURCE_SHA)
        self.assertEqual(ai_evidence["workflow_sha256"], AI_WORKFLOW_SHA)
        self.assertEqual(
            self.candidate["analytics_privacy"]["approval_source_sha"],
            DOCUMENTS_SOURCE_SHA,
        )
        privacy_evidence = self.release["analytics_privacy_approval"]["evidence"]
        self.assertEqual(privacy_evidence["head_sha"], DOCUMENTS_SOURCE_SHA)
        self.assertEqual(privacy_evidence["workflow_sha256"], DOCUMENTS_WORKFLOW_SHA)

    def test_diagnostic_snapshot_is_complete_and_explicitly_unsealable(self):
        snapshot = json.loads(
            (CONTRACT / "diagnostic-producer-snapshot.v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            set(snapshot),
            {
                "schema_version",
                "contract_use",
                "sealable",
                "repository",
                "source_sha",
                "capture_surface",
                "device_evidence",
                "catalog_sha256",
                "projection_contract_sha256",
                "assets_lock_sha256",
                "renderer_lock_sha256",
                "lanes",
                "release_blockers",
            },
        )
        self.assertEqual(snapshot["schema_version"], "leva.et13.diagnostic-snapshot.v1")
        self.assertEqual(snapshot["contract_use"], "diagnostic_observation_only")
        self.assertIs(snapshot["sealable"], False)
        self.assertEqual(snapshot["repository"], "DevPathAi/devpath-frontend")
        self.assertEqual(snapshot["source_sha"], DIAGNOSTIC_FRONTEND_SHA)
        self.assertEqual(snapshot["capture_surface"], "flutter_web_release_projection")
        self.assertIs(snapshot["device_evidence"], False)
        self.assertEqual(snapshot["catalog_sha256"], CATALOG_SHA)
        self.assertEqual(snapshot["projection_contract_sha256"], PROJECTION_SHA)
        self.assertEqual(snapshot["assets_lock_sha256"], ASSETS_LOCK_SHA)
        self.assertEqual(snapshot["renderer_lock_sha256"], RENDERER_LOCK_SHA)
        self.assertEqual(
            snapshot["release_blockers"],
            ["protected_baseline_approval", "canonical_candidate_rerun"],
        )
        canonical_candidate_sha = hashlib.sha256(CANDIDATE.read_bytes()).hexdigest()
        for label, expected in LANES.items():
            lane = snapshot["lanes"][expected["kind"]]
            expected_keys = {
                "generated_cases_path",
                "generated_cases_sha256",
                "input_provenance_sha256",
                "input_provenance_file_sha256",
                "result_manifest_path",
                "result_manifest_sha256",
                "evidence_file_path",
                "evidence_file_sha256",
                "candidate_spec_sha256",
                "status",
                "evidence_mode",
            }
            if expected["kind"] == "visual":
                expected_keys |= {
                    "baseline_status",
                    "baseline_set_sha256",
                    "baseline_approval_sha256",
                }
            self.assertEqual(set(lane), expected_keys)
            self.assertEqual(lane["generated_cases_sha256"], expected["case_sha"])
            self.assertEqual(
                lane["input_provenance_sha256"],
                expected["canonical_provenance_sha"],
            )
            self.assertEqual(
                lane["input_provenance_file_sha256"], expected["raw_provenance_sha"]
            )
            self.assertEqual(lane["result_manifest_sha256"], expected["manifest_sha"])
            self.assertEqual(lane["evidence_file_sha256"], expected["evidence_sha"])
            self.assertEqual(lane["candidate_spec_sha256"], expected["local_candidate_sha"])
            self.assertNotEqual(lane["candidate_spec_sha256"], canonical_candidate_sha)
            self.assertEqual(lane["evidence_mode"], "diagnostic")
        visual = snapshot["lanes"]["visual"]
        self.assertEqual(visual["baseline_status"], "pending_external_review")
        self.assertIsNone(visual["baseline_set_sha256"])
        self.assertIsNone(visual["baseline_approval_sha256"])

    def test_release_ready_provenance_requires_exact_baseline_authentication(self):
        catalog = self.candidate["quality_evidence_inputs"]["catalogs"][
            "frontend-visual"
        ]
        authentication = {
            "release_id": self.candidate["release_id"],
            "repository": "DevPathAi/devpath-frontend",
            "workflow_path": ".github/workflows/et13-baseline-approval.yml",
            "workflow_sha256": "1" * 64,
            "run_id": 7001,
            "run_attempt": 1,
            "head_sha": FRONTEND_SHA,
            "artifact_id": 8001,
            "artifact_name": (
                f'{self.candidate["release_id"]}-frontend-visual-approved-baseline-'
                "run-7001-attempt-1"
            ),
            "artifact_archive_sha256": "2" * 64,
            "approval_document_sha256": catalog["baseline_approval_sha256"],
            "approval_environment": "et13-baseline-approval",
            "approval_environment_id": 9001,
            "approved_by_id": 10001,
            "approved_by": "et13-reviewer",
            "approval_effective_at": "2099-01-01T00:00:00Z",
        }
        self.assertEqual(
            self.verifier.validate_frontend_baseline_authentication(
                authentication, self.candidate, catalog
            ),
            authentication,
        )
        self.verifier.validate_atomic_frontend_authentication(
            {
                "frontend-visual": authentication,
                "frontend-automated-a11y": copy.deepcopy(authentication),
            }
        )
        mismatched = copy.deepcopy(authentication)
        mismatched["artifact_id"] += 1
        with self.assertRaisesRegex(ValueError, "authentication mismatch"):
            self.verifier.validate_atomic_frontend_authentication(
                {
                    "frontend-visual": authentication,
                    "frontend-automated-a11y": mismatched,
                }
            )
        for field, invalid_value in (
            ("release_id", "other-release"),
            ("head_sha", "0" * 40),
            ("workflow_path", ".github/workflows/et13-evidence.yml"),
            ("approval_document_sha256", "0" * 64),
            ("approval_environment", "unprotected"),
            ("approved_by", "bad login"),
            ("approval_effective_at", "2099-01-01T09:00:00+09:00"),
            ("artifact_name", "lookalike"),
        ):
            with self.subTest(field=field):
                mutated = copy.deepcopy(authentication)
                mutated[field] = invalid_value
                with self.assertRaises(ValueError):
                    self.verifier.validate_frontend_baseline_authentication(
                        mutated, self.candidate, catalog
                    )

    def test_candidate_sidecar_and_all_release_references_bind_raw_bytes(self):
        candidate_sha = hashlib.sha256(CANDIDATE.read_bytes()).hexdigest()
        self.assertEqual(
            CANDIDATE.with_suffix(".sha256").read_text(encoding="utf-8").split(),
            [candidate_sha, CANDIDATE.name],
        )
        references = [self.release["candidate_spec"]["sha256"]]

        def collect(value):
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key == "candidate_spec_sha256":
                        references.append(nested)
                    collect(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect(nested)

        collect(self.release)
        self.assertEqual(len(references), 13)
        self.assertEqual(set(references), {candidate_sha})


if __name__ == "__main__":
    unittest.main()
