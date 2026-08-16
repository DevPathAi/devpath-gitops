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
RELEASE = ROOT / "tests" / "release" / "fixtures" / "valid-release.json"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PrivacyApprovalTrustTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_module(
            SCRIPTS / "validate_release_manifest.py", "privacy_release_validator"
        )
        cls.verifier = load_module(
            SCRIPTS / "verify_release_artifacts.py", "privacy_release_verifier"
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
        ref = release["analytics_privacy_approval"]["evidence"]
        ref["run_attempt"] = 1
        ref["artifact_name"] = (
            f'{release["release_id"]}-privacy-approval-run-'
            f'{ref["workflow_run_id"]}-attempt-1'
        )
        return release

    def _payload(self):
        privacy = self.candidate["analytics_privacy"]
        approved_at = "2098-12-31T00:00:00Z"
        return {
            "candidate_spec_sha256": self.candidate_sha,
            "status": "passed",
            "producer_run_id": 102,
            "producer_run_attempt": 1,
            "approved_at": approved_at,
            "collection_mode": privacy["collection_mode"],
            "region": privacy["region"],
            "project_identity": privacy["project_identity"],
            "retention_days": privacy["retention_days"],
            "access_owner": privacy["access_owner"],
            "deletion_runbook": privacy["deletion_runbook"],
            "approval_environment": "mission-spine-privacy-approval",
            "approval_environment_id": 8001,
            "approval_job_name": "Approve analytics privacy release",
            "approved_by": "privacy-reviewer",
            "approved_by_id": 8002,
            "approval_effective_at": approved_at,
        }

    def test_release_requires_attempt_one_run_scoped_privacy_artifact(self):
        release = self._release()
        self.validator.validate_release_manifest(
            release, self.candidate, self.candidate_sha, RELEASE
        )
        retry = self._release()
        ref = retry["analytics_privacy_approval"]["evidence"]
        ref["run_attempt"] = 2
        ref["artifact_name"] = (
            f'{retry["release_id"]}-privacy-approval-run-'
            f'{ref["workflow_run_id"]}-attempt-2'
        )
        with self.assertRaisesRegex(ValueError, "attempt 1"):
            self.validator.validate_release_manifest(
                retry, self.candidate, self.candidate_sha, RELEASE
            )

    def test_privacy_evidence_exactly_binds_candidate_and_protected_approval(self):
        payload = self._payload()
        self.verifier.validate_evidence_payload(
            "privacy-approval", payload, self.candidate_sha, self.candidate, 102, 1
        )
        for field, value in (
            ("producer_run_attempt", 2),
            ("collection_mode", "forged"),
            ("approved_at", "2098-12-30T23:59:59Z"),
            ("approval_effective_at", "2098-12-30T23:59:59Z"),
            ("approval_environment", "unprotected"),
            ("approval_job_name", "lookalike"),
            ("approved_by", "bad login"),
        ):
            with self.subTest(field=field):
                invalid = copy.deepcopy(payload)
                invalid[field] = value
                with self.assertRaises(ValueError):
                    self.verifier.validate_evidence_payload(
                        "privacy-approval",
                        invalid,
                        self.candidate_sha,
                        self.candidate,
                        102,
                        invalid.get("producer_run_attempt", 1),
                    )
        extra = copy.deepcopy(payload)
        extra["approval_source_sha"] = self.candidate["analytics_privacy"][
            "approval_source_sha"
        ]
        with self.assertRaises(ValueError):
            self.verifier.validate_evidence_payload(
                "privacy-approval", extra, self.candidate_sha, self.candidate, 102, 1
            )

    def test_privacy_workflow_inputs_and_current_protected_main_are_exact(self):
        raw = b"""name: Privacy approval\non:\n  workflow_dispatch:\n    inputs:\n      release_id:\n        required: true\n        type: string\n      candidate_spec_sha256:\n        required: true\n        type: string\n      approval_source_sha:\n        required: true\n        type: string\njobs: {}\n"""
        self.verifier.validate_workflow_dispatch_inputs(
            raw,
            {"release_id", "candidate_spec_sha256", "approval_source_sha"},
            "privacy-approval",
        )
        head = self.candidate["analytics_privacy"]["approval_source_sha"]
        branch = {"name": "main", "protected": True, "commit": {"sha": head}}
        run = {
            "run_attempt": 1,
            "head_branch": "main",
            "head_sha": head,
            "repository": {"full_name": "DevPathAi/documents"},
            "head_repository": {"full_name": "DevPathAi/documents"},
        }
        self.verifier.validate_privacy_approval_trust(branch, run, head)
        for target, field, value in (
            ("branch", "protected", False),
            ("branch", "commit", {"sha": "0" * 40}),
            ("run", "run_attempt", 2),
            ("run", "head_branch", "feature/privacy"),
            ("run", "head_repository", {"full_name": "fork/documents"}),
        ):
            with self.subTest(target=target, field=field):
                bad_branch = copy.deepcopy(branch)
                bad_run = copy.deepcopy(run)
                (bad_branch if target == "branch" else bad_run)[field] = value
                with self.assertRaises(ValueError):
                    self.verifier.validate_privacy_approval_trust(
                        bad_branch, bad_run, head
                    )

    def test_unique_privacy_dispatch_rejects_retry_and_competing_fresh_run(self):
        head = self.candidate["analytics_privacy"]["approval_source_sha"]
        workflow = self.validator.PRODUCER_WORKFLOWS["privacy-approval"]

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
            return [{"name": name, "expired": False, "workflow_run": {"id": run_id}}]

        with mock.patch.object(
            self.verifier,
            "_list_protected_runs",
            return_value=[run(102, 2), run(106, 1)],
        ), mock.patch.object(
            self.verifier, "_list_named_artifacts", side_effect=artifacts
        ):
            self.verifier.assert_unique_protected_producer_run(
                {}, "DevPathAi/documents", head, workflow,
                self.candidate["release_id"], "privacy-approval", 106
            )
        with mock.patch.object(
            self.verifier,
            "_list_protected_runs",
            return_value=[run(106, 1), run(107, 1)],
        ), mock.patch.object(
            self.verifier, "_list_named_artifacts", side_effect=artifacts
        ):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                self.verifier.assert_unique_protected_producer_run(
                    {}, "DevPathAi/documents", head, workflow,
                    self.candidate["release_id"], "privacy-approval", 106
                )

    def test_privacy_outer_zip_is_exact_regular_single_evidence_file(self):
        payload = json.dumps(self._payload(), separators=(",", ":")).encode()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            valid = root / "valid.zip"
            with zipfile.ZipFile(valid, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("evidence.json", payload)
            destination = root / "out"
            self.verifier.extract_privacy_approval_archive(valid, destination)
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
                        self.verifier.extract_privacy_approval_archive(
                            bad, root / f"out-{name}"
                        )
            link = root / "link.zip"
            with zipfile.ZipFile(link, "w") as archive:
                info = zipfile.ZipInfo("evidence.json")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, b"target")
            with self.assertRaises(ValueError):
                self.verifier.extract_privacy_approval_archive(link, root / "out-link")

    def test_direct_user_or_active_team_membership_is_proven_fail_closed(self):
        direct = {
            "protection_rules": [
                {
                    "type": "required_reviewers",
                    "reviewers": [
                        {
                            "type": "User",
                            "reviewer": {"id": 8002, "login": "privacy-reviewer"},
                        },
                        {
                            "type": "Team",
                            "reviewer": {"id": 8101, "slug": "privacy-release"},
                        },
                    ],
                }
            ]
        }
        with mock.patch.object(self.verifier, "_run_json_optional") as optional:
            self.assertEqual(
                self.verifier._configured_team_memberships(
                    {}, "DevPathAi/documents", direct, "privacy-reviewer", 8002
                ),
                set(),
            )
        optional.assert_not_called()

        teams = copy.deepcopy(direct)
        teams["protection_rules"][0]["reviewers"] = [
            {
                "type": "Team",
                "reviewer": {"id": 8101, "slug": "privacy-release"},
            },
            {
                "type": "Team",
                "reviewer": {"id": 8102, "slug": "platform-release"},
            },
        ]
        with mock.patch.object(
            self.verifier,
            "_run_json_optional",
            side_effect=[{"state": "active", "role": "member"}, None],
        ):
            self.assertEqual(
                self.verifier._configured_team_memberships(
                    {}, "DevPathAi/documents", teams, "privacy-reviewer", 8002
                ),
                {8101},
            )
        for membership in (
            {"state": "pending", "role": "member"},
            {"state": "active", "role": "admin"},
            None,
        ):
            with self.subTest(membership=membership), mock.patch.object(
                self.verifier,
                "_run_json_optional",
                side_effect=[membership, membership],
            ):
                self.assertEqual(
                    self.verifier._configured_team_memberships(
                        {}, "DevPathAi/documents", teams, "privacy-reviewer", 8002
                    ),
                    set(),
                )


if __name__ == "__main__":
    unittest.main()
