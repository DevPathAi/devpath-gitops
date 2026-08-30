from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-auth-smoke.yml"


class ProtectedInitiatorMergeDispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8").replace("\r\n", "\n")

    def test_normal_auth_smoke_jobs_remain_main_only(self) -> None:
        before_dispatch = self.workflow.split(
            "\n  dispatch-protected-initiator-merge:", 1
        )[0]
        self.assertIn("if: github.ref == 'refs/heads/main'", before_dispatch)
        self.assertIn(
            "if: github.ref == 'refs/heads/main' && inputs.full",
            before_dispatch,
        )

    def test_one_shot_helper_is_exact_and_app_only(self) -> None:
        helper = self.workflow.split(
            "\n  dispatch-protected-initiator-merge:", 1
        )[1]
        for expected in (
            "refs/heads/chore/protected-initiator-parity-merge",
            "EXPECTED_MAIN: 02800678ec36b1d662440b38885af2311298dfa8",
            "DESIRED_HEAD: bda5d3d991922db18ceafe379fb54e2d3ee603fc",
            'EXPECTED_PR: "142"',
            'test "$GITHUB_ACTOR" = "github-actions[bot]"',
            'test "$(jq -er \'.actor.id\' "$run_document")" = "41898282"',
            "permission-contents: write",
            "verify_gitops_write_authority.py",
            "refs/heads/release/protected-initiator-parity-main",
            'test "${#changed_paths[@]}" = "2"',
            'test "${changed_paths[0]}" = "scripts/release/verify_release_artifacts.py"',
            'test "${changed_paths[1]}" = "tests/release/test_signed_mobile_manual_trust.py"',
            'git -C gitops-main push origin "$DESIRED_HEAD:refs/heads/main"',
        ):
            self.assertIn(expected, helper)
        self.assertNotIn("--force", helper)
        self.assertNotIn("reset --hard", helper)


if __name__ == "__main__":
    unittest.main()
