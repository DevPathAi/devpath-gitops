import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-auth-smoke.yml"


class Prod27R4RebaselineFixPublishTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_exact_release_coordinates_are_pinned(self) -> None:
        for value in (
            "ms-20260909-prod27r4",
            "b00835d41d32888b2d5092aa45a82fd4879bbcdd",
            "e1758b8a0c9bc678c63354e432a371e6070401f3",
            "801bf75f47e5c8af67627a3a9ab6fc1f72b414a9",
            "chore/prod27r4-idempotent-rebaseline-publish-v2-20260912",
            "fix/prod27r4-idempotent-rebaseline-main-v2-20260912",
        ):
            self.assertIn(value, self.workflow)

    def test_helper_and_target_graph_are_fail_closed(self) -> None:
        for fragment in (
            'test "$GITHUB_ACTOR" = "VelkaressiaBlutkrone"',
            'test "$GITHUB_TRIGGERING_ACTOR" = "VelkaressiaBlutkrone"',
            'test "$GITHUB_RUN_ATTEMPT" = "1"',
            'test "$(git rev-parse HEAD^)" = "$HELPER_BASE_SHA"',
            'test "$(git -C gitops-main rev-parse "$TARGET_SHA^")" = "$MAIN_SHA"',
            "fix(release): make staging rebaseline idempotent",
            'test "${#target_rows[@]}" -eq 6',
            "M\\t.github/workflows/mission-spine-promote.yml",
            "A\\tscripts/release/inspect_staging_web_phase.py",
            "M\\tscripts/release/verify_promotion_chain.py",
            "A\\ttests/release/test_inspect_staging_web_phase.py",
            "M\\ttests/release/test_production_workflow_wiring.py",
            "M\\ttests/release/test_promotion_chain.py",
            "python -m unittest discover -s tests/release -p 'test_*.py'",
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

    def test_chain_is_authenticated_before_and_after_publish(self) -> None:
        self.assertEqual(
            2,
            self.workflow.count(
                "python target/scripts/release/verify_promotion_chain.py"
            ),
        )
        for fragment in (
            'test "$(sed -n \'s/^phase=//p\' "$before_output")" = "mission-on"',
            'test "$(sed -n \'s/^migration_commit=//p\' "$before_output")" = "$MIGRATION_SHA"',
            'test "$(sed -n \'s/^landing_direct_upload_source_fix_commit=//p\' "$before_output")" = "$DIRECT_UPLOAD_SHA"',
            'test "$(sed -n \'s/^landing_wrangler_isolation_fix_commit=//p\' "$before_output")" = "$MAIN_SHA"',
            "staging_rebaseline_idempotency_fix_commit",
            'test "$(sed -n \'s/^on_commit=//p\' "$before_output")" = "$TARGET_SHA"',
            'test "$(sed -n \'s/^current_commit=//p\' "$after_output")" = "$TARGET_SHA"',
        ):
            self.assertIn(fragment, self.workflow)


if __name__ == "__main__":
    unittest.main()
