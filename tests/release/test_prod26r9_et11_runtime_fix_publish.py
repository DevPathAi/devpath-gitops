import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-auth-smoke.yml"


class Prod26R9Et11RuntimeFixPublishTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_exact_release_coordinates_are_pinned(self) -> None:
        for value in (
            "ms-20260830-prod26r9",
            "5bcde50ed982c9b5382f7b87579a4096212c1b1b",
            "6ab7b95a2f981be176469300c1ce4693a5163952",
            "0576f3a52a02b3c434ab8b0931b53cc543e490ae",
            "6f3d57e555a92a9ff1941728cc32312feaf03ef3",
            "4544e2c5a8ccd59aa50a6823a0eb803b3aac8bd8",
            "b55dbd48ecd323b2a6d51b4a5319c951928b3c0a",
            "aaef09d39df61f1561f32ce616a498a00585c068",
            "cc9d0424f7a2dc1d962e4bb7e820248373cc40e4",
            "711273896cd3e6c8130ba8fab4b1683e08340260",
            "ab72e59f903be8b243ccd129a7d6e44de9ec1b1f",
            "e4408b007d2104a6a2743a1237ce2b9bd62a50ee",
            "9ae75a2f0dc9f22f2383775fd2f785d510f93740",
            "e447341d84c077e0bc3fe44f150bd39a935defa9",
            "chore/prod26r9-et11-runtime-fix-publish",
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
            'test "$(git rev-parse HEAD^)" = "$HELPER_BASE_SHA"',
            'test "$(git -C gitops-main rev-parse "$TARGET_SHA^")" = "$MAIN_SHA"',
            "fix(release): align canary runtime image forms",
            'test "${#target_paths[@]}" -eq 9',
            "scripts/release/build_production_canary.py",
            "scripts/release/verify_oci_images.py",
            "scripts/release/verify_promotion_chain.py",
            "scripts/release/verify_promotion_evidence.py",
            "tests/release/test_oci_image_trust.py",
            "tests/release/test_production_canary.py",
            "tests/release/test_promotion_chain.py",
            "tests/release/test_promotion_evidence.py",
            "tests/release/test_release_contract.py",
        ):
            self.assertIn(fragment, self.workflow)

    def test_only_release_app_can_fast_forward_main(self) -> None:
        for fragment in (
            "environment: mission-spine-production-on",
            "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1",
            "permission-administration: read",
            "permission-contents: write",
            "verify_gitops_write_authority.py",
            'git -C gitops-main push origin "$TARGET_SHA:refs/heads/main"',
            'test "$(git -C gitops-main rev-parse origin/main)" = "$TARGET_SHA"',
        ):
            self.assertIn(fragment, self.workflow)
        self.assertNotIn("--force", self.workflow)

    def test_tested_chain_is_reauthenticated_before_and_after_publish(self) -> None:
        self.assertIn("python3 -m unittest discover -s tests/release", self.workflow)
        self.assertEqual(
            2,
            self.workflow.count(
                "python target/scripts/release/verify_promotion_chain.py"
            ),
        )
        for fragment in (
            'test "$phase" = "mission-on"',
            'test "$migration_commit" = "$MIGRATION_SHA"',
            'test "$approval_fix_commit" = "$APPROVAL_FIX_SHA"',
            'test "$runtime_fix_commit" = "$RUNTIME_FIX_SHA"',
            'test "$admission_fix_commit" = "$ADMISSION_FIX_SHA"',
            'test "$identity_fix_commit" = "$IDENTITY_FIX_SHA"',
            'test "$services_commit" = "$SERVICES_SHA"',
            'test "$status_image_fix_commit" = "$STATUS_FIX_SHA"',
            'test "$source_status_fix_commit" = "$SOURCE_STATUS_FIX_SHA"',
            'test "$off_commit" = "$OFF_SHA"',
            'test "$on_commit" = "$MAIN_SHA"',
            'test "$canary_form_fix_commit" = "$TARGET_SHA"',
            'test "$current_commit" = "$TARGET_SHA"',
        ):
            self.assertIn(fragment, self.workflow)

    def test_transient_test_repositories_disable_detached_git_maintenance(self) -> None:
        for fragment in (
            'GIT_CONFIG_COUNT: "2"',
            "GIT_CONFIG_KEY_0: maintenance.auto",
            'GIT_CONFIG_VALUE_0: "false"',
            "GIT_CONFIG_KEY_1: gc.auto",
            'GIT_CONFIG_VALUE_1: "0"',
        ):
            self.assertIn(fragment, self.workflow)


if __name__ == "__main__":
    unittest.main()
