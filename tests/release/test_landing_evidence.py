import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "release"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "tested_landing_evidence", SCRIPTS / "verify_landing_evidence.py"
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


HEAD = "a" * 40
SERVICES = "b" * 40
MIGRATION = "c" * 40


def eligible_run(run_id=101):
    return {
        "id": run_id,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
        "event": "workflow_dispatch",
        "path": module.WORKFLOW_PATH,
        "head_sha": HEAD,
        "head_branch": "main",
        "repository": {"full_name": module.REPOSITORY},
        "head_repository": {"full_name": module.REPOSITORY},
    }


def landing_payload():
    values = {
        "schema_version": 1,
        "document_type": "mission-spine-landing-last",
        "release_id": "ms-20990101-landing-test",
        "candidate_spec_sha256": "d" * 64,
        "release_manifest_sha256": "e" * 64,
        "status": "passed",
        "landing_order": "last",
        "deployment_id": "11111111-1111-1111-1111-111111111111",
        "canary_run_id": 501,
        "canary_run_attempt": 1,
        "on_commit": HEAD,
        "services_commit": SERVICES,
        "migration_commit": MIGRATION,
        "producer_repository": module.REPOSITORY,
        "producer_workflow_path": module.WORKFLOW_PATH,
        "producer_workflow_sha256": "f" * 64,
        "producer_head_sha": HEAD,
        "producer_run_id": 101,
        "producer_run_attempt": 1,
        "approval_environment": module.ENVIRONMENT,
        "approval_environment_id": 201,
        "approval_job_name": module.JOB_NAME,
        "approved_by": "independent-reviewer",
        "approved_by_id": 301,
        "approval_effective_at": "2099-01-01T00:00:00Z",
    }
    return {key: values[key] for key in module.TOP_KEYS}


class LandingEvidenceTest(unittest.TestCase):
    def test_listed_current_and_attempt_one_runs_have_one_exact_coordinate(self):
        listed = eligible_run()
        module.validate_landing_run_pair(
            listed, copy.deepcopy(listed), copy.deepcopy(listed), HEAD
        )
        mutations = (
            ("listed id", "listed", "id", 102),
            ("current id", "current", "id", 102),
            ("attempt id", "attempt", "id", 102),
            ("current attempt", "current", "run_attempt", 2),
            ("attempt head", "attempt", "head_sha", "0" * 40),
            ("current path", "current", "path", module.WORKFLOW_PATH + "@main"),
            ("attempt repository", "attempt", "repository", {"full_name": "fork/repo"}),
        )
        for label, target, key, value in mutations:
            runs = {
                "listed": copy.deepcopy(listed),
                "current": copy.deepcopy(listed),
                "attempt": copy.deepcopy(listed),
            }
            runs[target][key] = value
            with self.subTest(label=label), self.assertRaisesRegex(
                ValueError, "run coordinate"
            ):
                module.validate_landing_run_pair(
                    runs["listed"], runs["current"], runs["attempt"], HEAD
                )

    def test_referenced_canary_binds_exact_run_attempt_and_promoted_chain(self):
        evidence = landing_payload()
        canary = {
            "run_id": evidence["canary_run_id"],
            "run_attempt": evidence["canary_run_attempt"],
            "on_commit": evidence["on_commit"],
            "services_commit": evidence["services_commit"],
            "migration_commit": evidence["migration_commit"],
        }
        module.validate_landing_canary_binding(evidence, canary)
        for key, value in (
            ("run_id", 999),
            ("run_attempt", 2),
            ("on_commit", "0" * 40),
            ("services_commit", "0" * 40),
            ("migration_commit", "0" * 40),
        ):
            invalid = copy.deepcopy(canary)
            invalid[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(
                ValueError, "live canary"
            ):
                module.validate_landing_canary_binding(evidence, invalid)

    def test_payload_mutations_fail_closed_on_deployment_run_and_chain_identity(self):
        payload = landing_payload()
        raw = (json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
        chain = {
            "phase": "mission-on",
            "on_commit": HEAD,
            "services_commit": SERVICES,
            "migration_commit": MIGRATION,
        }
        with mock.patch.object(module, "inspect_chain", return_value=chain) as inspect:
            module.validate_landing_payload(
                payload,
                raw,
                ROOT,
                payload["release_id"],
                {},
                payload["candidate_spec_sha256"],
                payload["release_manifest_sha256"],
                eligible_run(),
                payload["producer_workflow_sha256"],
            )
        inspect.assert_called_once_with(
            ROOT,
            {},
            payload["candidate_spec_sha256"],
            payload["release_manifest_sha256"],
            payload["on_commit"],
        )
        for key, value in (
            ("deployment_id", "not-a-deployment"),
            ("canary_run_id", False),
            ("canary_run_attempt", 2),
            ("producer_run_id", 102),
            ("producer_run_attempt", 2),
            ("producer_head_sha", "0" * 40),
            ("on_commit", "0" * 40),
            ("services_commit", "0" * 40),
            ("migration_commit", "0" * 40),
        ):
            invalid = copy.deepcopy(payload)
            invalid[key] = value
            invalid_raw = (
                json.dumps(invalid, separators=(",", ":"), ensure_ascii=True) + "\n"
            ).encode()
            with self.subTest(key=key), mock.patch.object(
                module, "inspect_chain", return_value=chain
            ), self.assertRaises(ValueError):
                module.validate_landing_payload(
                    invalid,
                    invalid_raw,
                    ROOT,
                    payload["release_id"],
                    {},
                    payload["candidate_spec_sha256"],
                    payload["release_manifest_sha256"],
                    eligible_run(),
                    payload["producer_workflow_sha256"],
                )

    def test_rollback_authenticates_landing_and_deployment_before_first_cf_mutation(self):
        workflow = (ROOT / ".github/workflows/mission-spine-rollback.yml").read_text(
            encoding="utf-8"
        )
        auth = workflow.index(
            "Authenticate Landing evidence and its referenced canary before rollback"
        )
        mutation = workflow.index("--action rollback-prior --deployment-id")
        self.assertLess(auth, mutation)
        between = workflow[auth:mutation]
        self.assertIn("verify_landing_evidence.py", between)
        self.assertIn('--github-output "$GITHUB_OUTPUT"', between)
        self.assertIn("LANDING_DEPLOYMENT_ID: ${{ steps.landing.outputs.deployment_id }}", between)
        self.assertIn('test -n "$LANDING_DEPLOYMENT_ID"', workflow[auth:mutation + 200])


if __name__ == "__main__":
    unittest.main()
