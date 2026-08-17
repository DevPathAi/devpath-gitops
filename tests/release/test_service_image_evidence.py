import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "verify_service_image_evidence.py"


def load_module():
    spec = importlib.util.spec_from_file_location("service_image_evidence", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def raw(document):
    return (json.dumps(document, indent=2) + "\n").encode()


class ServiceImageEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verifier = load_module()

    def setUp(self):
        self.source = "a" * 40
        self.run_id = 123
        self.workflow_sha = "b" * 64

    def identity(self, service, repository=None):
        repository = repository or f"DevPathAi/{service}"
        binding = {
            "repository": repository,
            "source_sha": self.source,
            "image_repository": f"ghcr.io/devpathai/{service}",
            "image_digest": "sha256:" + "1" * 64,
        }
        trust = {
            "repository": repository,
            "source_sha": self.source,
            "image_repository": binding["image_repository"],
            "root_digest": binding["image_digest"],
            "manifest_digest": "sha256:" + "2" * 64,
            "config_digest": "sha256:" + "3" * 64,
            "platform": {"os": "linux", "architecture": "amd64"},
            "rootfs_diff_ids": ["sha256:" + "4" * 64],
            "oci_labels": {
                "org.opencontainers.image.source": f"https://github.com/{repository}",
                "org.opencontainers.image.revision": self.source,
            },
        }
        return binding, trust

    def test_common_admin_and_ai_contracts_are_distinct_and_exact(self):
        common, trust = self.identity("devpath-community-svc")
        common_payload = {
            "schema_version": "devpath.immutable-image.v1",
            "status": "passed",
            "repository": common["repository"],
            "source_sha": self.source,
            "image_repository": common["image_repository"],
            "image_digest": common["image_digest"],
            "manifest_digest": trust["manifest_digest"],
            "config_digest": trust["config_digest"],
            "platform": {"os": "linux", "architecture": "amd64"},
            "rootfs_diff_ids": trust["rootfs_diff_ids"],
            "oci_labels": {
                "org.opencontainers.image.source": trust["oci_labels"][
                    "org.opencontainers.image.source"
                ],
                "org.opencontainers.image.revision": self.source,
            },
            "producer_workflow_path": ".github/workflows/ci.yml",
            "producer_workflow_sha256": self.workflow_sha,
            "producer_run_id": self.run_id,
            "producer_run_attempt": 1,
        }
        self.verifier.validate_service_image_payload(
            "devpath-community-svc",
            raw(common_payload),
            common,
            trust,
            self.run_id,
            self.workflow_sha,
        )

        admin, admin_trust = self.identity(
            "devpath-admin", "DevPathAi/devpath-frontend"
        )
        admin_payload = {
            "schema_version": "mission-spine.admin-artifact.v1",
            "source_sha": self.source,
            "image_repository": admin["image_repository"],
            "image_tag": self.source,
            "image_digest": admin["image_digest"],
            "image_config_digest": admin_trust["config_digest"],
            "publish_mode": "created",
            "image_labels": {
                "org.opencontainers.image.revision": self.source,
                "org.opencontainers.image.source": "https://github.com/DevPathAi/devpath-frontend",
            },
        }
        self.verifier.validate_service_image_payload(
            "devpath-admin",
            raw(admin_payload),
            admin,
            admin_trust,
            self.run_id,
            self.workflow_sha,
        )

        ai, ai_trust = self.identity("devpath-ai-svc")
        ai_payload = {
            "schema_version": "devpath.immutable-image.v1",
            "state": "present",
            "source_sha": self.source,
            "image_repository": ai["image_repository"],
            "image_tag": self.source,
            "image_digest": ai["image_digest"],
            "manifest_digest": ai_trust["manifest_digest"],
            "config_digest": ai_trust["config_digest"],
            "platform": "linux/amd64",
            "oci_labels": {
                "org.opencontainers.image.revision": self.source,
                "org.opencontainers.image.source": "https://github.com/DevPathAi/devpath-ai-svc",
            },
        }
        self.verifier.validate_service_image_payload(
            "devpath-ai-svc",
            raw(ai_payload),
            ai,
            ai_trust,
            self.run_id,
            self.workflow_sha,
        )

        confused = copy.deepcopy(common_payload)
        confused["image_repository"] = ai["image_repository"]
        confused["image_digest"] = ai["image_digest"]
        with self.assertRaisesRegex(ValueError, "keys/order"):
            self.verifier.validate_service_image_payload(
                "devpath-ai-svc",
                raw(confused),
                ai,
                ai_trust,
                self.run_id,
                self.workflow_sha,
            )

    def test_exposed_manifest_config_rootfs_and_workflow_drift_are_rejected(self):
        binding, trust = self.identity("devpath-community-svc")
        payload = {
            "schema_version": "devpath.immutable-image.v1",
            "status": "passed",
            "repository": binding["repository"],
            "source_sha": self.source,
            "image_repository": binding["image_repository"],
            "image_digest": binding["image_digest"],
            "manifest_digest": trust["manifest_digest"],
            "config_digest": trust["config_digest"],
            "platform": {"os": "linux", "architecture": "amd64"},
            "rootfs_diff_ids": trust["rootfs_diff_ids"],
            "oci_labels": {
                "org.opencontainers.image.source": f"https://github.com/{binding['repository']}",
                "org.opencontainers.image.revision": self.source,
            },
            "producer_workflow_path": ".github/workflows/ci.yml",
            "producer_workflow_sha256": self.workflow_sha,
            "producer_run_id": self.run_id,
            "producer_run_attempt": 1,
        }
        for key, value in (
            ("manifest_digest", "sha256:" + "8" * 64),
            ("config_digest", "sha256:" + "8" * 64),
            ("rootfs_diff_ids", ["sha256:" + "8" * 64]),
            ("producer_workflow_sha256", "8" * 64),
            ("producer_run_attempt", 2),
        ):
            changed = copy.deepcopy(payload)
            changed[key] = value
            with self.assertRaisesRegex(ValueError, "differs"):
                self.verifier.validate_service_image_payload(
                    "devpath-community-svc",
                    raw(changed),
                    binding,
                    trust,
                    self.run_id,
                    self.workflow_sha,
                )

    def test_artifact_names_files_and_unique_run_are_not_aliasable(self):
        binding, _ = self.identity("devpath-community-svc")
        run = {
            "id": self.run_id,
            "status": "completed",
            "conclusion": "success",
            "event": "push",
            "head_sha": self.source,
            "head_branch": "main",
            "path": ".github/workflows/ci.yml",
            "run_attempt": 1,
            "repository": {"full_name": binding["repository"]},
            "head_repository": {"full_name": binding["repository"]},
        }
        name, filename = self.verifier.artifact_contract(
            "devpath-community-svc", self.source, self.run_id
        )
        artifact = {
            "id": 456,
            "name": name,
            "expired": False,
            "workflow_run": {"id": self.run_id},
        }
        selected = self.verifier.select_unique_service_run(
            "devpath-community-svc", binding, [run], lambda value: [artifact] if value == name else []
        )
        self.assertEqual(selected[2], filename)
        self.assertNotEqual(
            self.verifier.artifact_contract("devpath-admin", self.source, self.run_id),
            self.verifier.artifact_contract("devpath-ai-svc", self.source, self.run_id),
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.verifier.select_unique_service_run(
                "devpath-community-svc",
                binding,
                [run],
                lambda _value: [artifact, copy.deepcopy(artifact)],
            )


if __name__ == "__main__":
    unittest.main()
