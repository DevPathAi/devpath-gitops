import copy
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "verify_main_release_context",
    ROOT / "scripts" / "release" / "verify_main_release_context.py",
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class MainReleaseContextTest(unittest.TestCase):
    def test_accepts_only_attempt_one_current_protected_main(self):
        head = "a" * 40
        values = {
            "control_head": head,
            "workflow_sha": head,
            "repository": module.REPOSITORY,
            "event_name": "workflow_dispatch",
            "ref": "refs/heads/main",
            "ref_name": "main",
            "run_attempt": 1,
            "workflow_ref": (
                f"{module.REPOSITORY}/.github/workflows/mission-spine-promote.yml"
                "@refs/heads/main"
            ),
            "workflow_path": ".github/workflows/mission-spine-promote.yml",
            "branch": {"name": "main", "protected": True, "commit": {"sha": head}},
        }
        module.validate_main_state(**values)
        mutations = (
            ("workflow_sha", "b" * 40),
            ("repository", "evil/repo"),
            ("event_name", "push"),
            ("ref", "refs/heads/release/candidate-r1"),
            ("ref_name", "release/candidate-r1"),
            ("run_attempt", 2),
            ("run_attempt", True),
            ("run_attempt", 1.0),
            ("workflow_ref", values["workflow_ref"].replace("main", "release/candidate-r1")),
        )
        for key, value in mutations:
            bad = dict(values)
            bad[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                module.validate_main_state(**bad)
        for change in (
            {"protected": False},
            {"name": "other"},
            {"commit": {"sha": "b" * 40}},
        ):
            bad = dict(values)
            bad["branch"] = {**values["branch"], **change}
            with self.assertRaises(ValueError):
                module.validate_main_state(**bad)

    def _git(self, root: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()

    def test_release_tree_derives_candidate_from_r_parent_not_validator_head(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.invalid")
            self._git(root, "config", "user.name", "test")
            self._git(root, "config", "core.autocrlf", "false")
            (root / "base.txt").write_bytes(b"base\n")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "base")
            base = self._git(root, "rev-parse", "HEAD")

            release_id = "ms-20260817-main-only"
            candidate_dir = root / "release-manifests" / "candidates"
            candidate_dir.mkdir(parents=True)
            # A minimally invalid document is enough to prove structural checks run first.
            candidate = candidate_dir / f"{release_id}.candidate-spec.json"
            candidate.write_bytes(b"{}\n")
            self._git(root, "add", candidate.as_posix())
            self._git(root, "commit", "-m", "candidate")
            candidate_head = self._git(root, "rev-parse", "HEAD")
            release_dir = root / "release-manifests" / "releases"
            release_dir.mkdir(parents=True)
            release = release_dir / f"{release_id}.json"
            release.write_bytes(b"{}\n")
            self._git(root, "add", release.as_posix())
            self._git(root, "commit", "-m", "release")
            sealed = self._git(root, "rev-parse", "HEAD")

            original = module.resolve_release_bundle
            module.resolve_release_bundle = lambda *_: (
                release,
                candidate,
                {},
                {"gitops": {"base_sha": base}},
                "f" * 64,
            )
            try:
                result = module.validate_release_data_tree(
                    root, release_id, sealed, sealed=True
                )
            finally:
                module.resolve_release_bundle = original

            with self.assertRaises(ValueError):
                module.validate_release_data_tree(
                    root, release_id, sealed, sealed=False
                )

            candidate_tree = self._git(root, "rev-parse", f"{candidate_head}^{{tree}}")
            side = self._git(
                root, "commit-tree", candidate_tree, "-p", base, "-m", "side"
            )
            candidate_merge = self._git(
                root,
                "commit-tree",
                candidate_tree,
                "-p",
                base,
                "-p",
                side,
                "-m",
                "candidate merge",
            )
            self._git(root, "checkout", "--detach", candidate_merge)
            with self.assertRaisesRegex(ValueError, "exactly one parent"):
                module.validate_release_data_tree(
                    root, release_id, candidate_merge, sealed=False
                )

            sealed_tree = self._git(root, "rev-parse", f"{sealed}^{{tree}}")
            release_side = self._git(
                root,
                "commit-tree",
                sealed_tree,
                "-p",
                candidate_head,
                "-m",
                "release side",
            )
            release_merge = self._git(
                root,
                "commit-tree",
                sealed_tree,
                "-p",
                candidate_head,
                "-p",
                release_side,
                "-m",
                "release merge",
            )
            self._git(root, "checkout", "--detach", release_merge)
            with self.assertRaisesRegex(ValueError, "exactly one parent"):
                module.validate_release_data_tree(
                    root, release_id, release_merge, sealed=True
                )

            self._git(root, "checkout", "--detach", sealed)
            (root / "untracked-shadow.py").write_bytes(b"raise SystemExit\n")
            with self.assertRaisesRegex(ValueError, "not clean"):
                module.validate_release_data_tree(
                    root, release_id, sealed, sealed=True
                )
            (root / "untracked-shadow.py").unlink()
            self.assertEqual(result["gitops_base_sha"], base)
            self.assertEqual(result["candidate_head_sha"], candidate_head)
            self.assertEqual(result["sealed_release_sha"], sealed)

            module.resolve_release_bundle = lambda *_: (
                release,
                candidate,
                {},
                {"gitops": {"base_sha": "e" * 40}},
                "f" * 64,
            )
            try:
                with self.assertRaisesRegex(ValueError, "actual sole parent"):
                    module.validate_release_data_tree(
                        root, release_id, sealed, sealed=True
                    )
            finally:
                module.resolve_release_bundle = original

            self._git(root, "checkout", "--detach", candidate_head)
            release.parent.mkdir(parents=True, exist_ok=True)
            release.write_bytes(b"{}\n")
            (root / "extra-release-path.txt").write_bytes(b"extra\n")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "release with extra path")
            release_extra = self._git(root, "rev-parse", "HEAD")
            module.resolve_release_bundle = lambda *_: (
                release,
                candidate,
                {},
                {"gitops": {"base_sha": base}},
                "f" * 64,
            )
            try:
                with self.assertRaisesRegex(ValueError, "add only the release manifest"):
                    module.validate_release_data_tree(
                        root, release_id, release_extra, sealed=True
                    )
            finally:
                module.resolve_release_bundle = original

            self._git(root, "checkout", "--detach", base)
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_bytes(b"{}\n")
            self._git(root, "add", candidate.as_posix())
            self._git(root, "update-index", "--chmod=+x", candidate.as_posix())
            self._git(root, "commit", "-m", "executable candidate blob")
            os.chmod(candidate, candidate.stat().st_mode | 0o111)
            self.assertEqual(
                self._git(root, "status", "--porcelain=v1", "--untracked-files=all"),
                "",
            )
            executable_candidate = self._git(root, "rev-parse", "HEAD")
            original_candidate = module.resolve_candidate_spec
            module.resolve_candidate_spec = lambda *_: (
                candidate,
                {"gitops": {"base_sha": base}},
                "f" * 64,
            )
            try:
                with self.assertRaisesRegex(ValueError, "regular Git blob"):
                    module.validate_release_data_tree(
                        root, release_id, executable_candidate, sealed=False
                    )
            finally:
                module.resolve_candidate_spec = original_candidate

            self._git(root, "checkout", "--detach", candidate_head)
            self._git(root, "update-index", "--assume-unchanged", candidate.as_posix())
            candidate.write_bytes(b'{"checkout":"drift"}\n')
            module.resolve_candidate_spec = lambda *_: (
                candidate,
                {"gitops": {"base_sha": base}},
                "f" * 64,
            )
            try:
                with self.assertRaisesRegex(ValueError, "checkout differs"):
                    module.validate_release_data_tree(
                        root, release_id, candidate_head, sealed=False
                    )
            finally:
                module.resolve_candidate_spec = original_candidate
                self._git(root, "update-index", "--no-assume-unchanged", candidate.as_posix())
                self._git(root, "checkout", "--", candidate.as_posix())

            self._git(root, "checkout", "-b", "mutated", candidate_head)
            (root / "extra.txt").write_bytes(b"extra\n")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "extra candidate mutation")
            original_candidate = module.resolve_candidate_spec
            module.resolve_candidate_spec = lambda *_: (
                candidate,
                {"gitops": {"base_sha": candidate_head}},
                "f" * 64,
            )
            try:
                with self.assertRaisesRegex(ValueError, "candidate commit"):
                    module.validate_release_data_tree(
                        root,
                        release_id,
                        self._git(root, "rev-parse", "HEAD"),
                        sealed=False,
                    )
            finally:
                module.resolve_candidate_spec = original_candidate

    def test_control_checkout_rejects_dirty_untracked_and_workflow_byte_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.invalid")
            self._git(root, "config", "user.name", "test")
            workflow_path = ".github/workflows/mission-spine-promote.yml"
            workflow = root / workflow_path
            workflow.parent.mkdir(parents=True)
            workflow.write_bytes(b"name: trusted\n")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "control")
            head = self._git(root, "rev-parse", "HEAD")
            environment = {
                "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_WORKFLOW_SHA": head,
                "GITHUB_REPOSITORY": module.REPOSITORY,
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_REF_NAME": "main",
                "GITHUB_WORKFLOW_REF": f"{module.REPOSITORY}/{workflow_path}@refs/heads/main",
                "GITHUB_SHA": head,
            }
            branch = {"name": "main", "protected": True, "commit": {"sha": head}}
            self.assertEqual(
                module.validate_control_checkout(root, workflow_path, branch, environment),
                head,
            )
            workflow.write_bytes(b"name: attacker\n")
            with self.assertRaisesRegex(ValueError, "modified tracked"):
                module.validate_control_checkout(root, workflow_path, branch, environment)
            self._git(root, "checkout", "--", workflow_path)
            (root / "shadow.py").write_bytes(b"raise SystemExit\n")
            with self.assertRaisesRegex(ValueError, "modified tracked"):
                module.validate_control_checkout(root, workflow_path, branch, environment)

    def test_verify_rechecks_current_main_and_release_branch_after_authentication(self):
        release_id = "ms-20260817-main-only"
        base = "a" * 40
        candidate_head = "b" * 40
        candidate_path = Path("candidate.json")
        outputs = {
            "gitops_base_sha": base,
            "candidate_head_sha": candidate_head,
            "candidate_spec_path": candidate_path.as_posix(),
            "candidate_spec_sha256": "c" * 64,
        }
        main = {"name": "main", "protected": True, "commit": {"sha": base}}
        release = {"commit": {"sha": candidate_head}}
        drifted_main = {"name": "main", "protected": True, "commit": {"sha": "d" * 40}}
        drifted_release = {"commit": {"sha": "e" * 40}}

        from unittest import mock

        common = (
            mock.patch.dict(
                os.environ,
                {"GH_TOKEN": "token", "RELEASE_EVIDENCE_TOKEN": "evidence"},
                clear=False,
            ),
            mock.patch.object(module, "validate_control_checkout", return_value=base),
            mock.patch.object(module, "validate_release_data_tree", return_value=outputs),
            mock.patch.object(
                module,
                "resolve_candidate_spec",
                return_value=(candidate_path, {"gitops": {"base_sha": base}}, "c" * 64),
            ),
            mock.patch.object(module, "verify_candidate_artifact"),
            mock.patch.object(Path, "read_bytes", return_value=b"{}\n"),
        )
        sequences = (
            [main, release, drifted_release],
            [main, release, release, drifted_main],
            [main, release, release, main, drifted_release],
        )
        for responses in sequences:
            with self.subTest(responses=responses):
                with common[0], common[1], common[2], common[3], common[4], common[5], mock.patch.object(
                    module, "_gh_json", side_effect=responses
                ):
                    with self.assertRaisesRegex(ValueError, "changed"):
                        module.verify(
                            Path("control"),
                            Path("data"),
                            ".github/workflows/mission-spine-promote.yml",
                            release_id,
                            "candidate",
                            None,
                        )
        with mock.patch.dict(os.environ, {"GH_TOKEN": "token"}, clear=False):
            with self.assertRaisesRegex(ValueError, "mode"):
                module.verify(
                    Path("control"), None, ".github/workflows/mission-spine-promote.yml",
                    release_id, "attacker", None
                )

    def test_github_outputs_allow_safe_hash_keys_and_reject_unsafe_content(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "github-output"
            output.write_bytes(b"")

            module._write_outputs(
                output,
                {
                    "candidate_spec_sha256": "c" * 64,
                    "validator_run_attempt": "1",
                },
            )
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "candidate_spec_sha256=" + "c" * 64 + "\nvalidator_run_attempt=1\n",
            )

            for key, value in (
                ("9invalid", "safe"),
                ("invalid-key", "safe"),
                ("valid_key", "unsafe\nvalue"),
            ):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(
                    ValueError, "unsafe"
                ):
                    module._write_outputs(output, {key: value})


if __name__ == "__main__":
    unittest.main()
