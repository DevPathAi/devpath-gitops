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
        cls.artifacts = load_module("verify_release_artifacts.py", "hardened_artifacts")
        cls.promoter = load_module("set_web_digest.py", "hardened_promoter")
        cls.cloudflare = load_module("cloudflare_pages.py", "hardened_cloudflare")

    def healthy_snapshot(self):
        digest = self.candidate["frontend"]["selected_on_digest"]
        image = f"ghcr.io/devpathai/devpath-web@{digest}"
        deployment = {
            "metadata": {"generation": 7},
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
        pods = {
            "items": [
                {
                    "metadata": {"uid": "pod-uid-1", "name": "web-abc", "deletionTimestamp": None},
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
        return deployment, pods, image

    def test_rollout_requires_actual_ready_nonterminating_digest_and_stable_restarts(self):
        deployment, pods, image = self.healthy_snapshot()
        baseline = {}
        self.rollout.validate_rollout_snapshot(
            deployment, pods, "devpath-web", {image}, image, baseline
        )
        self.assertEqual(baseline, {("pod-uid-1", "devpath-web"): 0})

        terminating = copy.deepcopy(pods)
        terminating["items"][0]["metadata"]["deletionTimestamp"] = "2099-01-01T00:01:00Z"
        with self.assertRaisesRegex(ValueError, "terminating"):
            self.rollout.validate_rollout_snapshot(
                deployment, terminating, "devpath-web", {image}, image, baseline
            )
        restarted = copy.deepcopy(pods)
        restarted["items"][0]["status"]["containerStatuses"][0]["restartCount"] = 1
        with self.assertRaisesRegex(ValueError, "restart"):
            self.rollout.validate_rollout_snapshot(
                deployment, restarted, "devpath-web", {image}, image, baseline
            )
        wrong_runtime = copy.deepcopy(pods)
        wrong_runtime["items"][0]["status"]["containerStatuses"][0]["imageID"] = (
            "docker-pullable://ghcr.io/devpathai/devpath-web@sha256:" + "0" * 64
        )
        with self.assertRaisesRegex(ValueError, "imageID"):
            self.rollout.validate_rollout_snapshot(
                deployment, wrong_runtime, "devpath-web", {image}, image, baseline
            )
        not_available = copy.deepcopy(deployment)
        not_available["status"]["availableReplicas"] = 0
        with self.assertRaisesRegex(ValueError, "available"):
            self.rollout.validate_rollout_snapshot(
                not_available, pods, "devpath-web", {image}, image, baseline
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
            "workflow_path": ".github/workflows/mission-spine-visual-evidence.yml",
            "workflow_sha256": hashlib.sha256(workflow).hexdigest(),
        }
        run = {
            "event": "workflow_dispatch",
            "head_sha": "a" * 40,
            "run_attempt": 2,
            "path": ".github/workflows/mission-spine-visual-evidence.yml",
            "status": "completed",
            "conclusion": "success",
        }
        self.artifacts.validate_run_provenance(
            "visual", run, reference, "a" * 40,
            ".github/workflows/mission-spine-visual-evidence.yml", workflow
        )
        mutations = (
            ("event", "push"),
            ("head_sha", "b" * 40),
            ("run_attempt", 3),
            ("path", "evil/.github/workflows/mission-spine-visual-evidence.yml"),
        )
        for field, value in mutations:
            invalid = copy.deepcopy(run)
            invalid[field] = value
            with self.assertRaises(ValueError, msg=field):
                self.artifacts.validate_run_provenance(
                    "visual", invalid, reference, "a" * 40,
                    ".github/workflows/mission-spine-visual-evidence.yml", workflow
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
                root, self.candidate["release_id"], base_sha, candidate_sha
            )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_sha, candidate_sha = make_tree(root, extra_candidate_file=True)
            with self.assertRaisesRegex(ValueError, "candidate tree delta"):
                self.artifacts.verify_validation_tree(
                    root, self.candidate["release_id"], base_sha, candidate_sha
                )

    def test_all_production_workflows_share_one_preemptible_lease(self):
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
        self.assertIn("cancel-in-progress: true", texts["mission-spine-rollback.yml"])
        self.assertIn("--action verify-prior", texts["mission-spine-rollback.yml"])

    def test_legacy_tag_must_equal_sealed_base_tag_before_digest_mutation(self):
        manifest = copy.deepcopy(self.candidate)
        source = """images:\n- name: ghcr.io/devpathai/devpath-web\n  newName: ghcr.io/devpathai/devpath-web\n  newTag: 2400fb4ece8dd250e5b29109bc8e28686c7edc03\n"""
        rendered = self.promoter.render_kustomization(source, manifest, "mission-off", "base")
        self.assertIn("digest: " + manifest["frontend"]["mission_off"]["image_digest"], rendered)
        arbitrary = source.replace(
            "2400fb4ece8dd250e5b29109bc8e28686c7edc03", "f" * 40
        )
        with self.assertRaisesRegex(ValueError, "trusted base tag"):
            self.promoter.render_kustomization(arbitrary, manifest, "mission-off", "base")
        promote = (ROOT / ".github/workflows/mission-spine-promote.yml").read_text(encoding="utf-8")
        self.assertRegex(promote, r"--phase prior[\s\\]+--canary-seconds 0")

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
            "case_count": 96,
            "passed_case_count": 96,
            "failed_case_count": 0,
            "surface_case_counts": {"web": 48, "admin": 16, "mobile": 16, "dp_design": 16},
            "render_provenance_sha256": self.candidate["quality_evidence_inputs"][
                "catalogs"
            ]["frontend-visual"]["provenance_sha256"],
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
                "home_source_sha": self.candidate["home"]["source_sha"],
                "dist_sha256": self.candidate["home"]["dist_sha256"],
            },
            "privacy-approval": {
                "candidate_spec_sha256": self.candidate_hash,
                "status": "passed",
                **producer,
                "approved_at": "2098-12-31T00:00:00Z",
                **{
                    key: self.candidate["analytics_privacy"][key]
                    for key in (
                        "collection_mode", "region", "project_identity", "retention_days",
                        "access_owner", "deletion_runbook",
                    )
                },
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
                "case_count": 24,
                "passed_case_count": 24,
                "failed_case_count": 0,
                "surface_case_counts": {"web": 12, "admin": 4, "mobile": 4, "dp_design": 4},
                "test_provenance_sha256": self.candidate["quality_evidence_inputs"][
                    "catalogs"
                ]["frontend-automated-a11y"]["provenance_sha256"],
                "standard": "WCAG2.2AA",
                "critical_violations": 0,
                "serious_violations": 0,
            },
        }
        for label, payload in exact_payloads.items():
            self.artifacts.validate_evidence_payload(
                label, payload, self.candidate_hash, self.candidate, 501, 3
            )
            invalid = copy.deepcopy(payload)
            invalid["detail"] = "free-form material"
            with self.assertRaisesRegex(ValueError, "key set", msg=label):
                self.artifacts.validate_evidence_payload(
                    label, invalid, self.candidate_hash, self.candidate, 501, 3
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
            "- name: Deploy Landing last and capture the exact created deployment ID", 1
        )[1].split("- name: Verify exact current deployment CAS", 1)[0]
        self.assertLess(
            deploy_block.index("verify_promotion_evidence.py"),
            deploy_block.index("--action preflight"),
        )
        self.assertLess(
            deploy_block.index("--action preflight"),
            deploy_block.index('wrangler" pages deploy'),
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
            return_value={"result": {"canonical_deployment": deployment}},
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

        prior_id = self.candidate["home"]["prior_production_deployment_id"]
        prior = {
            "id": prior_id,
            "environment": "production",
            "latest_stage": {"status": "success"},
        }
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
        self.assertIn("prepare-wrangler:", prepare)
        self.assertNotIn("environment:", prepare)
        self.assertIn("npm ci --ignore-scripts", prepare)
        self.assertNotRegex(deploy, r"\bnpx\b.*wrangler")

    def test_canonical_composed_source_pins_are_bound_in_candidate_fixture(self):
        expected = {
            "devpath-admin": "a18aee3d31e61dcd393517ef68125224eeb76c7a",
            "devpath-ai-svc": "47adf75283a9a8ba75a93a51bf76113a1d8315a1",
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
            "c96996b89e113664d37cd607cae21d4f267393f4",
        )
        self.assertEqual(
            self.candidate["shared_migration"]["shared_version"],
            "0.0.1-et9.20260816",
        )
        self.assertEqual(
            self.candidate["shared_migration"]["shared_jar_sha256"],
            "94e2adb769790d813a872163347ede20ad4c75ae88e5811df2ec6625a340f21f",
        )
        self.assertEqual(
            self.candidate["frontend"]["source_sha"],
            "a18aee3d31e61dcd393517ef68125224eeb76c7a",
        )


if __name__ == "__main__":
    unittest.main()
