from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
SPRING_SERVICES = (
    "devpath-ai-svc",
    "devpath-community-svc",
    "devpath-gateway",
    "devpath-lcs-svc",
    "devpath-learning-svc",
    "devpath-notification-svc",
    "devpath-platform-svc",
    "devpath-sandbox-svc",
)
RBAC = ROOT / "apps" / "devpath-migration" / "base" / "writer-fence-rbac.yaml"


def _service_container(service: str) -> dict:
    document = yaml.safe_load(
        (ROOT / "apps" / service / "base" / "deployment.yaml").read_text(encoding="utf-8")
    )
    containers = [
        container
        for container in document["spec"]["template"]["spec"]["containers"]
        if container["name"] == service
    ]
    if len(containers) != 1:
        raise AssertionError(f"{service} must have exactly one container named after the service")
    return containers[0]


class ProductionStartupBudgetTest(unittest.TestCase):
    def test_spring_services_have_a_production_startup_budget(self):
        # Without a startupProbe the liveness probe (20s delay + 3 x 10s) kills a JVM that needs
        # more than ~50s to start - the 2026-09-21 herd: several JVMs starting on one 4-CPU node.
        for service in SPRING_SERVICES:
            probe = _service_container(service).get("startupProbe")
            self.assertIsNotNone(probe, service)
            self.assertEqual(
                probe["httpGet"], {"path": "/actuator/health/liveness", "port": 8080}, service
            )
            self.assertGreaterEqual(probe["periodSeconds"] * probe["failureThreshold"], 300, service)
            self.assertLessEqual(probe["periodSeconds"], 10, service)
            self.assertGreaterEqual(probe["timeoutSeconds"], 3, service)

    def test_steady_state_probes_are_unchanged(self):
        for service in SPRING_SERVICES:
            container = _service_container(service)
            self.assertEqual(
                container["readinessProbe"],
                {
                    "httpGet": {"path": "/actuator/health/readiness", "port": 8080},
                    "initialDelaySeconds": 10,
                },
                service,
            )
            self.assertEqual(
                container["livenessProbe"],
                {
                    "httpGet": {"path": "/actuator/health/liveness", "port": 8080},
                    "initialDelaySeconds": 20,
                },
                service,
            )

    def test_migration_fence_service_account_declares_its_pull_secret(self):
        # The migration Job pulls a private ghcr.io image with this ServiceAccount. Until
        # 2026-09-21 the secret existed only as a manual cluster patch.
        documents = list(yaml.safe_load_all(RBAC.read_text(encoding="utf-8")))
        account = documents[0]
        self.assertEqual(
            (account["kind"], account["metadata"]["name"]),
            ("ServiceAccount", "devpath-migration-fence"),
        )
        self.assertEqual(account["imagePullSecrets"], [{"name": "ghcr-pull"}])
        self.assertIs(account["automountServiceAccountToken"], False)


if __name__ == "__main__":
    unittest.main()
