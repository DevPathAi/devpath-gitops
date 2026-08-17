import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "release"
if str(SCRIPTS) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPTS))
CANDIDATE = ROOT / "tests" / "release" / "fixtures" / "valid-candidate-spec.json"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CandidateArtifactAuthenticationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verifier = load_module(
            SCRIPTS / "verify_release_artifacts.py", "candidate_artifact_verifier"
        )
        cls.candidate_raw = CANDIDATE.read_bytes()
        cls.candidate = json.loads(cls.candidate_raw.decode("utf-8"))
        cls.release_id = cls.candidate["release_id"]
        cls.base_sha = cls.candidate["gitops"]["base_sha"]
        cls.head_sha = "f" * 40
        cls.workflow_raw = b"""name: Candidate
on:
  workflow_dispatch:
    inputs:
      release_id:
        required: true
        type: string
jobs: {}
"""

    def _trust_values(self, run_id=1201, attempt=1):
        run = {
            "id": run_id,
            "status": "completed",
            "conclusion": "success",
            "event": "workflow_dispatch",
            "path": self.verifier.CANDIDATE_WORKFLOW,
            "repository": {"full_name": self.verifier.CANDIDATE_REPOSITORY},
            "head_repository": {"full_name": self.verifier.CANDIDATE_REPOSITORY},
            "head_branch": f"release/candidate-{self.release_id}",
            "head_sha": self.head_sha,
            "run_attempt": attempt,
        }
        branch = {
            "name": "main",
            "protected": True,
            "commit": {"sha": self.base_sha},
        }
        commit = {
            "sha": self.head_sha,
            "parents": [{"sha": self.base_sha}],
        }
        comparison = {
            "status": "ahead",
            "ahead_by": 1,
            "behind_by": 0,
            "total_commits": 1,
            "files": [
                {
                    "filename": (
                        "release-manifests/candidates/"
                        f"{self.release_id}.candidate-spec.json"
                    ),
                    "status": "added",
                }
            ],
        }
        return run, branch, commit, comparison

    def test_trust_binds_exact_release_branch_one_file_child_and_workflow(self):
        values = self._trust_values()
        self.verifier.validate_candidate_producer_trust(
            self.release_id,
            self.candidate,
            self.candidate_raw,
            *values,
            self.workflow_raw,
            self.candidate_raw,
        )

        mutations = (
            (0, "head_branch", "release/lookalike"),
            (0, "event", "push"),
            (0, "path", ".github/workflows/lookalike.yml"),
            (0, "repository", {"full_name": "fork/devpath-gitops"}),
            (0, "head_repository", {"full_name": "fork/devpath-gitops"}),
            (1, "protected", False),
            (1, "commit", {"sha": "2" * 40}),
            (2, "parents", [{"sha": "2" * 40}]),
            (3, "ahead_by", 2),
            (3, "total_commits", 2),
            (3, "files", []),
        )
        for index, field, value in mutations:
            with self.subTest(index=index, field=field):
                invalid = [copy.deepcopy(item) for item in values]
                invalid[index][field] = value
                with self.assertRaises(ValueError):
                    self.verifier.validate_candidate_producer_trust(
                        self.release_id,
                        self.candidate,
                        self.candidate_raw,
                        *invalid,
                        self.workflow_raw,
                        self.candidate_raw,
                    )

        with self.assertRaises(ValueError):
            self.verifier.validate_candidate_producer_trust(
                self.release_id,
                self.candidate,
                self.candidate_raw,
                *values,
                self.workflow_raw.replace(b"required: true", b"required: false"),
                self.candidate_raw,
            )
        with self.assertRaisesRegex(ValueError, "source blob"):
            self.verifier.validate_candidate_producer_trust(
                self.release_id,
                self.candidate,
                self.candidate_raw,
                *values,
                self.workflow_raw,
                self.candidate_raw + b" ",
            )

    def test_candidate_zip_is_exact_regular_root_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "candidate.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as value:
                value.writestr("candidate-spec.json", self.candidate_raw)
            destination = root / "out"
            self.verifier.extract_candidate_spec_archive(archive, destination)
            self.assertEqual(
                (destination / "candidate-spec.json").read_bytes(), self.candidate_raw
            )

            for name, entries in (
                ("traversal", [("../candidate-spec.json", self.candidate_raw)]),
                (
                    "extra",
                    [
                        ("candidate-spec.json", self.candidate_raw),
                        ("raw.log", b"secret"),
                    ],
                ),
                (
                    "duplicate",
                    [
                        ("candidate-spec.json", self.candidate_raw),
                        ("candidate-spec.json", self.candidate_raw),
                    ],
                ),
            ):
                with self.subTest(name=name):
                    invalid = root / f"{name}.zip"
                    with zipfile.ZipFile(
                        invalid, "w", compression=zipfile.ZIP_STORED
                    ) as value:
                        for filename, raw in entries:
                            value.writestr(filename, raw)
                    with self.assertRaises(ValueError):
                        self.verifier.extract_candidate_spec_archive(
                            invalid, root / f"out-{name}"
                        )

            link = root / "link.zip"
            with zipfile.ZipFile(link, "w") as value:
                info = zipfile.ZipInfo("candidate-spec.json")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                value.writestr(info, b"target")
            with self.assertRaises(ValueError):
                self.verifier.extract_candidate_spec_archive(link, root / "out-link")

    def test_artifact_listing_paginates_and_parses_only_exact_names(self):
        exact = (
            f"{self.release_id}-candidate-spec-run-1201-attempt-2"
        )
        first_page = [
            {"name": f"unrelated-{index}", "id": index + 1}
            for index in range(100)
        ]
        responses = [
            {"artifacts": first_page},
            {"artifacts": [{"name": exact, "id": 4001}]},
        ]
        with mock.patch.object(
            self.verifier, "_run_json", side_effect=responses
        ) as run_json:
            values = self.verifier._list_candidate_artifacts({}, self.release_id)
        self.assertEqual(values[0][1:], (1201, 2))
        self.assertEqual(run_json.call_count, 2)

    def test_candidate_workflow_requires_the_exact_release_branch(self):
        workflow = (
            ROOT / ".github" / "workflows" / "mission-spine-candidate.yml"
        ).read_bytes()
        self.verifier.validate_workflow_dispatch_inputs(
            workflow, {"release_id"}, "candidate-spec"
        )
        text = workflow.decode("utf-8")
        self.assertIn(
            'test "$GITHUB_REF_NAME" = "release/candidate-$RELEASE_ID"', text
        )
        self.assertNotIn('test "$GITHUB_REF_NAME" != "main"', text)

    def _verify_with_runs(self, runs):
        branch = {
            "name": "main",
            "protected": True,
            "commit": {"sha": self.base_sha},
        }

        artifacts = []
        run_by_id = {}
        for run_id, attempt in runs:
            run, _, _, _ = self._trust_values(run_id, attempt)
            run_by_id[run_id] = run
            artifacts.append(
                (
                    {
                        "id": run_id + 9000,
                        "name": (
                            f"{self.release_id}-candidate-spec-run-{run_id}"
                            f"-attempt-{attempt}"
                        ),
                        "expired": False,
                        "expires_at": "2099-01-01T00:00:00Z",
                        "size_in_bytes": 100,
                        "digest": "sha256:" + "1" * 64,
                        "workflow_run": {"id": run_id, "head_sha": self.head_sha},
                    },
                    run_id,
                    attempt,
                )
            )

        def run_json(args, _env):
            endpoint = args[-1]
            if endpoint.endswith("/branches/main"):
                return branch
            if "/actions/runs/" in endpoint:
                return run_by_id[int(endpoint.rsplit("/", 1)[1])]
            if "/commits/" in endpoint:
                return self._trust_values()[2]
            if "/compare/" in endpoint:
                return self._trust_values()[3]
            raise AssertionError(endpoint)

        def download(_env, _artifact_id, _metadata, destination):
            destination.mkdir(parents=True)
            (destination / "candidate-spec.json").write_bytes(self.candidate_raw)

        def source(_repository, path, _source_sha, _env):
            if path == self.verifier.CANDIDATE_WORKFLOW:
                return self.workflow_raw
            return self.candidate_raw

        with mock.patch.object(
            self.verifier, "_list_candidate_artifacts", return_value=artifacts
        ), mock.patch.object(
            self.verifier, "_run_json", side_effect=run_json
        ), mock.patch.object(
            self.verifier, "download_candidate_spec_archive", side_effect=download
        ), mock.patch.object(
            self.verifier, "_repository_file_bytes", side_effect=source
        ):
            return self.verifier.verify_candidate_artifact(
                {}, self.release_id, self.candidate, self.candidate_raw
            )

    def test_unique_current_attempt_candidate_run_is_accepted(self):
        result = self._verify_with_runs([(1201, 2)])
        self.assertEqual(result["id"], 1201)
        self.assertEqual(result["run_attempt"], 2)

    def test_competing_fresh_candidate_runs_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self._verify_with_runs([(1201, 1), (1202, 1)])

    def test_stale_attempt_artifact_is_not_eligible(self):
        run, _, _, _ = self._trust_values(1201, 2)
        artifact = {
            "id": 9201,
            "name": f"{self.release_id}-candidate-spec-run-1201-attempt-1",
            "expired": False,
            "expires_at": "2099-01-01T00:00:00Z",
            "workflow_run": {"id": 1201, "head_sha": self.head_sha},
        }
        branch = {
            "name": "main",
            "protected": True,
            "commit": {"sha": self.base_sha},
        }

        def run_json(args, _env):
            return branch if args[-1].endswith("/branches/main") else run

        with mock.patch.object(
            self.verifier,
            "_list_candidate_artifacts",
            return_value=[(artifact, 1201, 1)],
        ), mock.patch.object(self.verifier, "_run_json", side_effect=run_json):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                self.verifier.verify_candidate_artifact(
                    {}, self.release_id, self.candidate, self.candidate_raw
                )


if __name__ == "__main__":
    unittest.main()
