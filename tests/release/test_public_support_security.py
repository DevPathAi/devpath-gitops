import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


class PublicSupportSecurityTest(unittest.TestCase):
    def test_platform_requires_a_separate_sealed_rate_limit_hmac(self):
        base = ROOT / "apps" / "devpath-platform-svc" / "base"
        kustomization = yaml.safe_load(
            (base / "kustomization.yaml").read_text(encoding="utf-8")
        )
        self.assertIn("sealedsecret-public-support.yaml", kustomization["resources"])

        sealed = yaml.safe_load(
            (base / "sealedsecret-public-support.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(sealed["metadata"]["name"], "platform-public-support")
        encrypted = sealed["spec"]["encryptedData"]
        self.assertEqual(set(encrypted), {"rate-limit-hmac-secret"})
        self.assertRegex(encrypted["rate-limit-hmac-secret"], r"^Ag[A-Za-z0-9+/=]+$")

        deployment = yaml.safe_load(
            (base / "deployment.yaml").read_text(encoding="utf-8")
        )
        env = {
            entry["name"]: entry
            for entry in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        ref = env["PUBLIC_SUPPORT_RATE_LIMIT_HMAC_SECRET"]["valueFrom"]["secretKeyRef"]
        self.assertEqual(ref["name"], "platform-public-support")
        self.assertEqual(ref["key"], "rate-limit-hmac-secret")
        self.assertNotIn("optional", ref)

    def test_platform_turnstile_secret_and_origin_are_fail_closed(self):
        base = ROOT / "apps" / "devpath-platform-svc" / "base"
        kustomization = yaml.safe_load(
            (base / "kustomization.yaml").read_text(encoding="utf-8")
        )
        self.assertIn("sealedsecret-turnstile.yaml", kustomization["resources"])

        sealed = yaml.safe_load(
            (base / "sealedsecret-turnstile.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(sealed["metadata"]["name"], "platform-turnstile")
        self.assertEqual(sealed["metadata"]["namespace"], "devpath")
        self.assertNotIn("data", sealed)
        self.assertNotIn("stringData", sealed)
        encrypted = sealed["spec"]["encryptedData"]
        self.assertEqual(set(encrypted), {"turnstile-secret"})
        self.assertRegex(encrypted["turnstile-secret"], r"^Ag[A-Za-z0-9+/=]+$")

        deployment = yaml.safe_load(
            (base / "deployment.yaml").read_text(encoding="utf-8")
        )
        env = {
            entry["name"]: entry
            for entry in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual(
            env["TURNSTILE_SECRET"]["valueFrom"]["secretKeyRef"],
            {"name": "platform-turnstile", "key": "turnstile-secret"},
        )
        self.assertEqual(env["TURNSTILE_HOSTNAMES"]["value"], "leva.ai.kr")

    def test_homepage_origin_is_not_globally_credentialed(self):
        deployment = yaml.safe_load(
            (ROOT / "apps" / "devpath-gateway" / "base" / "deployment.yaml").read_text(
                encoding="utf-8"
            )
        )
        env = {
            entry["name"]: entry.get("value")
            for entry in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual(
            env["CORS_ALLOWED_ORIGINS"],
            "https://app.leva.ai.kr,https://admin.leva.ai.kr",
        )
        self.assertEqual(env["PUBLIC_CORS_ALLOWED_ORIGINS"], "https://leva.ai.kr")


if __name__ == "__main__":
    unittest.main()
