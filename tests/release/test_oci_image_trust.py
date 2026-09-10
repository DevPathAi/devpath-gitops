import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "verify_oci_images.py"


def load_module():
    spec = importlib.util.spec_from_file_location("oci_image_trust", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def raw(document):
    return (json.dumps(document, separators=(",", ":"), sort_keys=True) + "\n").encode()


def digest(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


class OciImageTrustTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.oci = load_module()

    def setUp(self):
        self.repository = "DevPathAi/devpath-ai-svc"
        self.source_sha = "b" * 40
        self.image_repository = "ghcr.io/devpathai/devpath-ai-svc"
        self.config_document = {
            "architecture": "amd64",
            "os": "linux",
            "config": {
                "Labels": {
                    "org.opencontainers.image.revision": self.source_sha,
                    "org.opencontainers.image.source": "https://github.com/DevPathAi/devpath-ai-svc",
                    "org.opencontainers.image.title": "devpath-ai-svc",
                }
            },
            "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "1" * 64]},
        }
        self.config = raw(self.config_document)
        self.config_digest = digest(self.config)
        self.child_document = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": self.config_digest,
                "size": len(self.config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                    "digest": "sha256:" + "2" * 64,
                    "size": 123,
                }
            ],
        }
        self.child = raw(self.child_document)
        self.child_digest = digest(self.child)
        self.index_document = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": self.child_digest,
                    "size": len(self.child),
                    "platform": {"architecture": "amd64", "os": "linux"},
                },
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": "sha256:" + "3" * 64,
                    "size": 321,
                    "platform": {"architecture": "arm64", "os": "linux"},
                },
            ],
        }
        self.index = raw(self.index_document)
        self.root_digest = digest(self.index)

    def verify(self, **overrides):
        return self.oci.validate_oci_image(
            repository=self.repository,
            source_sha=self.source_sha,
            image_repository=self.image_repository,
            expected_root_digest=overrides.get("expected_root_digest", self.root_digest),
            root_bytes=overrides.get("root_bytes", self.index),
            root_content_type=overrides.get(
                "root_content_type", "application/vnd.oci.image.index.v1+json"
            ),
            root_digest_header=overrides.get("root_digest_header", self.root_digest),
            manifest_bytes=overrides.get("manifest_bytes", self.child),
            manifest_content_type=overrides.get(
                "manifest_content_type", "application/vnd.oci.image.manifest.v1+json"
            ),
            manifest_digest_header=overrides.get(
                "manifest_digest_header", self.child_digest
            ),
            config_bytes=overrides.get("config_bytes", self.config),
        )

    def test_exact_root_amd64_manifest_config_and_labels_pass(self):
        result = self.verify()
        self.assertEqual(result["root_digest"], self.root_digest)
        self.assertEqual(result["manifest_digest"], self.child_digest)
        self.assertEqual(result["config_digest"], self.config_digest)
        self.assertEqual(result["rootfs_diff_ids"], ["sha256:" + "1" * 64])
        self.assertEqual(
            self.oci.normalize_runtime_image_id(
                "docker-pullable://ghcr.io/devpathai/devpath-ai-svc@" + self.child_digest,
                result,
            ),
            {"digest": self.child_digest, "form": "linux-amd64-manifest"},
        )
        self.assertEqual(
            self.oci.normalize_runtime_image_id("docker://" + self.config_digest, result)[
                "form"
            ],
            "config",
        )
        self.assertTrue(
            self.oci.runtime_image_matches(
                self.child_digest, "linux-amd64-manifest", result
            )
        )
        self.assertTrue(
            self.oci.runtime_image_matches(self.config_digest, "config", result)
        )
        self.assertFalse(
            self.oci.runtime_image_matches(self.child_digest, "manifest", result)
        )
        self.assertFalse(
            self.oci.runtime_image_matches(self.config_digest, "linux-amd64-manifest", result)
        )

    def test_duplicate_amd64_or_other_platform_child_is_rejected(self):
        index = copy.deepcopy(self.index_document)
        index["manifests"].append(copy.deepcopy(index["manifests"][0]))
        index_bytes = raw(index)
        with self.assertRaisesRegex(ValueError, "duplicated|exactly one linux/amd64"):
            self.verify(
                root_bytes=index_bytes,
                expected_root_digest=digest(index_bytes),
                root_digest_header=digest(index_bytes),
            )
        index = copy.deepcopy(self.index_document)
        index["manifests"][0]["platform"]["architecture"] = "arm64"
        index_bytes = raw(index)
        with self.assertRaisesRegex(ValueError, "exactly one linux/amd64"):
            self.verify(
                root_bytes=index_bytes,
                expected_root_digest=digest(index_bytes),
                root_digest_header=digest(index_bytes),
            )

    def test_manifest_digest_size_and_config_relation_are_exact(self):
        index = copy.deepcopy(self.index_document)
        index["manifests"][0]["size"] += 1
        index_bytes = raw(index)
        with self.assertRaisesRegex(ValueError, "manifest descriptor size"):
            self.verify(
                root_bytes=index_bytes,
                expected_root_digest=digest(index_bytes),
                root_digest_header=digest(index_bytes),
            )
        child = copy.deepcopy(self.child_document)
        child["config"]["digest"] = "sha256:" + "4" * 64
        child_bytes = raw(child)
        index = copy.deepcopy(self.index_document)
        index["manifests"][0]["digest"] = digest(child_bytes)
        index["manifests"][0]["size"] = len(child_bytes)
        index_bytes = raw(index)
        with self.assertRaisesRegex(ValueError, "config digest"):
            self.verify(
                root_bytes=index_bytes,
                expected_root_digest=digest(index_bytes),
                root_digest_header=digest(index_bytes),
                manifest_bytes=child_bytes,
                manifest_digest_header=digest(child_bytes),
            )

    def test_source_and_revision_labels_are_independent_and_exact(self):
        for label, value in (
            ("org.opencontainers.image.revision", "c" * 40),
            ("org.opencontainers.image.source", "https://github.com/evil/repo"),
        ):
            config = copy.deepcopy(self.config_document)
            config["config"]["Labels"][label] = value
            config_bytes = raw(config)
            child = copy.deepcopy(self.child_document)
            child["config"]["digest"] = digest(config_bytes)
            child["config"]["size"] = len(config_bytes)
            child_bytes = raw(child)
            index = copy.deepcopy(self.index_document)
            index["manifests"][0]["digest"] = digest(child_bytes)
            index["manifests"][0]["size"] = len(child_bytes)
            index_bytes = raw(index)
            with self.assertRaisesRegex(ValueError, "OCI source labels"):
                self.verify(
                    root_bytes=index_bytes,
                    expected_root_digest=digest(index_bytes),
                    root_digest_header=digest(index_bytes),
                    manifest_bytes=child_bytes,
                    manifest_digest_header=digest(child_bytes),
                    config_bytes=config_bytes,
                )

    def test_layer_rootfs_cardinality_and_media_are_exact_and_nonforeign(self):
        config = copy.deepcopy(self.config_document)
        config["rootfs"]["diff_ids"].append("sha256:" + "5" * 64)
        config_bytes = raw(config)
        child = copy.deepcopy(self.child_document)
        child["config"]["digest"] = digest(config_bytes)
        child["config"]["size"] = len(config_bytes)
        child_bytes = raw(child)
        index = copy.deepcopy(self.index_document)
        index["manifests"][0]["digest"] = digest(child_bytes)
        index["manifests"][0]["size"] = len(child_bytes)
        index_bytes = raw(index)
        with self.assertRaisesRegex(ValueError, "rootfs diff IDs"):
            self.verify(
                root_bytes=index_bytes,
                expected_root_digest=digest(index_bytes),
                root_digest_header=digest(index_bytes),
                manifest_bytes=child_bytes,
                manifest_digest_header=digest(child_bytes),
                config_bytes=config_bytes,
            )
        child = copy.deepcopy(self.child_document)
        child["layers"][0]["mediaType"] = "application/vnd.oci.image.layer.evil"
        child["layers"][0]["urls"] = ["https://evil.invalid/layer"]
        child_bytes = raw(child)
        index = copy.deepcopy(self.index_document)
        index["manifests"][0]["digest"] = digest(child_bytes)
        index["manifests"][0]["size"] = len(child_bytes)
        index_bytes = raw(index)
        with self.assertRaisesRegex(ValueError, "layer descriptors"):
            self.verify(
                root_bytes=index_bytes,
                expected_root_digest=digest(index_bytes),
                root_digest_header=digest(index_bytes),
                manifest_bytes=child_bytes,
                manifest_digest_header=digest(child_bytes),
            )

    def test_runtime_image_id_accepts_only_authenticated_manifest_or_config(self):
        trust = self.verify()
        for image_id in (
            self.image_repository + "@" + self.root_digest,
            self.image_repository + ":main",
            "docker://sha256:" + "9" * 64,
            "evil.invalid/image@" + self.child_digest,
        ):
            with self.assertRaisesRegex(ValueError, "runtime imageID"):
                self.oci.normalize_runtime_image_id(image_id, trust)

    def test_single_manifest_root_is_supported_without_fabricating_a_child(self):
        result = self.oci.validate_oci_image(
            repository=self.repository,
            source_sha=self.source_sha,
            image_repository=self.image_repository,
            expected_root_digest=self.child_digest,
            root_bytes=self.child,
            root_content_type="application/vnd.oci.image.manifest.v1+json",
            root_digest_header=self.child_digest,
            manifest_bytes=self.child,
            manifest_content_type="application/vnd.oci.image.manifest.v1+json",
            manifest_digest_header=self.child_digest,
            config_bytes=self.config,
        )
        self.assertEqual(result["root_digest"], result["manifest_digest"])


if __name__ == "__main__":
    unittest.main()
