import copy
import hashlib
import importlib.util
import json
import jsonschema
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "release"
if str(SCRIPTS) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPTS))

CANDIDATE_FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-candidate-spec.json"
RELEASE_FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-release.json"
VALIDATOR = SCRIPTS / "validate_release_manifest.py"
VERIFIER = SCRIPTS / "verify_release_artifacts.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SignedMobileManualTrustTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_module(VALIDATOR, "signed_mobile_release_validator")
        cls.verifier = load_module(VERIFIER, "signed_mobile_artifact_verifier")
        cls.candidate = json.loads(CANDIDATE_FIXTURE.read_text(encoding="utf-8"))
        cls.release = json.loads(RELEASE_FIXTURE.read_text(encoding="utf-8"))
        cls.candidate_sha = hashlib.sha256(CANDIDATE_FIXTURE.read_bytes()).hexdigest()

    def _approval(self, label: str) -> dict:
        environment, job_name = self.verifier.PROTECTED_APPROVAL_CONTRACTS[label]
        return {
            "approval_environment": environment,
            "approval_environment_id": 3001,
            "approval_job_name": job_name,
            "approved_by": "release-reviewer",
            "approved_by_id": 4001,
            "approval_effective_at": "2099-01-01T00:00:00Z",
        }

    def _manual_payload(self, label: str) -> dict:
        catalog = self.candidate["quality_evidence_inputs"]["catalogs"][label]
        payload = {
            "candidate_spec_sha256": self.candidate_sha,
            "status": "passed",
            "producer_run_id": 109,
            "producer_run_attempt": 1,
            "repository": "DevPathAi/devpath-frontend",
            "source_sha": self.candidate["frontend"]["source_sha"],
            "case_catalog_sha256": catalog["sha256"],
            "case_count": catalog["case_count"],
            "passed_case_count": catalog["case_count"],
            "failed_case_count": 0,
            "assistive_technology": {
                "manual-nvda": "NVDA+Chromium",
                "manual-talkback": "TalkBack+Android",
            }[label],
            "test_provenance_sha256": catalog["provenance_sha256"],
            **self._approval(label),
        }
        mobile = self.candidate["quality_evidence_inputs"]["mobile_test_artifacts"]
        if label == "manual-talkback":
            payload["build_provenance_sha256"] = mobile["build_provenance_sha256"]
            payload["signed_apk_sha256"] = mobile["signed_apk_sha256"]
        return payload

    def _build_provenance(self) -> dict:
        mobile = self.candidate["quality_evidence_inputs"]["mobile_test_artifacts"]
        return {
            "schema_version": "leva.mission-spine.signed-android-build.v2",
            "release_id": self.candidate["release_id"],
            "repository": mobile["repository"],
            "source_sha": mobile["source_sha"],
            "event": mobile["event"],
            "workflow_path": mobile["workflow_path"],
            "workflow_sha256": mobile["workflow_sha256"],
            "producer_run_id": mobile["workflow_run_id"],
            "producer_run_attempt": mobile["run_attempt"],
            "pubspec_lock_sha256": "9" * 64,
            "toolchain": {
                "flutter_version": "3.44.1",
                "flutter_revision": "924134a44c189315be2148659913dda1671cbe99",
                "dart_sdk_version": "3.12.1",
                "android": {
                    "java_runtime": (
                        "OpenJDK Runtime Environment Temurin-17.0.20+8 "
                        "(build 17.0.20+8)"
                    ),
                    "compile_sdk": 36,
                },
            },
            "build_configuration": {
                "use_mock": False,
                "api_base_url": "https://api.leva.ai.kr",
                "web_app_url": "https://app.leva.ai.kr",
            },
            "android": {
                "artifact_path": mobile["signed_apk_file"],
                "sha256": mobile["signed_apk_sha256"],
                "bytes": 1024,
                "application_id": "ai.devpath.devpath_mobile",
                "version_name": "1.0.0",
                "version_code": 1,
                "signature_verified": True,
                "signing_classification": "org_keystore_release_test_distribution",
                "play_app_signing": False,
                "signing_certificate_sha256": "a" * 64,
            },
            "approvals": {
                "android": self._approval("signed-mobile-android"),
            },
        }

    def _manual_catalog_bundle(self, label, mutate_catalog=None, mutate_provenance=None):
        contract = self.verifier.MANUAL_CATALOG_CONTRACTS[label]
        entry_points = {
            case_id: (
                "today"
                if case_id.endswith("today-mission-spine")
                else "next_action"
                if case_id.endswith("next-action-navigation")
                else "content"
                if case_id.endswith("content-reading")
                else "offline_status"
            )
            for case_id in contract["case_ids"]
        }
        catalog = {
            "schema_version": "leva.mission-spine.manual-at-catalog.v1",
            "lane": label,
            "assistive_technology": contract["assistive_technology"],
            "test_provenance_path": contract["provenance_path"],
            "case_count": contract["case_count"],
            "cases": [
                {
                    "id": case_id,
                    "surface": contract["surface"],
                    "entry_point": entry_points[case_id],
                    "procedure": ["Run the authoritative physical-client procedure."],
                    "expected": ["The mission state is announced correctly."],
                }
                for case_id in contract["case_ids"]
            ],
        }
        if mutate_catalog is not None:
            mutate_catalog(catalog)
        catalog_raw = json.dumps(catalog, separators=(",", ":")).encode("utf-8")
        provenance = {
            "schema_version": "leva.mission-spine.manual-at-test-provenance.v1",
            "lane": label,
            "catalog_path": contract["path"],
            "catalog_sha256": hashlib.sha256(catalog_raw).hexdigest(),
            "assistive_technology": contract["assistive_technology"],
            "execution_mode": "manual_human",
            "required_platform": contract["required_platform"],
            "required_client": contract["required_client"],
            "required_artifact": contract["required_artifact"],
            "case_ids": list(contract["case_ids"]),
            "pass_policy": {
                "all_cases_required": True,
                "failed_case_count": 0,
                "synthetic_results_allowed": False,
                "emulator_allowed": False,
            },
        }
        if mutate_provenance is not None:
            mutate_provenance(provenance)
        provenance_raw = json.dumps(provenance, separators=(",", ":")).encode("utf-8")
        candidate = copy.deepcopy(self.candidate)
        binding = candidate["quality_evidence_inputs"]["catalogs"][label]
        binding["sha256"] = hashlib.sha256(catalog_raw).hexdigest()
        binding["provenance_sha256"] = hashlib.sha256(provenance_raw).hexdigest()
        return catalog_raw, provenance_raw, candidate

    def _release_bound_to_current_candidate(self) -> dict:
        release = copy.deepcopy(self.release)
        release["candidate_spec"]["sha256"] = self.candidate_sha

        def replace(value):
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key == "candidate_spec_sha256":
                        value[key] = self.candidate_sha
                    else:
                        replace(nested)
            elif isinstance(value, list):
                for nested in value:
                    replace(nested)

        replace(release)
        return release

    def test_candidate_exactly_binds_one_signed_android_artifact(self):
        mobile = self.candidate["quality_evidence_inputs"]["mobile_test_artifacts"]
        self.assertEqual(set(mobile), self.validator.SIGNED_MOBILE_BINDING_KEYS)
        self.validator.validate_candidate_spec(copy.deepcopy(self.candidate), CANDIDATE_FIXTURE)

        mutations = (
            ("repository", "Other/repo"),
            ("source_sha", "0" * 40),
            ("event", "push"),
            ("workflow_path", ".github/workflows/ci.yml"),
            ("artifact_name", "signed-android-build"),
            ("build_provenance_file", "provenance.json"),
            ("signed_apk_file", "leva-release.apk"),
            ("signed_ipa_file", "leva-release.ipa"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                invalid = copy.deepcopy(self.candidate)
                invalid["quality_evidence_inputs"]["mobile_test_artifacts"][field] = value
                with self.assertRaises(ValueError):
                    self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

    def test_signed_mobile_hashes_and_ids_fail_closed(self):
        for field in ("workflow_run_id", "run_attempt", "artifact_id"):
            with self.subTest(field=field):
                invalid = copy.deepcopy(self.candidate)
                invalid["quality_evidence_inputs"]["mobile_test_artifacts"][field] = 0
                with self.assertRaisesRegex(ValueError, field):
                    self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

        invalid = copy.deepcopy(self.candidate)
        mobile = invalid["quality_evidence_inputs"]["mobile_test_artifacts"]
        mobile["build_provenance_sha256"] = mobile["signed_apk_sha256"]
        with self.assertRaisesRegex(ValueError, "distinct"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

    def test_protected_attempt_two_is_rejected_but_fresh_attempt_one_is_valid(self):
        invalid = copy.deepcopy(self.candidate)
        mobile = invalid["quality_evidence_inputs"]["mobile_test_artifacts"]
        mobile["run_attempt"] = 2
        mobile["artifact_name"] = (
            f'{invalid["release_id"]}-signed-android-build-run-'
            f'{mobile["workflow_run_id"]}-attempt-2'
        )
        with self.assertRaisesRegex(ValueError, "attempt 1"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

        fresh = copy.deepcopy(self.candidate)
        mobile = fresh["quality_evidence_inputs"]["mobile_test_artifacts"]
        mobile["workflow_run_id"] += 1
        mobile["artifact_id"] += 1
        mobile["artifact_name"] = (
            f'{fresh["release_id"]}-signed-android-build-run-'
            f'{mobile["workflow_run_id"]}-attempt-1'
        )
        self.validator.validate_candidate_spec(fresh, CANDIDATE_FIXTURE)

        payload = self._manual_payload("manual-nvda")
        payload["producer_run_attempt"] = 2
        with self.assertRaisesRegex(ValueError, "attempt must be 1"):
            self.verifier.validate_evidence_payload(
                "manual-nvda", payload, self.candidate_sha, self.candidate, 109, 2
            )

        provenance = self._build_provenance()
        provenance["producer_run_attempt"] = 2
        with self.assertRaisesRegex(ValueError, "producer_run_attempt"):
            self.verifier.validate_signed_mobile_provenance(provenance, self.candidate)

    def test_schema_rejects_protected_attempt_two(self):
        schema = json.loads(
            (ROOT / "release-manifests" / "schema-v1.json").read_text(encoding="utf-8")
        )
        validator = jsonschema.Draft202012Validator(schema)
        candidate = copy.deepcopy(self.candidate)
        candidate["quality_evidence_inputs"]["mobile_test_artifacts"]["run_attempt"] = 2
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate(candidate)

        release = self._release_bound_to_current_candidate()
        release["quality_evidence"]["manual_nvda"]["run_attempt"] = 2
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate(release)

    def test_build_provenance_exactly_binds_toolchain_config_signatures_and_approvals(self):
        provenance = self._build_provenance()
        self.verifier.validate_signed_mobile_provenance(
            copy.deepcopy(provenance), self.candidate
        )
        mutations = (
            (("toolchain", "flutter_revision"), "0" * 40),
            (("toolchain", "android", "compile_sdk"), 35),
            (("build_configuration", "use_mock"), True),
            (("build_configuration", "api_base_url"), "https://mock.devpath.ai"),
            (("android", "signature_verified"), False),
            (("approvals", "android", "approval_environment"), "unprotected"),
        )
        for path, value in mutations:
            with self.subTest(path=path):
                invalid = copy.deepcopy(provenance)
                cursor = invalid
                for key in path[:-1]:
                    cursor = cursor[key]
                cursor[path[-1]] = value
                with self.assertRaises(ValueError):
                    self.verifier.validate_signed_mobile_provenance(invalid, self.candidate)

    def test_signed_bundle_rejects_missing_extra_link_and_hash_drift(self):
        provenance = self._build_provenance()
        raw = json.dumps(provenance, separators=(",", ":")).encode("utf-8")
        mobile = self.candidate["quality_evidence_inputs"]["mobile_test_artifacts"]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "mobile" / "android").mkdir(parents=True)
            (root / mobile["build_provenance_file"]).write_bytes(raw)
            (root / mobile["signed_apk_file"]).write_bytes(b"apk")
            bound = copy.deepcopy(self.candidate)
            binding = bound["quality_evidence_inputs"]["mobile_test_artifacts"]
            binding["build_provenance_sha256"] = hashlib.sha256(raw).hexdigest()
            binding["signed_apk_sha256"] = hashlib.sha256(b"apk").hexdigest()
            provenance["android"]["sha256"] = binding["signed_apk_sha256"]
            provenance["android"]["bytes"] = len(b"apk")
            raw = json.dumps(provenance, separators=(",", ":")).encode("utf-8")
            (root / mobile["build_provenance_file"]).write_bytes(raw)
            binding["build_provenance_sha256"] = hashlib.sha256(raw).hexdigest()
            self.verifier.validate_signed_mobile_bundle(root, bound)

            legacy_ios = root / "mobile" / "ios"
            legacy_ios.mkdir()
            (legacy_ios / "leva-release.ipa").write_bytes(b"legacy ipa")
            with self.assertRaisesRegex(ValueError, "file set"):
                self.verifier.validate_signed_mobile_bundle(root, bound)
            (legacy_ios / "leva-release.ipa").unlink()
            legacy_ios.rmdir()

            (root / "extra.txt").write_text("unexpected", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "file set"):
                self.verifier.validate_signed_mobile_bundle(root, bound)

    def test_signed_zip_extraction_rejects_traversal_links_and_extras(self):
        mobile = self.candidate["quality_evidence_inputs"]["mobile_test_artifacts"]
        expected = {
            mobile["build_provenance_file"]: b"{}",
            mobile["signed_apk_file"]: b"apk",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def archive(name, mutation=None):
                path = root / name
                with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as output:
                    for filename, raw in expected.items():
                        output.writestr(filename, raw)
                    if mutation is not None:
                        mutation(output)
                return path

            valid = archive("valid.zip")
            self.verifier._extract_signed_mobile_archive(
                valid, root / "valid-out", self.candidate
            )

            unsafe = (
                (
                    "traversal.zip",
                    lambda output: output.writestr("../outside.txt", b"bad"),
                    "unsafe",
                ),
                (
                    "extra.zip",
                    lambda output: output.writestr("extra.txt", b"bad"),
                    "unexpected",
                ),
                (
                    "legacy-ipa.zip",
                    lambda output: output.writestr(
                        "mobile/ios/leva-release.ipa", b"legacy ipa"
                    ),
                    "unexpected",
                ),
                (
                    "extra-directory.zip",
                    lambda output: output.writestr("unexpected/", b""),
                    "unexpected directory",
                ),
                (
                    "link.zip",
                    lambda output: self._write_zip_link(output),
                    "links",
                ),
            )
            for name, mutation, message in unsafe:
                with self.subTest(name=name):
                    path = archive(name, mutation)
                    with self.assertRaisesRegex(ValueError, message):
                        self.verifier._extract_signed_mobile_archive(
                            path, root / f"{name}-out", self.candidate
                        )

    @staticmethod
    def _write_zip_link(output):
        info = zipfile.ZipInfo("linked.apk")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        output.writestr(info, "mobile/android/leva-release.apk")

    def test_manual_catalogs_and_static_provenance_are_exact(self):
        for label in self.verifier.MANUAL_CATALOG_CONTRACTS:
            with self.subTest(label=label):
                catalog_raw, provenance_raw, candidate = self._manual_catalog_bundle(label)
                self.verifier.validate_manual_catalog_bundle(
                    label, catalog_raw, provenance_raw, candidate
                )

        mutations = (
            (
                "catalog-extra-key",
                lambda catalog: catalog.update({"source_sha": "0" * 40}),
                None,
                "invalid key set",
            ),
            (
                "catalog-order",
                lambda catalog: catalog["cases"].reverse(),
                None,
                "order/ID",
            ),
            (
                "entry-point",
                lambda catalog: catalog["cases"][0].update({"entry_point": "content"}),
                None,
                "entry_point",
            ),
            (
                "empty-procedure",
                lambda catalog: catalog["cases"][0].update({"procedure": []}),
                None,
                "procedure",
            ),
            (
                "provenance-order",
                None,
                lambda provenance: provenance["case_ids"].reverse(),
                "contract mismatch",
            ),
            (
                "synthetic-results",
                None,
                lambda provenance: provenance["pass_policy"].update(
                    {"synthetic_results_allowed": True}
                ),
                "contract mismatch",
            ),
            (
                "catalog-cross-link",
                None,
                lambda provenance: provenance.update({"catalog_sha256": "0" * 64}),
                "contract mismatch",
            ),
        )
        for name, catalog_mutation, provenance_mutation, message in mutations:
            with self.subTest(name=name):
                catalog_raw, provenance_raw, candidate = self._manual_catalog_bundle(
                    "manual-talkback",
                    catalog_mutation,
                    provenance_mutation,
                )
                with self.assertRaisesRegex(ValueError, message):
                    self.verifier.validate_manual_catalog_bundle(
                        "manual-talkback", catalog_raw, provenance_raw, candidate
                    )

    def test_dispatch_workflow_inputs_are_exact(self):
        signed = b"""name: signed\non:\n  workflow_dispatch:\n    inputs:\n      release_id:\n        required: true\n        type: string\njobs: {}\n"""
        manual = b"""name: manual\non:\n  workflow_dispatch:\n    inputs:\n      release_id:\n        required: true\n        type: string\n      candidate_run_id:\n        required: true\n        type: string\n      candidate_run_attempt:\n        required: true\n        type: string\n      candidate_artifact_id:\n        required: true\n        type: string\n      candidate_spec_sha256:\n        required: true\n        type: string\njobs: {}\n"""
        self.verifier.validate_workflow_dispatch_inputs(
            signed, {"release_id"}, "signed-mobile"
        )
        self.verifier.validate_workflow_dispatch_inputs(
            manual,
            {
                "release_id",
                "candidate_run_id",
                "candidate_run_attempt",
                "candidate_artifact_id",
                "candidate_spec_sha256",
            },
            "manual",
        )
        with self.assertRaisesRegex(ValueError, "exactly"):
            self.verifier.validate_workflow_dispatch_inputs(
                signed.replace(
                    b"        type: string\n",
                    b"        type: string\n"
                    b"      untrusted_result:\n"
                    b"        required: true\n"
                    b"        type: string\n",
                ),
                {"release_id"},
                "signed-mobile",
            )
        for name, invalid, message in (
            (
                "optional",
                signed.replace(b"required: true", b"required: false"),
                "must be required",
            ),
            (
                "default",
                signed.replace(
                    b"        required: true\n",
                    b"        required: true\n        default: spoofed\n",
                ),
                "forbidden properties",
            ),
            (
                "wrong-type",
                signed.replace(b"type: string", b"type: boolean"),
                "type must be string",
            ),
            (
                "nested-dispatch",
                signed.replace(
                    b"on:\n  workflow_dispatch:",
                    b"on:\n  schedule:\n    workflow_dispatch:",
                ),
                "only canonical workflow_dispatch",
            ),
            (
                "sibling-push",
                signed.replace(
                    b"on:\n  workflow_dispatch:",
                    b"on:\n  push:\n  workflow_dispatch:",
                ),
                "only canonical workflow_dispatch",
            ),
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, message):
                    self.verifier.validate_workflow_dispatch_inputs(
                        invalid, {"release_id"}, "signed-mobile"
                    )

    def test_manual_names_are_run_attempt_scoped_and_atomic(self):
        release_id = self.candidate["release_id"]
        release = self._release_bound_to_current_candidate()
        quality = release["quality_evidence"]
        for label, key in self.validator.QUALITY_EVIDENCE.items():
            if not label.startswith("manual-"):
                continue
            artifact = quality[key]
            self.assertEqual(
                artifact["artifact_name"],
                self.validator.quality_artifact_name(
                    label,
                    release_id,
                    artifact["run_attempt"],
                    artifact["workflow_run_id"],
                ),
            )
        self.validator.validate_release_manifest(
            release,
            copy.deepcopy(self.candidate),
            self.candidate_sha,
            RELEASE_FIXTURE,
        )

        for field in ("head_sha", "run_attempt", "workflow_sha256", "workflow_run_id"):
            with self.subTest(field=field):
                invalid = self._release_bound_to_current_candidate()
                current = invalid["quality_evidence"]["manual_talkback"][field]
                invalid["quality_evidence"]["manual_talkback"][field] = (
                    current + 1 if isinstance(current, int) else "0" * len(current)
                )
                with self.assertRaises(ValueError):
                    self.validator.validate_release_manifest(
                        invalid,
                        copy.deepcopy(self.candidate),
                        self.candidate_sha,
                        RELEASE_FIXTURE,
                    )

        retry = self._release_bound_to_current_candidate()
        for label, key in self.validator.QUALITY_EVIDENCE.items():
            if label not in self.validator.MANUAL_CATALOG_CONTRACTS:
                continue
            artifact = retry["quality_evidence"][key]
            artifact["run_attempt"] = 2
            artifact["artifact_name"] = self.validator.quality_artifact_name(
                label,
                retry["release_id"],
                2,
                artifact["workflow_run_id"],
            )
        with self.assertRaisesRegex(ValueError, "attempt 1"):
            self.validator.validate_release_manifest(
                retry,
                copy.deepcopy(self.candidate),
                self.candidate_sha,
                RELEASE_FIXTURE,
            )

        collision = self._release_bound_to_current_candidate()
        collision["quality_evidence"]["manual_nvda"]["artifact_id"] = self.candidate[
            "quality_evidence_inputs"
        ]["mobile_test_artifacts"]["artifact_id"]
        with self.assertRaisesRegex(ValueError, "signed-mobile artifact ID"):
            self.validator.validate_release_manifest(
                collision,
                copy.deepcopy(self.candidate),
                self.candidate_sha,
                RELEASE_FIXTURE,
            )

    def test_manual_evidence_requires_exact_protected_approval_claim(self):
        for label in ("manual-nvda", "manual-talkback"):
            with self.subTest(label=label):
                payload = self._manual_payload(label)
                self.verifier.validate_evidence_payload(
                    label, payload, self.candidate_sha, self.candidate, 109, 1
                )
                invalid = copy.deepcopy(payload)
                invalid["approval_job_name"] = "Unprotected approval"
                with self.assertRaisesRegex(ValueError, "approval_job_name"):
                    self.verifier.validate_evidence_payload(
                        label, invalid, self.candidate_sha, self.candidate, 109, 1
                    )

    def test_manual_catalog_and_provenance_hashes_cannot_collide(self):
        invalid = copy.deepcopy(self.candidate)
        catalogs = invalid["quality_evidence_inputs"]["catalogs"]
        catalogs["manual-talkback"]["provenance_sha256"] = catalogs["manual-nvda"][
            "sha256"
        ]
        with self.assertRaisesRegex(ValueError, "must all be distinct"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

    def test_unique_protected_run_ignores_attempt_two_and_rejects_competing_fresh_run(self):
        source_sha = self.candidate["frontend"]["source_sha"]
        workflow = self.verifier.SIGNED_MOBILE_WORKFLOW

        def run(run_id, attempt):
            return {
                "id": run_id,
                "status": "completed",
                "conclusion": "success",
                "event": "workflow_dispatch",
                "head_sha": source_sha,
                "head_branch": "main",
                "path": workflow,
                "run_attempt": attempt,
            }

        def artifacts(_env, _repository, name):
            run_id = int(name.split("-run-", 1)[1].split("-", 1)[0])
            return [
                {
                    "name": name,
                    "expired": False,
                    "workflow_run": {"id": run_id},
                }
            ]

        with mock.patch.object(
            self.verifier,
            "_list_protected_runs",
            return_value=[run(110, 2), run(111, 1)],
        ), mock.patch.object(
            self.verifier,
            "_list_named_artifacts",
            side_effect=artifacts,
        ):
            self.verifier.assert_unique_protected_producer_run(
                {},
                "DevPathAi/devpath-frontend",
                source_sha,
                workflow,
                self.candidate["release_id"],
                "signed-mobile",
                111,
            )

        with mock.patch.object(
            self.verifier,
            "_list_protected_runs",
            return_value=[run(111, 1), run(112, 1)],
        ), mock.patch.object(
            self.verifier,
            "_list_named_artifacts",
            side_effect=artifacts,
        ):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                self.verifier.assert_unique_protected_producer_run(
                    {},
                    "DevPathAi/devpath-frontend",
                    source_sha,
                    workflow,
                    self.candidate["release_id"],
                    "signed-mobile",
                    111,
                )

    def test_live_approval_accepts_authenticated_self_review_and_requires_successful_job(self):
        label = "manual-nvda"
        claim = self._approval(label)
        environment = {
            "id": claim["approval_environment_id"],
            "name": claim["approval_environment"],
            "can_admins_bypass": False,
            "protection_rules": [
                {
                    "type": "required_reviewers",
                    "prevent_self_review": True,
                    "reviewers": [
                        {
                            "type": "User",
                            "reviewer": {
                                "id": claim["approved_by_id"],
                                "login": claim["approved_by"],
                            },
                        }
                    ],
                }
            ],
        }
        approvals = [
            {
                "state": "approved",
                "user": {
                    "id": claim["approved_by_id"],
                    "login": claim["approved_by"],
                },
                "environments": [
                    {
                        "id": claim["approval_environment_id"],
                        "name": claim["approval_environment"],
                    }
                ],
            }
        ]
        run = {
            "run_attempt": 1,
            "head_branch": "main",
            "head_sha": self.candidate["frontend"]["source_sha"],
            "repository": {"full_name": "DevPathAi/devpath-frontend"},
            "actor": {
                "id": claim["approved_by_id"],
                "login": claim["approved_by"],
            },
            "triggering_actor": {
                "id": claim["approved_by_id"],
                "login": claim["approved_by"],
            },
            "run_started_at": "2098-12-31T23:59:59Z",
            "updated_at": "2099-01-01T00:01:00Z",
        }
        jobs = [
            {
                "name": claim["approval_job_name"],
                "status": "completed",
                "conclusion": "success",
                "head_sha": self.candidate["frontend"]["source_sha"],
                "started_at": claim["approval_effective_at"],
            }
        ]
        self.verifier.validate_protected_approval(
            label,
            claim,
            environment,
            approvals,
            jobs,
            run,
            self.candidate["frontend"]["source_sha"],
        )

        for mutation, message in (
            (("environment", "prevent_self_review", False), "prevent self-review"),
            (("environment-root", "can_admins_bypass", True), "identity"),
            (("run", "actor", None), "initiator identity"),
            (("run", "run_attempt", 2), "attempt must be 1"),
            (("run", "head_branch", "feature/retry"), "head_branch"),
            (("job", "conclusion", "failure"), "successful"),
            (("approval", "state", "rejected"), "approved"),
        ):
            with self.subTest(mutation=mutation):
                bad_environment = copy.deepcopy(environment)
                bad_approvals = copy.deepcopy(approvals)
                bad_jobs = copy.deepcopy(jobs)
                bad_run = copy.deepcopy(run)
                target, field, value = mutation
                if target == "environment":
                    bad_environment["protection_rules"][0][field] = value
                elif target == "environment-root":
                    bad_environment[field] = value
                elif target == "run":
                    bad_run[field] = value
                elif target == "job":
                    bad_jobs[0][field] = value
                else:
                    bad_approvals[0][field] = value
                with self.assertRaisesRegex(ValueError, message):
                    self.verifier.validate_protected_approval(
                        label,
                        claim,
                        bad_environment,
                        bad_approvals,
                        bad_jobs,
                        bad_run,
                        self.candidate["frontend"]["source_sha"],
                    )

        duplicate_approvals = copy.deepcopy(approvals)
        duplicate = copy.deepcopy(approvals[0])
        duplicate["user"] = {"id": 5001, "login": "second-reviewer"}
        duplicate_approvals.append(duplicate)
        with self.assertRaisesRegex(ValueError, "exactly one approved review"):
            self.verifier.validate_protected_approval(
                label,
                claim,
                environment,
                duplicate_approvals,
                jobs,
                run,
                self.candidate["frontend"]["source_sha"],
            )

        team_environment = copy.deepcopy(environment)
        team_environment["protection_rules"][0]["reviewers"] = [
            {
                "type": "Team",
                "reviewer": {"id": 6001, "slug": "release-reviewers"},
            }
        ]
        self.verifier.validate_protected_approval(
            label,
            claim,
            team_environment,
            approvals,
            jobs,
            run,
            self.candidate["frontend"]["source_sha"],
            approved_team_ids={6001},
        )

    def test_signed_android_must_complete_before_manual_approval(self):
        provenance = self._build_provenance()
        claim = self._approval("manual-talkback")
        signed_run = {"updated_at": "2098-12-31T23:00:00Z"}
        manual_run = {"run_started_at": "2098-12-31T23:30:00Z"}
        self.verifier.validate_manual_chronology(
            "manual-talkback", provenance, signed_run, manual_run, claim
        )

        with self.assertRaisesRegex(ValueError, "before signed-mobile completion"):
            self.verifier.validate_manual_chronology(
                "manual-talkback",
                provenance,
                {"updated_at": "2098-12-31T23:31:00Z"},
                manual_run,
                claim,
            )


if __name__ == "__main__":
    unittest.main()
