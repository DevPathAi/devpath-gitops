import copy
import binascii
import hashlib
import importlib.util
import json
import jsonschema
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "release"
if str(SCRIPTS) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPTS))

CANDIDATE_FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-candidate-spec.json"
RELEASE_FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-release.json"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _tar_octal(value: int, length: int) -> bytes:
    return f"{value:0{length - 1}o}\0".encode("ascii")


def _tar_header(name: str, size: int, type_flag: bytes = b"0") -> bytes:
    header = bytearray(512)
    encoded = name.encode("ascii")
    header[0 : len(encoded)] = encoded
    header[100:108] = _tar_octal(0o644, 8)
    header[108:116] = _tar_octal(0, 8)
    header[116:124] = _tar_octal(0, 8)
    header[124:136] = _tar_octal(size, 12)
    header[136:148] = _tar_octal(0, 12)
    header[148:156] = b" " * 8
    header[156:157] = type_flag
    header[257:263] = b"ustar\0"
    header[263:265] = b"00"
    header[329:337] = _tar_octal(0, 8)
    header[337:345] = _tar_octal(0, 8)
    checksum = sum(header)
    header[148:156] = f"{checksum:06o}\0 ".encode("ascii")
    return bytes(header)


def _canonical_tar(entries, *, type_flag: bytes = b"0") -> bytes:
    chunks = []
    for name, content in entries:
        chunks.extend((_tar_header(name, len(content), type_flag), content))
        chunks.append(b"\0" * ((-len(content)) % 512))
    chunks.append(b"\0" * 1024)
    return b"".join(chunks)


def _canonical_gzip(tar_bytes: bytes) -> bytes:
    chunks = [b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03"]
    for offset in range(0, len(tar_bytes), 65_535):
        block = tar_bytes[offset : offset + 65_535]
        final = offset + len(block) == len(tar_bytes)
        chunks.append(bytes([1 if final else 0]))
        chunks.append(struct.pack("<HH", len(block), (~len(block)) & 0xFFFF))
        chunks.append(block)
    chunks.append(struct.pack("<II", binascii.crc32(tar_bytes), len(tar_bytes)))
    return b"".join(chunks)


def _home_archive(entries=None, *, type_flag: bytes = b"0") -> bytes:
    values = entries or [
        ("dist/assets/app.js", b"console.log('ok');\n"),
        ("dist/index.html", b"<!doctype html>\n"),
    ]
    return _canonical_gzip(_canonical_tar(values, type_flag=type_flag))


class HomeDistTrustTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_module(
            SCRIPTS / "validate_release_manifest.py", "home_dist_validator"
        )
        cls.verifier = load_module(
            SCRIPTS / "verify_release_artifacts.py", "home_dist_verifier"
        )
        cls.sealer = load_module(
            SCRIPTS / "seal_release_manifest.py", "home_dist_sealer"
        )
        cls.candidate = json.loads(CANDIDATE_FIXTURE.read_text(encoding="utf-8"))
        cls.release = json.loads(RELEASE_FIXTURE.read_text(encoding="utf-8"))
        cls.candidate_sha = hashlib.sha256(CANDIDATE_FIXTURE.read_bytes()).hexdigest()

    def _release(self):
        release = copy.deepcopy(self.release)
        release["candidate_spec"]["sha256"] = self.candidate_sha

        def replace(value):
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key == "candidate_spec_sha256":
                        value[key] = self.candidate_sha
                    else:
                        replace(nested)
            elif isinstance(value, list):
                for nested in value:
                    replace(nested)

        replace(release)
        artifact = release["home_dist_artifact"]
        artifact["run_attempt"] = 1
        artifact["artifact_name"] = (
            f'{release["release_id"]}-home-dist-run-'
            f'{artifact["workflow_run_id"]}-attempt-1'
        )
        return release

    def _home_payload(self, attempt=1):
        return {
            "candidate_spec_sha256": self.candidate_sha,
            "status": "passed",
            "producer_run_id": 101,
            "producer_run_attempt": attempt,
            "home_source_sha": self.candidate["home"]["source_sha"],
            "dist_sha256": self.candidate["home"]["dist_sha256"],
        }

    def test_release_ref_requires_run_scoped_attempt_one_name(self):
        release = self._release()
        self.validator.validate_release_manifest(
            release,
            copy.deepcopy(self.candidate),
            self.candidate_sha,
            RELEASE_FIXTURE,
        )

        retry = self._release()
        artifact = retry["home_dist_artifact"]
        artifact["run_attempt"] = 2
        artifact["artifact_name"] = (
            f'{retry["release_id"]}-home-dist-run-'
            f'{artifact["workflow_run_id"]}-attempt-2'
        )
        with self.assertRaisesRegex(ValueError, "attempt 1"):
            self.validator.validate_release_manifest(
                retry,
                copy.deepcopy(self.candidate),
                self.candidate_sha,
                RELEASE_FIXTURE,
            )

        schema = json.loads(
            (ROOT / "release-manifests" / "schema-v1.json").read_text(encoding="utf-8")
        )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(schema).validate(retry)

    def test_home_evidence_attempt_two_is_rejected(self):
        self.verifier.validate_evidence_payload(
            "home-dist",
            self._home_payload(),
            self.candidate_sha,
            self.candidate,
            101,
            1,
        )
        with self.assertRaisesRegex(ValueError, "attempt must be 1"):
            self.verifier.validate_evidence_payload(
                "home-dist",
                self._home_payload(2),
                self.candidate_sha,
                self.candidate,
                101,
                2,
            )

    def test_home_workflow_only_accepts_exact_required_string_inputs(self):
        raw = b"""name: Home dist\non:\n  workflow_dispatch:\n    inputs:\n      release_id:\n        required: true\n        type: string\n      candidate_spec_sha256:\n        required: true\n        type: string\n      home_source_sha:\n        required: true\n        type: string\n      dist_sha256:\n        required: true\n        type: string\njobs: {}\n"""
        self.verifier.validate_workflow_dispatch_inputs(
            raw,
            {"release_id", "candidate_spec_sha256", "home_source_sha", "dist_sha256"},
            "home-dist",
        )
        for invalid in (
            raw.replace(b"required: true", b"required: false", 1),
            raw.replace(b"on:\n", b"on:\n  push:\n", 1),
        ):
            with self.assertRaises(ValueError):
                self.verifier.validate_workflow_dispatch_inputs(
                    invalid,
                    {
                        "release_id",
                        "candidate_spec_sha256",
                        "home_source_sha",
                        "dist_sha256",
                    },
                    "home-dist",
                )

    def test_home_run_binds_current_protected_master(self):
        head = self.candidate["home"]["source_sha"]
        branch = {"name": "master", "protected": True, "commit": {"sha": head}}
        run = {
            "run_attempt": 1,
            "head_branch": "master",
            "head_sha": head,
            "repository": {"full_name": "DevPathAi/devpath-home-page"},
            "head_repository": {"full_name": "DevPathAi/devpath-home-page"},
        }
        self.verifier.validate_home_master_trust(branch, run, head)
        for target, field, value in (
            ("branch", "protected", False),
            ("branch", "commit", {"sha": "0" * 40}),
            ("run", "run_attempt", 2),
            ("run", "head_branch", "feature/home"),
            ("run", "head_repository", {"full_name": "fork/devpath-home-page"}),
        ):
            with self.subTest(target=target, field=field):
                bad_branch = copy.deepcopy(branch)
                bad_run = copy.deepcopy(run)
                (bad_branch if target == "branch" else bad_run)[field] = value
                with self.assertRaises(ValueError):
                    self.verifier.validate_home_master_trust(bad_branch, bad_run, head)

    def test_home_outer_zip_is_downloaded_and_safely_extracted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "artifact.zip"
            dist = _home_archive()
            evidence = json.dumps(self._home_payload(), separators=(",", ":")).encode()
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
                bundle.writestr("dist.tar.gz", dist)
                bundle.writestr("evidence.json", evidence)
            destination = root / "out"
            self.verifier.extract_home_artifact_archive(archive, destination)
            self.assertEqual((destination / "dist.tar.gz").read_bytes(), dist)
            self.assertEqual((destination / "evidence.json").read_bytes(), evidence)

            for name, entries in (
                (
                    "traversal",
                    [("../dist.tar.gz", dist), ("evidence.json", evidence)],
                ),
                (
                    "extra",
                    [
                        ("dist.tar.gz", dist),
                        ("evidence.json", evidence),
                        ("raw.log", b"secret"),
                    ],
                ),
                (
                    "duplicate",
                    [
                        ("dist.tar.gz", dist),
                        ("dist.tar.gz", dist),
                        ("evidence.json", evidence),
                    ],
                ),
            ):
                with self.subTest(name=name):
                    bad = root / f"{name}.zip"
                    with zipfile.ZipFile(bad, "w", compression=zipfile.ZIP_STORED) as bundle:
                        for filename, content in entries:
                            bundle.writestr(filename, content)
                    with self.assertRaises(ValueError):
                        self.verifier.extract_home_artifact_archive(
                            bad, root / f"out-{name}"
                        )

            link = root / "link.zip"
            with zipfile.ZipFile(link, "w", compression=zipfile.ZIP_STORED) as bundle:
                link_info = zipfile.ZipInfo("dist.tar.gz")
                link_info.create_system = 3
                link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
                bundle.writestr(link_info, b"target")
                bundle.writestr("evidence.json", evidence)
            with self.assertRaises(ValueError):
                self.verifier.extract_home_artifact_archive(link, root / "out-link")

            corrupt = root / "corrupt.zip"
            corrupt.write_bytes(archive.read_bytes().replace(evidence, b"X" + evidence[1:], 1))
            with self.assertRaises(ValueError):
                self.verifier.extract_home_artifact_archive(corrupt, root / "out-corrupt")

    def test_home_tar_is_canonical_bounded_and_dist_prefixed(self):
        self.verifier.validate_home_dist_archive_bytes(_home_archive())
        mutations = {
            "outside dist": _home_archive([("index.html", b"x")]),
            "missing index": _home_archive([("dist/assets/app.js", b"x")]),
            "duplicate": _home_archive(
                [("dist/index.html", b"x"), ("dist/index.html", b"y")]
            ),
            "out of order": _home_archive(
                [("dist/index.html", b"x"), ("dist/assets/app.js", b"y")]
            ),
            "link": _home_archive(
                [("dist/index.html", b"target")], type_flag=b"2"
            ),
        }
        for name, raw in mutations.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.verifier.validate_home_dist_archive_bytes(raw)
        with self.assertRaises(ValueError):
            self.verifier.validate_home_dist_archive_bytes(_home_archive() * 2)
        with mock.patch.object(self.verifier, "MAX_HOME_TAR_BYTES", 1024):
            with self.assertRaises(ValueError):
                self.verifier.validate_home_dist_archive_bytes(_home_archive())

    def test_unique_home_run_ignores_retry_and_rejects_competing_fresh_dispatch(self):
        head = self.candidate["home"]["source_sha"]
        workflow = self.validator.PRODUCER_WORKFLOWS["home-dist"]

        def run(run_id, attempt):
            return {
                "id": run_id,
                "status": "completed",
                "conclusion": "success",
                "event": "workflow_dispatch",
                "head_sha": head,
                "head_branch": "master",
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
            return_value=[run(101, 2), run(102, 1)],
        ), mock.patch.object(
            self.verifier, "_list_named_artifacts", side_effect=artifacts
        ):
            self.verifier.assert_unique_protected_producer_run(
                {},
                "DevPathAi/devpath-home-page",
                head,
                workflow,
                self.candidate["release_id"],
                "home-dist",
                102,
            )

        with mock.patch.object(
            self.verifier,
            "_list_protected_runs",
            return_value=[run(102, 1), run(103, 1)],
        ), mock.patch.object(
            self.verifier, "_list_named_artifacts", side_effect=artifacts
        ):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                self.verifier.assert_unique_protected_producer_run(
                    {},
                    "DevPathAi/devpath-home-page",
                    head,
                    workflow,
                    self.candidate["release_id"],
                    "home-dist",
                    102,
                )

    def test_sealer_home_discovery_returns_the_authenticated_artifact(self):
        head = self.candidate["home"]["source_sha"]
        run = {
            "id": 101,
            "run_attempt": 1,
            "head_branch": "master",
            "head_sha": head,
            "repository": {"full_name": "DevPathAi/devpath-home-page"},
            "head_repository": {"full_name": "DevPathAi/devpath-home-page"},
        }
        branch = {"name": "master", "protected": True, "commit": {"sha": head}}
        expected = {"artifact_id": 201}
        with mock.patch.object(
            self.sealer, "select_unique_protected_producer_run", return_value=run
        ), mock.patch.object(
            self.sealer, "_gh_json", return_value=branch
        ), mock.patch.object(
            self.sealer, "_discover_external_artifact", return_value=expected
        ) as discover:
            actual = self.sealer._discover_home_dist(
                {}, self.candidate["release_id"], self.candidate_sha, self.candidate
            )
        self.assertEqual(actual, expected)
        self.assertEqual(discover.call_args.args[1], "home-dist")
        self.assertEqual(discover.call_args.kwargs["expected_run_attempt"], 1)


if __name__ == "__main__":
    unittest.main()
