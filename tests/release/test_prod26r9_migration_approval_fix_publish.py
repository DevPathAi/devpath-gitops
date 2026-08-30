import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-auth-smoke.yml"


class Prod26R9MigrationApprovalFixPublishTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_exact_release_coordinates_are_pinned(self) -> None:
        for value in (
            "ms-20260830-prod26r9",
            "5bcde50ed982c9b5382f7b87579a4096212c1b1b",
            "6ab7b95a2f981be176469300c1ce4693a5163952",
            "9ae75a2f0dc9f22f2383775fd2f785d510f93740",
            "chore/prod26r9-migration-approval-fix-publish",
            "fix/prod26r9-shared-migration-approval-contract",
        ):
            self.assertIn(value, self.workflow)

    def test_helper_and_target_graph_are_fail_closed(self) -> None:
        self.assertIn(
            "INPUT_FULL: ${{ inputs.full }}\n          GH_TOKEN: ${{ github.token }}",
            self.workflow,
        )
        for fragment in (
            'test "$GITHUB_ACTOR" = "VelkaressiaBlutkrone"',
            'test "$GITHUB_TRIGGERING_ACTOR" = "VelkaressiaBlutkrone"',
            'test "$GITHUB_RUN_ATTEMPT" = "1"',
            'test "$GITHUB_REF" = "refs/heads/$HELPER_BRANCH"',
            'test "$(git rev-parse HEAD^)" = "$MAIN_SHA"',
            'test "$(git -C gitops-main rev-parse "$TARGET_SHA^")" = "$MAIN_SHA"',
            "scripts/release/verify_promotion_chain.py",
            "scripts/release/verify_release_artifacts.py",
            "tests/release/test_migration_result_trust.py",
            "tests/release/test_promotion_chain.py",
        ):
            self.assertIn(fragment, self.workflow)

    def test_only_release_app_can_fast_forward_main(self) -> None:
        for fragment in (
            "environment: mission-spine-production-off",
            "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1",
            "permission-administration: read",
            "permission-contents: write",
            "verify_gitops_write_authority.py",
            'git -C gitops-main push origin "$TARGET_SHA:refs/heads/main"',
            'test "$(git -C gitops-main rev-parse origin/main)" = "$TARGET_SHA"',
        ):
            self.assertIn(fragment, self.workflow)
        self.assertNotIn("--force", self.workflow)

    def test_live_migration_evidence_and_resulting_chain_are_verified(self) -> None:
        self.assertIn("verify_migration_result.py", self.workflow)
        self.assertEqual(
            2,
            self.workflow.count(
                "python target/scripts/release/verify_promotion_chain.py"
            ),
        )
        self.assertIn('test "$phase" = "migration"', self.workflow)
        self.assertIn('test "$migration_commit" = "$MAIN_SHA"', self.workflow)
        self.assertIn('test "$current_commit" = "$TARGET_SHA"', self.workflow)


if __name__ == "__main__":
    unittest.main()
