import copy
import hashlib
import importlib.util
import json
import jsonschema
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-release.json"
CANDIDATE_FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-candidate-spec.json"
VALIDATOR = ROOT / "scripts" / "release" / "validate_release_manifest.py"
PROMOTER = ROOT / "scripts" / "release" / "set_web_digest.py"
ARTIFACT_VERIFIER = ROOT / "scripts" / "release" / "verify_release_artifacts.py"
JOURNEY_PREPARER = ROOT / "scripts" / "release" / "prepare_journey_evidence.py"
SEALER = ROOT / "scripts" / "release" / "seal_release_manifest.py"
CANDIDATE_WEB_BASE = ROOT / "scripts" / "release" / "verify_candidate_web_base.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReleaseManifestContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_module(VALIDATOR, "release_validator")
        cls.promoter = load_module(PROMOTER, "web_digest_promoter")
        cls.artifacts = load_module(ARTIFACT_VERIFIER, "release_artifact_verifier")
        cls.preparer = load_module(JOURNEY_PREPARER, "journey_evidence_preparer")
        cls.sealer = load_module(SEALER, "release_manifest_sealer")
        cls.candidate_web_base = load_module(
            CANDIDATE_WEB_BASE, "candidate_web_base_verifier"
        )
        cls.release = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.candidate = json.loads(CANDIDATE_FIXTURE.read_text(encoding="utf-8"))
        cls.candidate_sha = hashlib.sha256(CANDIDATE_FIXTURE.read_bytes()).hexdigest()

    def validate_bundle(self, candidate=None, release=None):
        candidate = copy.deepcopy(self.candidate if candidate is None else candidate)
        release = copy.deepcopy(self.release if release is None else release)
        return self.validator.validate_release_manifest(release, candidate, self.candidate_sha, FIXTURE)

    def test_valid_fixture_and_schema_pass(self):
        self.validator.validate_candidate_spec(copy.deepcopy(self.candidate), CANDIDATE_FIXTURE)
        self.validate_bundle()
        schema = json.loads((ROOT / "release-manifests" / "schema-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["additionalProperties"])
        jsonschema.Draft202012Validator.check_schema(schema)
        schema_validator = jsonschema.Draft202012Validator(
            schema,
            format_checker=jsonschema.FormatChecker(),
        )
        schema_validator.validate(self.candidate)
        schema_validator.validate(self.release)

    def test_ai_eval_config_separates_runtime_and_local_development_model(self):
        candidate = copy.deepcopy(self.candidate)
        self.validator.validate_candidate_spec(candidate, CANDIDATE_FIXTURE)

        schema = json.loads(
            (ROOT / "release-manifests" / "schema-v1.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator(
            schema,
            format_checker=jsonschema.FormatChecker(),
        ).validate(candidate)

        legacy = copy.deepcopy(candidate)
        config = legacy["ai_release_eval_config"]
        legacy["ai_release_eval_config"] = {
            "primary_model": config["runtime_primary_model"],
            "fallback_models": config["runtime_fallback_models"],
            "prompt_sha256": config["prompt_sha256"],
            "fixture_revision": config["fixture_revision"],
            "fixture_sha256": config["fixture_sha256"],
            "rendered_config_sha256": config["rendered_config_sha256"],
            "ollama_endpoint_sha256": config["ollama_endpoint_sha256"],
        }
        with self.assertRaisesRegex(ValueError, "ai_release_eval_config"):
            self.validator.validate_candidate_spec(legacy, CANDIDATE_FIXTURE)

        for field, value in (
            ("development_model", "claude-sonnet-4-6"),
            ("tuning_revision", "mentor-development-tuning-v2"),
            ("tuning_sha256", "0" * 64),
        ):
            invalid = copy.deepcopy(candidate)
            invalid["ai_release_eval_config"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, field
            ):
                self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)
            with self.subTest(schema_field=field), self.assertRaises(
                jsonschema.ValidationError
            ):
                jsonschema.Draft202012Validator(
                    schema,
                    format_checker=jsonschema.FormatChecker(),
                ).validate(invalid)

    def test_frontend_digests_are_distinct_and_selected_on_is_exact(self):
        invalid = copy.deepcopy(self.candidate)
        invalid["frontend"]["mission_on"]["image_digest"] = invalid["frontend"]["mission_off"]["image_digest"]
        with self.assertRaisesRegex(ValueError, "distinct"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)
        invalid = copy.deepcopy(self.candidate)
        invalid["frontend"]["selected_on_digest"] = invalid["frontend"]["rollback"]["prior_digest"]
        with self.assertRaisesRegex(ValueError, "selected_on_digest"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

    def test_prior_lineage_and_dedicated_staging_identity_are_exact(self):
        self.validator.validate_candidate_spec(
            copy.deepcopy(self.candidate), CANDIDATE_FIXTURE
        )
        for field, value in (
            ("ready", True),
            ("release_id", "ms-20981231-forged-prior"),
            ("candidate_spec_sha256", "f" * 64),
            ("image_digest", "sha256:" + "f" * 64),
        ):
            invalid = copy.deepcopy(self.candidate)
            invalid["frontend"]["rollback"]["prior_identity"][field] = value
            with self.subTest(prior_field=field), self.assertRaisesRegex(
                ValueError, "prior_identity"
            ):
                self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

        zero_prior = copy.deepcopy(self.candidate)
        zero_digest = "sha256:" + "0" * 64
        zero_prior["gitops"]["base_web_digest"] = zero_digest
        zero_prior["frontend"]["rollback"]["prior_digest"] = zero_digest
        zero_prior["frontend"]["rollback"]["prior_identity"] = {
            "ready": True,
            "release_id": "ms-20981231-prior-release",
            "candidate_spec_sha256": "f" * 64,
            "image_digest": zero_digest,
        }
        with self.assertRaisesRegex(ValueError, "non-zero prior image"):
            self.validator.validate_candidate_spec(zero_prior, CANDIDATE_FIXTURE)

        schema = json.loads(
            (ROOT / "release-manifests" / "schema-v1.json").read_text(
                encoding="utf-8"
            )
        )
        schema_validator = jsonschema.Draft202012Validator(
            schema,
            format_checker=jsonschema.FormatChecker(),
        )
        with self.assertRaises(jsonschema.ValidationError):
            schema_validator.validate(zero_prior)

        for field, value in (
            ("kubernetes_context", "other-staging"),
            ("namespace", "other-staging"),
            ("web_deployment", "other-web"),
            ("web_container", "other-web"),
            ("web_origin", "https://other.example.test"),
        ):
            invalid = copy.deepcopy(self.candidate)
            invalid["environments"]["staging"][field] = value
            with self.subTest(staging_field=field), self.assertRaisesRegex(
                ValueError, "environments.staging"
            ):
                self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)
            with self.subTest(schema_staging_field=field), self.assertRaises(
                jsonschema.ValidationError
            ):
                schema_validator.validate(invalid)

    def test_all_application_services_and_et11_migration_are_bound(self):
        invalid = copy.deepcopy(self.candidate)
        del invalid["services"]["devpath-lcs-svc"]
        with self.assertRaisesRegex(ValueError, "services"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)
        invalid = copy.deepcopy(self.candidate)
        invalid["services"]["devpath-gateway"]["repository"] = "DevPathAi/other-source"
        with self.assertRaisesRegex(ValueError, "DevPathAi/devpath-gateway"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)
        invalid = copy.deepcopy(self.candidate)
        invalid["shared_migration"]["flyway_target"] = "202608161010"
        with self.assertRaisesRegex(ValueError, "202608221001"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

    def test_candidate_accepts_exact_et11_shared_release_contract(self):
        candidate = copy.deepcopy(self.candidate)
        candidate["shared_migration"].update(
            {
                "source_sha": "c4d468a70e8870e8f60f25539e91599def75f0f2",
                "shared_version": "0.0.1-et11.20260822",
                "shared_jar_sha256": (
                    "eaab3aa3ad891f7dfeafb084e63d89645978d7716eb0c90a0dda42e0c40dac2e"
                ),
                "image_digest": (
                    "sha256:f1ac7dac56643c2adf3dc62ace83628758826d3ebae710a13c788bfeb73e0fc6"
                ),
                "flyway_target": "202608221001",
                "required_migration": (
                    "V202608221001__correct_question_bank_accuracy.sql"
                ),
            }
        )

        self.validator.validate_candidate_spec(candidate, CANDIDATE_FIXTURE)

    def test_evidence_and_ai_release_gate_fail_closed(self):
        invalid = copy.deepcopy(self.release)
        invalid["quality_evidence"]["frontend_visual"]["artifact_id"] = 0
        with self.assertRaises(ValueError):
            self.validate_bundle(release=invalid)
        invalid = copy.deepcopy(self.release)
        invalid["ai_release_eval"]["hard_invariants_percent"] = 99.9
        with self.assertRaisesRegex(ValueError, "hard_invariants"):
            self.validate_bundle(release=invalid)
        invalid = copy.deepcopy(self.release)
        invalid["ai_release_eval"]["usefulness_percent"] = 89.9
        with self.assertRaisesRegex(ValueError, "usefulness"):
            self.validate_bundle(release=invalid)

    def test_every_evidence_record_binds_exact_candidate_spec_hash(self):
        invalid = copy.deepcopy(self.release)
        invalid["journeys"]["activation"]["candidate_spec_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "immutable candidate-spec bytes"):
            self.validate_bundle(release=invalid)
        invalid = copy.deepcopy(self.release)
        invalid["quality_evidence"]["frontend_visual"]["repository"] = "DevPathAi/untrusted"
        with self.assertRaisesRegex(ValueError, "DevPathAi/devpath-frontend"):
            self.validate_bundle(release=invalid)

    def test_validation_attestation_binds_home_run_and_two_distinct_artifacts(self):
        invalid = copy.deepcopy(self.release)
        invalid["validation_attestation"]["home_source_sha"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "exact checked-out Home source"):
            self.validate_bundle(release=invalid)
        invalid = copy.deepcopy(self.release)
        invalid["journeys"]["contextual_practice"]["workflow_run_id"] += 1
        with self.assertRaisesRegex(ValueError, "validation attestation run"):
            self.validate_bundle(release=invalid)
        invalid = copy.deepcopy(self.release)
        invalid["journeys"]["contextual_practice"]["artifact_id"] = invalid["journeys"]["activation"]["artifact_id"]
        with self.assertRaisesRegex(ValueError, "artifact IDs must be distinct"):
            self.validate_bundle(release=invalid)

    def test_final_manifest_claims_must_match_sanitized_approval_and_ai_evidence(self):
        privacy = {
            "candidate_spec_sha256": self.candidate_sha,
            "status": "passed",
            "approved_at": self.release["analytics_privacy_approval"]["approved_at"],
        }
        self.artifacts.validate_sealed_metadata("privacy-approval", privacy, self.release)
        privacy["approved_at"] = "2099-01-03T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "approved_at"):
            self.artifacts.validate_sealed_metadata("privacy-approval", privacy, self.release)
        evaluation = {
            "candidate_spec_sha256": self.candidate_sha,
            "status": "passed",
            "hard_invariants_percent": 100,
            "usefulness_percent": 95,
            "baseline_delta_points": 0,
        }
        self.artifacts.validate_sealed_metadata("ai-release-eval", evaluation, self.release)
        evaluation["usefulness_percent"] = 89
        with self.assertRaisesRegex(ValueError, "usefulness_percent"):
            self.artifacts.validate_sealed_metadata("ai-release-eval", evaluation, self.release)

    def test_successful_validation_envelope_binds_final_raw_manifest_hash(self):
        release_hash = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
        payload = {
            "release_id": self.release["release_id"],
            "candidate_spec_sha256": self.candidate_sha,
            "release_manifest_sha256": release_hash,
            "status": "passed",
            "rollback_seconds": 180,
            "validator_run_id": 104,
            "validator_run_attempt": 1,
            "validator_head_sha": self.release["validation_attestation"]["validator_head_sha"],
            "validator_workflow_sha256": self.release["validation_attestation"]["validator_workflow_sha256"],
        }
        self.artifacts.validate_validation_seal_payload(
            payload,
            self.release["release_id"],
            self.candidate_sha,
            release_hash,
            self.release["validation_attestation"],
        )
        invalid = copy.deepcopy(payload)
        invalid["release_manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "final manifest bytes"):
            self.artifacts.validate_validation_seal_payload(
                invalid,
                self.release["release_id"],
                self.candidate_sha,
                release_hash,
                self.release["validation_attestation"],
            )

    def test_journey_evidence_payload_is_minimal_sanitized_and_candidate_bound(self):
        label = "journey-activation"
        rows = [
            {
                "route": self.artifacts.JOURNEY_ALLOWLISTS[label][step][0],
                "step": step,
                "result": "passed",
                "duration_ms": index + 1,
                "candidate_spec_sha256": self.candidate_sha,
            }
            for index, step in enumerate(self.artifacts.JOURNEY_ALLOWLISTS[label])
        ]
        self.artifacts.validate_evidence_payload(label, rows, self.candidate_sha, self.candidate)
        invalid = copy.deepcopy(rows)
        invalid[0]["raw_output"] = "private"
        with self.assertRaisesRegex(ValueError, "raw_output|key set"):
            self.artifacts.validate_evidence_payload(label, invalid, self.candidate_sha, self.candidate)
        invalid = copy.deepcopy(rows)
        invalid[0]["candidate_spec_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "does not bind"):
            self.artifacts.validate_evidence_payload(label, invalid, self.candidate_sha, self.candidate)
        invalid = copy.deepcopy(rows)
        invalid[0]["duration_ms"] = True
        with self.assertRaisesRegex(ValueError, "duration_ms"):
            self.artifacts.validate_evidence_payload(label, invalid, self.candidate_sha, self.candidate)

    def test_sanitized_manifest_rejects_raw_user_material(self):
        invalid = copy.deepcopy(self.release)
        invalid["journeys"]["activation"]["raw_output"] = "private execution output"
        with self.assertRaisesRegex(ValueError, "raw_output"):
            self.validate_bundle(release=invalid)
        invalid = copy.deepcopy(self.candidate)
        invalid["home"]["repository"] = "DevPathAi/devpath-home-page\nsecret-token"
        with self.assertRaisesRegex(ValueError, "multiline"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

    def test_rollout_order_and_budgets_are_exact(self):
        invalid = copy.deepcopy(self.candidate)
        invalid["rollout"]["production_order"][-1] = "landing-early"
        with self.assertRaisesRegex(ValueError, "production_order"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

    def test_admin_image_source_is_the_exact_frontend_source(self):
        invalid = copy.deepcopy(self.candidate)
        invalid["services"]["devpath-admin"]["source_sha"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "devpath-admin.*frontend source"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

    def test_migration_job_base_is_inert_and_emits_only_the_validated_target_marker(self):
        job = (ROOT / "apps/devpath-migration/base/job.yaml").read_text(encoding="utf-8")
        self.assertIn("metadata:\n  name: devpath-flyway-migrate\n", job)
        self.assertNotIn("argocd.argoproj.io/sync-options", job)
        self.assertNotIn("Force=true", job)
        self.assertRegex(job, r"spec:\n(?:  #.*\n)+  suspend: true\n  backoffLimit: 3\n")
        validate = (
            'flyway -locations="$migration_locations" '
            '-target="$TARGET_FLYWAY_VERSION" validate'
        )
        marker = (
            "printf 'mission-spine-flyway-target=%s status=validated\\n' "
            '"$TARGET_FLYWAY_VERSION"'
        )
        self.assertIn(marker, job)
        self.assertIn(validate, job)
        self.assertLess(job.index(validate), job.index(marker))
        # validate 가 target 을 잃으면 승인 범위 밖의 마이그레이션(이미지에는 있으나 아직
        # 적용하지 않기로 한 것)을 "적용 안 됨" 오류로 잡아 set -e 아래에서 Job 을 죽인다.
        # 실측: `Detected resolved migration not applied to database` → exit 1.
        self.assertNotIn(
            'flyway -locations="$migration_locations" validate',
            job,
            "validate 는 migrate 와 같은 target 을 받아야 한다",
        )

    def test_journey_harness_uses_canonical_production_origins_and_exact_dns_overrides(self):
        invalid = copy.deepcopy(self.candidate)
        invalid["journey_harness"]["app_origin"] = "https://different.example.test"
        with self.assertRaisesRegex(ValueError, "canonical production app origin"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)
        invalid = copy.deepcopy(self.candidate)
        del invalid["journey_harness"]["api_origin"]
        with self.assertRaisesRegex(ValueError, "missing fields: api_origin"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)
        invalid = copy.deepcopy(self.candidate)
        invalid["journey_harness"]["api_origin"] = "https://api.attacker.test"
        with self.assertRaisesRegex(ValueError, "canonical production API origin"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)
        invalid = copy.deepcopy(self.candidate)
        invalid["journey_harness"]["api_origin"] = "https://api.example.test:8443"
        with self.assertRaisesRegex(ValueError, "canonical production API origin"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)
        invalid = copy.deepcopy(self.candidate)
        invalid["journey_harness"]["api_origin"] = invalid["journey_harness"]["app_origin"]
        with self.assertRaisesRegex(ValueError, "canonical production API origin|origins must be distinct"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)
        invalid = copy.deepcopy(self.candidate)
        invalid["journey_harness"]["dns_overrides"][0]["hostname"] = "unbound.example.test"
        with self.assertRaisesRegex(ValueError, "cover exactly"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

    def test_canonical_fixture_is_bound_by_out_of_band_sha256(self):
        expected_path = CANDIDATE_FIXTURE.with_suffix(".sha256")
        expected = expected_path.read_text(encoding="ascii").strip().split()[0]
        actual = hashlib.sha256(CANDIDATE_FIXTURE.read_bytes()).hexdigest()
        self.assertEqual(actual, expected)
        invalid = copy.deepcopy(self.candidate)
        invalid["rollout"]["canary_seconds"] = 899
        with self.assertRaisesRegex(ValueError, "canary_seconds"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

    def test_release_id_resolution_rejects_traversal_and_filename_mismatch(self):
        with self.assertRaises(ValueError):
            self.validator.resolve_manifest(ROOT, "../escape")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidates = root / "release-manifests" / "candidates"
            releases = root / "release-manifests" / "releases"
            candidates.mkdir(parents=True)
            releases.mkdir(parents=True)
            candidate_data = copy.deepcopy(self.candidate)
            candidate_data["release_id"] = "ms-20990101-other"
            mobile = candidate_data["quality_evidence_inputs"]["mobile_test_artifacts"]
            mobile["artifact_name"] = (
                f'{candidate_data["release_id"]}-signed-android-build-run-'
                f'{mobile["workflow_run_id"]}-attempt-{mobile["run_attempt"]}'
            )
            candidate_path = candidates / "ms-20990101-contract-fixture.candidate-spec.json"
            candidate_path.write_text(json.dumps(candidate_data), encoding="utf-8")
            release_data = copy.deepcopy(self.release)
            release_data["candidate_spec"]["path"] = candidate_path.relative_to(root).as_posix()
            release_data["candidate_spec"]["sha256"] = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
            (releases / "ms-20990101-contract-fixture.json").write_text(json.dumps(release_data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "release_id"):
                self.validator.resolve_manifest(root, "ms-20990101-contract-fixture")

    def test_promoter_uses_manifest_digest_and_only_exact_web_kustomization(self):
        source = """apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
- deployment.yaml
configMapGenerator:
- name: devpath-web-release-identity
  literals:
  - MISSION_RELEASE_READY=false
  - MISSION_RELEASE_ID=unreleased
  - MISSION_CANDIDATE_SPEC_SHA256=0000000000000000000000000000000000000000000000000000000000000000
  - MISSION_IMAGE_DIGEST=sha256:0000000000000000000000000000000000000000000000000000000000000000
images:
- name: ghcr.io/devpathai/devpath-web
  newName: ghcr.io/devpathai/devpath-web
  newTag: 5c5f3a90f8d3da2523bb1dd13c057655f7b82897-mission-on
"""
        rendered = self.promoter.render_kustomization(
            source,
            self.candidate,
            "mission-on",
            candidate_spec_sha256=self.candidate_sha,
        )
        self.assertIn("  digest: sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", rendered)
        self.assertNotIn("newTag:", rendered)
        self.assertEqual(rendered.count("digest:"), 1)
        for literal in (
            "MISSION_RELEASE_READY=true",
            f"MISSION_RELEASE_ID={self.candidate['release_id']}",
            f"MISSION_CANDIDATE_SPEC_SHA256={self.candidate_sha}",
            "MISSION_IMAGE_DIGEST=sha256:" + "b" * 64,
        ):
            self.assertEqual(rendered.count(literal), 1)
        with self.assertRaisesRegex(ValueError, "current digest"):
            self.promoter.render_kustomization(
                rendered,
                self.candidate,
                "mission-off",
                candidate_spec_sha256=self.candidate_sha,
            )
        with self.assertRaisesRegex(ValueError, "images section"):
            self.promoter.render_kustomization(
                source.replace("images:\n", "notImages:\n", 1),
                self.candidate,
                "mission-on",
                candidate_spec_sha256=self.candidate_sha,
            )

    def test_production_kustomize_wires_exact_release_identity_and_probe_secret(self):
        binary = shutil.which("kubectl")
        if binary is None:
            self.skipTest("kubectl is unavailable")
        rendered = subprocess.run(
            [binary, "kustomize", str(ROOT / "apps/devpath-web/base")],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        documents = list(yaml.safe_load_all(rendered))
        config_map = next(item for item in documents if item["kind"] == "ConfigMap")
        deployment = next(item for item in documents if item["kind"] == "Deployment")
        self.assertEqual(
            config_map["data"],
            {
                "MISSION_RELEASE_READY": "false",
                "MISSION_RELEASE_ID": "unreleased",
                "MISSION_CANDIDATE_SPEC_SHA256": "0" * 64,
                "MISSION_IMAGE_DIGEST": "sha256:" + "0" * 64,
            },
        )
        containers = deployment["spec"]["template"]["spec"]["containers"]
        self.assertEqual(len(containers), 1)
        container = containers[0]
        self.assertEqual(
            container["envFrom"],
            [{"configMapRef": {"name": config_map["metadata"]["name"]}}],
        )
        self.assertEqual(
            container["env"],
            [
                {
                    "name": "MISSION_SYNTHETIC_PROBE_TOKEN",
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": "mission-spine-synthetic-probe",
                            "key": "token",
                            "optional": True,
                        }
                    },
                }
            ],
        )

    def test_candidate_authentication_binds_the_exact_base_web_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "apps", root / "apps")
            candidate_dir = root / "release-manifests" / "candidates"
            candidate_dir.mkdir(parents=True)
            candidate_path = candidate_dir / f"{self.candidate['release_id']}.candidate-spec.json"
            candidate_path.write_text(json.dumps(self.candidate), encoding="utf-8")
            self.candidate_web_base.verify_candidate_web_base(
                root, self.candidate["release_id"]
            )

            forged = copy.deepcopy(self.candidate)
            forged["frontend"]["rollback"]["prior_identity"] = {
                "ready": True,
                "release_id": "ms-20981231-forged-prior",
                "candidate_spec_sha256": "f" * 64,
                "image_digest": forged["frontend"]["rollback"]["prior_digest"],
            }
            (candidate_dir / f"{forged['release_id']}.candidate-spec.json").write_text(
                json.dumps(forged), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "base release identity is not exact"):
                self.candidate_web_base.verify_candidate_web_base(
                    root, forged["release_id"]
                )

    def test_kustomize_accepts_and_renders_exact_digest_syntax(self):
        if shutil.which("kubectl") is None:
            self.skipTest("kubectl is unavailable")
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "base"
            shutil.copytree(ROOT / "apps" / "devpath-web" / "base", base)
            kustomization = base / "kustomization.yaml"
            rendered = self.promoter.render_kustomization(
                kustomization.read_text(encoding="utf-8"),
                self.candidate,
                "mission-on",
                candidate_spec_sha256=self.candidate_sha,
            )
            kustomization.write_text(rendered, encoding="utf-8", newline="\n")
            result = subprocess.run(
                ["kubectl", "kustomize", str(base)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(
                "image: ghcr.io/devpathai/devpath-web@sha256:" + "b" * 64,
                result.stdout,
            )

    def test_home_journey_outputs_are_validated_and_copied_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidate_dir = root / "release-manifests" / "candidates"
            candidate_dir.mkdir(parents=True)
            (candidate_dir / f"{self.candidate['release_id']}.candidate-spec.json").write_bytes(
                CANDIDATE_FIXTURE.read_bytes()
            )
            evidence_dir = root / "raw-evidence"
            rows = {}
            for directory, label in (
                ("mission-spine-onboarding", "journey-activation"),
                ("mission-spine-workspace", "journey-contextual-practice"),
            ):
                rows[directory] = [
                    {
                        "route": self.artifacts.JOURNEY_ALLOWLISTS[label][step][0],
                        "step": step,
                        "result": "passed",
                        "duration_ms": index + 1,
                        "candidate_spec_sha256": self.candidate_sha,
                    }
                    for index, step in enumerate(self.artifacts.JOURNEY_ALLOWLISTS[label])
                ]
            raw_by_directory = {}
            for directory, payload in rows.items():
                path = evidence_dir / directory / "evidence.json"
                path.parent.mkdir(parents=True)
                raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
                path.write_bytes(raw)
                raw_by_directory[directory] = raw
            output_dir = root / "artifacts"
            outputs = self.preparer.prepare(
                root,
                self.candidate["release_id"],
                evidence_dir,
                output_dir,
            )
            self.assertEqual(
                Path(outputs["activation_path"]).read_bytes(),
                raw_by_directory["mission-spine-onboarding"],
            )
            self.assertEqual(
                Path(outputs["contextual_path"]).read_bytes(),
                raw_by_directory["mission-spine-workspace"],
            )
            self.assertNotEqual(outputs["activation_sha256"], outputs["contextual_sha256"])

    def test_sealer_builds_the_single_final_attestation_from_candidate_and_run(self):
        external = {
            "home_dist_artifact": copy.deepcopy(self.release["home_dist_artifact"]),
            "privacy": {
                "approved_at": self.release["analytics_privacy_approval"]["approved_at"],
                "reference": copy.deepcopy(self.release["analytics_privacy_approval"]["evidence"]),
            },
            "ai": {
                "hard_invariants_percent": 100,
                "usefulness_percent": 95,
                "baseline_delta_points": 0,
                "reference": copy.deepcopy(self.release["ai_release_eval"]["evidence"]),
            },
            "quality_evidence": copy.deepcopy(self.release["quality_evidence"]),
        }
        sealed = self.sealer.build_release_manifest(
            copy.deepcopy(self.candidate),
            "tests/release/fixtures/valid-candidate-spec.json",
            self.candidate_sha,
            external,
            "DevPathAi/devpath-gitops",
            104,
            1,
            self.release["validation_attestation"]["validator_head_sha"],
            self.release["validation_attestation"]["validator_workflow_sha256"],
            204,
            "6" * 64,
            205,
            "7" * 64,
            207,
            self.release["quality_evidence"]["home_visual"]["sha256"],
            self.release["quality_evidence"]["home_axe_browser_a11y"]["sha256"],
            "2099-01-02T00:00:00Z",
        )
        self.validator.validate_release_manifest(sealed, self.candidate, self.candidate_sha)
        self.assertEqual(sealed["validation_attestation"], self.release["validation_attestation"])

    def test_workflows_accept_release_id_only_and_encode_release_order(self):
        expected = {
            "mission-spine-validate.yml": ["mission-spine-staging", "reverse", "600"],
            "mission-spine-promote.yml": [
                "mission-spine-production-off",
                "mission-spine-production-on",
                "--phase migration",
                "promote_service_digests.py",
                "--phase services",
                "--phase mission-off",
                "--canary-seconds 900",
            ],
            "mission-spine-rollback.yml": ["landing-prior", "frontend-mission-off", "frontend-prior"],
            "mission-spine-landing-last.yml": [
                "mission-spine-production-landing",
                "--action preflight",
                "--action verify-new-production",
            ],
        }
        for filename, needles in expected.items():
            text = (ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8")
            inputs = text.split("inputs:", 1)[1].split("permissions:", 1)[0]
            self.assertIn("release_id:", inputs)
            self.assertNotIn("digest:", inputs)
            self.assertNotIn("image:", inputs)
            self.assertTrue(
                "base_sha" in text or "verify_main_release_context.py" in text
            )
            self.assertTrue(
                "prevent_self_review == true" in text
                or "verify_current_protected_approval.py" in text
            )
            self.assertTrue(
                "git diff --name-only" in text
                or "git -C gitops-main diff --name-only" in text
                or "verify_main_release_context.py" in text
            )
            for line in text.splitlines():
                if "${{ inputs.release_id }}" in line:
                    self.assertTrue(
                        line.strip().startswith(("RELEASE_ID:", "name:", "ref:")),
                        f"untrusted workflow input was interpolated into shell text: {line}",
                    )
            for needle in needles:
                self.assertIn(needle, text)

        validate = (ROOT / ".github" / "workflows" / "mission-spine-validate.yml").read_text(encoding="utf-8")
        ordered = [
            "--candidate-id",
            "ref: ${{ steps.candidate_manifest.outputs.home_source_sha }}",
            "npm run test:release",
            "prepare_journey_evidence.py",
            "-journey-activation",
            "-journey-contextual-practice",
            "seal_release_manifest.py",
            "Validate sealed final bundle",
            "stage exact OFF and ON digests",
            "Rehearse reverse rollback within 600 seconds",
            "-sealed-validation",
        ]
        positions = [validate.index(needle) for needle in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(
            'test "$(git -C candidate-data status --porcelain=v1 --untracked-files=all)" = "?? $release_path"',
            validate,
        )
        self.assertEqual(validate.count("npm run test:release\n"), 1)

        production_workflows = [
            "mission-spine-promote.yml",
            "mission-spine-landing-last.yml",
        ]
        for filename in production_workflows:
            text = (ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8")
            self.assertIn("group: mission-spine-production", text)
            self.assertNotIn("--force", text)
        rollback = (ROOT / ".github" / "workflows" / "mission-spine-rollback.yml").read_text(encoding="utf-8")
        self.assertIn("group: mission-spine-production", rollback)
        self.assertIn("cancel-in-progress: false", rollback)
        self.assertIn("verify_release_artifacts.py", rollback)
        self.assertNotIn("--force", rollback)

    def test_python_cli_validates_fixture(self):
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), "--manifest", str(FIXTURE)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
