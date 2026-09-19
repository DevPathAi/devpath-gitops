import copy
import hashlib
import importlib.util
import json
import jsonschema
from pathlib import Path
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


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ManualNvdaTrustTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_module(VALIDATOR, "manual_nvda_release_validator")
        cls.verifier = load_module(VERIFIER, "manual_nvda_artifact_verifier")
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
        return {
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
            "assistive_technology": "NVDA+Chromium",
            "test_provenance_sha256": catalog["provenance_sha256"],
            **self._approval(label),
        }

    def _manual_catalog_bundle(self, label, mutate_catalog=None, mutate_provenance=None):
        contract = self.verifier.MANUAL_CATALOG_CONTRACTS[label]
        entry_points = {
            case_id: (
                "today" if case_id.endswith("today-mission-spine") else "next_action"
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

    def test_protected_attempt_two_is_rejected(self):
        payload = self._manual_payload("manual-nvda")
        payload["producer_run_attempt"] = 2
        with self.assertRaisesRegex(ValueError, "attempt must be 1"):
            self.verifier.validate_evidence_payload(
                "manual-nvda", payload, self.candidate_sha, self.candidate, 109, 2
            )

    def test_schema_rejects_protected_attempt_two(self):
        schema = json.loads(
            (ROOT / "release-manifests" / "schema-v1.json").read_text(encoding="utf-8")
        )
        validator = jsonschema.Draft202012Validator(schema)
        release = self._release_bound_to_current_candidate()
        release["quality_evidence"]["manual_nvda"]["run_attempt"] = 2
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate(release)

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
                    "manual-nvda",
                    catalog_mutation,
                    provenance_mutation,
                )
                with self.assertRaisesRegex(ValueError, message):
                    self.verifier.validate_manual_catalog_bundle(
                        "manual-nvda", catalog_raw, provenance_raw, candidate
                    )

    def test_dispatch_workflow_inputs_are_exact(self):
        single = b"""name: single\non:\n  workflow_dispatch:\n    inputs:\n      release_id:\n        required: true\n        type: string\njobs: {}\n"""
        manual = b"""name: manual\non:\n  workflow_dispatch:\n    inputs:\n      release_id:\n        required: true\n        type: string\n      candidate_run_id:\n        required: true\n        type: string\n      candidate_run_attempt:\n        required: true\n        type: string\n      candidate_artifact_id:\n        required: true\n        type: string\n      candidate_spec_sha256:\n        required: true\n        type: string\njobs: {}\n"""
        self.verifier.validate_workflow_dispatch_inputs(
            single, {"release_id"}, "single-input"
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
                single.replace(
                    b"        type: string\n",
                    b"        type: string\n"
                    b"      untrusted_result:\n"
                    b"        required: true\n"
                    b"        type: string\n",
                ),
                {"release_id"},
                "single-input",
            )
        for name, invalid, message in (
            (
                "optional",
                single.replace(b"required: true", b"required: false"),
                "must be required",
            ),
            (
                "default",
                single.replace(
                    b"        required: true\n",
                    b"        required: true\n        default: spoofed\n",
                ),
                "forbidden properties",
            ),
            (
                "wrong-type",
                single.replace(b"type: string", b"type: boolean"),
                "type must be string",
            ),
            (
                "nested-dispatch",
                single.replace(
                    b"on:\n  workflow_dispatch:",
                    b"on:\n  schedule:\n    workflow_dispatch:",
                ),
                "only canonical workflow_dispatch",
            ),
            (
                "sibling-push",
                single.replace(
                    b"on:\n  workflow_dispatch:",
                    b"on:\n  push:\n  workflow_dispatch:",
                ),
                "only canonical workflow_dispatch",
            ),
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, message):
                    self.verifier.validate_workflow_dispatch_inputs(
                        invalid, {"release_id"}, "single-input"
                    )

    def test_manual_names_are_run_attempt_scoped(self):
        release_id = self.candidate["release_id"]
        release = self._release_bound_to_current_candidate()
        artifact = release["quality_evidence"]["manual_nvda"]
        self.assertEqual(
            artifact["artifact_name"],
            self.validator.quality_artifact_name(
                "manual-nvda",
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

        retry = self._release_bound_to_current_candidate()
        artifact = retry["quality_evidence"]["manual_nvda"]
        artifact["run_attempt"] = 2
        artifact["artifact_name"] = self.validator.quality_artifact_name(
            "manual-nvda", retry["release_id"], 2, artifact["workflow_run_id"]
        )
        with self.assertRaisesRegex(ValueError, "attempt 1"):
            self.validator.validate_release_manifest(
                retry,
                copy.deepcopy(self.candidate),
                self.candidate_sha,
                RELEASE_FIXTURE,
            )

    def test_manual_evidence_requires_exact_protected_approval_claim(self):
        for label in ("manual-nvda",):
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
        nvda = invalid["quality_evidence_inputs"]["catalogs"]["manual-nvda"]
        nvda["provenance_sha256"] = nvda["sha256"]
        with self.assertRaisesRegex(ValueError, "hashes must be distinct"):
            self.validator.validate_candidate_spec(invalid, CANDIDATE_FIXTURE)

    def test_unique_protected_run_ignores_attempt_two_and_rejects_competing_fresh_run(self):
        source_sha = self.candidate["frontend"]["source_sha"]
        workflow = self.validator.PRODUCER_WORKFLOWS["manual-nvda"]

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
                "manual",
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
                    "manual",
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

        actions_bot = {
            "id": 41898282,
            "login": "github-actions[bot]",
            "type": "Bot",
        }
        automation_run = copy.deepcopy(run)
        automation_run["actor"] = copy.deepcopy(actions_bot)
        automation_run["triggering_actor"] = copy.deepcopy(actions_bot)
        self.verifier.validate_protected_approval(
            label,
            claim,
            environment,
            approvals,
            jobs,
            automation_run,
            self.candidate["frontend"]["source_sha"],
        )

        for field, value in (
            ("id", 1),
            ("login", "github-actions-bot"),
            ("type", "User"),
        ):
            lookalike_run = copy.deepcopy(automation_run)
            lookalike_run["actor"][field] = value
            with self.subTest(actions_bot_lookalike=field), self.assertRaisesRegex(
                ValueError, "exact GitHub Actions automation"
            ):
                self.verifier.validate_protected_approval(
                    label,
                    claim,
                    environment,
                    approvals,
                    jobs,
                    lookalike_run,
                    self.candidate["frontend"]["source_sha"],
                )

        mismatched_initiator_run = copy.deepcopy(run)
        mismatched_initiator_run["triggering_actor"] = {
            "id": 4002,
            "login": "other-initiator",
        }
        with self.assertRaisesRegex(ValueError, "same identity"):
            self.verifier.validate_protected_approval(
                label,
                claim,
                environment,
                approvals,
                jobs,
                mismatched_initiator_run,
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


if __name__ == "__main__":
    unittest.main()
