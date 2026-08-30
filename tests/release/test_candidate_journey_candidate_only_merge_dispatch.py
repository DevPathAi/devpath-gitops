from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-auth-smoke.yml"


class CandidateJourneyCandidateOnlyMergeDispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8").replace("\r\n", "\n")

    def test_normal_auth_smoke_jobs_remain_main_only(self) -> None:
        before_dispatch = self.workflow.split(
            "\n  dispatch-candidate-journey-candidate-only-merge:", 1
        )[0]
        self.assertIn("if: github.ref == 'refs/heads/main'", before_dispatch)
        self.assertIn(
            "if: github.ref == 'refs/heads/main' && inputs.full",
            before_dispatch,
        )

    def test_one_shot_helper_is_exact_and_app_only(self) -> None:
        helper = self.workflow.split(
            "\n  dispatch-candidate-journey-candidate-only-merge:", 1
        )[1]
        for expected in (
            "refs/heads/chore/candidate-journey-candidate-only-merge",
            "EXPECTED_MAIN: 4aba68e3b5e24cd56e0eb177bba606376aaabe8d",
            "DESIRED_HEAD: fa41e5e625060a635131f42aa1bf9de87190f3eb",
            'EXPECTED_PR: "137"',
            'test "$GITHUB_ACTOR" = "github-actions[bot]"',
            'test "$(jq -er \'.actor.id\' "$run_document")" = "41898282"',
            "permission-contents: write",
            "verify_gitops_write_authority.py",
            "refs/heads/fix/candidate-journey-candidate-only",
            'test "${#changed_paths[@]}" = "5"',
            'test "${changed_paths[4]}" = "tests/release/test_release_hardening.py"',
            'git -C gitops-main push origin "$DESIRED_HEAD:refs/heads/main"',
        ):
            self.assertIn(expected, helper)
        self.assertNotIn("--force", helper)
        self.assertNotIn("reset --hard", helper)


if __name__ == "__main__":
    unittest.main()
