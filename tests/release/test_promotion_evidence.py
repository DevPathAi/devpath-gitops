import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / "scripts" / "release"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location(
    "verify_promotion_evidence", SCRIPT_DIR / "verify_promotion_evidence.py"
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class ProductionCanaryEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.candidate = json.loads(
            (ROOT / "tests/release/fixtures/valid-candidate-spec.json").read_text(
                encoding="utf-8"
            )
        )
        self.release_id = self.candidate["release_id"]
        self.candidate_hash = "a" * 64
        self.release_hash = "b" * 64
        self.commits = {
            "base_commit": self.candidate["gitops"]["base_sha"],
            "migration_commit": "1" * 40,
            "services_commit": "2" * 40,
            "off_commit": "3" * 40,
            "on_commit": "4" * 40,
        }
        self.run = {
            "id": 81,
            "run_attempt": 1,
            "head_sha": "5" * 40,
        }
        services = {}
        for index, name in enumerate(module.SERVICE_NAMES):
            binding = self.candidate["services"][name]
            services[name] = {
                "source_sha": binding["source_sha"],
                "image_repository": binding["image_repository"],
                "image_digest": binding["image_digest"],
                "manifest_digest": f"sha256:{index + 1:064x}",
                "config_digest": f"sha256:{index + 101:064x}",
            }
        runtime_services = {}
        for index, name in enumerate(module.SERVICE_NAMES):
            runtime_services[name] = {
                "service": name,
                "application_observed_revision": self.commits["on_commit"],
                "application_applied_revision": self.commits["services_commit"],
                "deployment_uid": f"deployment-{name}",
                "root_digest": services[name]["image_digest"],
                "manifest_digest": services[name]["manifest_digest"],
                "config_digest": services[name]["config_digest"],
                "pods": [
                    {
                        "pod_uid": f"pod-{name}",
                        "runtime_image_digest": services[name]["manifest_digest"],
                        "runtime_image_form": "linux-amd64-manifest",
                    }
                ],
            }
        migration = self.candidate["shared_migration"]
        self.payload = {
            "schema_version": 1,
            "document_type": "mission-spine-production-canary",
            "release_id": self.release_id,
            "candidate_spec_sha256": self.candidate_hash,
            "status": "passed",
            **self.commits,
            "migration_image": {
                "source_sha": migration["source_sha"],
                "image_repository": migration["image_repository"],
                "image_digest": migration["image_digest"],
            },
            "services": services,
            "service_runtime": {
                "schema_version": 1,
                "status": "passed",
                "services_commit": self.commits["services_commit"],
                "observed_commit": self.commits["on_commit"],
                "services": runtime_services,
            },
            "sync_detection_seconds": 5,
            "canary_seconds": 900,
            "promoter_repository": module.REPOSITORY,
            "promoter_workflow_path": module.WORKFLOW_PATH,
            "promoter_workflow_sha256": "c" * 64,
            "promoter_run_id": 81,
            "promoter_run_attempt": 1,
            "promoter_head_sha": "5" * 40,
            "release_manifest_sha256": self.release_hash,
            "candidate_spec_path": f"release-manifests/candidates/{self.release_id}.candidate-spec.json",
        }

    def validate(self, payload=None):
        value = self.payload if payload is None else payload
        raw = (json.dumps(value, separators=(",", ":")) + "\n").encode()
        state = {"phase": "mission-on", **self.commits}
        with mock.patch.object(module, "inspect_chain", return_value=state), mock.patch.object(
            module, "_git", return_value=self.commits["services_commit"]
        ):
            return module.validate_promotion_payload(
                value,
                raw,
                ROOT,
                self.release_id,
                self.candidate,
                self.candidate_hash,
                self.release_hash,
                self.run,
                "c" * 64,
            )

    def test_exact_full_chain_and_nine_service_payload_passes(self):
        result = self.validate()
        self.assertEqual(result["on_commit"], "4" * 40)
        self.assertEqual(tuple(result["services"]), module.SERVICE_NAMES)

    def test_chain_service_and_canonical_byte_mutations_fail(self):
        mutations = []
        chain = copy.deepcopy(self.payload)
        chain["services_commit"] = "9" * 40
        mutations.append(chain)
        service = copy.deepcopy(self.payload)
        service["services"][module.SERVICE_NAMES[0]]["source_sha"] = "9" * 40
        mutations.append(service)
        runtime = copy.deepcopy(self.payload)
        runtime["service_runtime"]["services"][module.SERVICE_NAMES[0]]["pods"][0][
            "runtime_image_digest"
        ] = "sha256:" + "9" * 64
        mutations.append(runtime)
        legacy_form = copy.deepcopy(self.payload)
        legacy_form["service_runtime"]["services"][module.SERVICE_NAMES[0]]["pods"][0][
            "runtime_image_form"
        ] = "manifest"
        mutations.append(legacy_form)
        extra = copy.deepcopy(self.payload)
        extra["extra"] = True
        mutations.append(extra)
        for payload in mutations:
            with self.subTest(keys=tuple(payload)), self.assertRaises(ValueError):
                self.validate(payload)
        raw = json.dumps(self.payload, indent=2).encode()
        with mock.patch.object(
            module, "inspect_chain", return_value={"phase": "mission-on", **self.commits}
        ), mock.patch.object(
            module, "_git", return_value=self.commits["services_commit"]
        ), self.assertRaisesRegex(ValueError, "canonical"):
            module.validate_promotion_payload(
                self.payload,
                raw,
                ROOT,
                self.release_id,
                self.candidate,
                self.candidate_hash,
                self.release_hash,
                self.run,
                "c" * 64,
            )

    def test_run_scoped_artifact_selection_accepts_history(self):
        sealed = "5" * 40
        runs = []
        for run_id in (80, 81):
            runs.append(
                {
                    "id": run_id,
                    "status": "completed",
                    "conclusion": "success",
                    "event": "workflow_dispatch",
                    "path": module.WORKFLOW_PATH,
                    "head_sha": sealed,
                    "head_branch": "main",
                    "run_attempt": 1,
                    "repository": {"full_name": module.REPOSITORY},
                    "head_repository": {"full_name": module.REPOSITORY},
                }
            )

        def lookup(name):
            run_id = int(name.split("-run-")[1].split("-")[0])
            return [
                {
                    "id": run_id + 1000,
                    "name": name,
                    "expired": False,
                    "workflow_run": {"id": run_id},
                }
            ]

        selected = module.select_canary_runs(runs, {sealed}, self.release_id, lookup)
        self.assertEqual([row[0]["id"] for row in selected], [80, 81])

    def test_duplicate_same_run_artifact_or_attempt_two_fails_closed(self):
        sealed = "5" * 40
        run = {
            "id": 81,
            "status": "completed",
            "conclusion": "success",
            "event": "workflow_dispatch",
            "path": module.WORKFLOW_PATH,
            "head_sha": sealed,
            "head_branch": "main",
            "run_attempt": 1,
            "repository": {"full_name": module.REPOSITORY},
            "head_repository": {"full_name": module.REPOSITORY},
        }
        artifact = {
            "id": 91,
            "name": module.canary_artifact_name(self.release_id, 81),
            "expired": False,
            "workflow_run": {"id": 81},
        }
        with self.assertRaisesRegex(ValueError, "duplicate active"):
            module.select_canary_runs(
                [run], {sealed}, self.release_id, lambda _: [artifact, artifact]
            )
        attempt_two = copy.deepcopy(run)
        attempt_two["run_attempt"] = 2
        self.assertEqual(
            module.select_canary_runs(
                [attempt_two], {sealed}, self.release_id, lambda _: [artifact]
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
