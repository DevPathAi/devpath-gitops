import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "verify_gitops_write_authority.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitops_write_authority", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GitOpsWriteAuthorityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.authority = load_module()

    def setUp(self):
        self.app_id = 123456
        self.installation_id = 789012
        self.app = {"id": self.app_id, "slug": "devpath-gitops-release"}
        self.repositories = {
            "total_count": 1,
            "repositories": [
                {"id": 42, "full_name": "DevPathAi/devpath-gitops", "archived": False}
            ],
        }
        self.classic = {
            "required_status_checks": None,
            "restrictions": {"users": [], "teams": [], "apps": [self.app]},
            "required_pull_request_reviews": {
                "dismiss_stale_reviews": True,
                "require_code_owner_reviews": False,
                "required_approving_review_count": 1,
                "require_last_push_approval": True,
                "bypass_pull_request_allowances": {
                    "users": [],
                    "teams": [],
                    "apps": [self.app],
                },
            },
            "enforce_admins": {"enabled": True},
            "required_linear_history": {"enabled": True},
            "required_conversation_resolution": {"enabled": True},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
        }
        common = {
            "target": "branch",
            "source_type": "Repository",
            "source": "DevPathAi/devpath-gitops",
            "enforcement": "active",
            "conditions": {
                "ref_name": {"include": ["refs/heads/main"], "exclude": []}
            },
        }
        self.integrity = {
            "id": 101,
            "name": "mission-spine-main-integrity",
            **common,
            "bypass_actors": [],
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {"type": "required_linear_history"},
            ],
        }
        self.governance = {
            "id": 102,
            "name": "mission-spine-main-governance",
            **common,
            "bypass_actors": [
                {
                    "actor_id": self.app_id,
                    "actor_type": "Integration",
                    "bypass_mode": "always",
                }
            ],
            "rules": [
                {
                    "type": "update",
                    "parameters": {"update_allows_fetch_and_merge": False},
                }
            ],
        }
        self.rulesets = [
            {key: self.integrity[key] for key in ("id", "name", "source_type", "source", "enforcement")},
            {key: self.governance[key] for key in ("id", "name", "source_type", "source", "enforcement")},
        ]
        self.details = {101: self.integrity, 102: self.governance}

    def validate(self, **overrides):
        return self.authority.validate_authority_state(
            app_slug=overrides.get("app_slug", "devpath-gitops-release"),
            installation_id=overrides.get("installation_id", self.installation_id),
            repositories=overrides.get("repositories", self.repositories),
            classic_protection_status=overrides.get("classic_protection_status", 200),
            classic_protection=overrides.get("classic_protection", self.classic),
            rulesets=overrides.get("rulesets", self.rulesets),
            rule_details=overrides.get("rule_details", self.details),
            expected_app_id=self.app_id,
        )

    def test_exact_app_classic_and_two_rulesets_pass(self):
        result = self.validate()
        self.assertEqual(result["ruleset_ids"], [101, 102])

    def test_summary_may_omit_target_but_detail_target_is_mandatory(self):
        self.validate()
        details = copy.deepcopy(self.details)
        del details[101]["target"]
        with self.assertRaisesRegex(ValueError, "target is not exact"):
            self.validate(rule_details=details)

    def test_hidden_or_extra_bypass_actor_fails_closed(self):
        details = copy.deepcopy(self.details)
        del details[102]["bypass_actors"]
        with self.assertRaisesRegex(ValueError, "bypass_actors.*visible"):
            self.validate(rule_details=details)
        details = copy.deepcopy(self.details)
        details[102]["bypass_actors"].append(
            {"actor_id": 77, "actor_type": "Team", "bypass_mode": "always"}
        )
        with self.assertRaisesRegex(ValueError, "sole GitHub App"):
            self.validate(rule_details=details)

    def test_governance_is_only_exact_non_merge_update_rule(self):
        for mutation in (
            lambda rule: rule["parameters"].__setitem__("update_allows_fetch_and_merge", True),
            lambda rule: rule.__setitem__("type", "pull_request"),
            lambda rule: rule.__setitem__("extra", False),
        ):
            details = copy.deepcopy(self.details)
            mutation(details[102]["rules"][0])
            with self.assertRaises(ValueError):
                self.validate(rule_details=details)

    def test_classic_status_restrictions_reviews_and_destructive_flags_are_exact(self):
        with self.assertRaisesRegex(ValueError, "must be present"):
            self.validate(classic_protection_status=404, classic_protection={})
        mutations = (
            lambda value: value.__setitem__("required_status_checks", {}),
            lambda value: value.pop("required_status_checks"),
            lambda value: value["restrictions"].__setitem__("users", [{"login": "admin"}]),
            lambda value: value["required_pull_request_reviews"].__setitem__("require_last_push_approval", False),
            lambda value: value["required_pull_request_reviews"].__setitem__("required_approving_review_count", True),
            lambda value: value["required_pull_request_reviews"]["bypass_pull_request_allowances"].__setitem__("apps", []),
            lambda value: value["enforce_admins"].__setitem__("enabled", False),
            lambda value: value["required_linear_history"].__setitem__("enabled", False),
            lambda value: value["required_conversation_resolution"].__setitem__("enabled", False),
            lambda value: value["allow_force_pushes"].__setitem__("enabled", True),
            lambda value: value["allow_deletions"].__setitem__("enabled", True),
        )
        for mutation in mutations:
            classic = copy.deepcopy(self.classic)
            mutation(classic)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.validate(classic_protection=classic)

    def test_disabled_dismissal_restrictions_normalizes_documented_api_shapes(self):
        self.validate()
        classic = copy.deepcopy(self.classic)
        classic["required_pull_request_reviews"]["dismissal_restrictions"] = None
        self.validate(classic_protection=classic)
        actors = {"users": [], "teams": [], "apps": []}
        classic["required_pull_request_reviews"]["dismissal_restrictions"] = actors
        self.validate(classic_protection=classic)
        partial = {
            **actors,
            "url": self.authority.DISMISSAL_URL,
        }
        classic["required_pull_request_reviews"]["dismissal_restrictions"] = partial
        self.validate(classic_protection=classic)
        exact = {
            "url": self.authority.DISMISSAL_URL,
            "users_url": self.authority.DISMISSAL_URL + "/users",
            "teams_url": self.authority.DISMISSAL_URL + "/teams",
            "users": [],
            "teams": [],
            "apps": [],
        }
        classic["required_pull_request_reviews"]["dismissal_restrictions"] = exact
        self.validate(classic_protection=classic)
        for mutation in (
            lambda value: value["apps"].append(self.app),
            lambda value: value.pop("teams"),
            lambda value: value.__setitem__("url", "https://api.github.com/evil"),
            lambda value: value.__setitem__("extra", []),
        ):
            changed = copy.deepcopy(classic)
            mutation(changed["required_pull_request_reviews"]["dismissal_restrictions"])
            with self.subTest(mutation=mutation), self.assertRaisesRegex(
                ValueError, "dismissal restrictions"
            ):
                self.validate(classic_protection=changed)

    def test_installation_token_inventory_must_be_gitops_only(self):
        repositories = copy.deepcopy(self.repositories)
        repositories["total_count"] = 2
        repositories["repositories"].append(
            {"id": 43, "full_name": "DevPathAi/devpath-shared", "archived": False}
        )
        with self.assertRaisesRegex(ValueError, "sole GitOps repository"):
            self.validate(repositories=repositories)
        repositories = copy.deepcopy(self.repositories)
        repositories["total_count"] = True
        with self.assertRaisesRegex(ValueError, "sole GitOps repository"):
            self.validate(repositories=repositories)

    def test_api_and_supported_installation_endpoints_are_frozen(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"X-GitHub-Api-Version": "2026-03-10"', source)
        self.assertNotIn('"X-GitHub-Api-Version": "2022-11-28"', source)
        self.assertNotIn('_request_json("/installation",', source)
        self.assertIn('"/installation/repositories?per_page=100"', source)


if __name__ == "__main__":
    unittest.main()
