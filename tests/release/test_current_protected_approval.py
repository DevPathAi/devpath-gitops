import copy
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "verify_current_protected_approval",
    ROOT / "scripts" / "release" / "verify_current_protected_approval.py",
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class CurrentProtectedApprovalTest(unittest.TestCase):
    def setUp(self):
        self.head = "a" * 40
        self.environment = {
            "id": 501,
            "name": "mission-spine-production-off",
            "can_admins_bypass": False,
            "deployment_branch_policy": {
                "protected_branches": False,
                "custom_branch_policies": True,
            },
            "protection_rules": [
                {
                    "type": "required_reviewers",
                    "prevent_self_review": True,
                    "reviewers": [
                        {
                            "type": "User",
                            "reviewer": {"id": 92, "login": "release-reviewer"},
                        }
                    ],
                }
            ],
        }
        self.approvals = [
            {
                "state": "approved",
                "user": {"id": 92, "login": "release-reviewer"},
                "environments": [
                    {"id": 501, "name": "mission-spine-production-off"}
                ],
            }
        ]
        self.jobs = [
            {
                "name": "Promote migration, services, and mission-OFF",
                "run_id": 7001,
                "run_attempt": 1,
                "head_sha": self.head,
                "status": "in_progress",
                "conclusion": None,
                "started_at": "2026-08-17T01:00:01Z",
            }
        ]
        self.run = {
            "id": 7001,
            "run_attempt": 1,
            "event": "workflow_dispatch",
            "path": ".github/workflows/mission-spine-promote.yml",
            "head_branch": "main",
            "head_sha": self.head,
            "repository": {"full_name": module.REPOSITORY},
            "head_repository": {"full_name": module.REPOSITORY},
            "status": "in_progress",
            "conclusion": None,
            "actor": {"id": 1, "login": "release-initiator"},
            "triggering_actor": {"id": 1, "login": "release-initiator"},
            "run_started_at": "2026-08-17T01:00:00Z",
        }

    def validate(self, **changes):
        values = {
            "environment_name": "mission-spine-production-off",
            "job_name": "Promote migration, services, and mission-OFF",
            "workflow_path": ".github/workflows/mission-spine-promote.yml",
            "expected_head": self.head,
            "expected_branch": "main",
            "run_id": 7001,
            "run_attempt": 1,
            "environment": copy.deepcopy(self.environment),
            "approvals": copy.deepcopy(self.approvals),
            "jobs": copy.deepcopy(self.jobs),
            "run": copy.deepcopy(self.run),
            "branch_policies": [{"id": 77, "name": "main", "type": "branch"}],
        }
        values.update(changes)
        return module.validate_current_protected_approval(**values)

    def test_accepts_exact_non_self_attempt_one_approval(self):
        result = self.validate()
        self.assertEqual(result["approved_by"], "release-reviewer")
        self.assertEqual(result["approval_effective_at"], "2026-08-17T01:00:01Z")

    def test_rejects_missing_self_or_wrong_environment_approval(self):
        with self.assertRaisesRegex(ValueError, "at least one approved"):
            self.validate(approvals=[])
        self.run["actor"] = {"id": 92, "login": "release-reviewer"}
        with self.assertRaisesRegex(ValueError, "differ"):
            self.validate()
        self.approvals[0]["environments"][0]["name"] = "wrong"
        with self.assertRaisesRegex(ValueError, "at least one approved"):
            self.validate()

    def test_accepts_repeated_same_reviewer_approvals_but_rejects_mixed_identity(self):
        approvals = copy.deepcopy(self.approvals)
        approvals.append(copy.deepcopy(approvals[0]))
        result = self.validate(approvals=approvals)
        self.assertEqual(result["approved_by"], "release-reviewer")
        approvals[1]["user"] = {"id": 93, "login": "other-reviewer"}
        with self.assertRaisesRegex(ValueError, "share one identity"):
            self.validate(approvals=approvals)

    def test_rejects_wrong_job_head_attempt_and_bypass_shape(self):
        for field, value in (
            ("name", "wrong"),
            ("head_sha", "b" * 40),
            ("run_attempt", 2),
            ("status", "completed"),
        ):
            bad = copy.deepcopy(self.jobs)
            bad[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(jobs=bad)
        with self.assertRaisesRegex(ValueError, "run_attempt"):
            self.validate(run_attempt=2)
        with self.assertRaisesRegex(ValueError, "execute from main"):
            self.validate(expected_branch="release/candidate-r1")
        run = copy.deepcopy(self.run)
        run["head_branch"] = "release/candidate-r1"
        with self.assertRaisesRegex(ValueError, "coordinate mismatch"):
            self.validate(run=run)
        environment = copy.deepcopy(self.environment)
        environment["protection_rules"][0]["prevent_self_review"] = False
        with self.assertRaisesRegex(ValueError, "prevent self-review"):
            self.validate(environment=environment)
        environment = copy.deepcopy(self.environment)
        environment["can_admins_bypass"] = True
        with self.assertRaisesRegex(ValueError, "administrator bypass"):
            self.validate(environment=environment)

    def test_team_reviewer_requires_active_membership_proof(self):
        environment = copy.deepcopy(self.environment)
        environment["protection_rules"][0]["reviewers"] = [
            {"type": "Team", "reviewer": {"id": 99, "slug": "release-team"}}
        ]
        with self.assertRaisesRegex(ValueError, "active configured team"):
            self.validate(environment=environment)
        result = self.validate(environment=environment, approved_team_ids={99})
        self.assertEqual(result["approved_by_id"], 92)

    def test_rejects_missing_wildcard_or_multiple_environment_branch_policies(self):
        for policies in (
            [],
            [{"name": "release/candidate-*", "type": "branch"}],
            [{"name": "main", "type": "tag"}],
            [
                {"name": "main", "type": "branch"},
                {"name": "release/candidate-*", "type": "branch"},
            ],
        ):
            with self.subTest(policies=policies), self.assertRaises(ValueError):
                self.validate(branch_policies=policies)
        environment = copy.deepcopy(self.environment)
        environment["deployment_branch_policy"]["custom_branch_policies"] = False
        with self.assertRaisesRegex(ValueError, "custom main"):
            self.validate(environment=environment)


if __name__ == "__main__":
    unittest.main()
