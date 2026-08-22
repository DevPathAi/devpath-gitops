import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "verify_migration_result.py"


def load_module():
    spec = importlib.util.spec_from_file_location("migration_result_trust", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def canonical(document):
    return (json.dumps(document, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


class MigrationResultTrustTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trust = load_module()

    def setUp(self):
        self.release_id = "ms-20260817-release"
        self.candidate_hash = "a" * 64
        self.release_hash = "b" * 64
        self.sealed_release_sha = "c" * 40
        self.base = "d" * 40
        self.migration_commit = "e" * 40
        self.digest = "sha256:" + "f" * 64
        self.candidate = {
            "release_id": self.release_id,
            "gitops": {"base_sha": self.base},
            "shared_migration": {
                "repository": "DevPathAi/devpath-shared",
                "source_sha": "1" * 40,
                "image_repository": "ghcr.io/devpathai/devpath-migration",
                "image_digest": self.digest,
            },
        }
        self.payload = {
            "schema_version": 1,
            "document_type": "mission-spine-migration-result",
            "release_id": self.release_id,
            "candidate_spec_sha256": self.candidate_hash,
            "release_manifest_sha256": self.release_hash,
            "shared": {
                "repository": "DevPathAi/devpath-shared",
                "source_sha": "1" * 40,
                "workflow_path": ".github/workflows/mission-spine-migration-release.yml",
                "workflow_ref": "DevPathAi/devpath-shared/.github/workflows/mission-spine-migration-release.yml@refs/heads/main",
                "workflow_sha256": "2" * 64,
                "run_id": 123,
                "run_attempt": 1,
                "event_name": "workflow_dispatch",
                "ref": "refs/heads/main",
                "job": "deploy",
                "environment": "mission-spine-migration-release",
            },
            "approval": {
                "environment_id": 456,
                "reviewer_login": "release-reviewer",
                "reviewer_id": 789,
                "reviewer_type": "User",
                "state": "approved",
                "approval_effective_at": "2026-08-17T00:00:00Z",
            },
            "gitops": {
                "repository": "DevPathAi/devpath-gitops",
                "base_sha": self.base,
                "sealed_release_sha": self.sealed_release_sha,
                "pre_push_main_sha": self.base,
                "migration_commit_sha": self.migration_commit,
                "migration_tree_sha": "3" * 40,
                "publish_mode": "published",
                "write_app_slug": "devpath-gitops-release",
                "write_app_id": 1001,
                "write_app_installation_id": 1002,
                "branch": "main",
                "sole_changed_path": "apps/devpath-migration/base/kustomization.yaml",
                "rendered_job_name": "devpath-flyway-migrate-" + "f" * 12 + "-" + "b" * 24,
                "commit_subject": f"deploy(devpath-migration): {self.release_id} sealed {self.release_hash}",
                "commit_author_name": "devpath-gitops-release[bot]",
                "commit_committer_name": "devpath-gitops-release[bot]",
            },
            "migration_image": {
                "repository": "ghcr.io/devpathai/devpath-migration",
                "digest": self.digest,
            },
        }

    def validate(self, payload=None, raw=None):
        value = payload if payload is not None else self.payload
        return self.trust.validate_migration_result_payload(
            value,
            canonical(value) if raw is None else raw,
            self.candidate,
            self.candidate_hash,
            self.release_hash,
            self.sealed_release_sha,
        )

    def test_exact_canonical_payload_passes(self):
        result = self.validate()
        self.assertEqual(result["migration_commit_sha"], self.migration_commit)
        self.assertEqual(result["run_id"], 123)

    def test_key_order_extra_or_noncanonical_bytes_are_rejected(self):
        payload = copy.deepcopy(self.payload)
        payload["extra"] = "no"
        with self.assertRaisesRegex(ValueError, "top-level keys"):
            self.validate(payload)
        payload = {key: self.payload[key] for key in reversed(self.payload)}
        with self.assertRaisesRegex(ValueError, "top-level keys"):
            self.validate(payload)
        with self.assertRaisesRegex(ValueError, "canonical"):
            self.validate(raw=canonical(self.payload).replace(b"\n", b"\r\n"))

    def test_release_source_image_and_job_name_are_exact(self):
        for mutate, message in (
            (lambda value: value["shared"].__setitem__("source_sha", "0" * 40), "shared source"),
            (lambda value: value["migration_image"].__setitem__("digest", "sha256:" + "0" * 64), "image"),
            (lambda value: value["gitops"].__setitem__("rendered_job_name", "devpath-flyway-migrate"), "rendered Job"),
            (lambda value: value.__setitem__("release_manifest_sha256", "0" * 64), "release manifest"),
        ):
            payload = copy.deepcopy(self.payload)
            mutate(payload)
            with self.assertRaisesRegex(ValueError, message):
                self.validate(payload)

    def test_attempt_approval_app_and_publish_mode_rules_are_exact(self):
        payload = copy.deepcopy(self.payload)
        payload["shared"]["run_attempt"] = 2
        with self.assertRaisesRegex(ValueError, "attempt"):
            self.validate(payload)
        payload = copy.deepcopy(self.payload)
        payload["approval"]["reviewer_type"] = "Team"
        with self.assertRaisesRegex(ValueError, "approval"):
            self.validate(payload)
        payload = copy.deepcopy(self.payload)
        payload["gitops"]["write_app_slug"] = "lookalike"
        with self.assertRaisesRegex(ValueError, "write App"):
            self.validate(payload)
        payload = copy.deepcopy(self.payload)
        payload["gitops"]["publish_mode"] = "reused"
        with self.assertRaisesRegex(ValueError, "publish mode"):
            self.validate(payload)
        payload["gitops"]["pre_push_main_sha"] = self.migration_commit
        self.validate(payload)

    def run_document(self, run_id=123, **overrides):
        document = {
            "id": run_id,
            "status": "completed",
            "conclusion": "success",
            "event": "workflow_dispatch",
            "head_sha": self.candidate["shared_migration"]["source_sha"],
            "head_branch": "main",
            "path": ".github/workflows/mission-spine-migration-release.yml",
            "run_attempt": 1,
            "repository": {"full_name": "DevPathAi/devpath-shared"},
            "head_repository": {"full_name": "DevPathAi/devpath-shared"},
        }
        document.update(overrides)
        return document

    def artifact_document(self, run_id=123, artifact_id=456):
        return {
            "id": artifact_id,
            "name": self.trust.migration_result_artifact_name(self.release_id, run_id),
            "expired": False,
            "workflow_run": {"id": run_id},
        }

    def test_unique_attempt_one_current_main_run_and_artifact_are_selected(self):
        run = self.run_document()
        artifact = self.artifact_document()
        selected_run, selected_artifact = self.trust.select_unique_migration_result(
            [self.run_document(run_id=122, run_attempt=2), run],
            self.candidate["shared_migration"]["source_sha"],
            self.release_id,
            lambda name: [artifact] if name == artifact["name"] else [],
        )
        self.assertIs(selected_run, run)
        self.assertIs(selected_artifact, artifact)

    def test_competing_run_duplicate_artifact_and_fork_are_rejected(self):
        runs = [self.run_document(123), self.run_document(124)]
        artifacts = {
            self.trust.migration_result_artifact_name(self.release_id, run["id"]): [
                self.artifact_document(run["id"], 900 + run["id"])
            ]
            for run in runs
        }
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.trust.select_unique_migration_result(
                runs,
                self.candidate["shared_migration"]["source_sha"],
                self.release_id,
                lambda name: artifacts.get(name, []),
            )
        artifact = self.artifact_document()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.trust.select_unique_migration_result(
                [self.run_document()],
                self.candidate["shared_migration"]["source_sha"],
                self.release_id,
                lambda _name: [artifact, copy.deepcopy(artifact)],
            )
        fork = self.run_document(
            head_repository={"full_name": "attacker/devpath-shared"}
        )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.trust.select_unique_migration_result(
                [fork],
                self.candidate["shared_migration"]["source_sha"],
                self.release_id,
                lambda _name: [artifact],
            )

    def test_branch_head_and_protection_are_exact(self):
        branch = {
            "name": "main",
            "protected": True,
            "commit": {"sha": self.candidate["shared_migration"]["source_sha"]},
        }
        self.trust._require_branch(
            branch,
            "main",
            self.candidate["shared_migration"]["source_sha"],
            "test",
        )
        for mutation in (
            {**branch, "protected": False},
            {**branch, "commit": {"sha": "0" * 40}},
        ):
            with self.assertRaises(ValueError):
                self.trust._require_branch(
                    mutation,
                    "main",
                    self.candidate["shared_migration"]["source_sha"],
                    "test",
                )


if __name__ == "__main__":
    unittest.main()
