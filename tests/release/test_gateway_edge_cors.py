from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "apps" / "devpath-gateway" / "base" / "deployment.yaml"
PREFIX = "SPRING_CLOUD_GATEWAY_SERVER_WEBFLUX_DEFAULT_FILTERS_"
# The gateway image's application.yml (devpath-gateway 391d984, PublicCorsDedupeTest) declares exactly this
# default filter. Spring Boot binds a list from the highest-precedence source as a whole, so an env that pins
# default-filters[0] alone replaces the list and silently drops the dedupe (2026-09-24 production: two
# Access-Control-Allow-Origin values and six Vary values on /mentor-access/invite-rounds). The env must
# therefore carry both entries itself.
DEDUPE = (
    "DedupeResponseHeader=Access-Control-Allow-Origin Access-Control-Allow-Credentials Vary, RETAIN_UNIQUE"
)


def _gateway_env() -> dict:
    document = yaml.safe_load(GATEWAY.read_text(encoding="utf-8"))
    containers = [
        container
        for container in document["spec"]["template"]["spec"]["containers"]
        if container["name"] == "devpath-gateway"
    ]
    if len(containers) != 1:
        raise AssertionError("the gateway must have exactly one container named devpath-gateway")
    env = {}
    for entry in containers[0]["env"]:
        if entry["name"] in env:
            raise AssertionError(f"duplicate env {entry['name']}")
        env[entry["name"]] = entry.get("value")
    return env


class GatewayEdgeCorsTest(unittest.TestCase):
    def test_production_default_filters_keep_the_host_and_dedupe_public_cors(self):
        env = _gateway_env()
        filters = {name: value for name, value in env.items() if name.startswith(PREFIX)}
        self.assertEqual(filters, {PREFIX + "0": "PreserveHostHeader", PREFIX + "1": DEDUPE})

    def test_dedupe_covers_every_header_the_public_route_duplicates(self):
        # platform-svc's public invite-rounds route adds its own CORS headers behind the gateway CorsWebFilter.
        _, spec = DEDUPE.split("=", 1)
        headers, strategy = (part.strip() for part in spec.split(","))
        self.assertEqual("RETAIN_UNIQUE", strategy)
        self.assertEqual(
            {"Access-Control-Allow-Origin", "Access-Control-Allow-Credentials", "Vary"},
            set(headers.split()),
        )
