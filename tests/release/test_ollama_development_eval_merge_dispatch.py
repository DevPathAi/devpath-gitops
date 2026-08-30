from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-auth-smoke.yml"


class OllamaDevelopmentEvalMergeDispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8").replace("\r\n", "\n")

    def test_normal_auth_smoke_jobs_remain_main_only(self) -> None:
        before_dispatch = self.workflow.split(
            "\n  dispatch-ollama-development-eval-contract-merge:", 1
        )[0]
        self.assertIn("if: github.ref == 'refs/heads/main'", before_dispatch)
        self.assertIn(
            "if: github.ref == 'refs/heads/main' && inputs.full",
            before_dispatch,
        )

    def test_one_shot_helper_is_exact_and_app_only(self) -> None:
        helper = self.workflow.split(
            "\n  dispatch-ollama-development-eval-contract-merge:", 1
        )[1]
        for expected in (
            "refs/heads/chore/ollama-development-eval-contract-merge",
            "EXPECTED_MAIN: 34df49f53eb4c4cc42a2c8e060f5738ce27691b7",
            "DESIRED_HEAD: 02800678ec36b1d662440b38885af2311298dfa8",
            'EXPECTED_PR: "140"',
            'test "$GITHUB_ACTOR" = "github-actions[bot]"',
            'test "$(jq -er \'.actor.id\' "$run_document")" = "41898282"',
            "permission-contents: write",
            "verify_gitops_write_authority.py",
            "refs/heads/release/ollama-development-eval-contract-main",
            'test "${#changed_paths[@]}" = "10"',
            'git -C gitops-main push origin "$DESIRED_HEAD:refs/heads/main"',
        ):
            self.assertIn(expected, helper)
        self.assertNotIn("--force", helper)
        self.assertNotIn("reset --hard", helper)


if __name__ == "__main__":
    unittest.main()
