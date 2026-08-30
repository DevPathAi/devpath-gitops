from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-auth-smoke.yml"


class KubernetesApiIngressMergeDispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8").replace("\r\n", "\n")

    def test_normal_auth_smoke_jobs_remain_main_only(self) -> None:
        before_dispatch = self.workflow.split(
            "\n  dispatch-kubernetes-api-ingress-merge:", 1
        )[0]
        self.assertIn("if: github.ref == 'refs/heads/main'", before_dispatch)
        self.assertIn(
            "if: github.ref == 'refs/heads/main' && inputs.full",
            before_dispatch,
        )

    def test_one_shot_helper_is_exact_and_app_only(self) -> None:
        helper = self.workflow.split(
            "\n  dispatch-kubernetes-api-ingress-merge:", 1
        )[1]
        expected = (
            "refs/heads/chore/kubernetes-api-ingress-merge",
            "EXPECTED_MAIN: fa41e5e625060a635131f42aa1bf9de87190f3eb",
            "DESIRED_HEAD: 34df49f53eb4c4cc42a2c8e060f5738ce27691b7",
            'EXPECTED_PR: "138"',
            'test "$GITHUB_ACTOR" = "github-actions[bot]"',
            'test "$(jq -er \'.actor.id\' "$run_document")" = "41898282"',
            "permission-contents: write",
            "verify_gitops_write_authority.py",
            "refs/heads/fix/github-actions-kubernetes-api-ingress",
            'test "$(git -C gitops-main rev-list --count "$EXPECTED_MAIN..$DESIRED_HEAD")" = "3"',
            'test "${#changed_paths[@]}" = "7"',
            'test "${changed_paths[0]}" = ".github/workflows/mission-spine-promote.yml"',
            'test "${changed_paths[6]}" = "tests/release/test_production_workflow_wiring.py"',
            "python3 -m unittest discover -s tests/release -p 'test_*.py'",
            'git -C gitops-main push origin "$DESIRED_HEAD:refs/heads/main"',
        )
        for value in expected:
            self.assertIn(value, helper)
        self.assertNotIn("--force", helper)
        self.assertNotIn("reset --hard", helper)


if __name__ == "__main__":
    unittest.main()
