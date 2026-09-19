import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest
from unittest import mock

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "release"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SCHEMA = ROOT / "release-manifests" / "schema-v1.json"
README = ROOT / "release-manifests" / "README.md"
CANDIDATE_FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-candidate-spec.json"
RELEASE_FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-release.json"

QUALITY_LABELS = (
    "frontend-visual",
    "home-visual",
    "frontend-automated-a11y",
    "home-axe-browser-a11y",
    "manual-nvda",
)
QUALITY_KEYS = (
    "frontend_visual",
    "home_visual",
    "frontend_automated_a11y",
    "home_axe_browser_a11y",
    "manual_nvda",
)
RESIDUE = re.compile(
    r"talkback|signed[-_ ]?(?:mobile|android|apk)|mobile_test_artifacts|SIGNED_MOBILE|\.apk|build-provenance",
    re.IGNORECASE,
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MobileFreeReleaseContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_module(
            SCRIPTS / "validate_release_manifest.py", "mobile_free_validator"
        )
        cls.verifier = load_module(
            SCRIPTS / "verify_release_artifacts.py", "mobile_free_verifier"
        )
        cls.sealer = load_module(
            SCRIPTS / "seal_release_manifest.py", "mobile_free_sealer"
        )
        cls.candidate = json.loads(CANDIDATE_FIXTURE.read_text(encoding="utf-8"))
        cls.release = json.loads(RELEASE_FIXTURE.read_text(encoding="utf-8"))
        cls.candidate_sha = hashlib.sha256(CANDIDATE_FIXTURE.read_bytes()).hexdigest()
        cls.schema = jsonschema.Draft202012Validator(
            json.loads(SCHEMA.read_text(encoding="utf-8"))
        )

    def _legacy_signed_binding(self) -> dict:
        release_id = self.candidate["release_id"]
        return {
            "schema_version": "leva.mission-spine.signed-android-build-binding.v2",
            "repository": "DevPathAi/devpath-frontend",
            "source_sha": self.candidate["frontend"]["source_sha"],
            "event": "workflow_dispatch",
            "workflow_path": ".github/workflows/mission-spine-signed-mobile-build.yml",
            "workflow_sha256": "3" * 64,
            "workflow_run_id": 701,
            "run_attempt": 1,
            "artifact_id": 801,
            "artifact_name": f"{release_id}-signed-android-build-run-701-attempt-1",
            "artifact_archive_sha256": "4" * 64,
            "build_provenance_file": "build-provenance.v2.json",
            "build_provenance_sha256": "5" * 64,
            "signed_apk_file": "mobile/android/leva-release.apk",
            "signed_apk_sha256": "6" * 64,
        }

    def test_fixtures_have_the_mobile_free_shape_and_validate(self):
        inputs = self.candidate["quality_evidence_inputs"]
        self.assertEqual(set(inputs), {"catalogs", "frontend_projection_contract"})
        self.assertEqual(tuple(inputs["catalogs"]), QUALITY_LABELS)
        self.assertEqual(tuple(self.release["quality_evidence"]), QUALITY_KEYS)
        self.schema.validate(self.candidate)
        self.schema.validate(self.release)
        self.validator.validate_candidate_spec(
            copy.deepcopy(self.candidate), CANDIDATE_FIXTURE
        )
        self.validator.validate_release_manifest(
            copy.deepcopy(self.release),
            copy.deepcopy(self.candidate),
            self.candidate_sha,
            RELEASE_FIXTURE,
        )

    def test_legacy_signed_mobile_binding_fails_closed(self):
        legacy = copy.deepcopy(self.candidate)
        legacy["quality_evidence_inputs"]["mobile_test_artifacts"] = (
            self._legacy_signed_binding()
        )
        with self.assertRaisesRegex(ValueError, "unknown fields: mobile_test_artifacts"):
            self.validator.validate_candidate_spec(legacy, CANDIDATE_FIXTURE)
        with self.assertRaises(jsonschema.ValidationError):
            self.schema.validate(legacy)

    def test_legacy_talkback_catalog_and_artifact_fail_closed(self):
        legacy = copy.deepcopy(self.candidate)
        catalogs = legacy["quality_evidence_inputs"]["catalogs"]
        talkback = copy.deepcopy(catalogs["manual-nvda"])
        talkback["path"] = "tool/release-evidence/catalogs/manual-talkback.v1.json"
        talkback["sha256"] = "7" * 64
        talkback["case_count"] = 4
        talkback["provenance_sha256"] = "8" * 64
        catalogs["manual-talkback"] = talkback
        with self.assertRaisesRegex(ValueError, "unknown fields: manual-talkback"):
            self.validator.validate_candidate_spec(legacy, CANDIDATE_FIXTURE)
        with self.assertRaises(jsonschema.ValidationError):
            self.schema.validate(legacy)

        release = copy.deepcopy(self.release)
        artifact = copy.deepcopy(release["quality_evidence"]["manual_nvda"])
        artifact["artifact_id"] += 1000
        artifact["artifact_name"] = artifact["artifact_name"].replace(
            "manual-nvda", "manual-talkback"
        )
        release["quality_evidence"]["manual_talkback"] = artifact
        with self.assertRaisesRegex(ValueError, "unknown fields: manual_talkback"):
            self.validator.validate_release_manifest(
                release,
                copy.deepcopy(self.candidate),
                self.candidate_sha,
                RELEASE_FIXTURE,
            )
        with self.assertRaises(jsonschema.ValidationError):
            self.schema.validate(release)

    def test_duplicate_logical_evidence_hash_is_rejected_without_a_lane_count(self):
        release = copy.deepcopy(self.release)
        quality = release["quality_evidence"]
        quality["manual_nvda"]["sha256"] = quality["frontend_visual"]["sha256"]
        with self.assertRaisesRegex(
            ValueError, "all logical evidence manifest hashes must be distinct"
        ):
            self.validator.validate_release_manifest(
                release,
                copy.deepcopy(self.candidate),
                self.candidate_sha,
                RELEASE_FIXTURE,
            )

    def test_contract_tables_are_nvda_only(self):
        self.assertEqual(tuple(self.validator.QUALITY_EVIDENCE), QUALITY_LABELS)
        self.assertEqual(tuple(self.validator.QUALITY_EVIDENCE.values()), QUALITY_KEYS)
        self.assertEqual(tuple(self.validator.QUALITY_EVIDENCE_FILES), QUALITY_LABELS)
        self.assertEqual(tuple(self.validator.MANUAL_CATALOG_CONTRACTS), ("manual-nvda",))
        self.assertNotIn("manual-talkback", self.validator.PRODUCER_WORKFLOWS)
        self.assertEqual(
            set(self.verifier.PROTECTED_APPROVAL_CONTRACTS),
            {
                "frontend-baseline",
                "ai-release-eval",
                "privacy-approval",
                "shared-migration-result",
                "manual-nvda",
            },
        )
        for module, removed in (
            (
                self.validator,
                (
                    "SIGNED_MOBILE_BINDING_KEYS",
                    "SIGNED_MOBILE_BINDING_VERSION",
                    "SIGNED_MOBILE_WORKFLOW",
                    "SIGNED_MOBILE_FILES",
                ),
            ),
            (
                self.verifier,
                (
                    "MAX_SIGNED_MOBILE_BINARY_BYTES",
                    "MAX_SIGNED_MOBILE_ARCHIVE_BYTES",
                    "SIGNED_MOBILE_PROVENANCE_KEYS",
                    "validate_signed_mobile_provenance",
                    "_extract_signed_mobile_archive",
                    "_download_signed_mobile_archive",
                    "validate_signed_mobile_bundle",
                    "validate_manual_chronology",
                    "_parse_mobile_version",
                    "verify_signed_mobile_artifact",
                ),
            ),
            (self.sealer, ("_discover_manual_trio",)),
        ):
            for name in removed:
                self.assertFalse(hasattr(module, name), f"{module.__name__}.{name}")

    def test_unknown_protected_producer_kind_is_rejected(self):
        with mock.patch.object(self.verifier, "_list_protected_runs", return_value=[]):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                self.verifier.select_unique_protected_producer_run(
                    {}, "DevPathAi/devpath-frontend", "a" * 40,
                    self.validator.PRODUCER_WORKFLOWS["manual-nvda"],
                    self.candidate["release_id"], "manual",
                )
        run = {
            "id": 111, "status": "completed", "conclusion": "success",
            "event": "workflow_dispatch", "head_sha": "a" * 40, "head_branch": "main",
            "path": self.validator.PRODUCER_WORKFLOWS["manual-nvda"], "run_attempt": 1,
        }
        with mock.patch.object(
            self.verifier, "_list_protected_runs", return_value=[run]
        ), mock.patch.object(self.verifier, "_list_named_artifacts", return_value=[]):
            with self.assertRaisesRegex(ValueError, "unknown protected producer kind"):
                self.verifier.select_unique_protected_producer_run(
                    {}, "DevPathAi/devpath-frontend", "a" * 40,
                    self.validator.PRODUCER_WORKFLOWS["manual-nvda"],
                    self.candidate["release_id"], "signed-mobile",
                )

    def test_sealer_discovers_the_single_manual_lane_from_one_run(self):
        release_id = self.candidate["release_id"]
        head = self.candidate["frontend"]["source_sha"]
        workflow = self.validator.PRODUCER_WORKFLOWS["manual-nvda"]

        def run(run_id):
            return {
                "id": run_id, "status": "completed", "conclusion": "success",
                "event": "workflow_dispatch", "head_sha": head, "head_branch": "main",
                "path": workflow, "run_attempt": 1,
            }

        published = {f"{release_id}-manual-nvda-run-412-attempt-1": 412}

        def artifacts(_env, _repository, name):
            if name not in published:
                return []
            return [{"name": name, "expired": False, "workflow_run": {"id": published[name]}}]

        with mock.patch.object(
            self.sealer, "list_manual_evidence_runs", return_value=[run(411), run(412)]
        ), mock.patch.object(
            self.sealer, "_list_named_artifacts", side_effect=artifacts
        ), mock.patch.object(
            self.sealer, "_discover_external_artifact", return_value={"artifact_id": 9}
        ) as discover:
            discovered = self.sealer._discover_manual_evidence(
                {}, release_id, self.candidate_sha, self.candidate
            )
        self.assertEqual(discovered, {"manual_nvda": {"artifact_id": 9}})
        discover.assert_called_once()
        args, kwargs = discover.call_args
        self.assertEqual(args[1], "manual-nvda")
        self.assertEqual(args[3], f"{release_id}-manual-nvda-run-412-attempt-1")
        self.assertEqual(kwargs["expected_run_id"], 412)
        self.assertNotIn("signed_mobile_context", kwargs)

        published[f"{release_id}-manual-nvda-run-411-attempt-1"] = 411
        with mock.patch.object(
            self.sealer, "list_manual_evidence_runs", return_value=[run(411), run(412)]
        ), mock.patch.object(
            self.sealer, "_list_named_artifacts", side_effect=artifacts
        ):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                self.sealer._discover_manual_evidence(
                    {}, release_id, self.candidate_sha, self.candidate
                )

    def test_release_contract_sources_carry_no_residue(self):
        for path in (
            SCRIPTS / "validate_release_manifest.py",
            SCRIPTS / "verify_release_artifacts.py",
            SCRIPTS / "seal_release_manifest.py",
            SCHEMA,
            README,
            CANDIDATE_FIXTURE,
            RELEASE_FIXTURE,
        ):
            with self.subTest(path=path.name):
                hits = sorted(set(RESIDUE.findall(path.read_text(encoding="utf-8"))))
                self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
