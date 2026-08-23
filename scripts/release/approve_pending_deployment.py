#!/usr/bin/env python3
"""Atomically approve protected deployments and restore self-review prevention."""

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
    comment: str,
    environment_name: str | None = None,
    environment_names: list[str] | None = None,
    api: Api = _gh_api,
) -> dict[str, Any]:
    if REPOSITORY.fullmatch(repository) is None:
        raise ValueError("repository is invalid")
    _positive(run_id, "run id")
    if environment_names is None:
        names = [environment_name] if environment_name is not None else []
    elif environment_name is None:
        names = list(environment_names)
    else:
        raise ValueError("environment name inputs are mutually exclusive")
    if (
        not names
        or len(names) > 10
        or len(set(names)) != len(names)
        or any(
            not isinstance(name, str) or ENVIRONMENT.fullmatch(name) is None
            for name in names
        )
    ):
        raise ValueError("environment names are invalid")
    if not isinstance(comment, str) or not comment.strip() or len(comment) > 280 or "\n" in comment or "\r" in comment:
        raise ValueError("approval comment is invalid")

    operator = api("GET", "user", None)
    operator_id = _positive(operator.get("id") if isinstance(operator, dict) else None, "operator id")
    operator_login = operator.get("login") if isinstance(operator, dict) else None
    if not isinstance(operator_login, str) or LOGIN.fullmatch(operator_login) is None:
        raise ValueError("operator login is invalid")

    states = []
    for name in names:
        environment_path = f"repos/{repository}/environments/{name}"
        environment = api("GET", environment_path, None)
        original = _policy_payload(environment)
        environment_id = _positive(
            environment.get("id") if isinstance(environment, dict) else None,
            "environment id",
        )
        if environment.get("name") != name:
            raise ValueError("protected environment identity mismatch")
        if original["prevent_self_review"] is not True:
            raise ValueError("protected environment must initially prevent self-review")
        _assert_exact_main_policy(
            api("GET", f"{environment_path}/deployment-branch-policies", None)
        )
        configured_rule = next(
            rule
            for rule in environment["protection_rules"]
            if isinstance(rule, dict) and rule.get("type") == "required_reviewers"
        )
        configured = configured_rule["reviewers"][0]
        configured_reviewer = (
            configured.get("reviewer") if isinstance(configured, dict) else None
        )
        if (
            configured.get("type") != "User"
            or not isinstance(configured_reviewer, dict)
            or configured_reviewer.get("id") != operator_id
            or configured_reviewer.get("login") != operator_login
        ):
            raise ValueError(
                "authenticated operator must be the sole configured user reviewer"
            )
        relaxed = dict(original)
        relaxed["prevent_self_review"] = False
        states.append(
            {
                "name": name,
                "id": environment_id,
                "path": environment_path,
                "original": original,
                "relaxed": relaxed,
            }
        )
    environment_ids = [state["id"] for state in states]
    if len(set(environment_ids)) != len(environment_ids):
        raise ValueError("protected environment ids must be unique")

    pending_path = f"repos/{repository}/actions/runs/{run_id}/pending_deployments"
    pending = api("GET", pending_path, None)
    for state in states:
        matches = [
            item
            for item in pending
            if isinstance(item, dict)
            and isinstance(item.get("environment"), dict)
            and item["environment"].get("id") == state["id"]
            and item["environment"].get("name") == state["name"]
        ] if isinstance(pending, list) else []
        if len(matches) != 1:
            raise ValueError(
                "exactly one pending deployment for each environment is required"
            )

    approval_error: Exception | None = None
    approval_response: Any = None
    relaxation_attempted = []
    try:
        for state in states:
            relaxation_attempted.append(state)
            api("PUT", state["path"], state["relaxed"])
            observed_relaxed = _policy_payload(api("GET", state["path"], None))
            if observed_relaxed != state["relaxed"]:
                raise ValueError("relaxed environment policy did not materialize exactly")
        approval_response = api(
            "POST",
            pending_path,
            {
                "environment_ids": environment_ids,
                "state": "approved",
                "comment": comment,
            },
        )
    except Exception as exc:
        approval_error = exc
    finally:
        restore_errors = []
        for state in reversed(relaxation_attempted):
            try:
                api("PUT", state["path"], state["original"])
                observed_original = _policy_payload(api("GET", state["path"], None))
                if observed_original != state["original"]:
                    raise ValueError(
                        "protected environment policy was not restored exactly"
                    )
                _assert_exact_main_policy(
                    api("GET", f"{state['path']}/deployment-branch-policies", None)
                )
            except Exception as restore_exc:
                restore_errors.append(restore_exc)
        if restore_errors:
            raise ValueError("protected environment restore failed") from restore_errors[0]
    if approval_error is not None:
        raise approval_error
    approved = approval_response if isinstance(approval_response, list) else []
    deployment_ids = []
    approved_names = []
    for item in approved:
        if not isinstance(item, dict) or item.get("environment") not in names:
            raise ValueError("GitHub approval response did not confirm the exact environments")
        deployment_ids.append(_positive(item.get("id"), "approved deployment id"))
        approved_names.append(item["environment"])
    if (
        not approved
        or any(name not in approved_names for name in names)
        or len(set(deployment_ids)) != len(deployment_ids)
    ):
        raise ValueError("GitHub approval response did not confirm the exact environments")
    result = {
        "repository": repository,
        "run_id": run_id,
        "environments": names,
        "environment_ids": environment_ids,
        "approved_by": operator_login,
        "approved_by_id": operator_id,
        "deployment_ids": deployment_ids,
        "restored_prevent_self_review": True,
    }
    if len(states) == 1:
        result["environment"] = states[0]["name"]
        result["environment_id"] = states[0]["id"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--environment", required=True, action="append")
    parser.add_argument("--comment", required=True)
    args = parser.parse_args()
    result = approve_pending_deployment(
        repository=args.repository,
        run_id=args.run_id,
        environment_names=args.environment,
        comment=args.comment,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
