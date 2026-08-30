from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-validate.yml"


class Prod26r9ValidationDispatchTest(unittest.TestCase):
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
            "if: github.ref == 'refs/heads/chore/prod26r9-validate-dispatch'",
            "RELEASE_ID: ms-20260830-prod26r9",
            "SOURCE_SHA: fa41e5e625060a635131f42aa1bf9de87190f3eb",
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

    def test_branch_staging_diagnostic_is_read_only_and_environment_scoped(self) -> None:
        diagnostic = self.workflow.split("\n  diagnose-prod26r9-staging:", 1)[1].split(
            "\n  dispatch-validation:", 1
        )[0]
        for expected in (
            "if: github.ref == 'refs/heads/chore/prod26r9-validate-dispatch'",
            "github.actor == 'github-actions[bot]'",
            "environment: mission-spine-staging",
            "contents: read",
            "secrets.STAGING_KUBECONFIG_B64",
            "--scope staging",
            "get deployment/devpath-web-staging",
            "get replicasets",
            "get pods",
        ):
            self.assertIn(expected, diagnostic)
        for forbidden in (
            "kubectl apply",
            "kubectl patch",
            "kubectl delete",
            "kubectl set image",
            "kubectl rollout restart",
        ):
            self.assertNotIn(forbidden, diagnostic)

        dispatcher = self.workflow.split(
            "\n  dispatch-prod26r9-staging-diagnostic:", 1
        )[1].split("\n  diagnose-prod26r9-staging:", 1)[0]
        for expected in (
            "github.actor == 'VelkaressiaBlutkrone'",
            "DIAGNOSTIC_BRANCH: chore/k3s-api-ingress-live-test",
            "DIAGNOSTIC_SHA: 5b955ece2de2d4755d7f80a9917bfd4c5e3d8a77",
            'test "$branch_sha" = "$DIAGNOSTIC_SHA"',
            '"ref": $diagnostic_branch',
            'test "$diagnostic_actor" = "github-actions[bot]"',
            'test "$diagnostic_actor_id" = "41898282"',
            'test "$diagnostic_attempt" = "1"',
        ):
            self.assertIn(expected, dispatcher)


if __name__ == "__main__":
    unittest.main()
