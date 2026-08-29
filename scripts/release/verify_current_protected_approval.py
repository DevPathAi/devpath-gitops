#!/usr/bin/env python3
"""Fail closed unless the current attempt-one job has an authenticated approval."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

REPOSITORY = "DevPathAi/devpath-gitops"
SHA40 = re.compile(r"[0-9a-f]{40}")
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
TEAM_SLUG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,98}[A-Za-z0-9])?")
ACTIONS_BOT_ID = 41898282
ACTIONS_BOT_LOGIN = "github-actions[bot]"
ACTIONS_BOT_TYPE = "Bot"


def _positive(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _utc_z(value: Any, label: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be a UTC-Z timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{label} must be a UTC-Z timestamp") from exc
    if parsed.tzinfo != dt.timezone.utc:
        raise ValueError(f"{label} must be a UTC-Z timestamp")
    return parsed


def _identity(value: Any, label: str) -> tuple[int, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} identity is missing")
    identity_id = _positive(value.get("id"), f"{label} id")
    login = value.get("login")
    if not isinstance(login, str) or LOGIN.fullmatch(login) is None:
        raise ValueError(f"{label} login is invalid")
    return identity_id, login


def _initiator_identity(value: Any, label: str) -> tuple[int, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} identity is missing")
    login = value.get("login")
    claims_automation = value.get("type") == ACTIONS_BOT_TYPE or (
        isinstance(login, str) and (login == ACTIONS_BOT_LOGIN or "[bot]" in login)
    )
    if claims_automation:
        if (
            value.get("id") != ACTIONS_BOT_ID
            or login != ACTIONS_BOT_LOGIN
            or value.get("type") != ACTIONS_BOT_TYPE
        ):
            raise ValueError(
                f"{label} must be exact GitHub Actions automation"
            )
        return ACTIONS_BOT_ID, ACTIONS_BOT_LOGIN
    return _identity(value, label)


def _matching_approvals(
    approvals: Any, environment_id: int, environment_name: str
) -> list[dict[str, Any]]:
    if not isinstance(approvals, list):
        raise ValueError("approval history response is invalid")
    matches: list[dict[str, Any]] = []
    for review in approvals:
        if not isinstance(review, dict) or review.get("state") != "approved":
            continue
        environments = review.get("environments")
        if not isinstance(environments, list):
            continue
        memberships = [
            item
            for item in environments
            if isinstance(item, dict)
            and item.get("id") == environment_id
            and item.get("name") == environment_name
        ]
        if len(memberships) == 1:
            matches.append(review)
    if not matches:
        raise ValueError("at least one approved review for the protected environment is required")
    return matches


def validate_current_protected_approval(
    *,
    environment_name: str,
    job_name: str,
    workflow_path: str,
    expected_head: str,
    expected_branch: str,
    run_id: int,
    run_attempt: int,
    environment: Any,
    approvals: Any,
    jobs: Any,
    run: Any,
    branch_policies: Any,
    approved_team_ids: set[int] | None = None,
) -> dict[str, Any]:
    _positive(run_id, "run id")
    if run_attempt != 1:
        raise ValueError("protected workflow run_attempt must be exactly 1")
    if SHA40.fullmatch(expected_head) is None:
        raise ValueError("expected head SHA is invalid")
    if expected_branch != "main":
        raise ValueError("protected workflow must execute from main")

    if not isinstance(environment, dict):
        raise ValueError("protected environment response is invalid")
    environment_id = _positive(environment.get("id"), "environment id")
    if environment.get("name") != environment_name:
        raise ValueError("protected environment identity mismatch")
    if environment.get("can_admins_bypass") is not False:
        raise ValueError("protected environment must forbid administrator bypass")
    deployment_policy = environment.get("deployment_branch_policy")
    if (
        not isinstance(deployment_policy, dict)
        or deployment_policy.get("protected_branches") is not False
        or deployment_policy.get("custom_branch_policies") is not True
    ):
        raise ValueError("protected environment must use exact custom main branch policy")
    if not isinstance(branch_policies, list) or len(branch_policies) != 1:
        raise ValueError("protected environment must have exactly one branch policy")
    policy = branch_policies[0]
    if (
        not isinstance(policy, dict)
        or policy.get("name") != "main"
        or policy.get("type") != "branch"
    ):
        raise ValueError("protected environment branch policy must be exact main")
    rules = environment.get("protection_rules")
    reviewer_rules = (
        [rule for rule in rules if isinstance(rule, dict) and rule.get("type") == "required_reviewers"]
        if isinstance(rules, list)
        else []
    )
    if len(reviewer_rules) != 1:
        raise ValueError("exactly one required-reviewers protection rule is required")
    rule = reviewer_rules[0]
    if rule.get("prevent_self_review") is not True:
        raise ValueError("protected environment must prevent self-review")
    configured = rule.get("reviewers")
    if not isinstance(configured, list) or len(configured) != 1:
        raise ValueError("protected environment must configure exactly one reviewer")

    if not isinstance(run, dict):
        raise ValueError("protected run response is invalid")
    if (
        run.get("id") != run_id
        or run.get("run_attempt") != 1
        or run.get("event") != "workflow_dispatch"
        or run.get("path") != workflow_path
        or run.get("head_branch") != expected_branch
        or run.get("head_sha") != expected_head
        or (run.get("repository") or {}).get("full_name") != REPOSITORY
        or (run.get("head_repository") or {}).get("full_name") != REPOSITORY
    ):
        raise ValueError("protected run coordinate mismatch")
    if run.get("status") != "in_progress" or run.get("conclusion") is not None:
        raise ValueError("protected run must be the current in-progress attempt")
    actor_identity = _initiator_identity(run.get("actor"), "run actor")
    triggering_identity = _initiator_identity(
        run.get("triggering_actor"), "run triggering actor"
    )
    if actor_identity != triggering_identity:
        raise ValueError("run actor and triggering actor must be the same identity")

    matching_approvals = _matching_approvals(
        approvals, environment_id, environment_name
    )
    approved_identities = [
        _identity(approval.get("user"), "approved reviewer")
        for approval in matching_approvals
    ]
    if len(set(approved_identities)) != 1:
        raise ValueError("all approvals for one protected environment must share one identity")
    approved_id, approved_login = approved_identities[0]

    entry = configured[0]
    reviewer = entry.get("reviewer") if isinstance(entry, dict) else None
    if not isinstance(reviewer, dict):
        raise ValueError("configured reviewer is invalid")
    if entry.get("type") == "User":
        if reviewer.get("id") != approved_id or reviewer.get("login") != approved_login:
            raise ValueError("approved user is not the configured reviewer")
    elif entry.get("type") == "Team":
        team_id = _positive(reviewer.get("id"), "configured team id")
        slug = reviewer.get("slug")
        if not isinstance(slug, str) or TEAM_SLUG.fullmatch(slug) is None:
            raise ValueError("configured team slug is invalid")
        if team_id not in (approved_team_ids or set()):
            raise ValueError("approved user is not an active configured team member")
    else:
        raise ValueError("configured reviewer type is invalid")

    if not isinstance(jobs, list):
        raise ValueError("protected job response is invalid")
    matching_jobs = [
        job for job in jobs if isinstance(job, dict) and job.get("name") == job_name
    ]
    if len(matching_jobs) != 1:
        raise ValueError("exactly one current protected job is required")
    job = matching_jobs[0]
    if (
        job.get("run_id") != run_id
        or job.get("run_attempt") != 1
        or job.get("head_sha") != expected_head
        or job.get("status") != "in_progress"
        or job.get("conclusion") is not None
    ):
        raise ValueError("current protected job coordinate or status mismatch")
    started_at = job.get("started_at")
    started = _utc_z(started_at, "protected job started_at")
    if started < _utc_z(run.get("run_started_at"), "protected run started_at"):
        raise ValueError("protected job started before its workflow run")
    return {
        "approval_environment": environment_name,
        "approval_environment_id": environment_id,
        "approval_job_name": job_name,
        "approved_by": approved_login,
        "approved_by_id": approved_id,
        "approval_effective_at": started_at,
    }


def _gh_json(path: str) -> Any:
    completed = subprocess.run(
        ["gh", "api", "-H", "X-GitHub-Api-Version: 2026-03-10", path],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        raise ValueError(f"GitHub API request failed for {path}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GitHub API returned invalid JSON for {path}") from exc


def _all_jobs(run_id: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for page in range(1, 1001):
        payload = _gh_json(
            f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/1/jobs?per_page=100&page={page}"
        )
        page_jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(page_jobs, list):
            raise ValueError("GitHub jobs response is invalid")
        jobs.extend(page_jobs)
        if len(page_jobs) < 100:
            return jobs
    raise ValueError("GitHub jobs pagination exceeded its safety bound")


def _all_branch_policies(environment_name: str) -> list[dict[str, Any]]:
    policies: list[dict[str, Any]] = []
    for page in range(1, 1001):
        payload = _gh_json(
            f"repos/{REPOSITORY}/environments/{environment_name}/deployment-branch-policies"
            f"?per_page=100&page={page}"
        )
        page_policies = payload.get("branch_policies") if isinstance(payload, dict) else None
        if not isinstance(page_policies, list):
            raise ValueError("GitHub environment branch policy response is invalid")
        policies.extend(page_policies)
        if len(page_policies) < 100:
            return policies
    raise ValueError("GitHub environment branch policy pagination exceeded its safety bound")


def verify_current_protected_approval(
    environment_name: str,
    job_name: str,
    workflow_path: str,
) -> dict[str, Any]:
    if not os.environ.get("GH_TOKEN"):
        raise ValueError("GH_TOKEN is required")
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("current repository is not the GitOps repository")
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        raise ValueError("current run attempt must be canonical string 1")
    try:
        run_id = int(os.environ.get("GITHUB_RUN_ID", ""))
        run_attempt = 1
    except ValueError as exc:
        raise ValueError("current run coordinates are invalid") from exc
    expected_head = os.environ.get("GITHUB_SHA", "")
    expected_branch = os.environ.get("GITHUB_REF_NAME", "")

    current = _gh_json(f"repos/{REPOSITORY}/actions/runs/{run_id}")
    if not isinstance(current, dict) or current.get("run_attempt") != 1:
        raise ValueError("current run is not attempt one")
    run = _gh_json(f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/1")
    environment = _gh_json(f"repos/{REPOSITORY}/environments/{environment_name}")
    approvals = _gh_json(f"repos/{REPOSITORY}/actions/runs/{run_id}/approvals")
    jobs = _all_jobs(run_id)
    branch_policies = _all_branch_policies(environment_name)

    approved_team_ids: set[int] = set()
    if isinstance(environment, dict):
        rules = environment.get("protection_rules") or []
        configured = [
            entry
            for rule in rules
            if isinstance(rule, dict) and rule.get("type") == "required_reviewers"
            for entry in (rule.get("reviewers") or [])
            if isinstance(entry, dict) and entry.get("type") == "Team"
        ]
        if configured:
            environment_id = _positive(environment.get("id"), "environment id")
            matching_approvals = _matching_approvals(
                approvals, environment_id, environment_name
            )
            approved_logins = {
                _identity(approval.get("user"), "approved reviewer")[1]
                for approval in matching_approvals
            }
            if len(approved_logins) != 1:
                raise ValueError(
                    "all approvals for one protected environment must share one identity"
                )
            approved_login = next(iter(approved_logins))
            if len(configured) != 1:
                raise ValueError("protected environment must configure exactly one reviewer")
            reviewer = configured[0].get("reviewer") or {}
            team_id = _positive(reviewer.get("id"), "configured team id")
            slug = reviewer.get("slug")
            if not isinstance(slug, str) or TEAM_SLUG.fullmatch(slug) is None:
                raise ValueError("configured team slug is invalid")
            membership = _gh_json(
                f"orgs/DevPathAi/teams/{slug}/memberships/{approved_login}"
            )
            if (
                isinstance(membership, dict)
                and membership.get("state") == "active"
                and membership.get("role") in {"member", "maintainer"}
            ):
                approved_team_ids.add(team_id)

    return validate_current_protected_approval(
        environment_name=environment_name,
        job_name=job_name,
        workflow_path=workflow_path,
        expected_head=expected_head,
        expected_branch=expected_branch,
        run_id=run_id,
        run_attempt=run_attempt,
        environment=environment,
        approvals=approvals,
        jobs=jobs,
        run=run,
        branch_policies=branch_policies,
        approved_team_ids=approved_team_ids,
    )


def _write_outputs(path: Path, values: dict[str, Any]) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("approval GITHUB_OUTPUT must be an existing regular file")
    with path.open("a", encoding="utf-8", newline="\n") as output:
        for key, value in values.items():
            rendered = str(value)
            if re.fullmatch(r"[a-z_]+", key) is None or "\n" in rendered or "\r" in rendered:
                raise ValueError("approval output is unsafe")
            output.write(f"{key}={rendered}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--workflow-path", required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    result = verify_current_protected_approval(
        args.environment, args.job_name, args.workflow_path
    )
    if args.github_output is not None:
        _write_outputs(args.github_output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
