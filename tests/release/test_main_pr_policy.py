import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "verify_main_pr_policy",
    ROOT / "scripts/release/verify_main_pr_policy.py",
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class MainPrPolicyTest(unittest.TestCase):
    @staticmethod
    def _git(root, *args, input_bytes=None):
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            input=input_bytes,
            capture_output=True,
            check=True,
        )
        return result.stdout.decode("utf-8").strip()

    def test_rejects_every_promotion_managed_selector(self):
        for path in sorted(module.PROMOTION_MANAGED_PATHS):
            with self.subTest(path=path), self.assertRaisesRegex(
                ValueError, "promotion-managed"
            ):
                module.validate_main_pr_delta([path], lambda *_: b"")

    def test_allows_only_exact_one_time_inert_job_bootstrap(self):
        before = b"apiVersion: batch/v1\nkind: Job\nmetadata:\n  name: devpath-flyway-migrate\nspec:\n  backoffLimit: 1\n"
        after = before.replace(b"spec:\n", b"spec:\n  suspend: true\n")
        blobs = {("base", module.MIGRATION_JOB_PATH): before, ("head", module.MIGRATION_JOB_PATH): after}
        module.validate_main_pr_delta(
            [module.MIGRATION_JOB_PATH], lambda revision, path: blobs[(revision, path)]
        )
        for mutation in (
            after.replace(b"true", b"false"),
            after + b"# unrelated\n",
            before,
            before.replace(b"metadata:\n", b"metadata:\n  suspend: true\n"),
        ):
            changed = dict(blobs)
            changed[("head", module.MIGRATION_JOB_PATH)] = mutation
            with self.subTest(mutation=mutation), self.assertRaisesRegex(
                ValueError, "inert migration"
            ):
                module.validate_main_pr_delta(
                    [module.MIGRATION_JOB_PATH],
                    lambda revision, path: changed[(revision, path)],
                )

    def test_unrelated_documentation_or_script_delta_is_allowed(self):
        module.validate_main_pr_delta(["README.md"], lambda *_: b"unused")

    def test_every_argo_managed_path_is_protected(self):
        for path in (
            "apps/devpath-admin/base/deployment.yaml",
            "apps/new-service/base/kustomization.yaml",
            "argocd/applicationset.yaml",
        ):
            with self.subTest(path=path), self.assertRaisesRegex(
                ValueError, "Argo-managed"
            ):
                module.validate_main_pr_delta([path], lambda *_: b"unused")

    def test_policy_implementation_and_target_workflow_are_immutable(self):
        for path in (
            *module.POLICY_MANAGED_PATHS,
            ".github/workflows/mission-spine-promote.yml",
            ".github/workflows/unrelated-spoof.yml",
            ".github/actions/spoof/action.yml",
            "scripts/release/validate_release_manifest.py",
            "tools/release-wrangler/wrangler.cjs",
            "release-manifests/schema-v1.json",
        ):
            with self.subTest(path=path), self.assertRaisesRegex(
                ValueError, "policy implementation"
            ):
                module.validate_main_pr_delta([path], lambda *_: b"unused")

    def test_base_owned_pull_request_target_workflow_runs_the_policy(self):
        workflow = (ROOT / module.POLICY_WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertIn("pull_request_target:", workflow)
        self.assertNotIn("pull_request:\n", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", workflow)
        self.assertIn('refs/pull/$PR_NUMBER/head', workflow)
        self.assertIn('test "$fetched_head" = "$POLICY_HEAD_SHA"', workflow)
        self.assertIn("scripts/release/verify_main_pr_policy.py", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("environment:", workflow)

    def test_actual_git_rename_copy_and_quoted_paths_cannot_hide_a_control_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._git(root, "init", "-q")
            self._git(root, "config", "user.name", "policy-test")
            self._git(root, "config", "user.email", "policy-test@example.invalid")
            protected = root / "scripts/release/guard.py"
            protected.parent.mkdir(parents=True)
            protected.write_text("print('guard')\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-q", "-m", "base")
            base = self._git(root, "rev-parse", "HEAD")

            outside = root / "docs/guard.py"
            outside.parent.mkdir()
            protected.rename(outside)
            self._git(root, "add", "-A")
            self._git(root, "commit", "-q", "-m", "rename")
            renamed = self._git(root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(ValueError, "policy implementation"):
                module.verify(root, base, renamed)

            copied = root / ".github/workflows/copied-policy.yml"
            copied.parent.mkdir(parents=True)
            copied.write_text(outside.read_text(encoding="utf-8"), encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-q", "-m", "copy")
            copy_head = self._git(root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(ValueError, "policy implementation"):
                module.verify(root, renamed, copy_head)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._git(root, "init", "-q")
            self._git(root, "config", "user.name", "policy-test")
            self._git(root, "config", "user.email", "policy-test@example.invalid")
            empty_tree = self._git(root, "mktree", input_bytes=b"")
            base = self._git(root, "commit-tree", empty_tree, "-m", "base")
            blob = self._git(root, "hash-object", "-w", "--stdin", input_bytes=b"name: spoof\n")
            workflows = self._git(
                root,
                "mktree",
                "-z",
                input_bytes=f'100644 blob {blob}\t"spoof\nname".yml\0'.encode(),
            )
            github = self._git(
                root,
                "mktree",
                "-z",
                input_bytes=f"040000 tree {workflows}\tworkflows\0".encode(),
            )
            tree = self._git(
                root,
                "mktree",
                "-z",
                input_bytes=f"040000 tree {github}\t.github\0".encode(),
            )
            head = self._git(root, "commit-tree", tree, "-p", base, "-m", "quoted")
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                module.verify(root, base, head)


if __name__ == "__main__":
    unittest.main()
