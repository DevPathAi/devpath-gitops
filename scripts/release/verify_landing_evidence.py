#!/usr/bin/env python3
"""Authenticate the protected Landing-last artifact before reverse rollback."""

from __future__ import annotations

import argparse
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
from urllib.parse import quote

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_release_manifest import resolve_release_bundle
import verify_release_artifacts as artifacts
from verify_promotion_chain import inspect_chain
import verify_promotion_evidence as promotion


REPOSITORY = "DevPathAi/devpath-gitops"
WORKFLOW_PATH = ".github/workflows/mission-spine-landing-last.yml"
ENVIRONMENT = "mission-spine-production-landing"
JOB_NAME = "Deploy Home landing-last"
TOP_KEYS = (
    "schema_version",
    "document_type",
    "release_id",
    "candidate_spec_sha256",
    "release_manifest_sha256",
    "status",
    "landing_order",
    "deployment_id",
    "canary_run_id",
    "canary_run_attempt",
    "on_commit",
    "services_commit",
    "migration_commit",
    "producer_repository",
    "producer_workflow_path",
    "producer_workflow_sha256",
    "producer_head_sha",
    "producer_run_id",
    "producer_run_attempt",
    "approval_environment",
    "approval_environment_id",
    "approval_job_name",
    "approved_by",
    "approved_by_id",
    "approval_effective_at",
)
SHA40 = re.compile(r"[0-9a-f]{40}")
CF_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _positive(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"landing-last {label} must be a positive integer")
    return value


def _canonical(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def landing_artifact_name(release_id: str, run_id: int) -> str:
    _positive(run_id, "run id")
    return f"{release_id}-landing-last-run-{run_id}-attempt-1"


def validate_landing_payload(
    payload: Any,
    raw: bytes,
    gitops_root: Path,
    release_id: str,
    candidate: dict[str, Any],
    candidate_hash: str,
    release_hash: str,
    run: dict[str, Any],
    workflow_hash: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or tuple(payload) != TOP_KEYS:
        raise ValueError("landing-last evidence keys/order are not exact")
    if raw != _canonical(payload) or len(raw) > 256 * 1024:
        raise ValueError("landing-last evidence is not canonical compact UTF-8 JSON+LF")
    artifacts._validate_sanitized(payload, "landing-last evidence")
    run_id = _positive(run.get("id"), "producer run id")
    _positive(payload["producer_run_id"], "producer run id")
    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or payload["document_type"] != "mission-spine-landing-last"
        or payload["release_id"] != release_id
        or payload["candidate_spec_sha256"] != candidate_hash
        or payload["release_manifest_sha256"] != release_hash
        or payload["status"] != "passed"
        or payload["landing_order"] != "last"
        or CF_ID.fullmatch(str(payload["deployment_id"])) is None
        or _positive(payload["canary_run_attempt"], "canary run attempt") != 1
        or _positive(payload["producer_run_attempt"], "producer run attempt") != 1
        or payload["producer_run_id"] != run_id
        or payload["producer_repository"] != REPOSITORY
        or payload["producer_workflow_path"] != WORKFLOW_PATH
        or payload["producer_workflow_sha256"] != workflow_hash
        or payload["producer_head_sha"] != run.get("head_sha")
        or payload["approval_environment"] != ENVIRONMENT
        or payload["approval_job_name"] != JOB_NAME
    ):
        raise ValueError("landing-last evidence identity/status is invalid")
    _positive(payload["canary_run_id"], "canary run id")
    for key in ("on_commit", "services_commit", "migration_commit"):
        if SHA40.fullmatch(str(payload[key])) is None:
            raise ValueError(f"landing-last {key} is invalid")
    _positive(payload["approval_environment_id"], "approval environment id")
    _positive(payload["approved_by_id"], "approved reviewer id")
    state = inspect_chain(
        gitops_root,
        candidate,
        candidate_hash,
        release_hash,
        payload["on_commit"],
    )
    if (
        state["phase"] != "mission-on"
        or state["on_commit"] != payload["on_commit"]
        or state["services_commit"] != payload["services_commit"]
        or state["migration_commit"] != payload["migration_commit"]
    ):
        raise ValueError("landing-last evidence does not bind the exact promoted chain")
    return payload


def _eligible_run(run: Any, landing_head: str) -> bool:
    return bool(
        isinstance(run, dict)
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("event") == "workflow_dispatch"
        and run.get("path") == WORKFLOW_PATH
        and run.get("head_sha") == landing_head
        and run.get("head_branch") == "main"
        and type(run.get("run_attempt")) is int
        and run.get("run_attempt") == 1
        and isinstance(run.get("id"), int)
        and not isinstance(run.get("id"), bool)
        and run["id"] > 0
        and (run.get("repository") or {}).get("full_name") == REPOSITORY
        and (run.get("head_repository") or {}).get("full_name") == REPOSITORY
    )


def _run_coordinate(run: dict[str, Any]) -> tuple[Any, ...]:
    return (
        run.get("id"),
        run.get("run_attempt"),
        run.get("run_number"),
        run.get("workflow_id"),
        run.get("event"),
        run.get("path"),
        run.get("head_sha"),
        run.get("head_branch"),
        (run.get("repository") or {}).get("full_name"),
        (run.get("head_repository") or {}).get("full_name"),
    )


def validate_landing_run_pair(
    listed_run: Any,
    current_run: Any,
    attempt_run: Any,
    landing_head: str,
) -> int:
    """Bind the list result, current run, and attempt-one endpoint to one run."""
    if not all(
        _eligible_run(run, landing_head)
        for run in (listed_run, current_run, attempt_run)
    ):
        raise ValueError("landing-last run coordinate is not eligible")
    coordinate = _run_coordinate(listed_run)
    if (
        _run_coordinate(current_run) != coordinate
        or _run_coordinate(attempt_run) != coordinate
    ):
        raise ValueError("landing-last run coordinate drifted across API endpoints")
    return int(listed_run["id"])


def validate_landing_canary_binding(
    landing: dict[str, Any], canary: dict[str, Any]
) -> None:
    """Bind Landing-last evidence to the exact authenticated canary record."""
    expected = {
        "run_id": landing["canary_run_id"],
        "run_attempt": landing["canary_run_attempt"],
        "on_commit": landing["on_commit"],
        "services_commit": landing["services_commit"],
        "migration_commit": landing["migration_commit"],
    }
    if not isinstance(canary, dict) or any(
        canary.get(key) != value for key, value in expected.items()
    ):
        raise ValueError("landing-last evidence differs from its live canary artifact")


def select_landing_runs(
    runs: list[dict[str, Any]],
    landing_head: str,
    release_id: str,
    artifact_lookup: Callable[[str], list[dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen: set[int] = set()
    for run in runs:
        if not _eligible_run(run, landing_head):
            continue
        run_id = run["id"]
        if run_id in seen:
            raise ValueError("landing-last run coordinate is duplicated")
        seen.add(run_id)
        name = landing_artifact_name(release_id, run_id)
        matches = [
            item
            for item in artifact_lookup(name)
            if isinstance(item, dict)
            and item.get("name") == name
            and item.get("expired") is False
            and (item.get("workflow_run") or {}).get("id") == run_id
        ]
        if len(matches) > 1:
            raise ValueError("landing-last run has duplicate active artifacts")
        if len(matches) == 1:
            selected.append((run, matches[0]))
    return selected


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ValueError("landing-last Git provenance lookup failed")
    return result.stdout.strip()


def _approval_claim(
    environment: Any, approvals: Any, jobs: Any
) -> dict[str, Any]:
    if not isinstance(environment, dict) or environment.get("name") != ENVIRONMENT:
        raise ValueError("landing-last protected environment identity is invalid")
    environment_id = _positive(environment.get("id"), "environment id")
    if not isinstance(approvals, list) or not isinstance(jobs, list):
        raise ValueError("landing-last approval API response is invalid")
    reviews = [
        review
        for review in approvals
        if isinstance(review, dict)
        and review.get("state") == "approved"
        and [
            item
            for item in (review.get("environments") or [])
            if isinstance(item, dict)
            and item.get("id") == environment_id
            and item.get("name") == ENVIRONMENT
        ]
        == [{"id": environment_id, "name": ENVIRONMENT}]
    ]
    matching_jobs = [
        job for job in jobs if isinstance(job, dict) and job.get("name") == JOB_NAME
    ]
    if len(reviews) != 1 or len(matching_jobs) != 1:
        raise ValueError("landing-last requires one exact approval and protected job")
    user = reviews[0].get("user") or {}
    job = matching_jobs[0]
    return {
        "approval_environment": ENVIRONMENT,
        "approval_environment_id": environment_id,
        "approval_job_name": JOB_NAME,
        "approved_by": user.get("login"),
        "approved_by_id": user.get("id"),
        "approval_effective_at": job.get("started_at"),
    }


def _branch_policies(command_env: dict[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for page in range(1, 1001):
        value = artifacts._run_json(
            [
                "gh",
                "api",
                (
                    f"repos/{REPOSITORY}/environments/{ENVIRONMENT}/"
                    f"deployment-branch-policies?per_page=100&page={page}"
                ),
            ],
            command_env,
        )
        rows = value.get("branch_policies")
        if not isinstance(rows, list):
            raise ValueError("landing-last environment branch policies are invalid")
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise ValueError("landing-last environment branch policy pagination exceeded its bound")


def verify(
    root: Path,
    release_id: str,
    gitops_root: Path | None = None,
    github_output: Path | None = None,
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
        raise ValueError("landing-last consumer must run in the GitOps repository")
    sealed_head = _git(root, "rev-parse", "HEAD")
    branch = f"release/candidate-{release_id}"
    if os.environ.get("GITHUB_REF_NAME") != "main":
        raise ValueError("landing-last consumer must execute from main")
    artifacts.verify_validation_tree(
        root,
        release_id,
        candidate["gitops"]["base_sha"],
        release["validation_attestation"]["validator_head_sha"],
    )
    command_env = os.environ.copy()
    command_env["GH_TOKEN"] = token
    branch_response = artifacts._run_json(
        ["gh", "api", f"repos/{REPOSITORY}/branches/{quote(branch, safe='')}"],
        command_env,
    )
    if (branch_response.get("commit") or {}).get("sha") != sealed_head:
        raise ValueError("landing-last release branch head drifted")
    release_hash = hashlib.sha256(release_path.read_bytes()).hexdigest()
    _git(gitops_root, "fetch", "--no-tags", "origin", "main:refs/remotes/origin/main")
    current_main = _git(gitops_root, "rev-parse", "refs/remotes/origin/main")
    if os.environ.get("GITHUB_SHA") != current_main:
        raise ValueError("landing-last consumer is not the current main commit")
    current_state = inspect_chain(
        gitops_root, candidate, candidate_hash, release_hash, current_main
    )
    if current_state["phase"] not in {"mission-on", "rollback-off", "rollback-prior"}:
        raise ValueError("landing-last consumer requires an exact ON or rollback chain")
    landing_head = current_state["on_commit"]
    selected = select_landing_runs(
        artifacts._list_protected_runs(command_env, REPOSITORY, landing_head),
        landing_head,
        release_id,
        lambda name: artifacts._list_named_artifacts(command_env, REPOSITORY, name),
    )
    validated: list[tuple[str, int, dict[str, Any]]] = []
    for listed_run, listed_artifact in selected:
        run_id = listed_run["id"]
        current_run = artifacts._run_json(
            ["gh", "api", f"repos/{REPOSITORY}/actions/runs/{run_id}"], command_env
        )
        attempt_run = artifacts._run_json(
            ["gh", "api", f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/1"],
            command_env,
        )
        validate_landing_run_pair(
            listed_run, current_run, attempt_run, landing_head
        )
        workflow_raw = subprocess.run(
            ["git", "show", f"{landing_head}:{WORKFLOW_PATH}"],
            cwd=gitops_root,
            capture_output=True,
            check=False,
        ).stdout
        if not workflow_raw or b"\r" in workflow_raw:
            raise ValueError("landing-last workflow blob is invalid")
        artifacts.validate_workflow_dispatch_inputs(
            workflow_raw, {"release_id"}, "landing-last"
        )
        workflow_hash = hashlib.sha256(workflow_raw).hexdigest()
        artifact_id = _positive(listed_artifact.get("id"), "artifact id")
        metadata = artifacts._run_json(
            ["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}"],
            command_env,
        )
        if (
            metadata.get("id") != artifact_id
            or metadata.get("name") != landing_artifact_name(release_id, run_id)
            or metadata.get("expired") is not False
            or (metadata.get("workflow_run") or {}).get("id") != run_id
        ):
            raise ValueError("landing-last artifact identity drifted")
        reference = {
            "event": "workflow_dispatch",
            "head_sha": landing_head,
            "run_attempt": 1,
            "workflow_path": WORKFLOW_PATH,
            "workflow_sha256": workflow_hash,
            "workflow_run_id": run_id,
        }
        artifacts.validate_run_provenance(
            "landing-last",
            attempt_run,
            reference,
            landing_head,
            WORKFLOW_PATH,
            workflow_raw,
        )
        with tempfile.TemporaryDirectory(prefix="mission-spine-landing-last-") as temp:
            destination = Path(temp) / "artifact"
            artifacts.download_landing_evidence_archive(
                command_env, REPOSITORY, artifact_id, metadata, destination
            )
            raw = (destination / "evidence.json").read_bytes()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("landing-last artifact is not UTF-8 JSON") from exc
        value = validate_landing_payload(
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
        environment = artifacts._run_json(
            ["gh", "api", f"repos/{REPOSITORY}/environments/{ENVIRONMENT}"],
            command_env,
        )
        approvals = artifacts._run_json_value(
            ["gh", "api", f"repos/{REPOSITORY}/actions/runs/{run_id}/approvals"],
            command_env,
        )
        jobs = artifacts._list_attempt_jobs(command_env, REPOSITORY, run_id, 1)
        deployment_policy = environment.get("deployment_branch_policy") if isinstance(environment, dict) else None
        if (
            not isinstance(environment, dict)
            or environment.get("can_admins_bypass") is not False
            or not isinstance(deployment_policy, dict)
            or deployment_policy.get("protected_branches") is not False
            or deployment_policy.get("custom_branch_policies") is not True
            or _branch_policies(command_env) != [{"id": 57524490, "name": "main", "type": "branch"}]
        ):
            raise ValueError("landing-last environment is not exact main-only/no-bypass")
        claim = {
            "approval_environment": value["approval_environment"],
            "approval_environment_id": value["approval_environment_id"],
            "approval_job_name": value["approval_job_name"],
            "approved_by": value["approved_by"],
            "approved_by_id": value["approved_by_id"],
            "approval_effective_at": value["approval_effective_at"],
        }
        team_ids = artifacts._configured_team_memberships(
            command_env,
            REPOSITORY,
            environment,
            claim["approved_by"],
            claim["approved_by_id"],
        )
        artifacts.validate_protected_approval(
            "landing-last",
            claim,
            environment,
            approvals,
            jobs,
            attempt_run,
            landing_head,
            expected_repository=REPOSITORY,
            expected_branch="main",
            approved_team_ids=team_ids,
        )
        canary = promotion.verify(
            root,
            release_id,
            gitops_root,
            value["canary_run_id"],
            require_current_head=False,
        )
        validate_landing_canary_binding(value, canary)
        validated.append((str(attempt_run.get("created_at") or ""), run_id, value))
    if not validated:
        raise ValueError("no authenticated Landing-last evidence exists")
    validated.sort(key=lambda item: (item[0], item[1]))
    chosen = validated[-1][2]
    if github_output is not None:
        if github_output.is_symlink() or not github_output.is_file():
            raise ValueError("landing-last GITHUB_OUTPUT must be an existing regular file")
        with github_output.open("a", encoding="utf-8", newline="\n") as output:
            for key in (
                "deployment_id",
                "on_commit",
                "services_commit",
                "migration_commit",
            ):
                output.write(f"{key}={chosen[key]}\n")
    return chosen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--gitops-root", type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(args.root, args.release_id, args.gitops_root, args.github_output)
        print(f"verified Landing-last deployment {result['deployment_id']}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Landing-last evidence verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
