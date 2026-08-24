import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
STACK = ROOT / "staging" / "mission-spine"
KAFKA = ROOT / "kafka" / "staging-cluster.yaml"
SHA40 = re.compile(r"^[0-9a-f]{40}$")


def load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_all(path: Path):
    return [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc]


def env_map(patch_name: str):
    patch = load(STACK / "patches" / patch_name)
    env = patch["spec"]["template"]["spec"]["containers"][0]["env"]
    return {item["name"]: item for item in env}


class MissionStagingStackTest(unittest.TestCase):
    def test_stack_reuses_only_non_secret_production_resources(self):
        config = load(STACK / "kustomization.yaml")
        self.assertEqual(config["namespace"], "devpath-staging")
        resources = set(config["resources"])
        for component in (
            "devpath-gateway",
            "devpath-platform-svc",
            "devpath-learning-svc",
            "devpath-sandbox-svc",
            "devpath-ai-svc",
            "devpath-lcs-svc",
            "devpath-redis",
            "devpath-sandbox-runner",
        ):
            self.assertTrue(any(component in item for item in resources), component)
        self.assertFalse(any("sealedsecret" in item.lower() for item in resources))
        for path in STACK.rglob("*.yaml"):
            for document in load_all(path):
                self.assertNotIn(document.get("kind"), {"Secret", "SealedSecret"}, path)

    def test_service_images_are_bound_to_protected_source_shas(self):
        config = load(STACK / "kustomization.yaml")
        images = {item["name"]: item for item in config["images"]}
        expected = {
            f"ghcr.io/devpathai/{name}"
            for name in (
                "devpath-gateway",
                "devpath-platform-svc",
                "devpath-learning-svc",
                "devpath-sandbox-svc",
                "devpath-ai-svc",
                "devpath-lcs-svc",
            )
        }
        self.assertEqual(set(images), expected)
        for image in images.values():
            self.assertRegex(image["newTag"], SHA40)
            self.assertRegex(image["digest"], re.compile(r"^sha256:[0-9a-f]{64}$"))

    def test_release_control_is_staging_only_and_secret_backed(self):
        platform = env_map("platform.yaml")
        self.assertEqual(platform["MISSION_RELEASE_CONTROL_ENABLED"]["value"], "true")
        self.assertRegex(platform["MISSION_RELEASE_FIXTURE_REVISION"]["value"], SHA40)
        for name, key in (
            ("MISSION_RELEASE_CONTROL_TOKEN", "control-token"),
            ("MISSION_RELEASE_INTERNAL_TOKEN", "internal-token"),
            ("MISSION_RELEASE_OAUTH_CLIENT_ID", "oauth-client-id"),
            ("MISSION_RELEASE_OAUTH_CLIENT_SECRET", "oauth-client-secret"),
        ):
            ref = platform[name]["valueFrom"]["secretKeyRef"]
            self.assertEqual(ref, {"name": "mission-spine-release-control", "key": key})

        capabilities = set(platform["MISSION_RELEASE_CAPABILITIES"]["value"].split(","))
        self.assertEqual(
            capabilities,
            {
                "production-artifact-probe",
                "deterministic-oauth",
                "required-consent-control",
                "database-authoritative-claim",
                "claim-replay",
                "content-linked-completion-replay",
                "contentless-completion-replay",
                "analytics-spy",
                "analytics-prepermission-zero",
                "analytics-permission-control",
                "real-sandbox-runtime",
                "sandbox-owner-recovery",
                "sandbox-disconnect-control",
                "sandbox-timeout-control",
                "sandbox-truncation-control",
                "sandbox-reconciliation-control",
                "kafka-outbox-review",
                "deterministic-review",
                "private-mentor-prompt",
                "mock-mentor-provider-capture",
                "partial-failure-control",
            },
        )

        for patch in ("learning.yaml", "sandbox.yaml", "ai.yaml", "lcs.yaml"):
            env = env_map(patch)
            self.assertEqual(env["MISSION_RELEASE_CONTROL_ENABLED"]["value"], "true")
            self.assertEqual(
                env["INTERNAL_API_TOKEN"]["valueFrom"]["secretKeyRef"],
                {"name": "mission-spine-release-control", "key": "internal-token"},
            )

    def test_dependencies_are_isolated_and_ai_providers_are_deterministic(self):
        for patch in ("platform.yaml", "learning.yaml", "sandbox.yaml", "ai.yaml"):
            env = env_map(patch)
            if "KAFKA_BOOTSTRAP" in env:
                self.assertEqual(
                    env["KAFKA_BOOTSTRAP"]["value"],
                    "devpath-staging-kafka-bootstrap.kafka.svc:9092",
                )
        for patch in ("platform.yaml", "learning.yaml", "lcs.yaml"):
            self.assertEqual(env_map(patch)["REDIS_HOST"]["value"], "redis.devpath-staging.svc")

        ai = env_map("ai.yaml")
        for provider in ("REVIEW_PROVIDER", "MENTOR_PROVIDER", "COMMUNITY_SEED_PROVIDER", "RETENTION_PROVIDER"):
            self.assertEqual(ai[provider]["value"], "mock")
        self.assertEqual(ai["MENTOR_FALLBACK"]["value"], "")

        kafka = load_all(KAFKA)
        self.assertEqual({doc["metadata"]["name"] for doc in kafka}, {"mission-spine-staging", "devpath-staging"})
        self.assertTrue(all(doc["metadata"]["namespace"] == "kafka" for doc in kafka))
        cluster = next(doc for doc in kafka if doc["kind"] == "Kafka")
        self.assertEqual(cluster["spec"]["kafka"]["config"]["default.replication.factor"], 1)

    def test_spring_services_have_staging_startup_budget(self):
        for patch_name in ("gateway.yaml", "platform.yaml", "learning.yaml", "sandbox.yaml", "ai.yaml", "lcs.yaml"):
            patch = load(STACK / "patches" / patch_name)
            probe = patch["spec"]["template"]["spec"]["containers"][0]["startupProbe"]
            self.assertEqual(probe["httpGet"], {"path": "/actuator/health/liveness", "port": 8080})
            self.assertGreaterEqual(probe["periodSeconds"] * probe["failureThreshold"], 300)

    def test_candidate_headers_route_only_canonical_app_and_api(self):
        routes = load_all(STACK / "canonical-routes.yaml")
        self.assertEqual(len(routes), 2)
        expected = {
            "mission-spine-candidate-app": ("app.leva.ai.kr", "devpath-web-staging"),
            "mission-spine-candidate-api": ("api.leva.ai.kr", "devpath-gateway"),
        }
        for route in routes:
            name = route["metadata"]["name"]
            host, service = expected[name]
            rule = route["spec"]["routes"][0]
            self.assertIn(f"Host(`{host}`)", rule["match"])
            self.assertIn("HeaderRegexp(`X-Candidate-Spec-Sha256`", rule["match"])
            self.assertIn("HeaderRegexp(`X-Release-Run-Key`", rule["match"])
            self.assertGreaterEqual(rule["priority"], 1000)
            self.assertEqual(rule["services"][0]["name"], service)

    def test_control_oauth_and_analytics_hosts_are_exact(self):
        ingress = load(STACK / "release-hosts-ingress.yaml")
        hosts = {rule["host"] for rule in ingress["spec"]["rules"]}
        self.assertEqual(
            hosts,
            {
                "release-control.staging.leva.ai.kr",
                "oauth.staging.leva.ai.kr",
                "analytics-spy.staging.leva.ai.kr",
            },
        )
        tls_hosts = set(ingress["spec"]["tls"][0]["hosts"])
        self.assertEqual(tls_hosts, hosts)

    def test_provisioning_preinstalls_privileged_database_extensions(self):
        script = (ROOT / "scripts" / "release" / "provision_mission_staging.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("CREATE EXTENSION IF NOT EXISTS vector", script)
        self.assertIn("-d devpath_staging", script)
        self.assertIn("REVOKE devpath_staging FROM %I", script)

    def test_provisioning_generates_http_tokens_without_trailing_newlines(self):
        script = (ROOT / "scripts" / "release" / "provision_mission_staging.sh").read_text(
            encoding="utf-8"
        )
        for token in ("control-token", "internal-token"):
            self.assertIn(
                f"openssl rand -hex 32 | tr -d '\\n' > \"$scratch/{token}\"",
                script,
            )


if __name__ == "__main__":
    unittest.main()
