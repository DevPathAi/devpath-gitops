#!/usr/bin/env python3
"""Approve one protected deployment and restore self-review prevention exactly."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from typing import Any, Callable


REPOSITORY = re.compile(r"DevPathAi/[A-Za-z0-9_.-]{1,100}")
ENVIRONMENT = re.compile(r"[A-Za-z0-9_.-]{1,100}")
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
Api = Callable[[str, str, dict[str, Any] | None], Any]


def _positive(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _gh_api(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    command = [
        "gh",
        "api",
        "-H",
        "X-GitHub-Api-Version: 2026-03-10",
        "-X",
        method,
        path,
    ]
    if payload is not None:
        command.extend(["--input", "-"])
    completed = subprocess.run(
        command,
        input=json.dumps(payload, separators=(",", ":")) if payload is not None else None,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError(f"GitHub API {method} failed for {path}")
    if not completed.stdout.strip():
        return None
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GitHub API {method} returned invalid JSON for {path}") from exc


def _policy_payload(environment: Any) -> dict[str, Any]:
    if not isinstance(environment, dict):
        raise ValueError("protected environment response is invalid")
    if environment.get("can_admins_bypass") is not False:
        raise ValueError("protected environment must forbid administrator bypass")
    deployment = environment.get("deployment_branch_policy")
    if (
        not isinstance(deployment, dict)
        or deployment.get("protected_branches") is not False
        or deployment.get("custom_branch_policies") is not True
    ):
        raise ValueError("protected environment must use an exact custom branch policy")
    rules = environment.get("protection_rules")
    if not isinstance(rules, list):
        raise ValueError("protected environment rules are invalid")
    reviewer_rules = [
        rule
        for rule in rules
        if isinstance(rule, dict) and rule.get("type") == "required_reviewers"
    ]
    if len(reviewer_rules) != 1:
        raise ValueError("exactly one required-reviewers rule is required")
    reviewer_rule = reviewer_rules[0]
    reviewers = reviewer_rule.get("reviewers")
    if not isinstance(reviewers, list) or len(reviewers) != 1:
        raise ValueError("exactly one configured reviewer is required")
    rendered_reviewers = []
    for entry in reviewers:
        reviewer = entry.get("reviewer") if isinstance(entry, dict) else None
        reviewer_type = entry.get("type") if isinstance(entry, dict) else None
        if reviewer_type not in {"User", "Team"} or not isinstance(reviewer, dict):
            raise ValueError("configured reviewer is invalid")
        rendered_reviewers.append(
            {
                "type": reviewer_type,
                "id": _positive(reviewer.get("id"), "configured reviewer id"),
            }
        )
    wait_rules = [
        rule for rule in rules if isinstance(rule, dict) and rule.get("type") == "wait_timer"
    ]
    if len(wait_rules) > 1:
        raise ValueError("multiple wait-timer rules are not supported")
    wait_timer = 0 if not wait_rules else wait_rules[0].get("wait_timer")
    if isinstance(wait_timer, bool) or not isinstance(wait_timer, int) or not 0 <= wait_timer <= 43200:
        raise ValueError("environment wait_timer is invalid")
    prevent_self_review = reviewer_rule.get("prevent_self_review")
    if not isinstance(prevent_self_review, bool):
        raise ValueError("prevent_self_review is invalid")
    return {
        "wait_timer": wait_timer,
        "prevent_self_review": prevent_self_review,
        "reviewers": rendered_reviewers,
        "deployment_branch_policy": {
            "protected_branches": False,
            "custom_branch_policies": True,
        },
        "can_admins_bypass": False,
    }


def _assert_exact_main_policy(payload: Any) -> None:
    policies = payload.get("branch_policies") if isinstance(payload, dict) else None
    if (
        not isinstance(policies, list)
        or len(policies) != 1
        or policies[0].get("name") != "main"
        or policies[0].get("type") != "branch"
    ):
        raise ValueError("protected environment must have exactly one exact main branch policy")


def approve_pending_deployment(
    *,
    repository: str,
    run_id: int,
    environment_name: str,
    comment: str,
    api: Api = _gh_api,
) -> dict[str, Any]:
    if REPOSITORY.fullmatch(repository) is None:
        raise ValueError("repository is invalid")
    _positive(run_id, "run id")
    if ENVIRONMENT.fullmatch(environment_name) is None:
        raise ValueError("environment name is invalid")
    if not isinstance(comment, str) or not comment.strip() or len(comment) > 280 or "\n" in comment or "\r" in comment:
        raise ValueError("approval comment is invalid")

    environment_path = f"repos/{repository}/environments/{environment_name}"
    environment = api("GET", environment_path, None)
    environment_id = _positive(environment.get("id"), "environment id")
    if environment.get("name") != environment_name:
        raise ValueError("protected environment identity mismatch")
    original = _policy_payload(environment)
    if original["prevent_self_review"] is not True:
        raise ValueError("protected environment must initially prevent self-review")
    _assert_exact_main_policy(
        api("GET", f"{environment_path}/deployment-branch-policies", None)
    )

    operator = api("GET", "user", None)
    operator_id = _positive(operator.get("id") if isinstance(operator, dict) else None, "operator id")
    operator_login = operator.get("login") if isinstance(operator, dict) else None
    if not isinstance(operator_login, str) or LOGIN.fullmatch(operator_login) is None:
        raise ValueError("operator login is invalid")
    configured_rule = next(
        rule
        for rule in environment["protection_rules"]
        if isinstance(rule, dict) and rule.get("type") == "required_reviewers"
    )
    configured = configured_rule["reviewers"][0]
    configured_reviewer = configured.get("reviewer") if isinstance(configured, dict) else None
    if (
        configured.get("type") != "User"
        or not isinstance(configured_reviewer, dict)
        or configured_reviewer.get("id") != operator_id
        or configured_reviewer.get("login") != operator_login
    ):
        raise ValueError("authenticated operator must be the sole configured user reviewer")

    pending_path = f"repos/{repository}/actions/runs/{run_id}/pending_deployments"
    pending = api("GET", pending_path, None)
    matches = [
        item
        for item in pending
        if isinstance(item, dict)
        and isinstance(item.get("environment"), dict)
        and item["environment"].get("id") == environment_id
        and item["environment"].get("name") == environment_name
    ] if isinstance(pending, list) else []
    if len(matches) != 1:
        raise ValueError("exactly one pending deployment for the environment is required")

    relaxed = dict(original)
    relaxed["prevent_self_review"] = False
    approval_error: Exception | None = None
    approval_response: Any = None
    try:
        api("PUT", environment_path, relaxed)
        observed_relaxed = _policy_payload(api("GET", environment_path, None))
        if observed_relaxed != relaxed:
            raise ValueError("relaxed environment policy did not materialize exactly")
        approval_response = api(
            "POST",
            pending_path,
            {
                "environment_ids": [environment_id],
                "state": "approved",
                "comment": comment,
            },
        )
    except Exception as exc:
        approval_error = exc
    finally:
        try:
            api("PUT", environment_path, original)
            observed_original = _policy_payload(api("GET", environment_path, None))
            if observed_original != original:
                raise ValueError("protected environment policy was not restored exactly")
            _assert_exact_main_policy(
                api("GET", f"{environment_path}/deployment-branch-policies", None)
            )
        except Exception as restore_exc:
            raise ValueError("protected environment restore failed") from restore_exc
    if approval_error is not None:
        raise approval_error
    approved = approval_response if isinstance(approval_response, list) else []
    deployment_ids = [
        _positive(item.get("id"), "approved deployment id")
        for item in approved
        if isinstance(item, dict) and item.get("environment") == environment_name
    ]
    if (
        not approved
        or len(deployment_ids) != len(approved)
        or len(set(deployment_ids)) != len(deployment_ids)
    ):
        raise ValueError("GitHub approval response did not confirm the exact environment")
    return {
        "repository": repository,
        "run_id": run_id,
        "environment": environment_name,
        "environment_id": environment_id,
        "approved_by": operator_login,
        "approved_by_id": operator_id,
        "deployment_ids": deployment_ids,
        "restored_prevent_self_review": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--comment", required=True)
    args = parser.parse_args()
    result = approve_pending_deployment(
        repository=args.repository,
        run_id=args.run_id,
        environment_name=args.environment,
        comment=args.comment,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
