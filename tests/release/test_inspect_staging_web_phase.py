import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "release"
FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-candidate-spec.json"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "inspect_staging_web_phase", SCRIPTS / "inspect_staging_web_phase.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class InspectStagingWebPhaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()
        cls.candidate = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.candidate_hash = "c" * 64

    def deployment(self, phase):
        deployment = self.module.build_patch(
            self.candidate, self.candidate_hash, phase
        )
        deployment["metadata"] = {
            "name": "devpath-web-staging",
            "namespace": "devpath-staging",
            "resourceVersion": "314159",
        }
        return deployment

    def inspect(self, deployment, output):
        completed = mock.Mock(returncode=0, stdout=json.dumps(deployment))
        with mock.patch.dict(
            "os.environ", {"KUBECONFIG": "test"}
        ), mock.patch.object(
            self.module.shutil, "which", return_value="kubectl"
        ), mock.patch.object(
            self.module,
            "resolve_release_bundle",
            return_value=(None, None, None, self.candidate, self.candidate_hash),
        ), mock.patch.object(
            self.module.subprocess, "run", return_value=completed
        ) as run:
            phase = self.module.inspect(
                ROOT,
                self.candidate["release_id"],
                ("prior", "mission-on"),
                output,
            )
        self.assertEqual(run.call_count, 1)
        self.assertIn("get", run.call_args.args[0])
        self.assertNotIn("patch", run.call_args.args[0])
        return phase

    def test_inspect_allows_exact_prior_or_promoted_target_and_writes_phase(self):
        for expected in ("prior", "mission-on"):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / "github-output"
                output.touch()
                actual = self.inspect(self.deployment(expected), output)
                self.assertEqual(actual, expected)
                self.assertEqual(
                    output.read_text(encoding="utf-8"),
                    f"current_phase={expected}\n",
                )

    def test_inspect_rejects_mission_off_without_emitting_output(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "github-output"
            output.touch()
            with self.assertRaisesRegex(ValueError, "not allowed"):
                self.inspect(self.deployment("mission-off"), output)
            self.assertEqual(output.read_text(encoding="utf-8"), "")

    def test_inspect_rejects_duplicate_or_unknown_allowed_phases(self):
        for allowed in (("prior", "prior"), ("prior", "unknown"), ()):
            with self.subTest(allowed=allowed), self.assertRaisesRegex(
                ValueError, "allowed staging phases"
            ):
                self.module.validate_allowed_phases(allowed)

    def test_inspect_requires_a_safe_existing_github_output_file(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing"
            with self.assertRaisesRegex(ValueError, "GITHUB_OUTPUT"):
                self.module.write_output(missing, "prior")


if __name__ == "__main__":
    unittest.main()
