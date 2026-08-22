#!/usr/bin/env python3
"""Authenticate the newest current-head production canary, including its full chain."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from promote_service_digests import SERVICE_NAMES, SERVICE_PATHS
from validate_release_manifest import resolve_release_bundle
import verify_release_artifacts as artifacts
from verify_promotion_chain import inspect_chain


REPOSITORY = "DevPathAi/devpath-gitops"
WORKFLOW_PATH = ".github/workflows/mission-spine-promote.yml"
TOP_KEYS = (
    "schema_version",
    "document_type",
    "release_id",
    "candidate_spec_sha256",
    "status",
    "base_commit",
    "migration_commit",
    "services_commit",
    "off_commit",
    "on_commit",
    "migration_image",
    "services",
    "service_runtime",
    "sync_detection_seconds",
    "canary_seconds",
    "promoter_repository",
    "promoter_workflow_path",
    "promoter_workflow_sha256",
    "promoter_run_id",
    "promoter_run_attempt",
    "promoter_head_sha",
    "release_manifest_sha256",
    "candidate_spec_path",
)
MIGRATION_KEYS = ("source_sha", "image_repository", "image_digest")
SERVICE_KEYS = (
    "source_sha",
    "image_repository",
    "image_digest",
    "manifest_digest",
    "config_digest",
)
RUNTIME_SERVICE_KEYS = (
    "service",
    "application_observed_revision",
    "application_applied_revision",
    "deployment_uid",
    "root_digest",
    "manifest_digest",
    "config_digest",
    "pods",
)
RUNTIME_POD_KEYS = ("pod_uid", "runtime_image_digest", "runtime_image_form")
SHA40 = re.compile(r"[0-9a-f]{40}")
SHA64 = re.compile(r"[0-9a-f]{64}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _positive(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"production canary {label} must be a positive integer")
    return value


def _ordered(value: Any, keys: tuple[str, ...], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or tuple(value) != keys:
        raise ValueError(f"production canary {label} keys/order are not exact")
    return value


def _canonical(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n").encode(
        "utf-8"
    )


def _reject_sensitive(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(
                r"(?i)(token|password|authorization|private[_-]?key|kubeconfig)", key
            ):
                raise ValueError(f"production canary contains a sensitive key at {path}")
            _reject_sensitive(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive(child, f"{path}[{index}]")
    elif isinstance(value, str) and ("\r" in value or "\n" in value):
        raise ValueError(f"production canary contains multiline data at {path}")


def validate_promotion_payload(
    payload: Any,
    raw: bytes,
    root: Path,
    release_id: str,
    candidate: dict[str, Any],
    candidate_hash: str,
    release_hash: str,
    run: dict[str, Any],
    workflow_hash: str,
) -> dict[str, Any]:
    top = _ordered(payload, TOP_KEYS, "top-level")
    if raw != _canonical(top) or len(raw) > 256 * 1024:
        raise ValueError("production canary bytes are not canonical compact UTF-8 JSON+LF")
    _reject_sensitive(top)
    if (
        top["schema_version"] != 1
        or top["document_type"] != "mission-spine-production-canary"
        or top["release_id"] != release_id
        or top["candidate_spec_sha256"] != candidate_hash
        or top["status"] != "passed"
        or top["release_manifest_sha256"] != release_hash
        or top["candidate_spec_path"]
        != f"release-manifests/candidates/{release_id}.candidate-spec.json"
    ):
        raise ValueError("production canary release identity is invalid")
    for key in ("base_commit", "migration_commit", "services_commit", "off_commit", "on_commit"):
        if SHA40.fullmatch(str(top[key])) is None:
            raise ValueError(f"production canary {key} is invalid")
    state = inspect_chain(root, candidate, release_hash, top["on_commit"])
    expected_state = {
        "base_commit": candidate["gitops"]["base_sha"],
        "migration_commit": state["migration_commit"],
        "services_commit": state["services_commit"],
        "off_commit": state["off_commit"],
        "on_commit": state["on_commit"],
    }
    if state["phase"] != "mission-on" or any(
        top[key] != expected for key, expected in expected_state.items()
    ):
        raise ValueError("production canary does not bind the exact B-M-S-O-N chain")

    migration = _ordered(top["migration_image"], MIGRATION_KEYS, "migration image")
    migration_binding = candidate["shared_migration"]
    if migration != {
        "source_sha": migration_binding["source_sha"],
        "image_repository": migration_binding["image_repository"],
        "image_digest": migration_binding["image_digest"],
    }:
        raise ValueError("production canary migration image differs from candidate")

    services = top["services"]
    if not isinstance(services, dict) or tuple(services) != SERVICE_NAMES:
        raise ValueError("production canary service order/set is invalid")
    for name in SERVICE_NAMES:
        service = _ordered(services[name], SERVICE_KEYS, f"{name} service")
        binding = candidate["services"][name]
        if (
            service["source_sha"] != binding["source_sha"]
            or service["image_repository"] != binding["image_repository"]
            or service["image_digest"] != binding["image_digest"]
            or DIGEST.fullmatch(str(service["manifest_digest"])) is None
            or DIGEST.fullmatch(str(service["config_digest"])) is None
        ):
            raise ValueError(f"production canary {name} service binding is invalid")
    runtime = _ordered(
        top["service_runtime"],
        (
            "schema_version",
            "status",
            "services_commit",
            "observed_commit",
            "services",
        ),
        "service runtime",
    )
    if (
        runtime["schema_version"] != 1
        or runtime["status"] != "passed"
        or runtime["services_commit"] != top["services_commit"]
        or runtime["observed_commit"] != top["on_commit"]
        or not isinstance(runtime["services"], dict)
        or tuple(runtime["services"]) != SERVICE_NAMES
    ):
        raise ValueError("production canary service runtime summary is invalid")
    applied_revisions = {
        name: _git(
            root,
            "log",
            "-1",
            "--format=%H",
            top["on_commit"],
            "--",
            SERVICE_PATHS[name],
        )
        for name in SERVICE_NAMES
    }
    if any(SHA40.fullmatch(value) is None for value in applied_revisions.values()):
        raise ValueError("production canary service applied revision is invalid")
    for name in SERVICE_NAMES:
        observed = _ordered(
            runtime["services"][name], RUNTIME_SERVICE_KEYS, f"{name} runtime"
        )
        authenticated = services[name]
        if (
            observed["service"] != name
            or observed["application_observed_revision"] != top["on_commit"]
            or observed["application_applied_revision"] != applied_revisions[name]
            or observed["root_digest"] != authenticated["image_digest"]
            or observed["manifest_digest"] != authenticated["manifest_digest"]
            or observed["config_digest"] != authenticated["config_digest"]
            or not isinstance(observed["deployment_uid"], str)
            or not observed["deployment_uid"]
            or not isinstance(observed["pods"], list)
            or not observed["pods"]
        ):
            raise ValueError(f"production canary {name} runtime binding is invalid")
        seen_pods: set[str] = set()
        for pod_value in observed["pods"]:
            pod = _ordered(pod_value, RUNTIME_POD_KEYS, f"{name} runtime Pod")
            if (
                not isinstance(pod["pod_uid"], str)
                or not pod["pod_uid"]
                or pod["pod_uid"] in seen_pods
                or DIGEST.fullmatch(str(pod["runtime_image_digest"])) is None
                or pod["runtime_image_form"] not in {"manifest", "config"}
                or pod["runtime_image_digest"]
                != authenticated[
                    "manifest_digest"
                    if pod["runtime_image_form"] == "manifest"
                    else "config_digest"
                ]
            ):
                raise ValueError(f"production canary {name} runtime Pod is invalid")
            seen_pods.add(pod["pod_uid"])
    sync_seconds = top["sync_detection_seconds"]
    if (
        isinstance(sync_seconds, bool)
        or not isinstance(sync_seconds, int)
        or not 0 <= sync_seconds <= candidate["rollout"]["sync_timeout_seconds"]
        or top["canary_seconds"] != candidate["rollout"]["canary_seconds"]
    ):
        raise ValueError("production canary timing differs from sealed policy")

    run_id = _positive(run.get("id"), "run ID")
    if (
        top["promoter_repository"] != REPOSITORY
        or top["promoter_workflow_path"] != WORKFLOW_PATH
        or top["promoter_workflow_sha256"] != workflow_hash
        or top["promoter_run_id"] != run_id
        or top["promoter_run_attempt"] != 1
        or top["promoter_head_sha"] != run.get("head_sha")
    ):
        raise ValueError("production canary producer coordinates are invalid")
    return top


def canary_artifact_name(release_id: str, run_id: int) -> str:
    return f"{release_id}-production-canary-run-{run_id}-attempt-1"


def _eligible_run_base(run: Any) -> bool:
    return bool(
        isinstance(run, dict)
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("event") == "workflow_dispatch"
        and run.get("path") == WORKFLOW_PATH
        and SHA40.fullmatch(str(run.get("head_sha"))) is not None
        and run.get("head_branch") == "main"
        and run.get("run_attempt") == 1
        and isinstance(run.get("id"), int)
        and not isinstance(run.get("id"), bool)
        and run["id"] > 0
        and (run.get("repository") or {}).get("full_name") == REPOSITORY
        and (run.get("head_repository") or {}).get("full_name") == REPOSITORY
    )


def _eligible_run(run: Any, eligible_heads: set[str]) -> bool:
    return _eligible_run_base(run) and run.get("head_sha") in eligible_heads


def select_canary_runs(
    runs: list[dict[str, Any]],
    eligible_heads: set[str],
    release_id: str,
    artifact_lookup: Callable[[str], list[dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen: set[tuple[int, int]] = set()
    for run in runs:
        if not _eligible_run(run, eligible_heads):
            continue
        coordinate = (run["id"], 1)
        if coordinate in seen:
            raise ValueError("production canary run coordinate is duplicated")
        seen.add(coordinate)
        name = canary_artifact_name(release_id, run["id"])
        matches = [
            artifact
            for artifact in artifact_lookup(name)
            if isinstance(artifact, dict)
            and artifact.get("name") == name
            and artifact.get("expired") is False
            and (artifact.get("workflow_run") or {}).get("id") == run["id"]
        ]
        if len(matches) > 1:
            raise ValueError("production canary run has duplicate active artifacts")
        if len(matches) == 1:
            selected.append((run, matches[0]))
    return selected


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ValueError("production canary Git provenance lookup failed")
    return result.stdout.strip()


def verify(
    root: Path,
    release_id: str,
    gitops_root: Path | None = None,
    expected_run_id: int | None = None,
    *,
    require_current_head: bool = True,
) -> dict[str, Any]:
    root = root.resolve()
    gitops_root = (gitops_root or root).resolve()
    release_path, _, release, candidate, candidate_hash = resolve_release_bundle(
        root, release_id
    )
    token = os.environ.get("RELEASE_EVIDENCE_TOKEN", "")
    if not token or shutil.which("gh") is None:
        raise ValueError("RELEASE_EVIDENCE_TOKEN and GitHub CLI are required")
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("production canary must be verified in the GitOps repository")
    sealed_head = _git(root, "rev-parse", "HEAD")
    if os.environ.get("GITHUB_REF_NAME") != "main":
        raise ValueError("production canary verifier must execute from main")
    artifacts.verify_validation_tree(
        root,
        release_id,
        candidate["gitops"]["base_sha"],
        release["validation_attestation"]["validator_head_sha"],
    )
    release_hash = hashlib.sha256(release_path.read_bytes()).hexdigest()
    env = os.environ.copy()
    env["GH_TOKEN"] = token

    current_main: str | None = None
    eligible_heads: set[str] | None = None
    if require_current_head:
        _git(gitops_root, "fetch", "--no-tags", "origin", "main:refs/remotes/origin/main")
        current_main = _git(gitops_root, "rev-parse", "refs/remotes/origin/main")
        if os.environ.get("GITHUB_SHA") != current_main:
            raise ValueError("production canary verifier is not current protected main")
        current_state = inspect_chain(gitops_root, candidate, release_hash, current_main)
        if current_state["phase"] != "mission-on":
            raise ValueError("GitOps main is not the exact mission-ON chain phase")
        eligible_heads = {current_state["off_commit"], current_state["on_commit"]}
        runs: list[dict[str, Any]] = []
        for eligible_head in sorted(eligible_heads):
            runs.extend(artifacts._list_protected_runs(env, REPOSITORY, eligible_head))
        selected = select_canary_runs(
            runs,
            eligible_heads,
            release_id,
            lambda name: artifacts._list_named_artifacts(env, REPOSITORY, name),
        )
        if expected_run_id is not None:
            _positive(expected_run_id, "expected run ID")
            selected = [item for item in selected if item[0].get("id") == expected_run_id]
    else:
        if expected_run_id is None:
            raise ValueError("historical production canary verification requires a run ID")
        expected_run_id = _positive(expected_run_id, "expected run ID")
        listed_run = artifacts._run_json(
            ["gh", "api", f"repos/{REPOSITORY}/actions/runs/{expected_run_id}"], env
        )
        if not _eligible_run_base(listed_run):
            raise ValueError("referenced production canary run is not eligible")
        name = canary_artifact_name(release_id, expected_run_id)
        matches = [
            item
            for item in artifacts._list_named_artifacts(env, REPOSITORY, name)
            if isinstance(item, dict)
            and item.get("name") == name
            and item.get("expired") is False
            and (item.get("workflow_run") or {}).get("id") == expected_run_id
        ]
        if len(matches) != 1:
            raise ValueError("referenced production canary must have one active artifact")
        selected = [(listed_run, matches[0])]
    if not selected:
        raise ValueError("no eligible successful production canary artifact exists")

    current_candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for listed_run, listed_artifact in selected:
        run_id = listed_run["id"]
        current_run = artifacts._run_json(
            ["gh", "api", f"repos/{REPOSITORY}/actions/runs/{run_id}"], env
        )
        attempt_run = artifacts._run_json(
            ["gh", "api", f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/1"],
            env,
        )
        eligible_now = (
            _eligible_run(current_run, eligible_heads)
            and _eligible_run(attempt_run, eligible_heads)
            if eligible_heads is not None
            else _eligible_run_base(current_run) and _eligible_run_base(attempt_run)
        )
        if not eligible_now or current_run.get("id") != run_id or attempt_run.get("id") != run_id:
            raise ValueError("production canary attempt-one run drifted")
        workflow_raw = subprocess.run(
            ["git", "show", f"{attempt_run['head_sha']}:{WORKFLOW_PATH}"],
            cwd=gitops_root,
            capture_output=True,
            check=False,
        ).stdout
        if not workflow_raw or b"\r" in workflow_raw:
            raise ValueError("production canary workflow blob is invalid")
        artifacts.validate_workflow_dispatch_inputs(
            workflow_raw, {"release_id"}, "production-canary"
        )
        workflow_hash = hashlib.sha256(workflow_raw).hexdigest()
        artifact_id = _positive(listed_artifact.get("id"), "artifact ID")
        metadata = artifacts._run_json(
            ["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}"], env
        )
        name = canary_artifact_name(release_id, run_id)
        if (
            metadata.get("id") != artifact_id
            or metadata.get("name") != name
            or metadata.get("expired") is not False
            or (metadata.get("workflow_run") or {}).get("id") != run_id
        ):
            raise ValueError("production canary artifact identity drifted")
        with tempfile.TemporaryDirectory(prefix="mission-spine-production-canary-") as temp:
            destination = Path(temp) / "artifact"
            artifacts.download_production_canary_archive(
                env, REPOSITORY, artifact_id, metadata, destination
            )
            raw = (destination / "evidence.json").read_bytes()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("production canary evidence is not UTF-8 JSON") from exc
        validated = validate_promotion_payload(
            payload,
            raw,
            gitops_root,
            release_id,
            candidate,
            candidate_hash,
            release_hash,
            attempt_run,
            workflow_hash,
        )
        historical_state = inspect_chain(
            gitops_root, candidate, release_hash, validated["on_commit"]
        )
        if attempt_run.get("head_sha") not in {
            historical_state["off_commit"],
            historical_state["on_commit"],
        }:
            raise ValueError("production canary run head is outside its exact OFF/ON chain")
        if current_main is None or validated["on_commit"] == current_main:
            current_candidates.append((attempt_run, validated))

    if not current_candidates:
        raise ValueError("no production canary evidence matches current GitOps main")
    current_candidates.sort(
        key=lambda item: (
            str(item[0].get("created_at") or ""),
            int(item[0]["id"]),
        )
    )
    chosen_run, chosen = current_candidates[-1]
    return {
        "run_id": chosen_run["id"],
        "run_attempt": 1,
        "on_commit": chosen["on_commit"],
        "services_commit": chosen["services_commit"],
        "migration_commit": chosen["migration_commit"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--gitops-root", type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(args.root, args.release_id, args.gitops_root, args.run_id)
        if args.github_output is not None:
            if args.github_output.is_symlink() or not args.github_output.is_file():
                raise ValueError("GITHUB_OUTPUT must be an existing regular file")
            with args.github_output.open("a", encoding="utf-8", newline="\n") as output:
                for key in (
                    "run_id",
                    "run_attempt",
                    "on_commit",
                    "services_commit",
                    "migration_commit",
                ):
                    value = result[key]
                    if "\n" in str(value) or "\r" in str(value):
                        raise ValueError("production canary output is unsafe")
                    output.write(f"{key}={value}\n")
        print(
            f"verified production canary run {result['run_id']} at {result['on_commit']}"
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"production canary gate failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
