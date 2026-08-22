import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "verify_kubernetes_release_runtime.py"


def load_module():
    spec = importlib.util.spec_from_file_location("kubernetes_release_runtime", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def trust(name="devpath-ai-svc"):
    return {
        "repository": f"DevPathAi/{name}",
        "source_sha": "a" * 40,
        "image_repository": f"ghcr.io/devpathai/{name}",
        "root_digest": "sha256:" + "1" * 64,
        "manifest_digest": "sha256:" + "2" * 64,
        "config_digest": "sha256:" + "3" * 64,
        "platform": {"os": "linux", "architecture": "amd64"},
        "rootfs_diff_ids": ["sha256:" + "4" * 64],
        "oci_labels": {
            "org.opencontainers.image.source": f"https://github.com/DevPathAi/{name}",
            "org.opencontainers.image.revision": "a" * 40,
        },
    }


def application(name, observed_revision, applied_revision=None):
    applied_revision = applied_revision or observed_revision
    return {
        "metadata": {"name": name, "namespace": "argocd"},
        "spec": {
            "project": "devpath",
            "source": {
                "repoURL": "https://github.com/DevPathAi/devpath-gitops.git",
                "targetRevision": "main",
                "path": f"apps/{name}/base",
            },
            "destination": {
                "server": "https://kubernetes.default.svc",
                "namespace": "devpath",
            },
        },
        "status": {
            "sync": {"status": "Synced", "revision": observed_revision},
            "health": {"status": "Healthy"},
            "operationState": {
                "phase": "Succeeded",
                "syncResult": {"revision": applied_revision},
            },
        },
    }


class KubernetesReleaseRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = load_module()

    def setUp(self):
        self.name = "devpath-ai-svc"
        self.commit = "b" * 40
        self.trust = trust(self.name)
        self.image = self.trust["image_repository"] + "@" + self.trust["root_digest"]
        self.app = application(self.name, self.commit)
        self.deployment = {
            "metadata": {"name": self.name, "namespace": "devpath", "uid": "deploy-uid", "generation": 7},
            "spec": {
                "replicas": 2,
                "selector": {"matchLabels": {"app": self.name}},
                "template": {
                    "metadata": {"labels": {"app": self.name}},
                    "spec": {"containers": [{"name": self.name, "image": self.image}]},
                },
            },
            "status": {
                "observedGeneration": 7,
                "replicas": 2,
                "updatedReplicas": 2,
                "readyReplicas": 2,
                "availableReplicas": 2,
                "unavailableReplicas": 0,
            },
        }
        self.replicasets = {
            "items": [
                {
                    "metadata": {
                        "name": self.name + "-abc",
                        "uid": "rs-uid",
                        "ownerReferences": [
                            {
                                "apiVersion": "apps/v1",
                                "kind": "Deployment",
                                "name": self.name,
                                "uid": "deploy-uid",
                                "controller": True,
                                "blockOwnerDeletion": True,
                            }
                        ],
                    },
                    "spec": {
                        "replicas": 2,
                        "template": {"spec": {"containers": [{"name": self.name, "image": self.image}]}},
                    },
                    "status": {"replicas": 2, "readyReplicas": 2, "availableReplicas": 2},
                }
            ]
        }
        self.pods = {"items": []}
        for index, runtime_digest in enumerate(
            (self.trust["manifest_digest"], self.trust["config_digest"])
        ):
            self.pods["items"].append(
                {
                    "metadata": {
                        "name": f"{self.name}-{index}",
                        "uid": f"pod-{index}",
                        "labels": {"app": self.name},
                        "ownerReferences": [
                            {
                                "apiVersion": "apps/v1",
                                "kind": "ReplicaSet",
                                "name": self.name + "-abc",
                                "uid": "rs-uid",
                                "controller": True,
                                "blockOwnerDeletion": True,
                            }
                        ],
                    },
                    "spec": {"containers": [{"name": self.name, "image": self.image}]},
                    "status": {
                        "phase": "Running",
                        "conditions": [{"type": "Ready", "status": "True"}],
                        "containerStatuses": [
                            {
                                "name": self.name,
                                "image": self.image,
                                "imageID": "containerd://" + runtime_digest,
                                "ready": True,
                                "restartCount": 0,
                                "state": {"running": {"startedAt": "2026-08-17T00:00:00Z"}},
                            }
                        ],
                    },
                }
            )

    def test_exact_application_deployment_rs_pods_and_image_ids_pass(self):
        result = self.runtime.validate_service_runtime(
            self.app,
            self.deployment,
            self.replicasets,
            self.pods,
            self.name,
            self.commit,
            self.commit,
            self.trust,
        )
        self.assertEqual(result["deployment_uid"], "deploy-uid")
        self.assertEqual(result["application_observed_revision"], self.commit)
        self.assertEqual(result["application_applied_revision"], self.commit)
        self.assertEqual(
            {item["runtime_image_form"] for item in result["pods"]},
            {"linux-amd64-manifest", "config"},
        )

    def test_monorepo_application_binds_current_and_last_applied_revisions(self):
        observed = "c" * 40
        applied = self.commit
        app = application(self.name, observed, applied)
        result = self.runtime.validate_service_runtime(
            app,
            self.deployment,
            self.replicasets,
            self.pods,
            self.name,
            observed,
            applied,
            self.trust,
        )
        self.assertEqual(result["application_observed_revision"], observed)
        self.assertEqual(result["application_applied_revision"], applied)

        for target, key in (("sync", "revision"), ("syncResult", "revision")):
            changed = copy.deepcopy(app)
            if target == "sync":
                changed["status"]["sync"][key] = "d" * 40
            else:
                changed["status"]["operationState"]["syncResult"][key] = "d" * 40
            with self.subTest(target=target), self.assertRaisesRegex(
                ValueError, "Argo Application revision"
            ):
                self.runtime.validate_service_runtime(
                    changed,
                    self.deployment,
                    self.replicasets,
                    self.pods,
                    self.name,
                    observed,
                    applied,
                    self.trust,
                )

    def test_wrong_argo_revision_path_or_runtime_owner_fails(self):
        app = copy.deepcopy(self.app)
        app["status"]["sync"]["revision"] = "c" * 40
        with self.assertRaisesRegex(ValueError, "Argo.*revision"):
            self.runtime.validate_service_runtime(
                app, self.deployment, self.replicasets, self.pods, self.name, self.commit, self.commit, self.trust
            )
        app = copy.deepcopy(self.app)
        app["spec"]["source"]["path"] = "apps/other/base"
        with self.assertRaisesRegex(ValueError, "Argo.*source"):
            self.runtime.validate_service_runtime(
                app, self.deployment, self.replicasets, self.pods, self.name, self.commit, self.commit, self.trust
            )
        for mutation in (
            lambda value: value["spec"].__setitem__("project", "default"),
            lambda value: value["spec"]["destination"].__setitem__("namespace", "other"),
            lambda value: value["spec"]["source"].__setitem__("plugin", {"name": "evil"}),
        ):
            app = copy.deepcopy(self.app)
            mutation(app)
            with self.assertRaisesRegex(ValueError, "Argo Application source"):
                self.runtime.validate_service_runtime(
                    app,
                    self.deployment,
                    self.replicasets,
                    self.pods,
                    self.name,
                    self.commit,
                    self.commit,
                    self.trust,
                )
        pods = copy.deepcopy(self.pods)
        pods["items"][0]["metadata"]["ownerReferences"][0]["uid"] = "stale-rs"
        with self.assertRaisesRegex(ValueError, "owner"):
            self.runtime.validate_service_runtime(
                self.app, self.deployment, self.replicasets, pods, self.name, self.commit, self.commit, self.trust
            )

    def test_tag_root_index_or_partial_rollout_is_rejected(self):
        deployment = copy.deepcopy(self.deployment)
        deployment["spec"]["template"]["spec"]["containers"][0]["image"] = (
            self.trust["image_repository"] + ":main"
        )
        with self.assertRaisesRegex(ValueError, "sealed root"):
            self.runtime.validate_service_runtime(
                self.app, deployment, self.replicasets, self.pods, self.name, self.commit, self.commit, self.trust
            )
        pods = copy.deepcopy(self.pods)
        pods["items"][0]["status"]["containerStatuses"][0]["imageID"] = (
            "containerd://" + self.trust["root_digest"]
        )
        with self.assertRaisesRegex(ValueError, "runtime imageID"):
            self.runtime.validate_service_runtime(
                self.app, self.deployment, self.replicasets, pods, self.name, self.commit, self.commit, self.trust
            )
        deployment = copy.deepcopy(self.deployment)
        deployment["status"]["availableReplicas"] = 1
        with self.assertRaisesRegex(ValueError, "replicas"):
            self.runtime.validate_service_runtime(
                self.app, deployment, self.replicasets, self.pods, self.name, self.commit, self.commit, self.trust
            )
        deployment = copy.deepcopy(self.deployment)
        deployment["spec"]["template"]["spec"]["containers"].append(
            {"name": "sidecar", "image": self.image}
        )
        with self.assertRaisesRegex(ValueError, "exact app container"):
            self.runtime.validate_service_runtime(
                self.app, deployment, self.replicasets, self.pods, self.name, self.commit, self.commit, self.trust
            )
        for location in ("spec", "status"):
            pods = copy.deepcopy(self.pods)
            key = "ephemeralContainers" if location == "spec" else "ephemeralContainerStatuses"
            pods["items"][0][location][key] = [{"name": "debug", "image": self.image}]
            with self.subTest(location=location), self.assertRaisesRegex(
                ValueError, "ephemeral"
            ):
                self.runtime.validate_service_runtime(
                    self.app,
                    self.deployment,
                    self.replicasets,
                    pods,
                    self.name,
                    self.commit,
                    self.commit,
                    self.trust,
                )
        pods = copy.deepcopy(self.pods)
        pods["items"][0]["status"]["containerStatuses"].append(
            {
                "name": "sidecar",
                "image": self.image,
                "imageID": "containerd://" + self.trust["config_digest"],
            }
        )
        with self.assertRaisesRegex(ValueError, "sole exact"):
            self.runtime.validate_service_runtime(
                self.app,
                self.deployment,
                self.replicasets,
                pods,
                self.name,
                self.commit,
                self.commit,
                self.trust,
            )

    def test_exact_derived_migration_job_completion_and_marker_pass(self):
        migration_trust = trust("devpath-migration")
        migration_trust["repository"] = "DevPathAi/devpath-shared"
        migration_trust["oci_labels"]["org.opencontainers.image.source"] = (
            "https://github.com/DevPathAi/devpath-shared"
        )
        release_hash = "a" * 64
        name = (
            "devpath-flyway-migrate-"
            + migration_trust["root_digest"][7:19]
            + "-"
            + release_hash[:24]
        )
        app = application("devpath-migration", self.commit)
        image = migration_trust["image_repository"] + "@" + migration_trust["root_digest"]
        job = {
            "metadata": {
                "name": name,
                "namespace": "devpath",
                "uid": "job-uid",
                "resourceVersion": "123",
                "generation": 1,
                "creationTimestamp": "2026-08-17T00:00:01Z",
            },
            "spec": {
                "suspend": False,
                "backoffLimit": 3,
                "template": {
                    "spec": {
                        "restartPolicy": "Never",
                        "initContainers": [
                            {
                                "name": "sandbox-low-lock-preflight",
                                "image": self.runtime.MIGRATION_PREFLIGHT_IMAGE,
                                "command": self.runtime.MIGRATION_PREFLIGHT_COMMAND,
                            }
                        ],
                        "containers": [
                            {
                                "name": "flyway",
                                "image": image,
                                "env": [
                                    {"name": "TARGET_FLYWAY_VERSION", "value": "202608201002"}
                                ],
                                "args": [
                                    "test -f /flyway/sql/V202608201002__validate_community_content_soft_delete.sql\n"
                                    "printf 'mission-spine-flyway-target=%s status=validated\\n' \"$TARGET_FLYWAY_VERSION\"\n"
                                ],
                            }
                        ],
                    }
                },
            },
            "status": {
                "active": 0,
                "failed": 0,
                "succeeded": 1,
                "conditions": [{"type": "Complete", "status": "True"}],
                "completionTime": "2026-08-17T00:01:00Z",
            },
        }
        pod = {
            "metadata": {
                "name": name + "-pod",
                "uid": "migration-pod",
                "ownerReferences": [
                    {
                        "apiVersion": "batch/v1",
                        "kind": "Job",
                        "name": name,
                        "uid": "job-uid",
                        "controller": True,
                        "blockOwnerDeletion": True,
                    }
                ],
            },
            "spec": {
                "restartPolicy": "Never",
                "initContainers": copy.deepcopy(
                    job["spec"]["template"]["spec"]["initContainers"]
                ),
                "containers": copy.deepcopy(
                    job["spec"]["template"]["spec"]["containers"]
                ),
            },
            "status": {
                "phase": "Succeeded",
                "initContainerStatuses": [
                    {
                        "name": "sandbox-low-lock-preflight",
                        "image": self.runtime.MIGRATION_PREFLIGHT_IMAGE,
                        "imageID": "containerd://"
                        + self.runtime.MIGRATION_PREFLIGHT_CONFIG_DIGEST,
                        "restartCount": 0,
                        "state": {"terminated": {"exitCode": 0, "reason": "Completed"}},
                    }
                ],
                "containerStatuses": [
                    {
                        "name": "flyway",
                        "image": image,
                        "imageID": "containerd://" + migration_trust["config_digest"],
                        "ready": False,
                        "restartCount": 0,
                        "state": {"terminated": {"exitCode": 0, "reason": "Completed"}},
                    }
                ],
            },
        }
        result = self.runtime.validate_migration_runtime(
            app,
            job,
            {"items": [pod]},
            "mission-spine-flyway-target=202608201002 status=validated\n",
            self.commit,
            release_hash,
            migration_trust,
            "202608201002",
            "V202608201002__validate_community_content_soft_delete.sql",
            "2026-08-17T00:00:00Z",
        )
        self.assertEqual(result["job"], name)
        self.assertEqual(result["status"], "passed")

        for mutation, message in (
            (lambda value: value["metadata"].__setitem__("name", "devpath-flyway-migrate"), "identity"),
            (lambda value: value["status"].__setitem__("failed", 1), "completion"),
            (lambda value: value["metadata"].__setitem__("uid", "stale"), "owner"),
        ):
            changed = copy.deepcopy(job)
            changed_pods = {"items": [copy.deepcopy(pod)]}
            mutation(changed)
            with self.assertRaisesRegex(ValueError, message):
                self.runtime.validate_migration_runtime(
                    app,
                    changed,
                    changed_pods,
                    "mission-spine-flyway-target=202608201002 status=validated\n",
                    self.commit,
                    release_hash,
                    migration_trust,
                    "202608201002",
                    "V202608201002__validate_community_content_soft_delete.sql",
                    "2026-08-17T00:00:00Z",
                )

        suspended = copy.deepcopy(job)
        suspended["spec"]["suspend"] = True
        with self.assertRaisesRegex(ValueError, "spec"):
            self.runtime.validate_migration_runtime(
                app,
                suspended,
                {"items": [pod]},
                "mission-spine-flyway-target=202608201002 status=validated\n",
                self.commit,
                release_hash,
                migration_trust,
                "202608201002",
                "V202608201002__validate_community_content_soft_delete.sql",
                "2026-08-17T00:00:00Z",
            )

        for field, value in (
            ("image", "postgres:17-alpine"),
            ("command", ["/bin/sh", "-c", "true"]),
        ):
            changed = copy.deepcopy(job)
            changed["spec"]["template"]["spec"]["initContainers"][0][field] = value
            with self.assertRaisesRegex(ValueError, "preflight image/command"):
                self.runtime.validate_migration_runtime(
                    app,
                    changed,
                    {"items": [pod]},
                    "mission-spine-flyway-target=202608201002 status=validated\n",
                    self.commit,
                    release_hash,
                    migration_trust,
                    "202608201002",
                    "V202608201002__validate_community_content_soft_delete.sql",
                    "2026-08-17T00:00:00Z",
                )

        changed_pod = copy.deepcopy(pod)
        changed_pod["spec"]["containers"][0]["args"] = ["echo spoofed"]
        with self.assertRaisesRegex(ValueError, "Pod spec"):
            self.runtime.validate_migration_runtime(
                app,
                job,
                {"items": [changed_pod]},
                "mission-spine-flyway-target=202608201002 status=validated\n",
                self.commit,
                release_hash,
                migration_trust,
                "202608201002",
                "V202608201002__validate_community_content_soft_delete.sql",
                "2026-08-17T00:00:00Z",
            )

        for target, field in (
            ("job-init", "initContainers"),
            ("job-app", "containers"),
            ("pod-init", "initContainers"),
            ("pod-app", "containers"),
        ):
            changed_job = copy.deepcopy(job)
            changed_pod = copy.deepcopy(pod)
            if target.startswith("job"):
                changed_job["spec"]["template"]["spec"][field].append(
                    {"name": "sidecar", "image": image}
                )
            else:
                changed_pod["spec"][field].append({"name": "sidecar", "image": image})
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.runtime.validate_migration_runtime(
                    app,
                    changed_job,
                    {"items": [changed_pod]},
                    "mission-spine-flyway-target=202608201002 status=validated\n",
                    self.commit,
                    release_hash,
                    migration_trust,
                    "202608201002",
                    "V202608201002__validate_community_content_soft_delete.sql",
                    "2026-08-17T00:00:00Z",
                )

        for location in ("spec", "status"):
            changed_pod = copy.deepcopy(pod)
            key = "ephemeralContainers" if location == "spec" else "ephemeralContainerStatuses"
            changed_pod[location][key] = [{"name": "debug", "image": image}]
            with self.subTest(location=location), self.assertRaisesRegex(
                ValueError, "ephemeral"
            ):
                self.runtime.validate_migration_runtime(
                    app,
                    job,
                    {"items": [changed_pod]},
                    "mission-spine-flyway-target=202608201002 status=validated\n",
                    self.commit,
                    release_hash,
                    migration_trust,
                    "202608201002",
                    "V202608201002__validate_community_content_soft_delete.sql",
                    "2026-08-17T00:00:00Z",
                )

        changed_pod = copy.deepcopy(pod)
        changed_pod["status"]["initContainerStatuses"][0]["imageID"] = (
            "containerd://sha256:" + "0" * 64
        )
        with self.assertRaisesRegex(ValueError, "preflight runtime imageID"):
            self.runtime.validate_migration_runtime(
                app,
                job,
                {"items": [changed_pod]},
                "mission-spine-flyway-target=202608201002 status=validated\n",
                self.commit,
                release_hash,
                migration_trust,
                "202608201002",
                "V202608201002__validate_community_content_soft_delete.sql",
                "2026-08-17T00:00:00Z",
            )

        with self.assertRaisesRegex(ValueError, "marker"):
            self.runtime.validate_migration_runtime(
                app,
                job,
                {"items": [pod]},
                "flyway complete\n",
                self.commit,
                release_hash,
                migration_trust,
                "202608201002",
                "V202608201002__validate_community_content_soft_delete.sql",
                "2026-08-17T00:00:00Z",
            )


if __name__ == "__main__":
    unittest.main()
