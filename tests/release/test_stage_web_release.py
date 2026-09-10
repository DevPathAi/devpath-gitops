import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "release"
FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-candidate-spec.json"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_module():
    spec = importlib.util.spec_from_file_location(
        "stage_web_release", SCRIPTS / "stage_web_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class StageWebReleaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()
        cls.candidate = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.candidate_hash = "c" * 64

    def isolated_candidate(self):
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
        return candidate

    def released_prior_candidate(self):
        candidate = self.isolated_candidate()
        candidate["frontend"]["rollback"]["prior_identity"] = {
            "ready": True,
            "release_id": "ms-20981231-prior-release",
            "candidate_spec_sha256": "a" * 64,
            "image_digest": candidate["frontend"]["rollback"]["prior_digest"],
        }
        return candidate

    def container(self, phase):
        patch = self.module.build_patch(
            self.isolated_candidate(), self.candidate_hash, phase
        )
        return patch["spec"]["template"]["spec"]["containers"][0]

    def test_off_and_on_atomically_bind_image_and_exact_release_identity(self):
        for phase, digest in (
            ("mission-off", self.candidate["frontend"]["mission_off"]["image_digest"]),
            ("mission-on", self.candidate["frontend"]["selected_on_digest"]),
        ):
            with self.subTest(phase=phase):
                container = self.container(phase)
                self.assertEqual(
                    container["image"], f"{self.module.WEB_IMAGE}@{digest}"
                )
                env = {item["name"]: item for item in container["env"]}
                self.assertEqual(env["MISSION_RELEASE_READY"]["value"], "true")
                self.assertEqual(
                    env["MISSION_RELEASE_ID"]["value"], self.candidate["release_id"]
                )
                self.assertEqual(
                    env["MISSION_CANDIDATE_SPEC_SHA256"]["value"],
                    self.candidate_hash,
                )
                self.assertEqual(env["MISSION_IMAGE_DIGEST"]["value"], digest)
                self.assertEqual(
                    env["MISSION_SYNTHETIC_PROBE_TOKEN"]["valueFrom"],
                    {
                        "secretKeyRef": {
                            "name": "mission-spine-synthetic-probe",
                            "key": "token",
                        }
                    },
                )

    def test_prior_restores_the_sealed_prior_identity_with_the_exact_prior_image(self):
        candidate = self.released_prior_candidate()
        patch = self.module.build_patch(candidate, self.candidate_hash, "prior")
        container = patch["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(
            container["image"],
            f"{self.module.WEB_IMAGE}@{candidate['frontend']['rollback']['prior_digest']}",
        )
        env = {item["name"]: item for item in container["env"]}
        prior = candidate["frontend"]["rollback"]["prior_identity"]
        self.assertEqual(env["MISSION_RELEASE_READY"]["value"], "true")
        self.assertEqual(env["MISSION_RELEASE_ID"]["value"], prior["release_id"])
        self.assertEqual(
            env["MISSION_CANDIDATE_SPEC_SHA256"]["value"],
            prior["candidate_spec_sha256"],
        )
        self.assertEqual(
            env["MISSION_IMAGE_DIGEST"]["value"], prior["image_digest"]
        )

    def test_atomic_transition_binds_observed_resource_version(self):
        candidate = self.isolated_candidate()
        current = self.module.build_patch(candidate, self.candidate_hash, "prior")
        current["metadata"] = {
            "name": "devpath-web-staging",
            "namespace": "devpath-staging",
            "resourceVersion": "314159",
        }
        patch = self.module.build_cas_patch(
            current,
            candidate,
            self.candidate_hash,
            "mission-off",
            "prior",
        )
        self.assertEqual(patch["metadata"], {"resourceVersion": "314159"})

        current["spec"]["template"]["spec"]["containers"][0]["image"] = (
            self.module.WEB_IMAGE + "@sha256:" + "f" * 64
        )
        with self.assertRaisesRegex(ValueError, "current staging phase"):
            self.module.build_cas_patch(
                current,
                candidate,
                self.candidate_hash,
                "mission-off",
                "prior",
            )

    def test_atomic_transition_rejects_container_execution_drift(self):
        candidate = self.isolated_candidate()
        for field, value in (
            ("command", ["/bin/sh", "-c", "id"]),
            ("securityContext", {"privileged": True}),
        ):
            current = self.module.build_patch(candidate, self.candidate_hash, "prior")
            current["metadata"] = {
                "name": "devpath-web-staging",
                "namespace": "devpath-staging",
                "resourceVersion": "314159",
            }
            current["spec"]["template"]["spec"]["containers"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, "container shape"
            ):
                self.module.build_cas_patch(
                    current,
                    candidate,
                    self.candidate_hash,
                    "mission-off",
                    "prior",
                )

        current = self.module.build_patch(candidate, self.candidate_hash, "prior")
        current["metadata"] = {
            "name": "devpath-web-staging",
            "namespace": "devpath-staging",
            "resourceVersion": "314159",
        }
        current["spec"]["template"]["spec"]["automountServiceAccountToken"] = True
        with self.assertRaisesRegex(ValueError, "Pod shape"):
            self.module.build_cas_patch(
                current,
                candidate,
                self.candidate_hash,
                "mission-off",
                "prior",
            )

    def test_stage_gets_then_patches_with_the_same_resource_version(self):
        candidate = self.isolated_candidate()
        current = self.module.build_patch(candidate, self.candidate_hash, "prior")
        current["metadata"] = {
            "name": "devpath-web-staging",
            "namespace": "devpath-staging",
            "resourceVersion": "271828",
        }
        completed = [
            mock.Mock(returncode=0, stdout=json.dumps(current)),
            mock.Mock(returncode=0, stdout="patched"),
        ]
        with mock.patch.dict("os.environ", {"KUBECONFIG": "test"}), mock.patch.object(
            self.module.shutil, "which", return_value="kubectl"
        ), mock.patch.object(
            self.module, "resolve_release_bundle", return_value=(None, None, None, candidate, self.candidate_hash)
        ), mock.patch.object(
            self.module.subprocess, "run", side_effect=completed
        ) as run:
            self.module.stage(
                ROOT, candidate["release_id"], "mission-off", "prior"
            )
        self.assertEqual(run.call_count, 2)
        self.assertIn("get", run.call_args_list[0].args[0])
        self.assertIn("patch", run.call_args_list[1].args[0])
        patch_arg = run.call_args_list[1].args[0]
        patch = json.loads(patch_arg[patch_arg.index("--patch") + 1])
        self.assertEqual(patch["metadata"]["resourceVersion"], "271828")

    def test_stage_fails_closed_before_or_at_each_kubectl_boundary(self):
        candidate = self.isolated_candidate()
        with mock.patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(
            ValueError, "KUBECONFIG"
        ):
            self.module.stage(ROOT, candidate["release_id"], "mission-off", "prior")
        with mock.patch.dict("os.environ", {"KUBECONFIG": "test"}), mock.patch.object(
            self.module.shutil, "which", return_value=None
        ), self.assertRaisesRegex(ValueError, "kubectl"):
            self.module.stage(ROOT, candidate["release_id"], "mission-off", "prior")

        bundle = (None, None, None, candidate, self.candidate_hash)
        for results, message in (
            ([mock.Mock(returncode=1, stdout="")], "read failed"),
            (
                [
                    mock.Mock(
                        returncode=0,
                        stdout=json.dumps(
                            {
                                **self.module.build_patch(
                                    candidate, self.candidate_hash, "prior"
                                ),
                                "metadata": {
                                    "name": "devpath-web-staging",
                                    "namespace": "devpath-staging",
                                    "resourceVersion": "9",
                                },
                            }
                        ),
                    ),
                    mock.Mock(returncode=1, stdout=""),
                ],
                "atomic staging web patch failed",
            ),
        ):
            with self.subTest(message=message), mock.patch.dict(
                "os.environ", {"KUBECONFIG": "test"}
            ), mock.patch.object(
                self.module.shutil, "which", return_value="kubectl"
            ), mock.patch.object(
                self.module, "resolve_release_bundle", return_value=bundle
            ), mock.patch.object(
                self.module.subprocess, "run", side_effect=results
            ), self.assertRaisesRegex(ValueError, message):
                self.module.stage(
                    ROOT, candidate["release_id"], "mission-off", "prior"
                )

    def test_two_releases_rebaseline_the_next_exact_prior_lineage(self):
        first = self.isolated_candidate()
        first_hash = "1" * 64
        deployment = self.module.build_patch(first, first_hash, "prior")
        deployment["metadata"] = {
            "name": "devpath-web-staging",
            "namespace": "devpath-staging",
            "resourceVersion": "100",
        }
        rebaseline = self.module.build_cas_patch(
            deployment, first, first_hash, "mission-on", "prior"
        )
        deployment = {
            "metadata": {
                "name": "devpath-web-staging",
                "namespace": "devpath-staging",
                "resourceVersion": "101",
            },
            "spec": rebaseline["spec"],
        }

        second = json.loads(
            json.dumps(first).replace(
                first["release_id"], "ms-20990102-contract-fixture"
            )
        )
        prior_digest = first["frontend"]["selected_on_digest"]
        second["gitops"]["base_web_digest"] = prior_digest
        second["frontend"]["rollback"]["prior_digest"] = prior_digest
        second["frontend"]["rollback"]["prior_identity"] = {
            "ready": True,
            "release_id": first["release_id"],
            "candidate_spec_sha256": first_hash,
            "image_digest": prior_digest,
        }
        second["frontend"]["mission_off"]["image_digest"] = "sha256:" + "d" * 64
        second["frontend"]["rollback"]["mission_off_digest"] = "sha256:" + "d" * 64
        second["frontend"]["mission_on"]["image_digest"] = "sha256:" + "e" * 64
        second["frontend"]["selected_on_digest"] = "sha256:" + "e" * 64
        transition = self.module.build_cas_patch(
            deployment, second, "2" * 64, "mission-off", "prior"
        )
        self.assertEqual(transition["metadata"]["resourceVersion"], "101")

    def test_staging_kustomize_runtime_matches_the_candidate_contract(self):
        binary = shutil.which("kubectl")
        if binary is None:
            self.skipTest("kubectl is unavailable")
        rendered = subprocess.run(
            [binary, "kustomize", str(ROOT / "staging" / "devpath-web")],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        documents = {
            (document["kind"], document["metadata"]["name"]): document
            for document in yaml.safe_load_all(rendered)
        }
        deployment = documents[("Deployment", "devpath-web-staging")]
        service = documents[("Service", "devpath-web-staging")]
        ingress = documents[("Ingress", "devpath-web-staging")]
        self.assertEqual(deployment["metadata"]["namespace"], "devpath-staging")
        self.assertEqual(service["metadata"]["namespace"], "devpath-staging")
        self.assertEqual(ingress["metadata"]["namespace"], "devpath-staging")
        containers = deployment["spec"]["template"]["spec"]["containers"]
        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0], self.container("prior"))
        self.assertIs(
            deployment["spec"]["template"]["spec"]["automountServiceAccountToken"],
            False,
        )
        self.assertEqual(
            ingress["spec"]["rules"][0]["host"],
            "staging-app.13-124-153-105.nip.io",
        )
        self.assertEqual(
            service["spec"]["selector"],
            deployment["spec"]["selector"]["matchLabels"],
        )

        default_deny = documents[
            ("NetworkPolicy", "devpath-web-staging-default-deny")
        ]
        self.assertEqual(
            default_deny["spec"]["podSelector"],
            {"matchLabels": {"app": "devpath-web-staging"}},
        )
        self.assertEqual(default_deny["spec"]["policyTypes"], ["Ingress", "Egress"])
        self.assertNotIn("ingress", default_deny["spec"])
        self.assertNotIn("egress", default_deny["spec"])

        allow_ingress = documents[("NetworkPolicy", "devpath-web-staging-ingress")]
        self.assertEqual(
            allow_ingress["spec"]["podSelector"],
            {"matchLabels": {"app": "devpath-web-staging"}},
        )
        self.assertEqual(allow_ingress["spec"]["policyTypes"], ["Ingress"])
        self.assertEqual(
            allow_ingress["spec"]["ingress"],
            [
                {
                    "from": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {
                                    "kubernetes.io/metadata.name": "kube-system"
                                }
                            },
                            "podSelector": {
                                "matchLabels": {"app.kubernetes.io/name": "traefik"}
                            },
                        }
                    ],
                    "ports": [{"protocol": "TCP", "port": 8080}],
                }
            ],
        )

    def test_invalid_phase_or_candidate_hash_fails_before_mutation(self):
        with self.assertRaisesRegex(ValueError, "phase"):
            self.module.build_patch(
                self.isolated_candidate(), self.candidate_hash, "unknown"
            )
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.module.build_patch(self.isolated_candidate(), "0", "mission-off")

    def test_only_the_dedicated_staging_runtime_can_be_mutated(self):
        expected = {
            "kubernetes_context": "devpath-staging",
            "namespace": "devpath-staging",
            "web_deployment": "devpath-web-staging",
            "web_container": "devpath-web",
            "web_origin": "https://staging-app.13-124-153-105.nip.io",
        }
        self.module.build_patch(
            self.isolated_candidate(), self.candidate_hash, "mission-off"
        )
        for field in expected:
            candidate = self.isolated_candidate()
            candidate["environments"]["staging"][field] = (
                "https://other.example.test" if field == "web_origin" else "other"
            )
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, r"environments\.staging|dedicated staging identity"
            ):
                self.module.build_patch(
                    candidate, self.candidate_hash, "mission-off"
                )

    def test_prior_cas_must_succeed_before_any_staging_mutation_or_restore(self):
        workflow = (
            ROOT / ".github" / "workflows" / "mission-spine-validate.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("id: prior_cas", workflow)
        self.assertIn(
            "if: always() && steps.kube.outcome == 'success' && "
            "steps.prior_cas.outcome == 'success'",
            workflow,
        )
        prior_step = workflow.split("id: prior_cas", 1)[1].split("      - name:", 1)[0]
        self.assertIn("MISSION_SYNTHETIC_PROBE_TOKEN:", prior_step)
        self.assertIn("--environment staging --phase prior", prior_step)
        self.assertNotIn("stage_web_release.py", prior_step)

    def test_validation_workflow_uses_exact_atomic_phase_transitions(self):
        workflow = (
            ROOT / ".github" / "workflows" / "mission-spine-validate.yml"
        ).read_text(encoding="utf-8")
        for target, current in (
            ("mission-off", "prior"),
            ("mission-on", "mission-off"),
            ("mission-off", "mission-on"),
            ("prior", "mission-off"),
        ):
            self.assertIn(
                f"--phase {target} --expected-current {current}", workflow
            )
        self.assertIn("--phase prior --expected-current candidate", workflow)
        journey, seal = workflow.split("  seal-and-staging:", 1)
        self.assertEqual(journey.count("stage_web_release.py"), 3)
        self.assertEqual(seal.count("stage_web_release.py"), 5)


if __name__ == "__main__":
    unittest.main()
