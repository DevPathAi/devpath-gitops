#!/usr/bin/env python3
"""Authenticate the protected Shared migration result and exact GitOps M commit."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import quote

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import verify_release_artifacts as evidence
from validate_release_manifest import resolve_release_bundle
from verify_promotion_chain import inspect_chain, migration_job_name


TOP_KEYS = (
    "schema_version",
    "document_type",
    "release_id",
    "candidate_spec_sha256",
    "release_manifest_sha256",
    "shared",
    "approval",
    "gitops",
    "migration_image",
)
SHARED_KEYS = (
    "repository",
    "source_sha",
    "workflow_path",
    "workflow_ref",
    "workflow_sha256",
    "run_id",
    "run_attempt",
    "event_name",
    "ref",
    "job",
    "environment",
)
APPROVAL_KEYS = (
    "environment_id",
    "reviewer_login",
    "reviewer_id",
    "reviewer_type",
    "state",
    "approval_effective_at",
)
GITOPS_KEYS = (
    "repository",
    "base_sha",
    "sealed_release_sha",
    "pre_push_main_sha",
    "migration_commit_sha",
    "migration_tree_sha",
    "publish_mode",
    "write_app_slug",
    "write_app_id",
    "write_app_installation_id",
    "branch",
    "sole_changed_path",
    "rendered_job_name",
    "commit_subject",
    "commit_author_name",
    "commit_committer_name",
)
MIGRATION_IMAGE_KEYS = ("repository", "digest")
SHA40 = re.compile(r"[0-9a-f]{40}")
SHA64 = re.compile(r"[0-9a-f]{64}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
RELEASE_ID = re.compile(r"ms-[0-9]{8}-[a-z0-9][a-z0-9-]{2,40}")
SHARED_REPOSITORY = "DevPathAi/devpath-shared"
GITOPS_REPOSITORY = "DevPathAi/devpath-gitops"
WORKFLOW_PATH = ".github/workflows/mission-spine-migration-release.yml"
APPROVAL_ENVIRONMENT = "mission-spine-migration-release"
APPROVAL_JOB = "deploy"


def _positive(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"migration result {label} must be a positive integer")
    return value


def _ordered(value: Any, keys: tuple[str, ...], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or tuple(value) != keys:
        raise ValueError(f"migration result {label} keys/order are not exact")
    return value


def _utc(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"migration result {label} is not UTC-Z")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ValueError(f"migration result {label} is invalid") from exc
    if parsed.tzinfo != timezone.utc:
        raise ValueError(f"migration result {label} is not UTC")


def _reject_sensitive(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(r"(?i)(token|password|private[_-]?key|authorization|kubeconfig)", key):
                raise ValueError(f"migration result contains a sensitive key at {path}")
            _reject_sensitive(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive(child, f"{path}[{index}]")
    elif isinstance(value, str) and ("\r" in value or "\n" in value):
        raise ValueError(f"migration result contains multiline data at {path}")


def validate_migration_result_payload(
    payload: Any,
    raw: bytes,
    candidate: dict[str, Any],
    candidate_hash: str,
    release_hash: str,
    sealed_release_sha: str,
) -> dict[str, Any]:
    """Validate exact canonical bytes and all candidate/release coordinates."""
    top = _ordered(payload, TOP_KEYS, "top-level")
    _reject_sensitive(top)
    canonical = (json.dumps(top, separators=(",", ":"), ensure_ascii=True) + "\n").encode(
        "utf-8"
    )
    if raw != canonical or len(raw) > 256 * 1024:
        raise ValueError("migration result bytes are not canonical compact UTF-8 JSON+LF")
    release_id = candidate.get("release_id")
    if (
        top["schema_version"] != 1
        or top["document_type"] != "mission-spine-migration-result"
        or RELEASE_ID.fullmatch(str(release_id)) is None
        or top["release_id"] != release_id
    ):
        raise ValueError("migration result release identity is invalid")
    if SHA64.fullmatch(candidate_hash or "") is None or top[
        "candidate_spec_sha256"
    ] != candidate_hash:
        raise ValueError("migration result candidate hash is invalid")
    if SHA64.fullmatch(release_hash or "") is None or top[
        "release_manifest_sha256"
    ] != release_hash:
        raise ValueError("migration result release manifest hash is invalid")
    shared = _ordered(top["shared"], SHARED_KEYS, "shared")
    migration = candidate["shared_migration"]
    expected_shared = {
        "repository": "DevPathAi/devpath-shared",
        "source_sha": migration["source_sha"],
        "workflow_path": ".github/workflows/mission-spine-migration-release.yml",
        "workflow_ref": "DevPathAi/devpath-shared/.github/workflows/mission-spine-migration-release.yml@refs/heads/main",
        "event_name": "workflow_dispatch",
        "ref": "refs/heads/main",
        "job": "deploy",
        "environment": "mission-spine-migration-release",
    }
    for key, expected in expected_shared.items():
        if shared[key] != expected:
            raise ValueError(f"migration result shared source/provenance {key} drifted")
    if SHA64.fullmatch(str(shared["workflow_sha256"])) is None:
        raise ValueError("migration result shared workflow hash is invalid")
    run_id = _positive(shared["run_id"], "shared run id")
    if shared["run_attempt"] != 1:
        raise ValueError("migration result protected run attempt must equal 1")
    approval = _ordered(top["approval"], APPROVAL_KEYS, "approval")
    _positive(approval["environment_id"], "approval environment id")
    _positive(approval["reviewer_id"], "approval reviewer id")
    if (
        LOGIN.fullmatch(str(approval["reviewer_login"])) is None
        or approval["reviewer_type"] != "User"
        or approval["state"] != "approved"
    ):
        raise ValueError("migration result approval identity is invalid")
    _utc(approval["approval_effective_at"], "approval effective time")
    gitops = _ordered(top["gitops"], GITOPS_KEYS, "gitops")
    base = candidate["gitops"]["base_sha"]
    if (
        gitops["repository"] != "DevPathAi/devpath-gitops"
        or gitops["base_sha"] != base
        or SHA40.fullmatch(sealed_release_sha or "") is None
        or gitops["sealed_release_sha"] != sealed_release_sha
        or SHA40.fullmatch(str(gitops["migration_commit_sha"])) is None
        or SHA40.fullmatch(str(gitops["migration_tree_sha"])) is None
        or gitops["branch"] != "main"
        or gitops["sole_changed_path"]
        != "apps/devpath-migration/base/kustomization.yaml"
    ):
        raise ValueError("migration result GitOps commit coordinates are invalid")
    migration_commit = gitops["migration_commit_sha"]
    if gitops["publish_mode"] == "published":
        expected_pre = base
    elif gitops["publish_mode"] == "reused":
        expected_pre = migration_commit
    else:
        raise ValueError("migration result publish mode is invalid")
    if gitops["pre_push_main_sha"] != expected_pre:
        raise ValueError("migration result publish mode does not match pre-push main")
    if (
        gitops["write_app_slug"] != "devpath-gitops-release"
        or _positive(gitops["write_app_id"], "write App id") <= 0
        or _positive(gitops["write_app_installation_id"], "write App installation id") <= 0
    ):
        raise ValueError("migration result write App identity is invalid")
    image = _ordered(top["migration_image"], MIGRATION_IMAGE_KEYS, "migration image")
    if (
        image["repository"] != migration["image_repository"]
        or image["digest"] != migration["image_digest"]
        or DIGEST.fullmatch(str(image["digest"])) is None
    ):
        raise ValueError("migration result image is not the sealed candidate image")
    expected_name = migration_job_name(image["digest"], release_hash)
    expected_subject = f"deploy(devpath-migration): {release_id} sealed {release_hash}"
    if (
        gitops["rendered_job_name"] != expected_name
        or gitops["commit_subject"] != expected_subject
    ):
        raise ValueError("migration result rendered Job or commit subject drifted")
    if (
        gitops["commit_author_name"] != "devpath-gitops-release[bot]"
        or gitops["commit_committer_name"] != "devpath-gitops-release[bot]"
    ):
        raise ValueError("migration result commit actor identity drifted")
    return {
        "run_id": run_id,
        "run_attempt": 1,
        "workflow_sha256": shared["workflow_sha256"],
        "migration_commit_sha": migration_commit,
        "migration_tree_sha": gitops["migration_tree_sha"],
        "rendered_job_name": expected_name,
        "write_app_id": gitops["write_app_id"],
        "write_app_installation_id": gitops["write_app_installation_id"],
        "approval": approval,
    }


def migration_result_artifact_name(release_id: str, run_id: int) -> str:
    if RELEASE_ID.fullmatch(release_id) is None:
        raise ValueError("migration result release ID is invalid")
    _positive(run_id, "artifact run id")
    return f"mission-spine-migration-result-{release_id}-{run_id}-attempt-1"


def _eligible_run(run: Any, expected_source: str) -> bool:
    return bool(
        isinstance(run, dict)
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("event") == "workflow_dispatch"
        and run.get("head_sha") == expected_source
        and run.get("head_branch") == "main"
        and run.get("path") == WORKFLOW_PATH
        and run.get("run_attempt") == 1
        and isinstance(run.get("id"), int)
        and not isinstance(run.get("id"), bool)
        and run["id"] > 0
        and (run.get("repository") or {}).get("full_name") == SHARED_REPOSITORY
        and (run.get("head_repository") or {}).get("full_name") == SHARED_REPOSITORY
    )


def select_unique_migration_result(
    runs: list[dict[str, Any]],
    expected_source: str,
    release_id: str,
    artifact_lookup: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Select one successful attempt-one current-source run and its sole artifact."""
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for run in runs:
        if not _eligible_run(run, expected_source):
            continue
        run_id = run["id"]
        artifact_name = migration_result_artifact_name(release_id, run_id)
        artifacts = [
            artifact
            for artifact in artifact_lookup(artifact_name)
            if isinstance(artifact, dict)
            and artifact.get("name") == artifact_name
            and artifact.get("expired") is False
            and (artifact.get("workflow_run") or {}).get("id") == run_id
        ]
        if len(artifacts) > 1:
            raise ValueError("shared-migration-result: duplicate active artifacts for run")
        if len(artifacts) == 1:
            matches.append((run, artifacts[0]))
    if len(matches) != 1:
        raise ValueError(
            "shared-migration-result: exactly one eligible protected producer run is required"
        )
    return matches[0]


def _git(root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ValueError("migration result Git provenance query failed")
    return result.stdout.strip()


def validate_git_coordinates(
    root: Path,
    candidate: dict[str, Any],
    release: dict[str, Any],
    release_hash: str,
    payload: dict[str, Any],
    *,
    gitops_root: Path | None = None,
) -> dict[str, str]:
    """Bind R and M to the exact local Git object graph and full-byte chain grammar."""
    sealed_sha = _git(root, ["rev-parse", "HEAD"])
    chain_root = (gitops_root or root).resolve()
    evidence.verify_validation_tree(
        root,
        candidate["release_id"],
        candidate["gitops"]["base_sha"],
        release["validation_attestation"]["validator_head_sha"],
    )
    gitops = payload["gitops"]
    if gitops["sealed_release_sha"] != sealed_sha:
        raise ValueError("migration result sealed release commit mismatch")
    migration_commit = gitops["migration_commit_sha"]
    parents = _git(chain_root, ["show", "-s", "--format=%P", migration_commit]).split()
    if parents != [candidate["gitops"]["base_sha"]]:
        raise ValueError("migration result commit is not the sole child of sealed base")
    exact_values = {
        "migration_tree_sha": _git(chain_root, ["show", "-s", "--format=%T", migration_commit]),
        "commit_subject": _git(chain_root, ["show", "-s", "--format=%s", migration_commit]),
        "commit_author_name": _git(chain_root, ["show", "-s", "--format=%an", migration_commit]),
        "commit_committer_name": _git(chain_root, ["show", "-s", "--format=%cn", migration_commit]),
    }
    for key, actual in exact_values.items():
        if gitops[key] != actual:
            raise ValueError(f"migration result {key} differs from committed Git object")
    changed = _git(
        chain_root,
        ["diff-tree", "--no-commit-id", "--name-only", "-r", migration_commit],
    ).splitlines()
    if changed != [gitops["sole_changed_path"]]:
        raise ValueError("migration result commit changed paths are not exact")
    state = inspect_chain(chain_root, candidate, release_hash, migration_commit)
    if state["phase"] != "migration" or state["migration_commit"] != migration_commit:
        raise ValueError("migration result commit is not the exact migration chain phase")
    return {
        "sealed_release_sha": sealed_sha,
        "migration_commit_sha": migration_commit,
        "migration_tree_sha": exact_values["migration_tree_sha"],
    }


def _require_branch(
    branch: Any, name: str, expected_sha: str, label: str, protected: bool = True
) -> None:
    if not isinstance(branch, dict) or branch.get("name") != name:
        raise ValueError(f"{label}: branch response is invalid")
    if protected and branch.get("protected") is not True:
        raise ValueError(f"{label}: branch must be protected")
    if (branch.get("commit") or {}).get("sha") != expected_sha:
        raise ValueError(f"{label}: branch head does not match the sealed coordinate")


def _write_outputs(path: Path, values: dict[str, Any]) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("migration result GITHUB_OUTPUT must be an existing regular file")
    with path.open("a", encoding="utf-8", newline="\n") as output:
        for key, value in values.items():
            rendered = str(value)
            if re.fullmatch(r"[a-z_]+", key) is None or "\n" in rendered or "\r" in rendered:
                raise ValueError("migration result GitHub output is unsafe")
            output.write(f"{key}={rendered}\n")


def discover_migration_result(
    root: Path,
    release_id: str,
    github_output: Path | None = None,
    gitops_root: Path | None = None,
) -> dict[str, Any]:
    """Authenticate the live protected Shared result and exact B->M Git state."""
    root = root.resolve()
    chain_root = (gitops_root or root).resolve()
    release_path, candidate_path, release, candidate, candidate_hash = resolve_release_bundle(
        root, release_id
    )
    token = os.environ.get("RELEASE_EVIDENCE_TOKEN", "")
    if not token:
        raise ValueError("RELEASE_EVIDENCE_TOKEN is required")
    if shutil.which("gh") is None:
        raise ValueError("GitHub CLI is required")
    command_env = os.environ.copy()
    command_env["GH_TOKEN"] = token
    expected_source = candidate["shared_migration"]["source_sha"]
    shared_branch = evidence._run_json(
        ["gh", "api", f"repos/{SHARED_REPOSITORY}/branches/main"], command_env
    )
    _require_branch(shared_branch, "main", expected_source, "shared-migration-result")

    runs = evidence._list_protected_runs(command_env, SHARED_REPOSITORY, expected_source)
    run, listed_artifact = select_unique_migration_result(
        runs,
        expected_source,
        release_id,
        lambda name: evidence._list_named_artifacts(command_env, SHARED_REPOSITORY, name),
    )
    run_id = run["id"]
    current_run = evidence._run_json(
        ["gh", "api", f"repos/{SHARED_REPOSITORY}/actions/runs/{run_id}"], command_env
    )
    attempt_run = evidence._run_json(
        [
            "gh",
            "api",
            f"repos/{SHARED_REPOSITORY}/actions/runs/{run_id}/attempts/1",
        ],
        command_env,
    )
    if not _eligible_run(current_run, expected_source) or not _eligible_run(
        attempt_run, expected_source
    ):
        raise ValueError("shared-migration-result: current attempt-one run drifted")
    artifact_id = _positive(listed_artifact.get("id"), "artifact id")
    artifact = evidence._run_json(
        ["gh", "api", f"repos/{SHARED_REPOSITORY}/actions/artifacts/{artifact_id}"],
        command_env,
    )
    artifact_name = migration_result_artifact_name(release_id, run_id)
    if (
        artifact.get("id") != artifact_id
        or artifact.get("name") != artifact_name
        or artifact.get("expired") is not False
        or (artifact.get("workflow_run") or {}).get("id") != run_id
    ):
        raise ValueError("shared-migration-result: artifact identity or lifetime drifted")

    workflow_raw = evidence._workflow_bytes(
        SHARED_REPOSITORY, WORKFLOW_PATH, expected_source, command_env
    )
    evidence.validate_workflow_dispatch_inputs(
        workflow_raw,
        {
            "release_id",
            "source_sha",
            "sealed_release_sha",
            "gitops_source_sha",
            "release_manifest_sha256",
        },
        "shared-migration-result",
    )
    reference = {
        "event": "workflow_dispatch",
        "head_sha": expected_source,
        "run_attempt": 1,
        "workflow_path": WORKFLOW_PATH,
        "workflow_sha256": hashlib.sha256(workflow_raw).hexdigest(),
        "workflow_run_id": run_id,
    }
    evidence.validate_run_provenance(
        "shared-migration-result",
        attempt_run,
        reference,
        expected_source,
        WORKFLOW_PATH,
        workflow_raw,
    )

    release_hash = hashlib.sha256(release_path.read_bytes()).hexdigest()
    sealed_sha = _git(root, ["rev-parse", "HEAD"])
    with tempfile.TemporaryDirectory(prefix="mission-spine-migration-result-") as temp_dir:
        destination = Path(temp_dir) / "artifact"
        evidence.download_migration_result_archive(
            command_env, SHARED_REPOSITORY, artifact_id, artifact, destination
        )
        raw = (destination / "evidence.json").read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("shared-migration-result: evidence is not UTF-8 JSON") from exc
    validated = validate_migration_result_payload(
        payload,
        raw,
        candidate,
        candidate_hash,
        release_hash,
        sealed_sha,
    )
    if validated["run_id"] != run_id:
        raise ValueError("shared-migration-result: evidence run ID mismatch")
    if validated["workflow_sha256"] != hashlib.sha256(workflow_raw).hexdigest():
        raise ValueError("shared-migration-result: evidence workflow bytes mismatch")
    approval = validated["approval"]
    evidence.verify_live_protected_approval(
        command_env,
        SHARED_REPOSITORY,
        run_id,
        1,
        attempt_run,
        "shared-migration-result",
        {
            "approval_environment": APPROVAL_ENVIRONMENT,
            "approval_environment_id": approval["environment_id"],
            "approval_job_name": APPROVAL_JOB,
            "approved_by": approval["reviewer_login"],
            "approved_by_id": approval["reviewer_id"],
            "approval_effective_at": approval["approval_effective_at"],
        },
        expected_source,
    )

    release_branch_name = f"release/candidate-{release_id}"
    release_branch = evidence._run_json(
        [
            "gh",
            "api",
            f"repos/{GITOPS_REPOSITORY}/branches/{quote(release_branch_name, safe='')}",
        ],
        command_env,
    )
    _require_branch(
        release_branch,
        release_branch_name,
        sealed_sha,
        "shared-migration-result sealed release",
        protected=False,
    )
    migration_commit = validated["migration_commit_sha"]
    main_branch = evidence._run_json(
        ["gh", "api", f"repos/{GITOPS_REPOSITORY}/branches/main"], command_env
    )
    current_main = (main_branch.get("commit") or {}).get("sha")
    if SHA40.fullmatch(str(current_main)) is None:
        raise ValueError("shared-migration-result GitOps main: branch head is invalid")
    _require_branch(
        main_branch, "main", current_main, "shared-migration-result GitOps main"
    )
    _git(chain_root, ["fetch", "--no-tags", "origin", "main:refs/remotes/origin/main"])
    if _git(chain_root, ["rev-parse", "refs/remotes/origin/main"]) != current_main:
        raise ValueError("shared-migration-result: fetched GitOps main drifted")
    coordinates = validate_git_coordinates(
        root, candidate, release, release_hash, payload, gitops_root=chain_root
    )
    current_state = inspect_chain(chain_root, candidate, release_hash, current_main)
    if current_state.get("migration_commit") != migration_commit:
        raise ValueError("shared-migration-result: current main does not retain exact migration")
    outputs: dict[str, Any] = {
        **coordinates,
        "current_main_sha": current_main,
        "current_phase": current_state["phase"],
        "migration_run_id": run_id,
        "migration_run_attempt": 1,
        "migration_artifact_id": artifact_id,
        "rendered_job_name": validated["rendered_job_name"],
        "write_app_id": validated["write_app_id"],
        "write_app_installation_id": validated["write_app_installation_id"],
        "candidate_spec_path": candidate_path.relative_to(root).as_posix(),
    }
    if github_output is not None:
        _write_outputs(github_output, outputs)
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--gitops-root", type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        values = discover_migration_result(
            args.root, args.release_id, args.github_output, args.gitops_root
        )
        print(json.dumps(values, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"migration result verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
