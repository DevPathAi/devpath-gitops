import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.request import Request


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "release" / "fixtures"
SCRIPTS = ROOT / "scripts" / "release"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
WORKFLOWS = [
    ROOT / ".github" / "workflows" / "ci.yml",
    ROOT / ".github" / "workflows" / "mission-spine-validate.yml",
    ROOT / ".github" / "workflows" / "mission-spine-promote.yml",
    ROOT / ".github" / "workflows" / "mission-spine-landing-last.yml",
    ROOT / ".github" / "workflows" / "mission-spine-rollback.yml",
]


def load_module(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReleaseHardeningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate = json.loads(
            (FIXTURES / "valid-candidate-spec.json").read_text(encoding="utf-8")
        )
        cls.release = json.loads((FIXTURES / "valid-release.json").read_text(encoding="utf-8"))
        cls.candidate_hash = hashlib.sha256(
            (FIXTURES / "valid-candidate-spec.json").read_bytes()
        ).hexdigest()
        cls.rollout = load_module("wait_web_rollout.py", "hardened_rollout")
        cls.stager = load_module("stage_web_release.py", "hardened_stager")
        cls.artifacts = load_module("verify_release_artifacts.py", "hardened_artifacts")
        cls.promoter = load_module("set_web_digest.py", "hardened_promoter")
        cls.cloudflare = load_module("cloudflare_pages.py", "hardened_cloudflare")

    def healthy_snapshot(self):
        digest = self.candidate["frontend"]["selected_on_digest"]
        image = f"ghcr.io/devpathai/devpath-web@{digest}"
        commit = "c" * 40
        application = {
            "metadata": {"name": "devpath-web", "namespace": "argocd"},
            "spec": {
                "project": "devpath",
                "source": {
                    "repoURL": "https://github.com/DevPathAi/devpath-gitops.git",
                    "targetRevision": "main",
                    "path": "apps/devpath-web/base",
                },
                "destination": {
                    "server": "https://kubernetes.default.svc",
                    "namespace": "devpath",
                },
            },
            "status": {
                "sync": {"status": "Synced", "revision": commit},
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Succeeded",
                    "syncResult": {"revision": commit},
                },
            },
        }
        deployment = {
            "metadata": {
                "name": "devpath-web",
                "namespace": "devpath",
                "uid": "deployment-uid",
                "generation": 7,
            },
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": "devpath-web"}},
                "template": {"spec": {"containers": [{"name": "devpath-web", "image": image}]}},
            },
            "status": {
                "observedGeneration": 7,
                "replicas": 1,
                "updatedReplicas": 1,
                "readyReplicas": 1,
                "availableReplicas": 1,
                "unavailableReplicas": 0,
            },
        }
        replicasets = {
            "items": [
                {
                    "metadata": {
                        "name": "devpath-web-abc",
                        "namespace": "devpath",
                        "uid": "replicaset-uid",
                        "ownerReferences": [
                            {
                                "apiVersion": "apps/v1",
                                "kind": "Deployment",
                                "name": "devpath-web",
                                "uid": "deployment-uid",
                                "controller": True,
                                "blockOwnerDeletion": True,
                            }
                        ],
                    },
                    "spec": {
                        "replicas": 1,
                        "template": {
                            "spec": {
                                "containers": [
                                    {"name": "devpath-web", "image": image}
                                ]
                            }
                        },
                    },
                    "status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1},
                }
            ]
        }
        pods = {
            "items": [
                {
                    "metadata": {
                        "uid": "pod-uid-1",
                        "name": "web-abc",
                        "deletionTimestamp": None,
                        "ownerReferences": [
                            {
                                "apiVersion": "apps/v1",
                                "kind": "ReplicaSet",
                                "name": "devpath-web-abc",
                                "uid": "replicaset-uid",
                                "controller": True,
                                "blockOwnerDeletion": True,
                            }
                        ],
                    },
                    "spec": {"containers": [{"name": "devpath-web", "image": image}]},
                    "status": {
                        "phase": "Running",
                        "conditions": [{"type": "Ready", "status": "True"}],
                        "containerStatuses": [
                            {
                                "name": "devpath-web",
                                "ready": True,
                                "restartCount": 0,
                                "imageID": f"docker-pullable://{image}",
                                "state": {"running": {"startedAt": "2099-01-01T00:00:00Z"}},
                            }
                        ],
                    },
                }
            ]
        }
        return application, deployment, replicasets, pods, image, commit

    def test_rollout_requires_actual_ready_nonterminating_digest_and_stable_restarts(self):
        application, deployment, replicasets, pods, image, commit = self.healthy_snapshot()
        baseline = {}
        self.rollout.validate_rollout_snapshot(
            application,
            deployment,
            replicasets,
            pods,
            "devpath-web",
            {image},
            image,
            baseline,
            commit,
        )
        self.assertEqual(baseline, {("pod-uid-1", "devpath-web"): 0})

        terminating = copy.deepcopy(pods)
        terminating["items"][0]["metadata"]["deletionTimestamp"] = "2099-01-01T00:01:00Z"
        with self.assertRaisesRegex(ValueError, "terminating"):
            self.rollout.validate_rollout_snapshot(
                application, deployment, replicasets, terminating,
                "devpath-web", {image}, image, baseline, commit
            )
        restarted = copy.deepcopy(pods)
        restarted["items"][0]["status"]["containerStatuses"][0]["restartCount"] = 1
        with self.assertRaisesRegex(ValueError, "restart"):
            self.rollout.validate_rollout_snapshot(
                application, deployment, replicasets, restarted,
                "devpath-web", {image}, image, baseline, commit
            )
        wrong_runtime = copy.deepcopy(pods)
        wrong_runtime["items"][0]["status"]["containerStatuses"][0]["imageID"] = (
            "docker-pullable://ghcr.io/devpathai/devpath-web@sha256:" + "0" * 64
        )
        with self.assertRaisesRegex(ValueError, "imageID"):
            self.rollout.validate_rollout_snapshot(
                application, deployment, replicasets, wrong_runtime,
                "devpath-web", {image}, image, baseline, commit
            )
        not_available = copy.deepcopy(deployment)
        not_available["status"]["availableReplicas"] = 0
        with self.assertRaisesRegex(ValueError, "available"):
            self.rollout.validate_rollout_snapshot(
                application, not_available, replicasets, pods,
                "devpath-web", {image}, image, baseline, commit
            )

        for label, mutated in (
            ("wrong Argo revision", (copy.deepcopy(application), copy.deepcopy(deployment), copy.deepcopy(replicasets), copy.deepcopy(pods))),
            ("wrong applied Argo revision", (copy.deepcopy(application), copy.deepcopy(deployment), copy.deepcopy(replicasets), copy.deepcopy(pods))),
            ("extra Deployment sidecar", (copy.deepcopy(application), copy.deepcopy(deployment), copy.deepcopy(replicasets), copy.deepcopy(pods))),
            ("Pod init container", (copy.deepcopy(application), copy.deepcopy(deployment), copy.deepcopy(replicasets), copy.deepcopy(pods))),
            ("wrong Pod owner", (copy.deepcopy(application), copy.deepcopy(deployment), copy.deepcopy(replicasets), copy.deepcopy(pods))),
        ):
            app, dep, rss, pod_set = mutated
            if label == "wrong Argo revision":
                app["status"]["sync"]["revision"] = "d" * 40
            elif label == "wrong applied Argo revision":
                app["status"]["operationState"]["syncResult"]["revision"] = "d" * 40
            elif label == "extra Deployment sidecar":
                dep["spec"]["template"]["spec"]["containers"].append(
                    {"name": "sidecar", "image": image}
                )
            elif label == "Pod init container":
                pod_set["items"][0]["spec"]["initContainers"] = [
                    {"name": "unexpected", "image": image}
                ]
            else:
                pod_set["items"][0]["metadata"]["ownerReferences"][0]["uid"] = "wrong"
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.rollout.validate_rollout_snapshot(
                    app, dep, rss, pod_set, "devpath-web", {image}, image, {}, commit
                )

    def test_rollout_accepts_candidate_bound_staging_deployment_identity(self):
        application, deployment, replicasets, pods, image, _ = self.healthy_snapshot()
        expected_container = self.stager.build_patch(
            copy.deepcopy(self.candidate), self.candidate_hash, "mission-on"
        )["spec"]["template"]["spec"]["containers"][0]
        deployment["metadata"]["name"] = "devpath-web-staging"
        deployment["metadata"]["namespace"] = "devpath-staging"
        replicasets["items"][0]["metadata"]["ownerReferences"][0]["name"] = (
            "devpath-web-staging"
        )
        for pod_spec in (
            deployment["spec"]["template"]["spec"],
            replicasets["items"][0]["spec"]["template"]["spec"],
            pods["items"][0]["spec"],
        ):
            pod_spec["automountServiceAccountToken"] = False
            pod_spec["containers"] = [copy.deepcopy(expected_container)]

        self.rollout.validate_rollout_snapshot(
            application,
            deployment,
            replicasets,
            pods,
            "devpath-web",
            {image},
            image,
            {},
            None,
            expected_deployment="devpath-web-staging",
            expected_namespace="devpath-staging",
            expected_container_spec=expected_container,
            require_automount_disabled=True,
        )

        deployment["spec"]["template"]["spec"]["containers"][0]["command"] = [
            "/bin/sh",
            "-c",
            "id",
        ]
        with self.assertRaisesRegex(ValueError, "container shape"):
            self.rollout.validate_rollout_snapshot(
                application,
                deployment,
                replicasets,
                pods,
                "devpath-web",
                {image},
                image,
                {},
                None,
                expected_deployment="devpath-web-staging",
                expected_namespace="devpath-staging",
                expected_container_spec=expected_container,
                require_automount_disabled=True,
            )

    def test_wait_rollout_passes_candidate_bound_staging_identity_on_every_poll(self):
        candidate = copy.deepcopy(self.candidate)
        candidate["environments"]["staging"].update(
            {
                "kubernetes_context": "devpath-staging",
                "namespace": "devpath-staging",
                "web_deployment": "devpath-web-staging",
                "web_container": "devpath-web",
                "web_origin": "https://staging-app.13-124-153-105.nip.io",
            }
        )
        snapshot = ({}, {}, {}, {})
        with mock.patch.dict("os.environ", {"KUBECONFIG": "test"}, clear=True), mock.patch.object(
            self.rollout,
            "resolve_release_bundle",
            return_value=(None, None, None, candidate, self.candidate_hash),
        ), mock.patch.object(
            self.rollout, "_snapshot", return_value=snapshot
        ), mock.patch.object(
            self.rollout, "validate_rollout_snapshot"
        ) as validate, mock.patch.object(
            self.rollout.time, "monotonic", side_effect=(0, 1, 1, 1)
        ):
            self.rollout.wait_rollout(
                ROOT, candidate["release_id"], "staging", "prior", 0
            )
        self.assertEqual(validate.call_count, 2)
        expected_container = self.stager.build_patch(
            candidate, self.candidate_hash, "prior"
        )["spec"]["template"]["spec"]["containers"][0]
        for call in validate.call_args_list:
            self.assertEqual(
                call.kwargs,
                {
                    "expected_deployment": "devpath-web-staging",
                    "expected_namespace": "devpath-staging",
                    "expected_container_spec": expected_container,
                    "require_automount_disabled": True,
                },
            )

    def test_synthetic_probe_identity_is_exact_and_release_specific(self):
        digest = self.candidate["frontend"]["selected_on_digest"]
        valid = {
            "release_id": self.candidate["release_id"],
            "candidate_spec_sha256": self.candidate_hash,
            "image_digest": digest,
            "status": "ready",
        }
        self.rollout.validate_synthetic_identity(
            valid, self.candidate["release_id"], self.candidate_hash, digest
        )
        for field, bad in (
            ("release_id", "ms-20990101-other"),
            ("candidate_spec_sha256", "0" * 64),
            ("image_digest", "sha256:" + "0" * 64),
        ):
            invalid = copy.deepcopy(valid)
            invalid[field] = bad
            with self.assertRaises(ValueError):
                self.rollout.validate_synthetic_identity(
                    invalid, self.candidate["release_id"], self.candidate_hash, digest
                )

    def test_prior_probe_uses_the_exact_sealed_prior_lineage(self):
        legacy = copy.deepcopy(self.candidate)
        self.assertIsNone(
            self.rollout._synthetic_identity(legacy, self.candidate_hash, "prior")
        )
        prior = legacy["frontend"]["rollback"]["prior_identity"]
        prior.update(
            {
                "ready": True,
                "release_id": "ms-20981231-prior-release",
                "candidate_spec_sha256": "d" * 64,
                "image_digest": legacy["frontend"]["rollback"]["prior_digest"],
            }
        )
        self.assertEqual(
            self.rollout._synthetic_identity(legacy, self.candidate_hash, "prior"),
            (
                "ms-20981231-prior-release",
                "d" * 64,
                legacy["frontend"]["rollback"]["prior_digest"],
            ),
        )

    def test_release_http_probes_never_follow_redirects(self):
        request = Request(
            "https://app.leva.ai.kr/.well-known/mission-spine",
            headers={"Authorization": "Bearer secret"},
        )
        for module in (self.rollout, self.cloudflare):
            handler = module._NoRedirectHandler()
            self.assertIsNone(
                handler.redirect_request(
                    request,
                    None,
                    302,
                    "Found",
                    {"Location": "https://attacker.invalid/steal"},
                    "https://attacker.invalid/steal",
                )
            )
            source = (SCRIPTS / Path(module.__file__).name).read_text(encoding="utf-8")
            self.assertIn("build_opener(_NoRedirectHandler())", source)
            self.assertNotIn("with urlopen(request", source)

    def test_artifact_run_provenance_is_exact_not_suffix_or_branch_spoofable(self):
        workflow = b"name: exact producer\n"
        reference = {
            "event": "workflow_dispatch",
            "head_sha": "a" * 40,
            "run_attempt": 2,
            "workflow_path": ".github/workflows/et13-evidence.yml",
            "workflow_sha256": hashlib.sha256(workflow).hexdigest(),
        }
        run = {
            "event": "workflow_dispatch",
            "head_sha": "a" * 40,
            "run_attempt": 2,
            "path": ".github/workflows/et13-evidence.yml",
            "status": "completed",
            "conclusion": "success",
        }
        self.artifacts.validate_run_provenance(
            "visual", run, reference, "a" * 40,
            ".github/workflows/et13-evidence.yml", workflow
        )
        mutations = (
            ("event", "push"),
            ("head_sha", "b" * 40),
            ("run_attempt", 3),
            ("path", "evil/.github/workflows/et13-evidence.yml"),
        )
        for field, value in mutations:
            invalid = copy.deepcopy(run)
            invalid[field] = value
            with self.assertRaises(ValueError, msg=field):
                self.artifacts.validate_run_provenance(
                    "visual", invalid, reference, "a" * 40,
                    ".github/workflows/et13-evidence.yml", workflow
                )

    def test_validation_tree_allows_only_direct_candidate_then_final_commits(self):
        def git(root, *args):
            result = subprocess.run(
                ["git", *args], cwd=root, check=True, capture_output=True, text=True
            )
            return result.stdout.strip()

        def make_tree(root, extra_candidate_file=False):
            git(root, "init", "-q")
            git(root, "config", "user.name", "release-test")
            git(root, "config", "user.email", "release-test@example.invalid")
            (root / "trusted.txt").write_text("base\n", encoding="utf-8")
            git(root, "add", "trusted.txt")
            git(root, "commit", "-qm", "base")
            base_sha = git(root, "rev-parse", "HEAD")
            candidate_path = root / "release-manifests" / "candidates" / (
                self.candidate["release_id"] + ".candidate-spec.json"
            )
            candidate_path.parent.mkdir(parents=True)
            candidate_path.write_bytes((FIXTURES / "valid-candidate-spec.json").read_bytes())
            git(root, "add", candidate_path.relative_to(root).as_posix())
            if extra_candidate_file:
                (root / "untrusted.txt").write_text("branch spoof\n", encoding="utf-8")
                git(root, "add", "untrusted.txt")
            git(root, "commit", "-qm", "candidate")
            candidate_sha = git(root, "rev-parse", "HEAD")
            release_path = root / "release-manifests" / "releases" / (
                self.candidate["release_id"] + ".json"
            )
            release_path.parent.mkdir(parents=True)
            release_path.write_bytes((FIXTURES / "valid-release.json").read_bytes())
            git(root, "add", release_path.relative_to(root).as_posix())
            git(root, "commit", "-qm", "final")
            return base_sha, candidate_sha

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_sha, candidate_sha = make_tree(root)
            self.artifacts.verify_validation_tree(
                root, self.candidate["release_id"], base_sha, "f" * 40
            )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_sha, candidate_sha = make_tree(root, extra_candidate_file=True)
            with self.assertRaisesRegex(ValueError, "candidate tree delta"):
                self.artifacts.verify_validation_tree(
                    root, self.candidate["release_id"], base_sha, "f" * 40
                )

    def test_all_production_workflows_share_one_nonpreemptible_lease(self):
        texts = {
            path.name: path.read_text(encoding="utf-8")
            for path in WORKFLOWS
            if path.name.startswith("mission-spine-")
        }
        for name in (
            "mission-spine-promote.yml",
            "mission-spine-landing-last.yml",
            "mission-spine-rollback.yml",
        ):
            self.assertIn("group: mission-spine-production", texts[name])
        for name in (
            "mission-spine-promote.yml",
            "mission-spine-landing-last.yml",
            "mission-spine-rollback.yml",
        ):
            text = texts[name]
            self.assertIn("cancel-in-progress: false", text)
            self.assertNotIn("cancel-in-progress: true", text)
        self.assertIn("--action verify-prior", texts["mission-spine-rollback.yml"])

    def test_staging_lease_queues_every_pending_transition(self):
        workflow_dir = ROOT / ".github" / "workflows"
        validate = (workflow_dir / "mission-spine-validate.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "group: mission-spine-staging\n  queue: max\n  cancel-in-progress: false",
            validate,
        )
        for name in ("mission-spine-promote.yml", "mission-spine-rollback.yml"):
            text = (workflow_dir / name).read_text(encoding="utf-8")
            section = text[text.index("  rebaseline_staging:") :]
            self.assertIn(
                "group: mission-spine-staging\n      queue: max\n"
                "      cancel-in-progress: false",
                section,
            )

    def test_legacy_tag_must_equal_sealed_base_tag_before_digest_mutation(self):
        manifest = copy.deepcopy(self.candidate)
        source = """configMapGenerator:
- name: devpath-web-release-identity
  literals:
  - MISSION_RELEASE_READY=false
  - MISSION_RELEASE_ID=unreleased
  - MISSION_CANDIDATE_SPEC_SHA256=0000000000000000000000000000000000000000000000000000000000000000
  - MISSION_IMAGE_DIGEST=sha256:0000000000000000000000000000000000000000000000000000000000000000
images:
- name: ghcr.io/devpathai/devpath-web
  newName: ghcr.io/devpathai/devpath-web
  newTag: 5c5f3a90f8d3da2523bb1dd13c057655f7b82897-mission-on
"""
        rendered = self.promoter.render_kustomization(
            source,
            manifest,
            "mission-off",
            "base",
            candidate_spec_sha256=self.candidate_hash,
        )
        self.assertIn("digest: " + manifest["frontend"]["mission_off"]["image_digest"], rendered)
        arbitrary = source.replace(
            "5c5f3a90f8d3da2523bb1dd13c057655f7b82897-mission-on", "f" * 40
        )
        with self.assertRaisesRegex(ValueError, "trusted base tag"):
            self.promoter.render_kustomization(
                arbitrary,
                manifest,
                "mission-off",
                "base",
                candidate_spec_sha256=self.candidate_hash,
            )
        promote = (ROOT / ".github/workflows/mission-spine-promote.yml").read_text(encoding="utf-8")
        ordered = [
            "--phase migration",
            "promote_service_digests.py",
            "--phase services",
            "--target mission-off",
            "--phase mission-off --canary-seconds 0",
            "--target mission-on",
        ]
        positions = [promote.index(needle) for needle in ordered]
        self.assertEqual(positions, sorted(positions))

    def test_base_and_rollback_require_the_exact_sealed_prior_identity(self):
        manifest = copy.deepcopy(self.candidate)
        prior_digest = manifest["frontend"]["rollback"]["prior_digest"]
        prior = {
            "ready": True,
            "release_id": "ms-20981231-prior-release",
            "candidate_spec_sha256": "d" * 64,
            "image_digest": prior_digest,
        }
        manifest["frontend"]["rollback"]["prior_identity"] = prior
        source = f"""configMapGenerator:
- name: devpath-web-release-identity
  literals:
  - MISSION_RELEASE_READY=true
  - MISSION_RELEASE_ID={prior['release_id']}
  - MISSION_CANDIDATE_SPEC_SHA256={prior['candidate_spec_sha256']}
  - MISSION_IMAGE_DIGEST={prior['image_digest']}
images:
- name: ghcr.io/devpathai/devpath-web
  newName: ghcr.io/devpathai/devpath-web
  digest: {prior_digest}
"""
        rendered = self.promoter.render_kustomization(
            source,
            manifest,
            "mission-off",
            "base",
            candidate_spec_sha256=self.candidate_hash,
        )
        self.assertIn(
            "MISSION_RELEASE_ID=" + manifest["release_id"], rendered
        )
        forged = source.replace(prior["release_id"], "ms-20981230-forged-prior")
        with self.assertRaisesRegex(ValueError, "base release identity is not exact"):
            self.promoter.render_kustomization(
                forged,
                manifest,
                "mission-off",
                "base",
                candidate_spec_sha256=self.candidate_hash,
            )

        off = self.promoter.render_kustomization(
            source,
            manifest,
            "mission-off",
            "base",
            candidate_spec_sha256=self.candidate_hash,
        )
        restored = self.promoter.render_kustomization(
            off,
            manifest,
            "prior",
            "mission-off",
            candidate_spec_sha256=self.candidate_hash,
        )
        for value in prior.values():
            expected = "true" if value is True else str(value)
            self.assertIn(expected, restored)

    def test_each_evidence_kind_has_exact_schema_and_journey_allowlists(self):
        producer = {"producer_run_id": 501, "producer_run_attempt": 3}
        activation_steps = list(self.artifacts.JOURNEY_ALLOWLISTS["journey-activation"])
        rows = [
            {
                "route": self.artifacts.JOURNEY_ALLOWLISTS["journey-activation"][step][0],
                "step": step,
                "result": "passed",
                "duration_ms": index + 1,
                "candidate_spec_sha256": self.candidate_hash,
            }
            for index, step in enumerate(activation_steps)
        ]
        self.artifacts.validate_evidence_payload(
            "journey-activation", rows, self.candidate_hash, self.candidate
        )
        injected = copy.deepcopy(rows)
        injected[0]["detail"] = "user prompt"
        with self.assertRaisesRegex(ValueError, "key set"):
            self.artifacts.validate_evidence_payload(
                "journey-activation", injected, self.candidate_hash, self.candidate
            )
        wrong_step = copy.deepcopy(rows)
        wrong_step[0]["step"] = "arbitrary-sensitive-detail"
        with self.assertRaisesRegex(ValueError, "step sequence"):
            self.artifacts.validate_evidence_payload(
                "journey-activation", wrong_step, self.candidate_hash, self.candidate
            )

        visual = {
            "candidate_spec_sha256": self.candidate_hash,
            "status": "passed",
            **producer,
            "repository": self.candidate["frontend"]["repository"],
            "source_sha": self.candidate["frontend"]["source_sha"],
            "case_catalog_sha256": self.candidate["quality_evidence_inputs"]["catalogs"]["frontend-visual"]["sha256"],
            "case_catalog_version": self.candidate["quality_evidence_inputs"]["catalogs"]["frontend-visual"]["case_catalog_version"],
            "case_catalog_schema_version": self.candidate["quality_evidence_inputs"]["catalogs"]["frontend-visual"]["case_catalog_schema_version"],
            "projection_contract_sha256": self.candidate["quality_evidence_inputs"]["catalogs"]["frontend-visual"]["projection_contract_sha256"],
            "fixture_ids": self.candidate["quality_evidence_inputs"]["catalogs"]["frontend-visual"]["fixture_ids"],
            "capture_surface": "flutter_web_release_projection",
            "device_evidence": False,
            "evidence_mode": "release_ready",
            "case_count": 96,
            "passed_case_count": 96,
            "failed_case_count": 0,
            "surface_case_counts": {"web": 48, "admin": 16, "mobile": 16, "dp_design": 16},
            "input_provenance_sha256": self.candidate["quality_evidence_inputs"][
                "catalogs"
            ]["frontend-visual"]["input_provenance_sha256"],
            "input_provenance_file_sha256": self.candidate["quality_evidence_inputs"][
                "catalogs"
            ]["frontend-visual"]["input_provenance_file_sha256"],
            "result_manifest_sha256": "e" * 64,
            "baseline_status": "approved",
            "baseline_set_sha256": self.candidate["quality_evidence_inputs"]["catalogs"]["frontend-visual"]["baseline_set_sha256"],
            "baseline_approval_sha256": self.candidate["quality_evidence_inputs"]["catalogs"]["frontend-visual"]["baseline_approval_sha256"],
            "pixel_diff_percent": 0,
        }
        self.artifacts.validate_evidence_payload(
            "frontend-visual", visual, self.candidate_hash, self.candidate, 501, 3
        )
        with self.assertRaisesRegex(ValueError, "producer run attempt"):
            self.artifacts.validate_evidence_payload(
                "frontend-visual", visual, self.candidate_hash, self.candidate, 501, 4
            )
        visual["notes"] = "raw screenshot details"
        with self.assertRaisesRegex(ValueError, "key set"):
            self.artifacts.validate_evidence_payload(
                "frontend-visual", visual, self.candidate_hash, self.candidate
            )

        exact_payloads = {
            "home-dist": {
                "candidate_spec_sha256": self.candidate_hash,
                "status": "passed",
                **producer,
                "producer_run_attempt": 1,
                "home_source_sha": self.candidate["home"]["source_sha"],
                "dist_sha256": self.candidate["home"]["dist_sha256"],
            },
            "privacy-approval": {
                "candidate_spec_sha256": self.candidate_hash,
                "status": "passed",
                **producer,
                "producer_run_attempt": 1,
                "approved_at": "2098-12-31T00:00:00Z",
                **{
                    key: self.candidate["analytics_privacy"][key]
                    for key in (
                        "collection_mode", "region", "project_identity", "retention_days",
                        "access_owner", "deletion_runbook",
                    )
                },
                "approval_environment": "mission-spine-privacy-approval",
                "approval_environment_id": 7001,
                "approval_job_name": "Approve analytics privacy release",
                "approved_by": "release-reviewer",
                "approved_by_id": 7002,
                "approval_effective_at": "2098-12-31T00:00:00Z",
            },
            "frontend-automated-a11y": {
                "candidate_spec_sha256": self.candidate_hash,
                "status": "passed",
                **producer,
                "repository": self.candidate["frontend"]["repository"],
                "source_sha": self.candidate["frontend"]["source_sha"],
                "case_catalog_sha256": self.candidate["quality_evidence_inputs"][
                    "catalogs"
                ]["frontend-automated-a11y"]["sha256"],
                "case_catalog_version": self.candidate["quality_evidence_inputs"][
                    "catalogs"
                ]["frontend-automated-a11y"]["case_catalog_version"],
                "case_catalog_schema_version": self.candidate["quality_evidence_inputs"][
                    "catalogs"
                ]["frontend-automated-a11y"]["case_catalog_schema_version"],
                "projection_contract_sha256": self.candidate["quality_evidence_inputs"][
                    "catalogs"
                ]["frontend-automated-a11y"]["projection_contract_sha256"],
                "fixture_ids": self.candidate["quality_evidence_inputs"]["catalogs"][
                    "frontend-automated-a11y"
                ]["fixture_ids"],
                "capture_surface": "flutter_web_release_projection",
                "device_evidence": False,
                "evidence_mode": "release_ready",
                "case_count": 24,
                "passed_case_count": 24,
                "failed_case_count": 0,
                "surface_case_counts": {"web": 12, "admin": 4, "mobile": 4, "dp_design": 4},
                "input_provenance_sha256": self.candidate["quality_evidence_inputs"][
                    "catalogs"
                ]["frontend-automated-a11y"]["input_provenance_sha256"],
                "input_provenance_file_sha256": self.candidate["quality_evidence_inputs"][
                    "catalogs"
                ]["frontend-automated-a11y"]["input_provenance_file_sha256"],
                "result_manifest_sha256": "e" * 64,
                "standard": "WCAG 2.2 AA",
                "critical_violations": 0,
                "serious_violations": 0,
            },
        }
        for label, payload in exact_payloads.items():
            attempt = 1 if label in {"home-dist", "privacy-approval"} else 3
            self.artifacts.validate_evidence_payload(
                label, payload, self.candidate_hash, self.candidate, 501, attempt
            )
            invalid = copy.deepcopy(payload)
            invalid["detail"] = "free-form material"
            with self.assertRaisesRegex(ValueError, "key set", msg=label):
                self.artifacts.validate_evidence_payload(
                    label, invalid, self.candidate_hash, self.candidate, 501, attempt
                )

    def test_journey_execution_job_is_read_only_and_separate_from_write_seal(self):
        workflow = (ROOT / ".github/workflows/mission-spine-validate.yml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(
            workflow,
            r"candidate-journeys:[\s\S]*?permissions:[\s\S]*?contents: read[\s\S]*?persist-credentials: false",
        )
        journey_block, seal_block = workflow.split("  seal-and-staging:", 1)
        self.assertNotIn("contents: write", journey_block)
        self.assertIn("contents: write", seal_block)
        self.assertNotIn("npm run test:release", seal_block)
        self.assertIn("validator_run_attempt", journey_block)
        self.assertIn("attempt-${{ github.run_attempt }}", journey_block)
        self.assertIn("needs.candidate-journeys.outputs.validator_run_attempt", seal_block)

    def test_cloudflare_marker_and_created_deployment_are_exact(self):
        marker = {
            "release_id": self.candidate["release_id"],
            "candidate_spec_sha256": self.candidate_hash,
            "dist_sha256": self.candidate["home"]["dist_sha256"],
        }
        self.cloudflare.validate_public_marker(
            marker,
            self.candidate["release_id"],
            self.candidate_hash,
            self.candidate["home"]["dist_sha256"],
        )
        marker["extra"] = "unsealed"
        with self.assertRaisesRegex(ValueError, "marker"):
            self.cloudflare.validate_public_marker(
                marker,
                self.candidate["release_id"],
                self.candidate_hash,
                self.candidate["home"]["dist_sha256"],
            )
        workflow = (ROOT / ".github/workflows/mission-spine-landing-last.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("deployment_id=", workflow)
        self.assertIn("--deployment-id", workflow)
        self.assertNotIn("npx --yes wrangler", workflow)
        deploy_block = workflow.split(
            "- name: Deploy or reuse Landing last and capture its exact deployment ID", 1
        )[1].split("- name: Verify exact current deployment CAS", 1)[0]
        self.assertLess(
            deploy_block.index("verify_promotion_evidence.py"),
            deploy_block.index("--action preflight"),
        )
        self.assertLess(
            deploy_block.index("--action preflight"),
            deploy_block.index("wrangler pages deploy"),
        )

        created_id = "33333333-3333-3333-3333-333333333333"
        deployment = {
            "id": created_id,
            "environment": "production",
            "created_on": "2099-01-02T00:00:01Z",
            "latest_stage": {"status": "success"},
            "deployment_trigger": {
                "metadata": {"commit_hash": self.candidate["home"]["source_sha"]}
            },
        }
        with mock.patch.object(
            self.cloudflare,
            "_api",
            return_value={
                "result": {
                    "canonical_deployment": deployment,
                    "production_branch": "develop",
                    "source": {
                        "config": {
                            "production_branch": "develop",
                            "production_deployments_enabled": False,
                        }
                    },
                }
            },
        ) as api:
            self.assertEqual(
                self.cloudflare._current_production(
                    "test-token", "/accounts/a/pages/projects/p/deployments"
                ),
                deployment,
            )
        api.assert_called_once_with(
            "test-token", "GET", "/accounts/a/pages/projects/p"
        )

        # A Direct Upload Pages project has no external Git source at all.
        # This is a stronger sole-writer posture than a connected source with
        # production deployments disabled and must remain sealable.
        with mock.patch.object(
            self.cloudflare,
            "_api",
            return_value={
                "result": {
                    "canonical_deployment": deployment,
                    "production_branch": "develop",
                    "source": None,
                }
            },
        ):
            self.assertEqual(
                self.cloudflare._current_production(
                    "test-token", "/accounts/a/pages/projects/p/deployments"
                ),
                deployment,
            )

        for mutation in (
            {"production_branch": "main"},
            {"source": {"config": {"production_branch": "develop", "production_deployments_enabled": True}}},
            {"source": {}},
        ):
            project = {
                "canonical_deployment": deployment,
                "production_branch": "develop",
                "source": {
                    "config": {
                        "production_branch": "develop",
                        "production_deployments_enabled": False,
                    }
                },
            }
            project.update(mutation)
            with mock.patch.object(
                self.cloudflare, "_api", return_value={"result": project}
            ), self.assertRaisesRegex(ValueError, "disable external"):
                self.cloudflare._current_production(
                    "test-token", "/accounts/a/pages/projects/p/deployments"
                )

        pages = [
            {
                "result": [deployment],
                "result_info": {"page": 1, "per_page": 100, "count": 1, "total_pages": 2},
            },
            {
                "result": [
                    {
                        **deployment,
                        "id": "55555555-5555-5555-5555-555555555555",
                    }
                ],
                "result_info": {"page": 2, "per_page": 100, "count": 1, "total_pages": 2},
            },
        ]
        with mock.patch.object(self.cloudflare, "_api", side_effect=pages) as api:
            self.assertEqual(
                len(
                    self.cloudflare._production_deployments(
                        "test-token", "/accounts/a/pages/projects/p/deployments"
                    )
                ),
                2,
            )
        self.assertEqual(api.call_count, 2)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            with (
                mock.patch.object(
                    self.cloudflare,
                    "resolve_release_bundle",
                    return_value=(None, None, None, self.candidate, self.candidate_hash),
                ),
                mock.patch.object(
                    self.cloudflare,
                    "_production_deployments",
                    return_value=[deployment],
                ),
                mock.patch.object(self.cloudflare, "_current_production", return_value=deployment),
                mock.patch.object(self.cloudflare, "_probe_marker"),
                mock.patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "test-token"}),
            ):
                self.cloudflare.execute(
                    ROOT,
                    self.candidate["release_id"],
                    "capture-new-production",
                    not_before_epoch=4_070_995_200,
                    github_output=output,
                )
            self.assertEqual(output.read_text(encoding="utf-8"), f"deployment_id={created_id}\n")

        prior_id = self.candidate["home"]["prior_production_deployment_id"]
        prior = {
            "id": prior_id,
            "environment": "production",
            "latest_stage": {"status": "success"},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            output.touch()
            with (
                mock.patch.object(
                    self.cloudflare,
                    "resolve_release_bundle",
                    return_value=(None, None, None, self.candidate, self.candidate_hash),
                ),
                mock.patch.object(
                    self.cloudflare,
                    "_deployment",
                    side_effect=[
                        {
                            **deployment,
                            "id": self.candidate["home"]["candidate_deployment_id"],
                            "environment": "preview",
                        },
                        prior,
                        deployment,
                    ],
                ),
                mock.patch.object(
                    self.cloudflare, "_current_production", return_value=deployment
                ),
                mock.patch.object(self.cloudflare, "_probe_marker"),
                mock.patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "test-token"}),
            ):
                self.cloudflare.execute(
                    ROOT,
                    self.candidate["release_id"],
                    "preflight",
                    github_output=output,
                )
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                f"deploy_mode=reuse\ndeployment_id={created_id}\n",
            )

        concurrent = copy.deepcopy(deployment)
        concurrent["id"] = "44444444-4444-4444-4444-444444444444"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            with (
                mock.patch.object(
                    self.cloudflare,
                    "resolve_release_bundle",
                    return_value=(None, None, None, self.candidate, self.candidate_hash),
                ),
                mock.patch.object(
                    self.cloudflare,
                    "_production_deployments",
                    return_value=[deployment, concurrent],
                ),
                mock.patch.object(self.cloudflare, "_current_production", return_value=deployment),
                mock.patch.object(self.cloudflare, "_probe_marker"),
                mock.patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "test-token"}),
            ):
                with self.assertRaisesRegex(ValueError, "exactly one"):
                    self.cloudflare.execute(
                        ROOT,
                        self.candidate["release_id"],
                        "capture-new-production",
                        not_before_epoch=4_070_995_200,
                        github_output=output,
                    )

        foreign = copy.deepcopy(deployment)
        foreign["id"] = "66666666-6666-6666-6666-666666666666"
        foreign["deployment_trigger"]["metadata"]["commit_hash"] = "0" * 40
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            with (
                mock.patch.object(
                    self.cloudflare,
                    "resolve_release_bundle",
                    return_value=(None, None, None, self.candidate, self.candidate_hash),
                ),
                mock.patch.object(
                    self.cloudflare,
                    "_production_deployments",
                    return_value=[foreign, deployment],
                ),
                mock.patch.object(
                    self.cloudflare, "_current_production", return_value=deployment
                ),
                mock.patch.object(self.cloudflare, "_probe_marker"),
                mock.patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "test-token"}),
            ):
                with self.assertRaisesRegex(ValueError, "foreign source"):
                    self.cloudflare.execute(
                        ROOT,
                        self.candidate["release_id"],
                        "capture-new-production",
                        not_before_epoch=4_070_995_200,
                        github_output=output,
                    )

        in_flight = copy.deepcopy(deployment)
        in_flight["latest_stage"] = {"status": "active"}
        canceled = copy.deepcopy(in_flight)
        canceled["latest_stage"] = {"status": "canceled"}
        with (
            mock.patch.object(
                self.cloudflare,
                "resolve_release_bundle",
                return_value=(None, None, None, self.candidate, self.candidate_hash),
            ),
            mock.patch.object(self.cloudflare, "_deployment", return_value=prior),
            mock.patch.object(self.cloudflare, "_current_production", return_value=prior),
            mock.patch.object(
                self.cloudflare,
                "_production_deployments",
                side_effect=[[in_flight], [canceled], [canceled]],
            ) as census,
            mock.patch.object(self.cloudflare, "_probe"),
            mock.patch.object(self.cloudflare.time, "sleep"),
            mock.patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "test-token"}),
        ):
            self.cloudflare.execute(
                ROOT,
                self.candidate["release_id"],
                "rollback-prior",
            )
        self.assertGreaterEqual(census.call_count, 3)

        candidate_production = {
            **deployment,
            "id": created_id,
            "environment": "production",
        }
        with (
            mock.patch.object(
                self.cloudflare,
                "resolve_release_bundle",
                return_value=(None, None, None, self.candidate, self.candidate_hash),
            ),
            mock.patch.object(self.cloudflare, "_deployment", side_effect=[prior, prior]),
            mock.patch.object(
                self.cloudflare,
                "_current_production",
                side_effect=[
                    candidate_production,
                    candidate_production,
                    prior,
                    prior,
                    prior,
                ],
            ),
            mock.patch.object(self.cloudflare, "_wait_production_quiescent"),
            mock.patch.object(
                self.cloudflare, "_api", return_value={"result": prior, "success": True}
            ),
            mock.patch.object(self.cloudflare, "_probe_marker"),
            mock.patch.object(self.cloudflare, "_probe"),
            mock.patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "test-token"}),
        ):
            self.cloudflare.execute(
                ROOT,
                self.candidate["release_id"],
                "rollback-prior",
                deployment_id=created_id,
            )

        with (
            mock.patch.object(
                self.cloudflare,
                "resolve_release_bundle",
                return_value=(None, None, None, self.candidate, self.candidate_hash),
            ),
            mock.patch.object(self.cloudflare, "_deployment", return_value=prior),
            mock.patch.object(
                self.cloudflare, "_current_production", return_value=candidate_production
            ),
            mock.patch.object(self.cloudflare, "_wait_production_quiescent"),
            mock.patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "test-token"}),
            self.assertRaisesRegex(ValueError, "neither exact candidate nor prior"),
        ):
            self.cloudflare.execute(
                ROOT,
                self.candidate["release_id"],
                "rollback-prior",
                deployment_id="77777777-7777-7777-7777-777777777777",
            )

        rollback_workflow = (
            ROOT / ".github/workflows/mission-spine-rollback.yml"
        ).read_text(encoding="utf-8")
        mission_on = rollback_workflow.split("mission-on)", 1)[1].split(";;", 1)[0]
        self.assertIn("--action rollback-prior --deployment-id", mission_on)
        self.assertNotIn("--action verify-new-production", mission_on)

    def test_actions_are_full_sha_pinned_and_wrangler_has_integrity_lock(self):
        action_pin = re.compile(r"^\s*-?\s*uses:\s*[^\s@]+@([0-9a-f]{40})(?:\s+#.*)?$", re.MULTILINE)
        for path in WORKFLOWS:
            text = path.read_text(encoding="utf-8")
            uses_lines = [line for line in text.splitlines() if "uses:" in line]
            self.assertTrue(uses_lines, path.name)
            self.assertEqual(len(action_pin.findall(text)), len(uses_lines), path.name)
        lock_path = ROOT / "tools" / "release-wrangler" / "package-lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(lock["packages"][""]["dependencies"]["wrangler"], "4.123.0")
        for name, package in lock["packages"].items():
            if name and "resolved" in package:
                self.assertRegex(package.get("integrity", ""), r"^sha512-")
        landing = (ROOT / ".github/workflows/mission-spine-landing-last.yml").read_text(
            encoding="utf-8"
        )
        prepare, deploy = landing.split("  landing-last:", 1)
        self.assertNotIn("prepare-wrangler:", prepare)
        self.assertIn("environment: mission-spine-production-landing", deploy)
        self.assertIn("actions/setup-node@", deploy)
        self.assertIn("npm ci --ignore-scripts --no-audit --no-fund", deploy)
        self.assertIn(
            'require(\'./node_modules/wrangler/package.json\').version")" = "4.123.0"',
            deploy,
        )
        self.assertNotRegex(deploy, r"\bnpx\b.*wrangler")

    def test_canonical_composed_source_pins_are_bound_in_candidate_fixture(self):
        expected = {
            "devpath-admin": "dbc1cc9010dea56471e8eec462a0c52cee946d15",
            "devpath-ai-svc": "b7203bcb000edfc0030f77a4c05dd8e1a83f7ce6",
            "devpath-community-svc": "d8bdff0df558e212a4974731d4614c4b626e3264",
            "devpath-gateway": "f55add639992fbe45fcc17adc210eb8e92277885",
            "devpath-lcs-svc": "077a34a5aa0a8e09a0932887b1444fd725f32824",
            "devpath-learning-svc": "c36840e980fe8dee8a80cdca318ab6ca5162cae6",
            "devpath-notification-svc": "91bc1fc8179116d5a660aa3043ad2a10cc13ae3e",
            "devpath-platform-svc": "5c32686814599b2530629fc41ab5e62e805e3442",
            "devpath-sandbox-svc": "a0d440d7ca9250234f681f075ea191275a525139",
        }
        self.assertEqual(
            {name: value["source_sha"] for name, value in self.candidate["services"].items()},
            expected,
        )
        self.assertEqual(
            self.candidate["shared_migration"]["source_sha"],
            "c4d468a70e8870e8f60f25539e91599def75f0f2",
        )
        self.assertEqual(
            self.candidate["shared_migration"]["shared_version"],
            "0.0.1-et11.20260822",
        )
        self.assertEqual(
            self.candidate["shared_migration"]["shared_jar_sha256"],
            "eaab3aa3ad891f7dfeafb084e63d89645978d7716eb0c90a0dda42e0c40dac2e",
        )
        self.assertEqual(
            self.candidate["frontend"]["source_sha"],
            "dbc1cc9010dea56471e8eec462a0c52cee946d15",
        )
        self.assertEqual(
            self.candidate["home"]["source_sha"],
            "dc5f37cb495f99cdfd43c6957db9958ddad7def7",
        )


if __name__ == "__main__":
    unittest.main()
