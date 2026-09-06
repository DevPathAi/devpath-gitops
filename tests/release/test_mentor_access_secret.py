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

    def test_platform_requires_the_invite_hmac_and_bounded_batch_policy(self):
        deployment = yaml.safe_load(
            (PLATFORM_BASE / "deployment.yaml").read_text(encoding="utf-8")
        )
        env = {
            entry["name"]: entry
            for entry in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        }

        ref = env["MENTOR_INVITE_CODE_HMAC_SECRET"]["valueFrom"]["secretKeyRef"]
        self.assertEqual(ref, {
            "name": "mentor-access",
            "key": "invite-code-hmac-secret",
        })
        self.assertEqual(env["MENTOR_INVITE_BATCH_ENABLED"]["value"], "true")
        self.assertEqual(env["MENTOR_INVITE_BATCH_CRON"]["value"], "0 0 10 * * *")
        self.assertEqual(env["MENTOR_INVITE_BATCH_ZONE"]["value"], "Asia/Seoul")
        self.assertEqual(env["MENTOR_INVITE_BATCH_CHUNK_SIZE"]["value"], "25")
        self.assertEqual(env["MENTOR_INVITE_BATCH_DAILY_CAP"]["value"], "100")


if __name__ == "__main__":
    unittest.main()
