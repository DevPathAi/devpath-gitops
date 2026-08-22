import base64
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "manage_production_kubeconfig",
    ROOT / "scripts/release/manage_production_kubeconfig.py",
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)


class ProductionKubeconfigTest(unittest.TestCase):
    def test_create_is_exclusive_mode_0600_and_cleanup_is_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            github_env = root / "github-env"
            github_env.touch()
            target = module.create(
                root,
                github_env,
                base64.b64encode(b"apiVersion: v1\nkind: Config\n").decode(),
                token_factory=lambda _: "a" * 32,
            )
            self.assertEqual(target.read_bytes(), b"apiVersion: v1\nkind: Config\n")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(github_env.read_text(), f"KUBECONFIG={target.as_posix()}\n")
            module.cleanup(root, str(target))
            self.assertFalse(target.exists())

    def test_preoccupied_regular_or_symlink_is_never_followed_or_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            github_env = root / "github-env"
            github_env.touch()
            occupied = root / (module.PREFIX + "b" * 32)
            occupied.write_bytes(b"keep")
            with self.assertRaisesRegex(ValueError, "unique"):
                module.create(
                    root,
                    github_env,
                    base64.b64encode(b"secret").decode(),
                    token_factory=lambda _: "b" * 32,
                )
            self.assertEqual(occupied.read_bytes(), b"keep")
            if hasattr(os, "symlink"):
                occupied.unlink()
                victim = root / "victim"
                victim.write_bytes(b"keep")
                try:
                    occupied.symlink_to(victim)
                except OSError:
                    self.skipTest("symlinks are unavailable")
                with self.assertRaisesRegex(ValueError, "unique"):
                    module.create(
                        root,
                        github_env,
                        base64.b64encode(b"secret").decode(),
                        token_factory=lambda _: "b" * 32,
                    )
                self.assertEqual(victim.read_bytes(), b"keep")
                with self.assertRaisesRegex(ValueError, "cleanup target"):
                    module.cleanup(root, str(occupied))

    def test_invalid_input_or_github_env_symlink_leaves_no_kubeconfig(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            github_env = root / "github-env"
            github_env.touch()
            with self.assertRaisesRegex(ValueError, "base64"):
                module.create(root, github_env, "not base64")
            self.assertEqual(list(root.glob(module.PREFIX + "*")), [])
            target = root / "env-target"
            target.touch()
            github_env.unlink()
            try:
                github_env.symlink_to(target)
            except OSError:
                self.skipTest("symlinks are unavailable")
            with self.assertRaisesRegex(ValueError, "GITHUB_ENV"):
                module.create(
                    root,
                    github_env,
                    base64.b64encode(b"secret").decode(),
                    token_factory=lambda _: "c" * 32,
                )
            self.assertEqual(list(root.glob(module.PREFIX + "*")), [])

    def test_staging_scope_uses_a_distinct_exact_name_and_is_cleanable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            github_env = root / "github-env"
            github_env.touch()
            target = module.create(
                root,
                github_env,
                base64.b64encode(b"apiVersion: v1\nkind: Config\n").decode(),
                scope="staging",
                token_factory=lambda _: "d" * 32,
            )
            self.assertEqual(
                target.name,
                "mission-spine-staging-kubeconfig-" + "d" * 32,
            )
            module.cleanup(root, str(target))
            with self.assertRaisesRegex(ValueError, "scope"):
                module.create(
                    root,
                    github_env,
                    base64.b64encode(b"secret").decode(),
                    scope="other",
                )


if __name__ == "__main__":
    unittest.main()
