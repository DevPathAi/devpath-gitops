import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "verify_post_promotion_staging_context.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "verify_post_promotion_staging_context", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PostPromotionStagingContextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def valid_environment(self, workflow="mission-spine-promote.yml"):
        return {
            "GITHUB_REPOSITORY": "DevPathAi/devpath-gitops",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_NAME": "main",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_WORKFLOW_SHA": "a" * 40,
            "GITHUB_WORKFLOW_REF": (
                "DevPathAi/devpath-gitops/"
                f".github/workflows/{workflow}@refs/heads/main"
            ),
        }

    def test_run_coordinates_bind_the_original_protected_main_workflow(self):
        self.module.validate_run_coordinates(
            self.valid_environment(),
            "a" * 40,
            ".github/workflows/mission-spine-promote.yml",
            "mission-on",
        )
        for field, value in (
            ("GITHUB_SHA", "b" * 40),
            ("GITHUB_WORKFLOW_SHA", "b" * 40),
            ("GITHUB_RUN_ATTEMPT", "2"),
            ("GITHUB_REF_NAME", "feature"),
        ):
            invalid = self.valid_environment()
            invalid[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.module.validate_run_coordinates(
                    invalid,
                    "a" * 40,
                    ".github/workflows/mission-spine-promote.yml",
                    "mission-on",
                )

    def test_run_coordinates_bind_the_exact_rollback_workflow(self):
        self.module.validate_run_coordinates(
            self.valid_environment("mission-spine-rollback.yml"),
            "a" * 40,
            ".github/workflows/mission-spine-rollback.yml",
            "prior",
        )
        with self.assertRaises(ValueError):
            self.module.validate_run_coordinates(
                self.valid_environment("mission-spine-rollback.yml"),
                "a" * 40,
                ".github/workflows/mission-spine-rollback.yml",
                "mission-on",
            )

    def test_transition_state_accepts_resumed_promotion_and_rollback_prior(self):
        head = "b" * 40
        self.module.validate_transition_state(
            {"phase": "services", "web_phase": "base", "current_commit": "a" * 40},
            {
                "phase": "mission-on",
                "web_phase": "mission-on",
                "current_commit": head,
                "on_commit": head,
            },
            head,
            "mission-on",
        )
        for state in (
            {"phase": "migration", "web_phase": "base", "current_commit": head},
            {"phase": "services", "web_phase": "prior", "current_commit": head},
        ):
            with self.subTest(state=state):
                self.module.validate_transition_state(
                    {
                        "phase": "mission-on",
                        "web_phase": "mission-on",
                        "current_commit": "a" * 40,
                    },
                    state,
                    head,
                    "prior",
                )

    def test_transition_state_rejects_wrong_final_lineage(self):
        with self.assertRaises(ValueError):
            self.module.validate_transition_state(
                {"phase": "mission-on", "web_phase": "mission-on"},
                {
                    "phase": "rollback-off",
                    "web_phase": "mission-off",
                    "current_commit": "b" * 40,
                },
                "b" * 40,
                "prior",
            )

    def test_live_refs_are_exact_and_rechecked(self):
        branch = {
            "name": "main",
            "protected": True,
            "commit": {"sha": "b" * 40},
        }
        release = {"commit": {"sha": "c" * 40}}
        self.module.validate_live_refs(branch, release, "b" * 40, "c" * 40)
        for invalid_branch, invalid_release in (
            ({**branch, "protected": False}, release),
            (branch, {"commit": {"sha": "d" * 40}}),
        ):
            with self.assertRaises(ValueError):
                self.module.validate_live_refs(
                    invalid_branch, invalid_release, "b" * 40, "c" * 40
                )

    def verify_fixture(self, *, git_side_effect=None, github_side_effect=None):
        release_id = "ms-20990101-contract-fixture"
        control_head = "a" * 40
        current_head = "b" * 40
        release_head = "c" * 40
        workflow = ".github/workflows/mission-spine-promote.yml"
        workflow_bytes = b"name: exact-control\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = root / "control"
            current = root / "current"
            data = root / "data"
            for checkout in (control, current):
                path = checkout / workflow
                path.parent.mkdir(parents=True)
                path.write_bytes(workflow_bytes)
            data.mkdir()
            release_path = data / "release.json"
            release_path.write_bytes(b"{}\n")
            environment = self.valid_environment()
            environment["GH_TOKEN"] = "test-token"

            def default_git(_root, *args, binary=False):
                return workflow_bytes if binary else ""

            main = {
                "name": "main",
                "protected": True,
                "commit": {"sha": current_head},
            }
            release = {"commit": {"sha": release_head}}
            github_values = github_side_effect or [main, release, main, release]
            with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
                self.module,
                "_require_clean",
                side_effect=[control_head, current_head, release_head],
            ), mock.patch.object(
                self.module, "_git", side_effect=git_side_effect or default_git
            ) as git_call, mock.patch.object(
                self.module, "validate_release_data_tree"
            ) as data_check, mock.patch.object(
                self.module,
                "resolve_release_bundle",
                return_value=(release_path, None, None, {}, "d" * 64),
            ), mock.patch.object(
                self.module,
                "inspect_chain",
                side_effect=[
                    {
                        "phase": "services",
                        "web_phase": "base",
                        "current_commit": control_head,
                    },
                    {
                        "phase": "mission-on",
                        "web_phase": "mission-on",
                        "current_commit": current_head,
                        "on_commit": current_head,
                    },
                ],
            ) as chain_check, mock.patch.object(
                self.module, "_gh_json", side_effect=github_values
            ) as github_call:
                self.module.verify(
                    control,
                    current,
                    data,
                    release_id,
                    current_head,
                    "mission-on",
                    workflow,
                )
            data_check.assert_called_once_with(data.resolve(), release_id, release_head, sealed=True)
            self.assertEqual(chain_check.call_count, 2)
            self.assertEqual(github_call.call_count, 4)
            self.assertTrue(
                any(call.args[1:3] == ("merge-base", "--is-ancestor") for call in git_call.call_args_list)
            )

    def test_verify_authenticates_the_full_resumable_transition_and_rechecks_refs(self):
        self.verify_fixture()

    def test_verify_rejects_nonancestor_control_and_live_ref_drift(self):
        workflow_bytes = b"name: exact-control\n"

        def nonancestor(_root, *args, binary=False):
            if args and args[0] == "merge-base":
                raise ValueError("not ancestor")
            return workflow_bytes if binary else ""

        with self.assertRaisesRegex(ValueError, "not ancestor"):
            self.verify_fixture(git_side_effect=nonancestor)

        main = {
            "name": "main",
            "protected": True,
            "commit": {"sha": "b" * 40},
        }
        drifted = {
            "name": "main",
            "protected": True,
            "commit": {"sha": "e" * 40},
        }
        release = {"commit": {"sha": "c" * 40}}
        with self.assertRaisesRegex(ValueError, "protected main"):
            self.verify_fixture(
                github_side_effect=[main, release, drifted, release]
            )

    def test_dirty_checkout_fails_before_transition_authentication(self):
        with mock.patch.object(
            self.module,
            "_git",
            side_effect=["a" * 40, "?? untracked-control"],
        ), self.assertRaisesRegex(ValueError, "not clean"):
            self.module._require_clean(Path("control"), "control")


if __name__ == "__main__":
    unittest.main()
