from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-validate.yml"


class KubernetesApiIngressLiveTestWiring(unittest.TestCase):
    def test_exact_bot_branch_runs_read_only_staging_probe_with_cleanup(self):
        workflow = WORKFLOW.read_text(encoding="utf-8").replace("\r\n", "\n")
        section = workflow.split("\n  verify-kubernetes-api-ingress-live:", 1)[1]
        expected = (
            "github.ref == 'refs/heads/chore/k3s-api-ingress-live-test'",
            "github.actor == 'github-actions[bot]'",
            "environment: mission-spine-staging",
            "id-token: write",
            "aws-actions/configure-aws-credentials@e6de054238d6b7531b4efff3b6587d9aade6a06c",
            "manage_kubernetes_api_ingress.py open",
            "manage_production_kubeconfig.py create",
            "auth can-i get deployments",
            "get deployment/devpath-web-staging",
            "manage_production_kubeconfig.py cleanup",
            "manage_kubernetes_api_ingress.py close",
        )
        for value in expected:
            self.assertIn(value, section)
        order = tuple(section.index(value) for value in expected[5:])
        self.assertEqual(order, tuple(sorted(order)))
        for forbidden in ("kubectl apply", "kubectl patch", "kubectl delete"):
            self.assertNotIn(forbidden, section)


if __name__ == "__main__":
    unittest.main()
