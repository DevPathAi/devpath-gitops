#!/usr/bin/env python3
"""Fetch and verify sealed, sanitized release evidence from GitHub Actions."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from validate_release_manifest import (
    PRODUCER_WORKFLOWS,
    SHA40,
    SHA64,
    _validate_sanitized,
    resolve_release_bundle,
)


MAX_EVIDENCE_BYTES = 256 * 1024
MAX_HOME_PAYLOAD_BYTES = 100 * 1024 * 1024
JOURNEY_ROW_KEYS = {"route", "step", "result", "duration_ms", "candidate_spec_sha256"}
PRODUCER_EVIDENCE_KEYS = {"producer_run_id", "producer_run_attempt"}
JOURNEY_ALLOWLISTS = {
    "journey-activation": {
        "landing-prepermission-zero": ("/",),
        "opaque-journey-handoff": ("/diagnostic",),
        "guest-diagnostic-fifteen": ("/diagnostic",),
        "guest-preview-refresh": ("/diagnostic",),
        "oauth-callback-replay": ("/consent",),
        "required-consent-claim-replay": ("/diagnostic",),
        "explicit-path-to-today": ("/dashboard",),
        "content-linked-completion-replay": ("/dashboard",),
        "contentless-completion-replay": ("/dashboard",),
        "onboarding-analytics-ordered": ("/dashboard",),
    },
    "journey-contextual-practice": {
        "authenticated-authoritative-today": ("/dashboard",),
        "canonical-content-to-sandbox": ("/mission/1/sandbox",),
        "immediate-disconnect-timeout-recovery": ("/mission/1/sandbox",),
        "midstream-disconnect-truncated-recovery": ("/mission/1/sandbox",),
        "stale-session-reconciliation": ("/mission/1/sandbox",),
        "outbox-review-durable": ("/mission/1/sandbox",),
        "private-context-preview-commit": ("/mission/1/mentor",),
        "mentor-partial-retry-payload-parity": ("/mission/1/mentor",),
        "workspace-analytics-and-boundaries": ("/mission/1/mentor",),
    },
}
VALIDATION_SEAL_KEYS = {
    "release_id",
    "candidate_spec_sha256",
    "release_manifest_sha256",
    "status",
    "rollback_seconds",
    "validator_run_id",
    "validator_run_attempt",
    "validator_head_sha",
    "validator_workflow_sha256",
}


def _route_allowed(label: str, step: str, route: str) -> bool:
    if route in JOURNEY_ALLOWLISTS[label][step]:
        return True
    if label == "journey-activation" and step in {
        "explicit-path-to-today",
        "content-linked-completion-replay",
        "contentless-completion-replay",
        "onboarding-analytics-ordered",
    }:
        parts = route.split("/")
        return len(parts) == 4 and parts[1] == "path" and parts[2].isdigit() and parts[3] == "today"
    if label == "journey-contextual-practice":
        parts = route.split("/")
        if step == "authenticated-authoritative-today":
            return len(parts) == 4 and parts[1] == "path" and parts[2].isdigit() and parts[3] == "today"
        expected_tail = "mentor" if step in {
            "private-context-preview-commit",
            "mentor-partial-retry-payload-parity",
            "workspace-analytics-and-boundaries",
        } else "sandbox"
        return len(parts) == 4 and parts[1] == "mission" and parts[2].isdigit() and parts[3] == expected_tail
    return False


def _exact_payload(payload: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ValueError(f"{label} evidence has an invalid key set")
    return payload


def _bounded_number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{label} is outside its approved range")
    return number


def validate_evidence_payload(
    label: str,
    payload: Any,
    candidate_hash: str,
    candidate: dict[str, Any],
    producer_run_id: int | None = None,
    producer_run_attempt: int | None = None,
) -> None:
    _validate_sanitized(payload, "evidence")
    if label in JOURNEY_ALLOWLISTS:
        if not isinstance(payload, list) or not payload:
            raise ValueError("journey evidence must be a non-empty JSON array")
        expected_steps = list(JOURNEY_ALLOWLISTS[label])
        actual_steps: list[str] = []
        for index, row in enumerate(payload):
            if not isinstance(row, dict) or set(row) != JOURNEY_ROW_KEYS:
                raise ValueError(f"journey evidence row {index} has an invalid key set")
            actual_steps.append(row.get("step"))
            if row["candidate_spec_sha256"] != candidate_hash:
                raise ValueError(f"journey evidence row {index} does not bind candidate-spec")
            if row["result"] != "passed":
                raise ValueError(f"journey evidence row {index} did not pass")
            if (
                isinstance(row["duration_ms"], bool)
                or not isinstance(row["duration_ms"], int)
                or not 0 <= row["duration_ms"] <= 600_000
            ):
                raise ValueError(f"journey evidence row {index} has invalid duration_ms")
            if row["step"] not in JOURNEY_ALLOWLISTS[label]:
                raise ValueError("journey evidence step sequence is not the approved allowlist")
            if not isinstance(row["route"], str) or not _route_allowed(label, row["step"], row["route"]):
                raise ValueError(f"journey evidence row {index} has a disallowed route")
        if actual_steps != expected_steps:
            raise ValueError("journey evidence step sequence is not the approved allowlist")
        return
    if not isinstance(payload, dict) or payload.get("candidate_spec_sha256") != candidate_hash:
        raise ValueError("evidence does not bind candidate-spec")
    if payload.get("status") != "passed":
        raise ValueError("evidence status must be passed")
    for field, expected in (
        ("producer_run_id", producer_run_id),
        ("producer_run_attempt", producer_run_attempt),
    ):
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"evidence {field} must be a positive integer")
        if expected is not None and value != expected:
            label_text = field.replace("_", " ")
            raise ValueError(f"evidence {label_text} mismatch")
    if label == "home-dist":
        value = _exact_payload(
            payload,
            {
                "candidate_spec_sha256", "status", "home_source_sha", "dist_sha256",
                *PRODUCER_EVIDENCE_KEYS,
            },
            label,
        )
        if value["home_source_sha"] != candidate["home"]["source_sha"]:
            raise ValueError("home-dist source SHA mismatch")
        if value["dist_sha256"] != candidate["home"]["dist_sha256"]:
            raise ValueError("home-dist payload SHA mismatch")
        return
    if label == "privacy-approval":
        keys = {
            "candidate_spec_sha256", "status", "approved_at", "collection_mode", "region",
            "project_identity", "retention_days", "access_owner", "deletion_runbook",
            *PRODUCER_EVIDENCE_KEYS,
        }
        value = _exact_payload(payload, keys, label)
        for field in keys - {
            "candidate_spec_sha256", "status", "approved_at", *PRODUCER_EVIDENCE_KEYS,
        }:
            if value[field] != candidate["analytics_privacy"][field]:
                raise ValueError(f"privacy-approval {field} mismatch")
        if not isinstance(value["approved_at"], str) or not value["approved_at"].endswith("Z"):
            raise ValueError("privacy-approval approved_at must be UTC")
        try:
            datetime.fromisoformat(value["approved_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("privacy-approval approved_at is invalid") from exc
        return
    if label == "ai-release-eval":
        config = candidate["ai_release_eval_config"]
        value = _exact_payload(
            payload,
            {
                "candidate_spec_sha256", "status", "ai_source_sha", "primary_model",
                "fallback_models", "prompt_sha256", "fixture_revision", "fixture_sha256",
                "hard_invariants_percent", "usefulness_percent", "baseline_delta_points",
                *PRODUCER_EVIDENCE_KEYS,
            },
            label,
        )
        expected = {
            "ai_source_sha": candidate["services"]["devpath-ai-svc"]["source_sha"],
            "primary_model": config["primary_model"],
            "fallback_models": config["fallback_models"],
            "prompt_sha256": config["prompt_sha256"],
            "fixture_revision": config["fixture_revision"],
            "fixture_sha256": config["fixture_sha256"],
        }
        for field, expected_value in expected.items():
            if value[field] != expected_value:
                raise ValueError(f"ai-release-eval {field} mismatch")
        if _bounded_number(value["hard_invariants_percent"], "hard invariants", 100, 100) != 100:
            raise ValueError("hard invariants must be 100")
        _bounded_number(value["usefulness_percent"], "usefulness", 90, 100)
        _bounded_number(value["baseline_delta_points"], "baseline delta", -5, 100)
        return
    if label == "visual":
        value = _exact_payload(
            payload,
            {
                "candidate_spec_sha256", "status", "frontend_source_sha", "viewport_profile",
                "screenshot_count", "pixel_diff_percent",
                *PRODUCER_EVIDENCE_KEYS,
            },
            label,
        )
        if value["frontend_source_sha"] != candidate["frontend"]["source_sha"]:
            raise ValueError("visual frontend source mismatch")
        if value["viewport_profile"] != "mission-spine.desktop-mobile.v1":
            raise ValueError("visual viewport profile is not approved")
        if isinstance(value["screenshot_count"], bool) or not isinstance(value["screenshot_count"], int):
            raise ValueError("visual screenshot_count must be an integer")
        if not 1 <= value["screenshot_count"] <= 64:
            raise ValueError("visual screenshot_count is outside its approved range")
        if _bounded_number(value["pixel_diff_percent"], "visual pixel diff", 0, 0) != 0:
            raise ValueError("visual pixel diff must be zero")
        return
    if label == "accessibility":
        value = _exact_payload(
            payload,
            {
                "candidate_spec_sha256", "status", "frontend_source_sha", "standard",
                "critical_violations", "serious_violations",
                *PRODUCER_EVIDENCE_KEYS,
            },
            label,
        )
        if value["frontend_source_sha"] != candidate["frontend"]["source_sha"]:
            raise ValueError("accessibility frontend source mismatch")
        if value["standard"] != "WCAG2.2AA":
            raise ValueError("accessibility standard must be WCAG2.2AA")
        for field in ("critical_violations", "serious_violations"):
            if isinstance(value[field], bool) or value[field] != 0:
                raise ValueError(f"accessibility {field} must be integer zero")
        return
    raise ValueError(f"unknown evidence kind: {label}")


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


def validate_run_provenance(
    label: str,
    run: dict[str, Any],
    reference: dict[str, Any],
    expected_head: str,
    expected_workflow_path: str,
    workflow_bytes: bytes,
) -> None:
    """Bind evidence to one successful manual run and its exact workflow blob."""
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ValueError(f"{label}: producer workflow did not complete successfully")
    if run.get("event") != "workflow_dispatch" or reference.get("event") != "workflow_dispatch":
        raise ValueError(f"{label}: producer event must be workflow_dispatch")
    if run.get("head_sha") != expected_head or reference.get("head_sha") != expected_head:
        raise ValueError(f"{label}: producer head SHA mismatch")
    run_attempt = run.get("run_attempt")
    if (
        isinstance(run_attempt, bool)
        or not isinstance(run_attempt, int)
        or run_attempt <= 0
        or reference.get("run_attempt") != run_attempt
    ):
        raise ValueError(f"{label}: producer run attempt mismatch")
    if run.get("path") != expected_workflow_path:
        raise ValueError(f"{label}: producer workflow path mismatch")
    if reference.get("workflow_path") != expected_workflow_path:
        raise ValueError(f"{label}: sealed workflow path mismatch")
    actual_hash = hashlib.sha256(workflow_bytes).hexdigest()
    if reference.get("workflow_sha256") != actual_hash:
        raise ValueError(f"{label}: producer workflow bytes mismatch")


def _workflow_bytes(
    repository: str,
    workflow_path: str,
    head_sha: str,
    env: dict[str, str],
) -> bytes:
    result = subprocess.run(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github.raw+json",
            f"repos/{repository}/contents/{workflow_path}?ref={head_sha}",
        ],
        capture_output=True,
        env=env,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise ValueError("producer workflow blob download failed")
    return result.stdout


def _git_output(root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ValueError("GitOps provenance git query failed")
    return result.stdout.strip()


def verify_validation_tree(
    root: Path,
    release_id: str,
    base_sha: str,
    validator_head_sha: str,
) -> None:
    """Reject a validation run from a look-alike branch or extra tree delta."""
    current_head = _git_output(root, ["rev-parse", "HEAD"])
    if _git_output(root, ["rev-parse", "HEAD^"]) != validator_head_sha:
        raise ValueError("validator head is not the sole parent of the sealed release commit")
    if _git_output(root, ["rev-parse", f"{validator_head_sha}^"]) != base_sha:
        raise ValueError("validator head is not based directly on sealed GitOps base")
    candidate_path = f"release-manifests/candidates/{release_id}.candidate-spec.json"
    release_path = f"release-manifests/releases/{release_id}.json"
    candidate_delta = _git_output(root, ["diff", "--name-only", f"{base_sha}...{validator_head_sha}"])
    if candidate_delta.splitlines() != [candidate_path]:
        raise ValueError("validator head has a disallowed candidate tree delta")
    final_delta = _git_output(root, ["diff", "--name-only", f"{validator_head_sha}...{current_head}"])
    if final_delta.splitlines() != [release_path]:
        raise ValueError("sealed release commit has a disallowed tree delta")
    if _git_output(root, ["show", f"{validator_head_sha}:{candidate_path}"]) != (
        root / candidate_path
    ).read_text(encoding="utf-8").strip():
        raise ValueError("validator candidate blob differs from sealed branch bytes")


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
    if payload["validator_head_sha"] != attestation["validator_head_sha"]:
        raise ValueError("sealed-validation validator head mismatch")
    if payload["validator_workflow_sha256"] != attestation["validator_workflow_sha256"]:
        raise ValueError("sealed-validation validator workflow mismatch")


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
    base_sha: str,
) -> None:
    attestation = release["validation_attestation"]
    repository = attestation["validator_repository"]
    run_id = attestation["validator_run_id"]
    artifact_name = (
        f"{release_id}-sealed-validation-attempt-{attestation['validator_run_attempt']}"
    )
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
    workflow_bytes = _workflow_bytes(
        repository,
        attestation["validator_workflow_path"],
        attestation["validator_head_sha"],
        command_env,
    )
    validate_run_provenance(
        "sealed-validation",
        run,
        {
            "event": attestation["validator_event"],
            "head_sha": attestation["validator_head_sha"],
            "run_attempt": attestation["validator_run_attempt"],
            "workflow_path": attestation["validator_workflow_path"],
            "workflow_sha256": attestation["validator_workflow_sha256"],
        },
        attestation["validator_head_sha"],
        PRODUCER_WORKFLOWS["journey-activation"],
        workflow_bytes,
    )
    verify_validation_tree(
        release_path.parents[2],
        release_id,
        base_sha,
        attestation["validator_head_sha"],
    )
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
        "privacy-approval": candidate["analytics_privacy"]["approval_source_sha"],
        "ai-release-eval": candidate["services"]["devpath-ai-svc"]["source_sha"],
        "journey-activation": release["validation_attestation"]["validator_head_sha"],
        "journey-contextual-practice": release["validation_attestation"]["validator_head_sha"],
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
            workflow_path = PRODUCER_WORKFLOWS[label]
            workflow_bytes = _workflow_bytes(
                repository,
                workflow_path,
                expected_head[label],
                command_env,
            )
            validate_run_provenance(
                label,
                run,
                artifact,
                expected_head[label],
                workflow_path,
                workflow_bytes,
            )
            if journey:
                attestation = release["validation_attestation"]
                if repository != attestation["validator_repository"]:
                    raise ValueError(f"{label}: validator repository mismatch")
                if run_id != attestation["validator_run_id"]:
                    raise ValueError(f"{label}: validator run mismatch")
                if run.get("run_attempt") != attestation["validator_run_attempt"]:
                    raise ValueError(f"{label}: validator run attempt mismatch")

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
            validate_evidence_payload(
                label,
                payload,
                candidate_hash,
                candidate,
                run_id,
                run.get("run_attempt"),
            )
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
    _verify_validation_seal(
        command_env,
        release_path,
        release_id,
        release,
        candidate_hash,
        candidate["gitops"]["base_sha"],
    )
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
