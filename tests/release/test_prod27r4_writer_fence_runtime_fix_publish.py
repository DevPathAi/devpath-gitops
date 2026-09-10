import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-auth-smoke.yml"


class Prod27R4WriterFenceRuntimeFixPublishTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_exact_release_coordinates_are_pinned(self) -> None:
        for value in (
            "ms-20260909-prod27r4",
            "a5a6263f33a023ee11269c8f2d000758fce7b22c",
            "f619d645e3d1e9f8040efddcdab7b6c74e6c606a",
            "801bf75f47e5c8af67627a3a9ab6fc1f72b414a9",
            "chore/prod27r4-writer-fence-runtime-fix-publish",
            "fix/prod27r4-writer-fence-runtime-verifier-20260910",
        ):
            self.assertIn(value, self.workflow)

    def test_helper_and_target_graph_are_fail_closed(self) -> None:
        for fragment in (
            'test "$GITHUB_ACTOR" = "VelkaressiaBlutkrone"',
            'test "$GITHUB_TRIGGERING_ACTOR" = "VelkaressiaBlutkrone"',
            'test "$GITHUB_RUN_ATTEMPT" = "1"',
            'test "$(git rev-parse HEAD^)" = "$HELPER_BASE_SHA"',
            'test "$(git -C gitops-main rev-parse "$TARGET_SHA^")" = "$MAIN_SHA"',
            "fix(release): authenticate writer-fence migration runtime",
            'test "${#target_paths[@]}" -eq 6',
            "scripts/release/verify_kubernetes_release_runtime.py",
            "scripts/release/verify_promotion_chain.py",
            "tests/release/test_kubernetes_release_runtime.py",
            "tests/release/test_promotion_chain.py",
            "tests/release/test_release_contract.py",
            "tests/release/test_service_promotion.py",
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
            'test "$(sed -n \'s/^phase=//p\' "$before_output")" = "migration"',
            'test "$(sed -n \'s/^migration_commit=//p\' "$before_output")" = "$MIGRATION_SHA"',
            "migration_writer_fence_runtime_fix_commit",
            'test "$(sed -n \'s/^current_commit=//p\' "$after_output")" = "$TARGET_SHA"',
        ):
            self.assertIn(fragment, self.workflow)


if __name__ == "__main__":
    unittest.main()
