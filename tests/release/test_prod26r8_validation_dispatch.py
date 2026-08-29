from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-validate.yml"


class Prod26r8ValidationDispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8").replace("\r\n", "\n")

    def test_protected_jobs_remain_main_only(self) -> None:
        candidate = self.workflow.split("\n  candidate-journeys:", 1)[1].split(
            "\n  seal-and-staging:", 1
        )[0]
        sealed = self.workflow.split("\n  seal-and-staging:", 1)[1].split(
            "\n  dispatch-validation:", 1
        )[0]
        self.assertIn("if: github.ref == 'refs/heads/main'", candidate)
        self.assertIn("if: github.ref == 'refs/heads/main'", sealed)
        self.assertIn("environment: mission-spine-staging", candidate)
        self.assertIn("environment: mission-spine-staging", sealed)

    def test_branch_dispatcher_is_exact_and_actions_only(self) -> None:
        dispatcher = self.workflow.split("\n  dispatch-validation:", 1)[1]
        for expected in (
            "if: github.ref == 'refs/heads/chore/prod26r8-validate-dispatch'",
            "RELEASE_ID: ms-20260829-prod26r8",
            "SOURCE_SHA: df94fba536fd6e1740333d6d6e79e11349b9e2b7",
            "actions: write",
            "contents: read",
            'test "$GITHUB_ACTOR" = "VelkaressiaBlutkrone"',
            'test "$inner_actor" = "github-actions[bot]"',
            'test "$(jq -er \'.actor.id\' "$run_document")" = "41898282"',
            'test "$(jq -er \'.actor.type\' "$run_document")" = "Bot"',
            "actions/workflows/mission-spine-validate.yml/dispatches",
        ):
            self.assertIn(expected, dispatcher)
        self.assertNotIn("environment:", dispatcher)


if __name__ == "__main__":
    unittest.main()
