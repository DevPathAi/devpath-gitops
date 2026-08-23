import copy
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "approve_pending_deployment",
    ROOT / "scripts" / "release" / "approve_pending_deployment.py",
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class FakeApi:
    def __init__(self, *, fail_approval=False, fail_restore=False):
        self.fail_approval = fail_approval
        self.fail_restore = fail_restore
        self.calls = []
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
                            "reviewer": {
                                "id": 92,
                                "login": "release-operator",
                            },
                        }
                    ],
                },
                {"type": "branch_policy"},
            ],
        }

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        if path == "user":
            return {"id": 92, "login": "release-operator"}
        if path.endswith("/deployment-branch-policies"):
            return {"branch_policies": [{"id": 77, "name": "main", "type": "branch"}]}
        if path.endswith("/pending_deployments") and method == "GET":
            return [
                {
                    "environment": {
                        "id": 501,
                        "name": "mission-spine-production-off",
                    }
                }
            ]
        if path.endswith("/pending_deployments") and method == "POST":
            if self.fail_approval:
                raise ValueError("approval failed")
            return [{"environment": {"id": 501, "name": "mission-spine-production-off"}}]
        if "/environments/" in path and method == "GET":
            return copy.deepcopy(self.environment)
        if "/environments/" in path and method == "PUT":
            if payload["prevent_self_review"] is True and self.fail_restore:
                raise ValueError("restore failed")
            self.environment["can_admins_bypass"] = payload["can_admins_bypass"]
            self.environment["deployment_branch_policy"] = copy.deepcopy(
                payload["deployment_branch_policy"]
            )
            reviewer_rule = next(
                rule
                for rule in self.environment["protection_rules"]
                if rule["type"] == "required_reviewers"
            )
            reviewer_rule["prevent_self_review"] = payload["prevent_self_review"]
            reviewer_rule["reviewers"] = [
                {
                    "type": entry["type"],
                    "reviewer": {
                        "id": entry["id"],
                        "login": "release-operator",
                    },
                }
                for entry in payload["reviewers"]
            ]
            return copy.deepcopy(self.environment)
        raise AssertionError(f"unexpected API call: {method} {path}")


class ApprovePendingDeploymentTest(unittest.TestCase):
    def approve(self, api):
        return module.approve_pending_deployment(
            repository="DevPathAi/devpath-gitops",
            run_id=7001,
            environment_name="mission-spine-production-off",
            comment="AI-operated release approval",
            api=api,
        )

    def test_temporarily_allows_self_review_approves_and_restores_exact_policy(self):
        api = FakeApi()
        api.environment["protection_rules"].reverse()
        result = self.approve(api)
        self.assertEqual(result["approved_by"], "release-operator")
        reviewer_rule = next(
            rule
            for rule in api.environment["protection_rules"]
            if rule["type"] == "required_reviewers"
        )
        self.assertTrue(reviewer_rule["prevent_self_review"])
        puts = [call for call in api.calls if call[0] == "PUT"]
        self.assertEqual(
            [call[2]["prevent_self_review"] for call in puts], [False, True]
        )
        posts = [call for call in api.calls if call[0] == "POST"]
        self.assertEqual(posts[0][2]["environment_ids"], [501])

    def test_restores_policy_when_approval_fails(self):
        api = FakeApi(fail_approval=True)
        with self.assertRaisesRegex(ValueError, "approval failed"):
            self.approve(api)
        self.assertTrue(api.environment["protection_rules"][0]["prevent_self_review"])

    def test_fails_closed_when_restore_fails(self):
        api = FakeApi(fail_restore=True)
        with self.assertRaisesRegex(ValueError, "restore failed"):
            self.approve(api)

    def test_rejects_unconfigured_operator_and_non_main_policy_before_mutation(self):
        api = FakeApi()
        api.environment["protection_rules"][0]["reviewers"][0]["reviewer"]["id"] = 93
        with self.assertRaisesRegex(ValueError, "authenticated operator"):
            self.approve(api)
        self.assertFalse(any(call[0] == "PUT" for call in api.calls))

        api = FakeApi()
        original = api.__call__

        def wrong_policy(method, path, payload=None):
            if path.endswith("/deployment-branch-policies"):
                return {"branch_policies": [{"id": 77, "name": "release/*", "type": "branch"}]}
            return original(method, path, payload)

        with self.assertRaisesRegex(ValueError, "exact main"):
            module.approve_pending_deployment(
                repository="DevPathAi/devpath-gitops",
                run_id=7001,
                environment_name="mission-spine-production-off",
                comment="AI-operated release approval",
                api=wrong_policy,
            )
        self.assertFalse(any(call[0] == "PUT" for call in api.calls))


if __name__ == "__main__":
    unittest.main()
