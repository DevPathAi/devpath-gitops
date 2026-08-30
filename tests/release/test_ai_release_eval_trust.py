import copy
import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "release"
if str(SCRIPTS) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPTS))
CANDIDATE = ROOT / "tests" / "release" / "fixtures" / "valid-candidate-spec.json"
RELEASE = ROOT / "tests" / "release" / "fixtures" / "valid-release.json"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def rendered_ai_config(**overrides: str) -> bytes:
    values = {
        "MENTOR_PROVIDER": "ollama",
        "MENTOR_FALLBACK": "claude",
        "MENTOR_OLLAMA_MODEL": "qwen2.5:3b",
        "MENTOR_CLAUDE_MODEL": "claude-sonnet-4-6",
    }
    values.update(overrides)
    environment = "".join(
        f"        - name: {name}\n          value: {value}\n"
        for name, value in values.items()
    )
    return (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: devpath-ai-svc\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "      - env:\n"
        f"{environment}"
        "        image: ghcr.io/devpathai/devpath-ai-svc:"
        "76ad759877f5900c4e11daaf413868c830d75879\n"
        "        name: devpath-ai-svc\n"
    ).encode()


class AiReleaseEvalTrustTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_module(
            SCRIPTS / "validate_release_manifest.py", "ai_release_validator"
        )
        cls.verifier = load_module(
            SCRIPTS / "verify_release_artifacts.py", "ai_release_verifier"
        )
        cls.installer = load_module(
            SCRIPTS / "install_pinned_kustomize.py", "ai_release_installer"
        )
        cls.candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
        cls.release = json.loads(RELEASE.read_text(encoding="utf-8"))
        cls.candidate_sha = hashlib.sha256(CANDIDATE.read_bytes()).hexdigest()

    def _release(self):
        release = copy.deepcopy(self.release)
        release["candidate_spec"]["sha256"] = self.candidate_sha

        def bind(value):
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key == "candidate_spec_sha256":
                        value[key] = self.candidate_sha
                    else:
                        bind(nested)
            elif isinstance(value, list):
                for nested in value:
                    bind(nested)

        bind(release)
        ref = release["ai_release_eval"]["evidence"]
        ref["run_attempt"] = 1
        ref["artifact_name"] = (
            f'{release["release_id"]}-ai-eval-run-'
            f'{ref["workflow_run_id"]}-attempt-1'
        )
        return release

    def _payload(self):
        config = self.candidate["ai_release_eval_config"]
        return {
            "candidate_spec_sha256": self.candidate_sha,
            "status": "passed",
            "producer_run_id": 103,
            "producer_run_attempt": 1,
            "ai_source_sha": self.candidate["services"]["devpath-ai-svc"]["source_sha"],
            "gitops_source_sha": self.candidate["gitops"]["base_sha"],
            "runtime_primary_model": config["runtime_primary_model"],
            "runtime_fallback_models": config["runtime_fallback_models"],
            "development_model": config["development_model"],
            "tuning_revision": config["tuning_revision"],
            "tuning_sha256": config["tuning_sha256"],
            "prompt_sha256": config["prompt_sha256"],
            "fixture_revision": config["fixture_revision"],
            "fixture_sha256": config["fixture_sha256"],
            "rendered_config_sha256": config["rendered_config_sha256"],
            "ollama_endpoint_sha256": config["ollama_endpoint_sha256"],
            "hard_invariants_percent": 100,
            "usefulness_percent": 95,
            "baseline_delta_points": 0,
            "approval_environment": "mission-spine-ai-release-eval",
            "approval_environment_id": 7001,
            "approval_job_name": "Run AI release evaluation",
            "approved_by": "release-reviewer",
            "approved_by_id": 7002,
            "approval_effective_at": "2099-01-01T00:00:00Z",
        }

    def test_candidate_and_release_bind_rendered_config_and_attempt_one_name(self):
        self.assertRegex(
            self.candidate["ai_release_eval_config"]["rendered_config_sha256"],
            r"^[0-9a-f]{64}$",
        )
        release = self._release()
        self.validator.validate_release_manifest(
            release, self.candidate, self.candidate_sha, RELEASE
        )
        retry = self._release()
        retry_ref = retry["ai_release_eval"]["evidence"]
        retry_ref["run_attempt"] = 2
        retry_ref["artifact_name"] = (
            f'{retry["release_id"]}-ai-eval-run-'
            f'{retry_ref["workflow_run_id"]}-attempt-2'
        )
        with self.assertRaisesRegex(ValueError, "attempt 1"):
            self.validator.validate_release_manifest(
                retry, self.candidate, self.candidate_sha, RELEASE
            )

    def test_ai_evidence_exactly_binds_gitops_config_and_protected_approval(self):
        payload = self._payload()
        self.verifier.validate_evidence_payload(
            "ai-release-eval",
            payload,
            self.candidate_sha,
            self.candidate,
            103,
            1,
        )
        for field, value in (
            ("producer_run_attempt", 2),
            ("gitops_source_sha", "2" * 40),
            ("rendered_config_sha256", "2" * 64),
            ("ollama_endpoint_sha256", "2" * 64),
            ("approval_environment", "unprotected"),
            ("approval_job_name", "lookalike"),
            ("approved_by", "bad login"),
        ):
            with self.subTest(field=field):
                invalid = copy.deepcopy(payload)
                invalid[field] = value
                with self.assertRaises(ValueError):
                    self.verifier.validate_evidence_payload(
                        "ai-release-eval",
                        invalid,
                        self.candidate_sha,
                        self.candidate,
                        103,
                        invalid.get("producer_run_attempt", 1),
                    )

    def test_ai_evidence_binds_runtime_identity_and_local_tuning_separately(self):
        candidate = copy.deepcopy(self.candidate)
        config = candidate["ai_release_eval_config"]
        payload = self._payload()
        self.verifier.validate_evidence_payload(
            "ai-release-eval",
            payload,
            self.candidate_sha,
            candidate,
            103,
            1,
        )

        for field, value in (
            ("development_model", "claude-sonnet-4-6"),
            ("tuning_revision", "mentor-development-tuning-v2"),
            ("tuning_sha256", "4" * 64),
        ):
            invalid = copy.deepcopy(payload)
            invalid[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, field
            ):
                self.verifier.validate_evidence_payload(
                    "ai-release-eval",
                    invalid,
                    self.candidate_sha,
                    candidate,
                    103,
                    1,
                )

    def test_ai_workflow_inputs_and_current_protected_main_are_exact(self):
        raw = b"""name: AI eval\non:\n  workflow_dispatch:\n    inputs:\n      release_id:\n        required: true\n        type: string\n      candidate_spec_sha256:\n        required: true\n        type: string\n      ai_source_sha:\n        required: true\n        type: string\n      gitops_source_sha:\n        required: true\n        type: string\njobs: {}\n"""
        self.verifier.validate_workflow_dispatch_inputs(
            raw,
            {
                "release_id",
                "candidate_spec_sha256",
                "ai_source_sha",
                "gitops_source_sha",
            },
            "ai-release-eval",
        )
        head = self.candidate["services"]["devpath-ai-svc"]["source_sha"]
        branch = {"name": "main", "protected": True, "commit": {"sha": head}}
        run = {
            "run_attempt": 1,
            "head_branch": "main",
            "head_sha": head,
            "repository": {"full_name": "DevPathAi/devpath-ai-svc"},
            "head_repository": {"full_name": "DevPathAi/devpath-ai-svc"},
        }
        self.verifier.validate_ai_release_eval_trust(branch, run, head)
        for target, field, value in (
            ("branch", "protected", False),
            ("branch", "commit", {"sha": "0" * 40}),
            ("run", "run_attempt", 2),
            ("run", "head_branch", "feature/eval"),
            ("run", "head_repository", {"full_name": "fork/devpath-ai-svc"}),
        ):
            with self.subTest(target=target, field=field):
                bad_branch = copy.deepcopy(branch)
                bad_run = copy.deepcopy(run)
                (bad_branch if target == "branch" else bad_run)[field] = value
                with self.assertRaises(ValueError):
                    self.verifier.validate_ai_release_eval_trust(
                        bad_branch, bad_run, head
                    )

    def test_unique_ai_dispatch_rejects_retry_and_competing_fresh_run(self):
        head = self.candidate["services"]["devpath-ai-svc"]["source_sha"]
        workflow = self.validator.PRODUCER_WORKFLOWS["ai-release-eval"]

        def run(run_id, attempt):
            return {
                "id": run_id,
                "status": "completed",
                "conclusion": "success",
                "event": "workflow_dispatch",
                "head_sha": head,
                "head_branch": "main",
                "path": workflow,
                "run_attempt": attempt,
            }

        def artifacts(_env, _repository, name):
            run_id = int(name.split("-run-", 1)[1].split("-", 1)[0])
            return [
                {
                    "name": name,
                    "expired": False,
                    "workflow_run": {"id": run_id},
                }
            ]

        with mock.patch.object(
            self.verifier,
            "_list_protected_runs",
            return_value=[run(103, 2), run(104, 1)],
        ), mock.patch.object(
            self.verifier, "_list_named_artifacts", side_effect=artifacts
        ):
            self.verifier.assert_unique_protected_producer_run(
                {},
                "DevPathAi/devpath-ai-svc",
                head,
                workflow,
                self.candidate["release_id"],
                "ai-release-eval",
                104,
            )
        with mock.patch.object(
            self.verifier,
            "_list_protected_runs",
            return_value=[run(104, 1), run(105, 1)],
        ), mock.patch.object(
            self.verifier, "_list_named_artifacts", side_effect=artifacts
        ):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                self.verifier.assert_unique_protected_producer_run(
                    {},
                    "DevPathAi/devpath-ai-svc",
                    head,
                    workflow,
                    self.candidate["release_id"],
                    "ai-release-eval",
                    104,
                )

    def test_ai_outer_zip_is_exact_regular_single_evidence_file(self):
        payload = json.dumps(self._payload(), separators=(",", ":")).encode()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            valid = root / "valid.zip"
            with zipfile.ZipFile(valid, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("evidence.json", payload)
            destination = root / "out"
            self.verifier.extract_ai_release_eval_archive(valid, destination)
            self.assertEqual((destination / "evidence.json").read_bytes(), payload)
            for name, entries in (
                ("traversal", [("../evidence.json", payload)]),
                ("extra", [("evidence.json", payload), ("raw.json", b"{}")]),
                ("duplicate", [("evidence.json", payload), ("evidence.json", payload)]),
            ):
                with self.subTest(name=name):
                    bad = root / f"{name}.zip"
                    with zipfile.ZipFile(bad, "w", compression=zipfile.ZIP_STORED) as archive:
                        for filename, content in entries:
                            archive.writestr(filename, content)
                    with self.assertRaises(ValueError):
                        self.verifier.extract_ai_release_eval_archive(
                            bad, root / f"out-{name}"
                        )
            link = root / "link.zip"
            with zipfile.ZipFile(link, "w") as archive:
                info = zipfile.ZipInfo("evidence.json")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, b"target")
            with self.assertRaises(ValueError):
                self.verifier.extract_ai_release_eval_archive(link, root / "out-link")

    def test_pinned_renderer_archive_and_workflow_install_contract_are_exact(self):
        self.assertEqual(self.installer.KUSTOMIZE_VERSION, "v5.4.3")
        self.assertEqual(
            self.installer.KUSTOMIZE_ARCHIVE_SHA256,
            "3669470b454d865c8184d6bce78df05e977c9aea31c30df3c669317d43bcc7a7",
        )
        self.assertEqual(
            self.installer.KUSTOMIZE_BINARY_SHA256,
            "1d6bae90ee8591f7a4ed5b75be3f9bf80b7609f0c785921320827cd93e7c3a9a",
        )
        self.assertEqual(self.installer.KUSTOMIZE_BINARY_BYTES, 15_101_952)
        for workflow in (
            "mission-spine-validate.yml",
            "mission-spine-promote.yml",
            "mission-spine-landing-last.yml",
            "mission-spine-rollback.yml",
        ):
            raw = (ROOT / ".github" / "workflows" / workflow).read_text(
                encoding="utf-8"
            )
            self.assertIn("scripts/release/install_pinned_kustomize.py", raw)
            self.assertIn("MISSION_SPINE_KUSTOMIZE_BIN=", raw)

        binary = b"synthetic-kustomize"
        member = tarfile.TarInfo("kustomize")
        member.mode = 0o755
        member.size = len(binary)
        buffer = BytesIO()
        with tarfile.open(
            fileobj=buffer, mode="w:gz", format=tarfile.USTAR_FORMAT
        ) as archive:
            archive.addfile(member, BytesIO(binary))
        raw = buffer.getvalue()
        with mock.patch.object(
            self.installer, "KUSTOMIZE_ARCHIVE_SHA256", hashlib.sha256(raw).hexdigest()
        ), mock.patch.object(
            self.installer, "KUSTOMIZE_BINARY_SHA256", hashlib.sha256(binary).hexdigest()
        ), mock.patch.object(
            self.installer, "KUSTOMIZE_BINARY_BYTES", len(binary)
        ):
            self.assertEqual(self.installer.validate_archive(raw), binary)
            with self.assertRaisesRegex(ValueError, "archive SHA-256"):
                self.installer.validate_archive(raw + b"x")

    def test_gitops_config_is_independently_rendered_with_exact_command(self):
        rendered = rendered_ai_config()
        candidate = copy.deepcopy(self.candidate)
        candidate["ai_release_eval_config"]["rendered_config_sha256"] = (
            hashlib.sha256(rendered).hexdigest()
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            binary = Path(temp_dir) / "kustomize"
            binary.write_bytes(b"synthetic-binary")
            binary_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
            calls = [
                mock.Mock(returncode=0, stdout=b"v5.4.3\n", stderr=b""),
                mock.Mock(returncode=0, stdout=rendered, stderr=b""),
            ]
            with mock.patch.dict(
                "os.environ", {"MISSION_SPINE_KUSTOMIZE_BIN": str(binary.resolve())}
            ), mock.patch.object(
                self.verifier, "KUSTOMIZE_BINARY_BYTES", binary.stat().st_size
            ), mock.patch.object(
                self.verifier, "KUSTOMIZE_BINARY_SHA256", binary_hash
            ), mock.patch.object(
                self.verifier, "_materialize_git_tree"
            ) as materialize, mock.patch.object(
                self.verifier.subprocess, "run", side_effect=calls
            ) as run:
                self.verifier.verify_ai_rendered_config(ROOT, candidate)
            materialize.assert_called_once()
            self.assertEqual(run.call_args_list[0].args[0], [str(binary), "version"])
            self.assertEqual(
                run.call_args_list[1].args[0],
                [str(binary), "build", "apps/devpath-ai-svc/base"],
            )

        for name, value in (
            ("CRLF", rendered.replace(b"\n", b"\r\n")),
            ("missing LF", rendered.rstrip(b"\n")),
            ("hash drift", rendered + b"# drift\n"),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.verifier.validate_ai_rendered_config_bytes(
                    value, hashlib.sha256(rendered).hexdigest()
                )

    def test_gitops_runtime_is_exact_ollama_then_claude_and_rejects_reversion(self):
        deployment = yaml.safe_load(
            (ROOT / "apps" / "devpath-ai-svc" / "base" / "deployment.yaml").read_text(
                encoding="utf-8"
            )
        )
        containers = deployment["spec"]["template"]["spec"]["containers"]
        container = next(
            item for item in containers if item["name"] == "devpath-ai-svc"
        )
        values = {
            item["name"]: item["value"]
            for item in container["env"]
            if "value" in item
        }
        expected = {
            "MENTOR_PROVIDER": "ollama",
            "MENTOR_FALLBACK": "claude",
            "MENTOR_OLLAMA_MODEL": "qwen2.5:3b",
            "MENTOR_CLAUDE_MODEL": "claude-sonnet-4-6",
        }
        self.verifier.validate_ai_runtime_environment(values)
        self.assertEqual(
            {key: values[key] for key in expected},
            expected,
        )
        for field, value in (
            ("MENTOR_PROVIDER", "claude"),
            ("MENTOR_FALLBACK", ""),
            ("MENTOR_OLLAMA_MODEL", "qwen2.5:7b"),
            ("MENTOR_CLAUDE_MODEL", "claude-sonnet-release"),
        ):
            with self.subTest(field=field):
                reverted = dict(values)
                reverted[field] = value
                with self.assertRaisesRegex(ValueError, field):
                    self.verifier.validate_ai_runtime_environment(reverted)
                rendered = rendered_ai_config(**{field: value})
                with self.assertRaisesRegex(ValueError, field):
                    self.verifier.validate_ai_rendered_config_bytes(
                        rendered, hashlib.sha256(rendered).hexdigest()
                    )

        rendered = rendered_ai_config()
        sidecar_environment = b"".join(
            f"        - name: {name}\n          value: {value}\n".encode()
            for name, value in {
                "MENTOR_PROVIDER": "ollama",
                "MENTOR_FALLBACK": "claude",
                "MENTOR_OLLAMA_MODEL": "qwen2.5:3b",
                "MENTOR_CLAUDE_MODEL": "claude-sonnet-4-6",
            }.items()
        )
        malformed = {
            "wrong template parent": rendered.replace(
                b"  template:\n", b"  bogus:\n", 1
            ),
            "wrong pod spec parent": rendered.replace(
                b"    spec:\n      containers:\n",
                b"    bogus:\n      containers:\n",
                1,
            ),
            "initContainer escape": rendered.replace(
                b"      containers:\n      - env:\n",
                b"      containers:\n      initContainers:\n      - env:\n",
                1,
            ),
            "sidecar substitution": rendered.replace(
                b"        name: devpath-ai-svc\n",
                b"        name: runtime-app\n"
                b"      - env:\n"
                + sidecar_environment
                + b"        image: busybox:1.36\n"
                b"        name: devpath-ai-svc\n",
                1,
            ),
            "renamed env anchor": rendered.replace(
                b"      - env:\n", b"      - bogus:\n", 1
            ),
            "second env block": rendered.replace(
                b"        image:",
                b"        env:\n"
                b"          - name: NON_TRUST_INPUT\n"
                b"            value: ignored\n"
                b"        image:",
                1,
            ),
            "required valueFrom plus duplicate": rendered.replace(
                b"        - name: MENTOR_PROVIDER\n          value: ollama\n",
                b"        - name: MENTOR_PROVIDER\n"
                b"          valueFrom:\n"
                b"            secretKeyRef:\n"
                b"              name: spoof\n"
                b"              key: provider\n"
                b"        - name: MENTOR_PROVIDER\n"
                b"          value: ollama\n",
                1,
            ),
            "required value plus inline valueFrom": rendered.replace(
                b"        - name: MENTOR_PROVIDER\n          value: ollama\n",
                b"        - name: MENTOR_PROVIDER\n"
                b"          value: ollama\n"
                b"          valueFrom: {}\n",
                1,
            ),
        }
        for name, raw in malformed.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.verifier.validate_ai_rendered_config_bytes(
                    raw, hashlib.sha256(raw).hexdigest()
                )


if __name__ == "__main__":
    unittest.main()
