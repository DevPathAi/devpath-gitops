from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-auth-smoke.yml"


class CandidateJourneyStageMergeDispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8").replace("\r\n", "\n")

    def test_normal_auth_smoke_jobs_remain_main_only(self) -> None:
        before_dispatch = self.workflow.split(
            "\n  dispatch-candidate-journey-stage-merge:", 1
        )[0]
        self.assertIn("if: github.ref == 'refs/heads/main'", before_dispatch)
        self.assertIn(
            "if: github.ref == 'refs/heads/main' && inputs.full",
            before_dispatch,
        )

    def test_one_shot_helper_is_exact_and_app_only(self) -> None:
        helper = self.workflow.split(
            "\n  dispatch-candidate-journey-stage-merge:", 1
        )[1]
        for expected in (
            "refs/heads/chore/candidate-journey-stage-merge",
            "EXPECTED_MAIN: df94fba536fd6e1740333d6d6e79e11349b9e2b7",
            "DESIRED_HEAD: 44d0e5f72fbd3c82e4499fc3c8587f5aff92d313",
            'EXPECTED_PR: "135"',
            'test "$GITHUB_ACTOR" = "github-actions[bot]"',
            'test "$(jq -er \'.actor.id\' "$run_document")" = "41898282"',
            "permission-contents: write",
            "verify_gitops_write_authority.py",
            "refs/heads/fix/stage-candidate-before-journeys",
            'test "${#changed_paths[@]}" = "3"',
            'git -C gitops-main push origin "$DESIRED_HEAD:refs/heads/main"',
        ):
            self.assertIn(expected, helper)
        self.assertNotIn("--force", helper)
        self.assertNotIn("reset --hard", helper)


if __name__ == "__main__":
    unittest.main()
