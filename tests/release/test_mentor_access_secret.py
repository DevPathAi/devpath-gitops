import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
PLATFORM_BASE = ROOT / "apps" / "devpath-platform-svc" / "base"


class MentorAccessSecretTest(unittest.TestCase):
    def test_platform_base_binds_a_sealed_invite_hmac_secret(self):
        kustomization = yaml.safe_load(
            (PLATFORM_BASE / "kustomization.yaml").read_text(encoding="utf-8")
        )
        self.assertIn("sealedsecret-mentor-access.yaml", kustomization["resources"])

        document = yaml.safe_load(
            (PLATFORM_BASE / "sealedsecret-mentor-access.yaml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(document["apiVersion"], "bitnami.com/v1alpha1")
        self.assertEqual(document["kind"], "SealedSecret")
        self.assertEqual(document["metadata"]["name"], "mentor-access")
        self.assertEqual(document["metadata"]["namespace"], "devpath")
        self.assertNotIn("data", document)
        self.assertNotIn("stringData", document)

        encrypted = document["spec"]["encryptedData"]
        self.assertEqual(set(encrypted), {"invite-code-hmac-secret"})
        self.assertRegex(encrypted["invite-code-hmac-secret"], r"^Ag[A-Za-z0-9+/=]+$")
        self.assertEqual(document["spec"]["template"]["metadata"]["name"], "mentor-access")
        self.assertEqual(document["spec"]["template"]["metadata"]["namespace"], "devpath")


if __name__ == "__main__":
    unittest.main()
