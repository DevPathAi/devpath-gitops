#!/usr/bin/env python3
"""Seal a final release after candidate-bound Home quality evidence and journeys pass."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
    FRONTEND_CATALOG_CONTRACTS,
    FRONTEND_EVIDENCE_FILES,
    PRODUCER_WORKFLOWS,
    QUALITY_EVIDENCE,
    QUALITY_EVIDENCE_EVENTS,
    QUALITY_EVIDENCE_FILES,
    SHA40,
    SHA64,
    quality_artifact_name,
    quality_source,
    resolve_candidate_spec,
    validate_release_manifest,
)
from verify_release_artifacts import (
    MAX_HOME_PAYLOAD_BYTES,
    _frontend_expected_entries,
    _workflow_bytes,
    validate_evidence_payload,
    validate_frontend_evidence_bundle,
    validate_run_provenance,
)


EXTERNAL_ARTIFACTS = {
    "home_dist_artifact": ("home-dist", "DevPathAi/devpath-home-page", "home-dist", "home"),
    "privacy": ("privacy-approval", "DevPathAi/documents", "privacy-approval", "privacy"),
    "ai": ("ai-release-eval", "DevPathAi/devpath-ai-svc", "ai-eval", "ai"),
}


def _gh_json(args: list[str], env: dict[str, str]) -> dict[str, Any]:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        raise ValueError("GitHub attestation query failed")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("GitHub attestation query returned invalid JSON")
    return value


def _discover_external_artifact(
    env: dict[str, str],
    label: str,
    repository: str,
    artifact_name: str,
    candidate_hash: str,
    expected_head: str,
    candidate: dict[str, Any],
    expected_payload_hash: str | None = None,
    *,
    expected_event: str = "workflow_dispatch",
    evidence_file: str = "evidence.json",
    expected_files: list[str] | None = None,
    expected_run_id: int | None = None,
    expected_run_attempt: int | None = None,
) -> dict[str, Any]:
    name = artifact_name
    listing = _gh_json(
        ["api", f"repos/{repository}/actions/artifacts?name={name}&per_page=100"],
        env,
    )
    artifacts = [item for item in listing.get("artifacts", []) if item.get("expired") is False]
    workflow_path = PRODUCER_WORKFLOWS[label]
    workflow = _workflow_bytes(repository, workflow_path, expected_head, env)
    matches: list[tuple[dict[str, Any], int, dict[str, Any]]] = []
    for metadata in artifacts:
        if metadata.get("name") != name or not isinstance(metadata.get("id"), int):
            continue
        run_id = (metadata.get("workflow_run") or {}).get("id")
        if not isinstance(run_id, int) or run_id <= 0:
            continue
        if expected_run_id is not None and run_id != expected_run_id:
            continue
        run = _gh_json(["api", f"repos/{repository}/actions/runs/{run_id}"], env)
        if (
            run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and run.get("event") == expected_event
            and run.get("head_sha") == expected_head
            and run.get("path") == workflow_path
            and (
                expected_run_attempt is None
                or run.get("run_attempt") == expected_run_attempt
            )
        ):
            matches.append((metadata, run_id, run))
    if len(matches) != 1:
        raise ValueError(f"{label}: exactly one active artifact from the exact producer run is required")
    metadata, run_id, run = matches[0]
    reference_provenance = {
        "event": expected_event,
        "head_sha": expected_head,
        "run_attempt": run.get("run_attempt"),
        "workflow_path": workflow_path,
        "workflow_sha256": hashlib.sha256(workflow).hexdigest(),
    }
    validate_run_provenance(
        label,
        run,
        reference_provenance,
        expected_head,
        workflow_path,
        workflow,
        expected_event,
    )

    with tempfile.TemporaryDirectory(prefix=f"mission-spine-{label}-") as temp_dir:
        download = subprocess.run(
            [
                "gh",
                "run",
                "download",
                str(run_id),
                "--repo",
                repository,
                "--name",
                name,
                "--dir",
                temp_dir,
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if download.returncode != 0:
            raise ValueError(f"{label}: artifact download failed")
        root = Path(temp_dir)
        entries = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
        files = expected_files or (["dist.tar.gz", "evidence.json"] if expected_payload_hash else [evidence_file])
        expected_entries = (
            _frontend_expected_entries(label)
            if label in FRONTEND_EVIDENCE_FILES
            else sorted(files)
        )
        if entries != expected_entries:
            raise ValueError(f"{label}: artifact file set is not canonical")
        for filename in files:
            path = root / filename
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"{label}: artifact file must be a regular file")
        evidence_bytes = (root / evidence_file).read_bytes()
        if len(evidence_bytes) > 256 * 1024:
            raise ValueError(f"{label}: evidence exceeds sanitized size limit")
        payload = json.loads(evidence_bytes.decode("utf-8"))
        validate_evidence_payload(
            label,
            payload,
            candidate_hash,
            candidate,
            run_id,
            run.get("run_attempt"),
        )
        if label in FRONTEND_EVIDENCE_FILES:
            validate_frontend_evidence_bundle(label, root, payload, candidate)
        reference = {
            "candidate_spec_sha256": candidate_hash,
            "repository": repository,
            **reference_provenance,
            "workflow_run_id": run_id,
            "artifact_id": metadata["id"],
            "artifact_name": name,
            "evidence_file": evidence_file,
            "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        }
        if expected_payload_hash is not None:
            payload_path = root / "dist.tar.gz"
            if payload_path.stat().st_size > MAX_HOME_PAYLOAD_BYTES:
                raise ValueError("home-dist: payload exceeds 100 MiB")
            payload_hash = hashlib.sha256(payload_path.read_bytes()).hexdigest()
            if payload_hash != expected_payload_hash:
                raise ValueError("home-dist: payload does not match candidate home.dist_sha256")
            reference.update({"payload_file": "dist.tar.gz", "payload_sha256": payload_hash})
        return reference


def select_frontend_evidence_run(
    runs: list[dict[str, Any]],
    expected_head: str,
) -> dict[str, Any]:
    """Select the highest successful attempt of exactly one dispatched producer run."""
    eligible = [
        run
        for run in runs
        if run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("event") == "workflow_dispatch"
        and run.get("head_sha") == expected_head
        and run.get("path") == ".github/workflows/et13-evidence.yml"
        and isinstance(run.get("id"), int)
        and not isinstance(run.get("id"), bool)
        and run["id"] > 0
        and isinstance(run.get("run_attempt"), int)
        and not isinstance(run.get("run_attempt"), bool)
        and run["run_attempt"] > 0
    ]
    run_ids = {run["id"] for run in eligible}
    if len(run_ids) != 1:
        raise ValueError("exactly one frontend producer run is required")
    return max(eligible, key=lambda run: run["run_attempt"])


def _discover_frontend_pair(
    env: dict[str, str],
    release_id: str,
    candidate_hash: str,
    candidate: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    repository = candidate["frontend"]["repository"]
    expected_head = candidate["frontend"]["source_sha"]
    listing = _gh_json(
        [
            "api",
            (
                f"repos/{repository}/actions/runs?head_sha={expected_head}"
                "&event=workflow_dispatch&status=success&per_page=100"
            ),
        ],
        env,
    )
    runs = listing.get("workflow_runs", [])
    if not isinstance(runs, list):
        raise ValueError("frontend producer run query returned invalid JSON")
    selected = select_frontend_evidence_run(runs, expected_head)
    run_id = selected["id"]
    run_attempt = selected["run_attempt"]
    discovered: dict[str, dict[str, Any]] = {}
    for label in FRONTEND_CATALOG_CONTRACTS:
        key = QUALITY_EVIDENCE[label]
        discovered[key] = _discover_external_artifact(
            env,
            label,
            repository,
            quality_artifact_name(label, release_id, run_attempt, run_id),
            candidate_hash,
            expected_head,
            candidate,
            expected_event="workflow_dispatch",
            evidence_file="evidence.json",
            expected_files=list(FRONTEND_EVIDENCE_FILES[label]),
            expected_run_id=run_id,
            expected_run_attempt=run_attempt,
        )
    return discovered


def load_home_quality_manifests(
    visual_path: Path,
    a11y_path: Path,
    candidate_hash: str,
    candidate: dict[str, Any],
) -> dict[str, bytes]:
    """Read the exact two regular manifests from one validator-owned artifact."""
    paths = {
        "home-visual": visual_path.absolute(),
        "home-axe-browser-a11y": a11y_path.absolute(),
    }
    for label, path in paths.items():
        if path.name != QUALITY_EVIDENCE_FILES[label] or path.is_symlink() or not path.is_file():
            raise ValueError(f"{label}: evidence path is not the exact regular manifest file")
    parents = {path.parent for path in paths.values()}
    if len(parents) != 1:
        raise ValueError("Home quality manifests must come from one downloaded artifact")
    parent = parents.pop()
    expected_files = sorted(QUALITY_EVIDENCE_FILES[label] for label in paths)
    if sorted(path.name for path in parent.iterdir()) != expected_files:
        raise ValueError("Home quality artifact file set is not canonical")

    manifests: dict[str, bytes] = {}
    for label, path in paths.items():
        raw = path.read_bytes()
        if len(raw) > 256 * 1024:
            raise ValueError(f"{label}: evidence exceeds sanitized size limit")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{label}: evidence is not UTF-8 JSON") from exc
        validate_evidence_payload(label, payload, candidate_hash, candidate)
        manifests[label] = raw
    return manifests


def build_release_manifest(
    candidate: dict[str, Any],
    candidate_path: str,
    candidate_hash: str,
    external: dict[str, dict[str, Any]],
    validator_repository: str,
    validator_run_id: int,
    validator_run_attempt: int,
    validator_head_sha: str,
    validator_workflow_sha256: str,
    activation_artifact_id: int,
    activation_sha256: str,
    contextual_artifact_id: int,
    contextual_sha256: str,
    home_quality_artifact_id: int,
    home_visual_sha256: str,
    home_a11y_sha256: str,
    created_at: str,
) -> dict[str, Any]:
    release_id = candidate["release_id"]
    for value, path in (
        (activation_sha256, "activation_sha256"),
        (contextual_sha256, "contextual_sha256"),
        (home_visual_sha256, "home_visual_sha256"),
        (home_a11y_sha256, "home_a11y_sha256"),
        (validator_workflow_sha256, "validator_workflow_sha256"),
    ):
        if SHA64.fullmatch(value) is None:
            raise ValueError(f"{path} must be SHA-256")
    if activation_sha256 == contextual_sha256:
        raise ValueError("journey evidence hashes must be distinct")
    if validator_repository != "DevPathAi/devpath-gitops":
        raise ValueError("validator repository is not canonical")
    if SHA40.fullmatch(validator_head_sha) is None:
        raise ValueError("validator_head_sha must be an exact commit SHA")
    for value, path in (
        (validator_run_id, "validator_run_id"),
        (validator_run_attempt, "validator_run_attempt"),
        (activation_artifact_id, "activation_artifact_id"),
        (contextual_artifact_id, "contextual_artifact_id"),
        (home_quality_artifact_id, "home_quality_artifact_id"),
    ):
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"{path} must be a positive integer")

    def journey_ref(suffix: str, artifact_id: int, evidence_hash: str) -> dict[str, Any]:
        return {
            "candidate_spec_sha256": candidate_hash,
            "repository": validator_repository,
            "event": "workflow_dispatch",
            "head_sha": validator_head_sha,
            "run_attempt": validator_run_attempt,
            "workflow_path": PRODUCER_WORKFLOWS["journey-activation"],
            "workflow_sha256": validator_workflow_sha256,
            "workflow_run_id": validator_run_id,
            "artifact_id": artifact_id,
            "artifact_name": (
                f"{release_id}-journey-{suffix}-attempt-{validator_run_attempt}"
            ),
            "evidence_file": "evidence.json",
            "sha256": evidence_hash,
        }

    def home_quality_ref(label: str, evidence_hash: str) -> dict[str, Any]:
        return {
            "candidate_spec_sha256": candidate_hash,
            "repository": validator_repository,
            "event": "workflow_dispatch",
            "head_sha": validator_head_sha,
            "run_attempt": validator_run_attempt,
            "workflow_path": PRODUCER_WORKFLOWS[label],
            "workflow_sha256": validator_workflow_sha256,
            "workflow_run_id": validator_run_id,
            "artifact_id": home_quality_artifact_id,
            "artifact_name": quality_artifact_name(
                label,
                release_id,
                validator_run_attempt,
            ),
            "evidence_file": QUALITY_EVIDENCE_FILES[label],
            "sha256": evidence_hash,
        }

    quality_evidence = {
        key: external["quality_evidence"][key]
        for label, key in QUALITY_EVIDENCE.items()
        if label not in {"home-visual", "home-axe-browser-a11y"}
    }
    quality_evidence[QUALITY_EVIDENCE["home-visual"]] = home_quality_ref(
        "home-visual",
        home_visual_sha256,
    )
    quality_evidence[QUALITY_EVIDENCE["home-axe-browser-a11y"]] = home_quality_ref(
        "home-axe-browser-a11y",
        home_a11y_sha256,
    )

    return {
        "$schema": "../schema-v1.json",
        "schema_version": 1,
        "document_type": "release-manifest",
        "release_id": release_id,
        "created_at": created_at,
        "candidate_spec": {"path": candidate_path, "sha256": candidate_hash},
        "home_dist_artifact": external["home_dist_artifact"],
        "analytics_privacy_approval": {
            "approved_at": external["privacy"]["approved_at"],
            "evidence": external["privacy"]["reference"],
        },
        "ai_release_eval": {
            "hard_invariants_percent": external["ai"]["hard_invariants_percent"],
            "usefulness_percent": external["ai"]["usefulness_percent"],
            "baseline_delta_points": external["ai"]["baseline_delta_points"],
            "evidence": external["ai"]["reference"],
        },
        "journeys": {
            "activation": journey_ref("activation", activation_artifact_id, activation_sha256),
            "contextual_practice": journey_ref(
                "contextual-practice",
                contextual_artifact_id,
                contextual_sha256,
            ),
        },
        "quality_evidence": quality_evidence,
        "validation_attestation": {
            "validator_repository": validator_repository,
            "validator_run_id": validator_run_id,
            "validator_run_attempt": validator_run_attempt,
            "validator_event": "workflow_dispatch",
            "validator_head_sha": validator_head_sha,
            "validator_workflow_path": PRODUCER_WORKFLOWS["journey-activation"],
            "validator_workflow_sha256": validator_workflow_sha256,
            "home_source_sha": candidate["home"]["source_sha"],
            "candidate_spec_sha256": candidate_hash,
            "activation_sha256": activation_sha256,
            "contextual_practice_sha256": contextual_sha256,
        },
    }


def seal(root: Path, args: argparse.Namespace) -> Path:
    candidate_path, candidate, candidate_hash = resolve_candidate_spec(root, args.release_id)
    token = os.environ.get("RELEASE_EVIDENCE_TOKEN", "")
    validator_repository = os.environ.get("GITHUB_REPOSITORY", "")
    validator_run_id = int(os.environ.get("GITHUB_RUN_ID", "0"))
    validator_run_attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", "0"))
    validator_head_sha = os.environ.get("GITHUB_SHA", "")
    if not token:
        raise ValueError("RELEASE_EVIDENCE_TOKEN is required")
    if shutil.which("gh") is None:
        raise ValueError("GitHub CLI is required")
    gh_env = os.environ.copy()
    gh_env["GH_TOKEN"] = token
    source = {
        "home": candidate["home"]["source_sha"],
        "ai": candidate["services"]["devpath-ai-svc"]["source_sha"],
        "frontend": candidate["frontend"]["source_sha"],
        "privacy": candidate["analytics_privacy"]["approval_source_sha"],
    }
    discovered: dict[str, dict[str, Any]] = {}
    for key, (label, repository, suffix, source_key) in EXTERNAL_ARTIFACTS.items():
        discovered[key] = _discover_external_artifact(
            gh_env,
            label,
            repository,
            f"{args.release_id}-{suffix}",
            candidate_hash,
            source[source_key],
            candidate,
            candidate["home"]["dist_sha256"] if key == "home_dist_artifact" else None,
        )
    discovered.update(
        _discover_frontend_pair(
            gh_env,
            args.release_id,
            candidate_hash,
            candidate,
        )
    )
    for label, key in QUALITY_EVIDENCE.items():
        if label in {
            "frontend-visual",
            "frontend-automated-a11y",
            "home-visual",
            "home-axe-browser-a11y",
        }:
            continue
        repository, source_sha = quality_source(candidate, label)
        discovered[key] = _discover_external_artifact(
            gh_env,
            label,
            repository,
            quality_artifact_name(label, args.release_id),
            candidate_hash,
            source_sha,
            candidate,
            expected_event=QUALITY_EVIDENCE_EVENTS[label],
            evidence_file=QUALITY_EVIDENCE_FILES[label],
            expected_files=(
                ["a11y-evidence.v2.json", "visual-evidence.v2.json"]
                if label in {"home-visual", "home-axe-browser-a11y"}
                else None
            ),
        )

    # Approval/eval scores are sanitized metadata inside their evidence objects.
    def evidence_json(reference: dict[str, Any]) -> dict[str, Any]:
        repository = reference["repository"]
        run_id = reference["workflow_run_id"]
        name = reference["artifact_name"]
        with tempfile.TemporaryDirectory(prefix="mission-spine-metadata-") as temp_dir:
            result = subprocess.run(
                ["gh", "run", "download", str(run_id), "--repo", repository, "--name", name, "--dir", temp_dir],
                env=gh_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode != 0:
                raise ValueError("sanitized metadata artifact download failed")
            return json.loads((Path(temp_dir) / "evidence.json").read_text(encoding="utf-8"))

    privacy_payload = evidence_json(discovered["privacy"])
    ai_payload = evidence_json(discovered["ai"])
    external = {
        "home_dist_artifact": discovered["home_dist_artifact"],
        "privacy": {
            "approved_at": privacy_payload.get("approved_at"),
            "reference": discovered["privacy"],
        },
        "ai": {
            "hard_invariants_percent": ai_payload.get("hard_invariants_percent"),
            "usefulness_percent": ai_payload.get("usefulness_percent"),
            "baseline_delta_points": ai_payload.get("baseline_delta_points"),
            "reference": discovered["ai"],
        },
        "quality_evidence": {
            key: discovered[key]
            for label, key in QUALITY_EVIDENCE.items()
            if label not in {"home-visual", "home-axe-browser-a11y"}
        },
    }
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    workflow_path = PRODUCER_WORKFLOWS["journey-activation"]
    workflow_result = subprocess.run(
        ["git", "show", f"{validator_head_sha}:{workflow_path}"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if workflow_result.returncode != 0 or not workflow_result.stdout:
        raise ValueError("validator workflow bytes are unavailable at GITHUB_SHA")
    validator_workflow_sha256 = hashlib.sha256(workflow_result.stdout).hexdigest()

    journey_bytes: dict[str, bytes] = {}
    for label, path in (
        ("journey-activation", args.activation_evidence),
        ("journey-contextual-practice", args.contextual_evidence),
    ):
        raw = path.read_bytes()
        if len(raw) > 256 * 1024:
            raise ValueError(f"{label}: evidence exceeds sanitized size limit")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{label}: evidence is not UTF-8 JSON") from exc
        validate_evidence_payload(label, payload, candidate_hash, candidate)
        journey_bytes[label] = raw
    home_bytes = load_home_quality_manifests(
        args.home_visual_evidence,
        args.home_a11y_evidence,
        candidate_hash,
        candidate,
    )
    relative_candidate = candidate_path.relative_to(root).as_posix()
    release = build_release_manifest(
        candidate,
        relative_candidate,
        candidate_hash,
        external,
        validator_repository,
        validator_run_id,
        validator_run_attempt,
        validator_head_sha,
        validator_workflow_sha256,
        args.activation_artifact_id,
        hashlib.sha256(journey_bytes["journey-activation"]).hexdigest(),
        args.contextual_artifact_id,
        hashlib.sha256(journey_bytes["journey-contextual-practice"]).hexdigest(),
        args.home_quality_artifact_id,
        hashlib.sha256(home_bytes["home-visual"]).hexdigest(),
        hashlib.sha256(home_bytes["home-axe-browser-a11y"]).hexdigest(),
        created_at,
    )
    validate_release_manifest(release, candidate, candidate_hash)
    release_path = root / "release-manifests" / "releases" / f"{args.release_id}.json"
    release_path.parent.mkdir(parents=True, exist_ok=True)
    with release_path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(release, output, ensure_ascii=True, indent=2, sort_keys=True)
        output.write("\n")
    return release_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--activation-artifact-id", type=int, required=True)
    parser.add_argument("--activation-evidence", type=Path, required=True)
    parser.add_argument("--contextual-artifact-id", type=int, required=True)
    parser.add_argument("--contextual-evidence", type=Path, required=True)
    parser.add_argument("--home-quality-artifact-id", type=int, required=True)
    parser.add_argument("--home-visual-evidence", type=Path, required=True)
    parser.add_argument("--home-a11y-evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        release_path = seal(args.root.resolve(), args)
        print(f"sealed final release-manifest: {release_path}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"release seal failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
