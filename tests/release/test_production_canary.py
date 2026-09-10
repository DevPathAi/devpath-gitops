import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "build_production_canary.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_production_canary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ProductionCanaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.canary = load_module()

    def test_run_identity_rejects_bool_float_and_non_attempt_one_before_io(self):
        for run_id, attempt in ((True, 1), (1.0, 1), (1, True), (1, 1.0), (1, 2)):
            with self.subTest(run_id=run_id, attempt=attempt), self.assertRaises(
                ValueError
            ):
                self.canary.build_canary(
                    Path("does-not-exist"),
                    "ms-20260817-example",
                    "a" * 40,
                    {},
                    {},
                    0,
                    0,
                    run_id,
                    attempt,
                )

    def test_runtime_image_form_uses_canonical_oci_contract(self):
        manifest = "sha256:" + "1" * 64
        config = "sha256:" + "2" * 64
        authenticated = {"manifest_digest": manifest, "config_digest": config}
        self.assertTrue(
            self.canary.runtime_image_matches(
                manifest, "linux-amd64-manifest", authenticated
            )
        )
        self.assertTrue(
            self.canary.runtime_image_matches(config, "config", authenticated)
        )
        self.assertFalse(
            self.canary.runtime_image_matches(manifest, "manifest", authenticated)
        )

    def test_output_is_exclusive_and_never_follows_a_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "evidence.json"
            self.canary._write_new(output, {"schema_version": 1})
            self.assertEqual(output.read_bytes(), b'{"schema_version":1}\n')
            with self.assertRaises(ValueError):
                self.canary._write_new(output, {"schema_version": 2})

            target = root / "target.json"
            link = root / "broken-evidence.json"
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")
            with self.assertRaises(ValueError):
                self.canary._write_new(link, {"schema_version": 3})
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
