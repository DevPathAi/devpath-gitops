#!/usr/bin/env python3
"""Fetch and verify sealed, sanitized release evidence from GitHub Actions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from validate_release_manifest import SHA64, _validate_sanitized, resolve_release_bundle


MAX_EVIDENCE_BYTES = 256 * 1024
MAX_HOME_PAYLOAD_BYTES = 100 * 1024 * 1024
JOURNEY_ROW_KEYS = {"route", "step", "result", "duration_ms", "candidate_spec_sha256"}
VALIDATION_SEAL_KEYS = {
    "release_id",
    "candidate_spec_sha256",
    "release_manifest_sha256",
    "status",
    "rollback_seconds",
    "validator_run_id",
    "validator_run_attempt",
}


def validate_evidence_payload(payload: Any, candidate_hash: str, journey: bool = False) -> None:
    _validate_sanitized(payload, "evidence")
    if journey:
        if not isinstance(payload, list) or not payload:
            raise ValueError("journey evidence must be a non-empty JSON array")
        for index, row in enumerate(payload):
            if not isinstance(row, dict) or set(row) != JOURNEY_ROW_KEYS:
                raise ValueError(f"journey evidence row {index} has an invalid key set")
            if row["candidate_spec_sha256"] != candidate_hash:
                raise ValueError(f"journey evidence row {index} does not bind candidate-spec")
            if row["result"] != "passed":
                raise ValueError(f"journey evidence row {index} did not pass")
            if (
                isinstance(row["duration_ms"], bool)
                or not isinstance(row["duration_ms"], int)
                or row["duration_ms"] < 0
            ):
                raise ValueError(f"journey evidence row {index} has invalid duration_ms")
            for field in ("route", "step"):
                if (
                    not isinstance(row[field], str)
                    or not row[field]
                    or len(row[field]) > 512
                    or "\n" in row[field]
                    or "\r" in row[field]
                ):
                    raise ValueError(f"journey evidence row {index} has invalid {field}")
        return
    if not isinstance(payload, dict):
        raise ValueError("sanitized evidence must be a JSON object")
    if payload.get("candidate_spec_sha256") != candidate_hash:
        raise ValueError("evidence does not bind candidate-spec")
    if payload.get("status") != "passed":
        raise ValueError("evidence status must be passed")


def validate_sealed_metadata(label: str, payload: dict[str, Any], release: dict[str, Any]) -> None:
    """Prevent a manifest from claiming better approval/eval values than its evidence."""
    if label == "privacy-approval":
        expected_approval = release["analytics_privacy_approval"]["approved_at"]
        if payload.get("approved_at") != expected_approval:
            raise ValueError("privacy-approval: approved_at does not match the sealed manifest")
    if label == "ai-release-eval":
        sealed_eval = release["ai_release_eval"]
        for field in (
            "hard_invariants_percent",
            "usefulness_percent",
            "baseline_delta_points",
        ):
            if payload.get(field) != sealed_eval[field]:
                raise ValueError(f"ai-release-eval: {field} does not match the sealed manifest")


def validate_validation_seal_payload(
    payload: Any,
    release_id: str,
    candidate_hash: str,
    release_hash: str,
    attestation: dict[str, Any],
) -> None:
    _validate_sanitized(payload, "sealed-validation")
    if not isinstance(payload, dict) or set(payload) != VALIDATION_SEAL_KEYS:
        raise ValueError("sealed-validation evidence has an invalid key set")
    if payload["release_id"] != release_id:
        raise ValueError("sealed-validation release_id mismatch")
    if payload["candidate_spec_sha256"] != candidate_hash:
        raise ValueError("sealed-validation candidate-spec hash mismatch")
    if SHA64.fullmatch(str(payload["release_manifest_sha256"])) is None:
        raise ValueError("sealed-validation final manifest hash is invalid")
    if payload["release_manifest_sha256"] != release_hash:
        raise ValueError("sealed-validation final manifest bytes do not match")
    if payload["status"] != "passed":
        raise ValueError("sealed-validation did not pass")
    rollback_seconds = payload["rollback_seconds"]
    if isinstance(rollback_seconds, bool) or not isinstance(rollback_seconds, int):
        raise ValueError("sealed-validation rollback_seconds must be an integer")
    if not 0 <= rollback_seconds <= 600:
        raise ValueError("sealed-validation reverse rollback exceeded 600 seconds")
    if payload["validator_run_id"] != attestation["validator_run_id"]:
        raise ValueError("sealed-validation validator run mismatch")
    if payload["validator_run_attempt"] != attestation["validator_run_attempt"]:
        raise ValueError("sealed-validation validator run attempt mismatch")


def _run_json(command: list[str], env: dict[str, str]) -> dict[str, Any]:
    result = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        raise ValueError(f"GitHub evidence API command failed: {command[1]}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("GitHub evidence API returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("GitHub evidence API returned an invalid object")
    return value


def _artifact_entries(release: dict[str, Any]) -> list[tuple[str, dict[str, Any], bool, bool]]:
    return [
        ("home-dist", release["home_dist_artifact"], False, True),
        ("privacy-approval", release["analytics_privacy_approval"]["evidence"], False, False),
        ("ai-release-eval", release["ai_release_eval"]["evidence"], False, False),
        ("journey-activation", release["journeys"]["activation"], True, False),
        ("journey-contextual-practice", release["journeys"]["contextual_practice"], True, False),
        ("visual", release["quality_evidence"]["visual"], False, False),
        ("accessibility", release["quality_evidence"]["accessibility"], False, False),
    ]


def _verify_validation_seal(
    command_env: dict[str, str],
    release_path: Path,
    release_id: str,
    release: dict[str, Any],
    candidate_hash: str,
) -> None:
    attestation = release["validation_attestation"]
    repository = attestation["validator_repository"]
    run_id = attestation["validator_run_id"]
    artifact_name = f"{release_id}-sealed-validation"
    listing = _run_json(
        ["gh", "api", f"repos/{repository}/actions/artifacts?name={artifact_name}&per_page=100"],
        command_env,
    )
    active = [item for item in listing.get("artifacts", []) if item.get("expired") is False]
    if len(active) != 1:
        raise ValueError("sealed-validation: exactly one active artifact is required")
    metadata = active[0]
    if metadata.get("name") != artifact_name:
        raise ValueError("sealed-validation: artifact name mismatch")
    if (metadata.get("workflow_run") or {}).get("id") != run_id:
        raise ValueError("sealed-validation: validator run mismatch")
    run = _run_json(["gh", "api", f"repos/{repository}/actions/runs/{run_id}"], command_env)
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ValueError("sealed-validation: validator workflow is not successful")
    if run.get("run_attempt") != attestation["validator_run_attempt"]:
        raise ValueError("sealed-validation: validator run attempt mismatch")
    if not str(run.get("path", "")).endswith(".github/workflows/mission-spine-validate.yml"):
        raise ValueError("sealed-validation: untrusted validator workflow")
    with tempfile.TemporaryDirectory(prefix="mission-spine-validation-seal-") as temp_dir:
        result = subprocess.run(
            [
                "gh",
                "run",
                "download",
                str(run_id),
                "--repo",
                repository,
                "--name",
                artifact_name,
                "--dir",
                temp_dir,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=command_env,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError("sealed-validation: artifact download failed")
        entries = [path for path in Path(temp_dir).rglob("*")]
        if len(entries) != 1 or entries[0].relative_to(temp_dir).as_posix() != "evidence.json":
            raise ValueError("sealed-validation: artifact contains unexpected files")
        if not entries[0].is_file() or entries[0].is_symlink():
            raise ValueError("sealed-validation: evidence must be a regular file")
        raw = entries[0].read_bytes()
        if len(raw) > MAX_EVIDENCE_BYTES:
            raise ValueError("sealed-validation: evidence exceeds the sanitized size limit")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("sealed-validation: evidence is not valid UTF-8 JSON") from exc
    release_hash = hashlib.sha256(release_path.read_bytes()).hexdigest()
    validate_validation_seal_payload(
        payload,
        release_id,
        candidate_hash,
        release_hash,
        attestation,
    )


def verify_artifacts(root: Path, release_id: str, materialize_home: Path | None = None) -> None:
    release_path, _, release, candidate, candidate_hash = resolve_release_bundle(root, release_id)
    token = os.environ.get("RELEASE_EVIDENCE_TOKEN", "")
    if not token:
        raise ValueError("RELEASE_EVIDENCE_TOKEN is required")
    if shutil.which("gh") is None:
        raise ValueError("GitHub CLI is required")
    command_env = os.environ.copy()
    command_env["GH_TOKEN"] = token

    expected_head = {
        "home-dist": candidate["home"]["source_sha"],
        "ai-release-eval": candidate["services"]["devpath-ai-svc"]["source_sha"],
        "visual": candidate["frontend"]["source_sha"],
        "accessibility": candidate["frontend"]["source_sha"],
    }

    with tempfile.TemporaryDirectory(prefix="mission-spine-evidence-") as temp_dir:
        temp = Path(temp_dir)
        for label, artifact, journey, has_payload in _artifact_entries(release):
            repository = artifact["repository"]
            artifact_id = artifact["artifact_id"]
            run_id = artifact["workflow_run_id"]
            metadata = _run_json(
                ["gh", "api", f"repos/{repository}/actions/artifacts/{artifact_id}"],
                command_env,
            )
            if metadata.get("id") != artifact_id or metadata.get("name") != artifact["artifact_name"]:
                raise ValueError(f"{label}: artifact identity mismatch")
            if metadata.get("expired") is not False:
                raise ValueError(f"{label}: artifact is expired or expiry is unknown")
            if (metadata.get("workflow_run") or {}).get("id") != run_id:
                raise ValueError(f"{label}: workflow run identity mismatch")
            run = _run_json(
                ["gh", "api", f"repos/{repository}/actions/runs/{run_id}"],
                command_env,
            )
            if run.get("status") != "completed" or run.get("conclusion") != "success":
                raise ValueError(f"{label}: producer workflow did not complete successfully")
            if journey:
                attestation = release["validation_attestation"]
                if repository != attestation["validator_repository"]:
                    raise ValueError(f"{label}: validator repository mismatch")
                if run_id != attestation["validator_run_id"]:
                    raise ValueError(f"{label}: validator run mismatch")
                if run.get("run_attempt") != attestation["validator_run_attempt"]:
                    raise ValueError(f"{label}: validator run attempt mismatch")
                if not str(run.get("path", "")).endswith(
                    ".github/workflows/mission-spine-validate.yml"
                ):
                    raise ValueError(f"{label}: untrusted validator workflow")
            if label in expected_head and run.get("head_sha") != expected_head[label]:
                raise ValueError(f"{label}: producer source SHA mismatch")

            destination = temp / label
            destination.mkdir()
            download = subprocess.run(
                [
                    "gh",
                    "run",
                    "download",
                    str(run_id),
                    "--repo",
                    repository,
                    "--name",
                    artifact["artifact_name"],
                    "--dir",
                    str(destination),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=command_env,
                check=False,
            )
            if download.returncode != 0:
                raise ValueError(f"{label}: artifact download failed")
            entries = sorted(
                path.relative_to(destination).as_posix()
                for path in destination.rglob("*")
            )
            expected_files = ["dist.tar.gz", "evidence.json"] if has_payload else ["evidence.json"]
            if entries != expected_files:
                raise ValueError(f"{label}: artifact contains an unexpected file set")
            for filename in expected_files:
                path = destination / filename
                if not path.is_file() or path.is_symlink():
                    raise ValueError(f"{label}: artifact file must be a regular file")
            evidence_path = destination / "evidence.json"
            raw = evidence_path.read_bytes()
            if len(raw) > MAX_EVIDENCE_BYTES:
                raise ValueError(f"{label}: evidence exceeds the sanitized size limit")
            if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
                raise ValueError(f"{label}: evidence SHA-256 mismatch")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"{label}: evidence is not valid UTF-8 JSON") from exc
            validate_evidence_payload(payload, candidate_hash, journey=journey)
            if not journey:
                validate_sealed_metadata(label, payload, release)
            if has_payload:
                payload_path = destination / "dist.tar.gz"
                if payload_path.stat().st_size > MAX_HOME_PAYLOAD_BYTES:
                    raise ValueError("home-dist: payload exceeds 100 MiB")
                if hashlib.sha256(payload_path.read_bytes()).hexdigest() != artifact["payload_sha256"]:
                    raise ValueError("home-dist: payload SHA-256 mismatch")
                if materialize_home is not None:
                    materialize_home.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(payload_path, materialize_home)
    _verify_validation_seal(command_env, release_path, release_id, release, candidate_hash)
    print(f"verified seven sealed artifacts and final validation seal for {release_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--materialize-home", type=Path)
    args = parser.parse_args(argv)
    try:
        verify_artifacts(args.root.resolve(), args.release_id, args.materialize_home)
        return 0
    except (OSError, ValueError) as exc:
        print(f"release artifact verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
