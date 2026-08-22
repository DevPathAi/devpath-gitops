#!/usr/bin/env python3
"""Fail closed unless the protected GitOps write App has the exact authority."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any
import urllib.error
import urllib.request


API_ROOT = "https://api.github.com"
REPOSITORY = "DevPathAi/devpath-gitops"
ORGANIZATION = "DevPathAi"
APP_SLUG = "devpath-gitops-release"
INTEGRITY_RULESET = "mission-spine-main-integrity"
GOVERNANCE_RULESET = "mission-spine-main-governance"
MAX_RESPONSE_BYTES = 1024 * 1024
DISMISSAL_URL = (
    f"{API_ROOT}/repos/{REPOSITORY}/branches/main/protection/dismissal_restrictions"
)


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _exact_keys(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{label} keys are not exact")
    return value


def _require_common_ruleset(rule: dict[str, Any], name: str) -> None:
    for key, expected in (
        ("name", name),
        ("target", "branch"),
        ("source_type", "Repository"),
        ("source", REPOSITORY),
        ("enforcement", "active"),
    ):
        if rule.get(key) != expected:
            raise ValueError(f"{name}: {key} is not exact")
    _positive_int(rule.get("id"), f"{name} id")
    conditions = _exact_keys(rule.get("conditions"), {"ref_name"}, f"{name} conditions")
    ref_name = _exact_keys(
        conditions["ref_name"], {"include", "exclude"}, f"{name} ref condition"
    )
    if ref_name != {"include": ["refs/heads/main"], "exclude": []}:
        raise ValueError(f"{name}: exact main ref condition is required")
    if "bypass_actors" not in rule:
        # GitHub intentionally hides this field when the caller cannot inspect it.
        raise ValueError(f"{name}: bypass_actors must be visible to fail closed")
    if not isinstance(rule["bypass_actors"], list):
        raise ValueError(f"{name}: bypass_actors must be a visible list")
    if not isinstance(rule.get("rules"), list):
        raise ValueError(f"{name}: rules must be visible")


def _validate_integrity(rule: dict[str, Any]) -> None:
    _require_common_ruleset(rule, INTEGRITY_RULESET)
    if rule["bypass_actors"] != []:
        raise ValueError("integrity ruleset may not have any bypass actor")
    expected = [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {"type": "required_linear_history"},
    ]
    if sorted(rule["rules"], key=lambda item: item.get("type", "")) != sorted(
        expected, key=lambda item: item["type"]
    ):
        raise ValueError("integrity rules are not exact")


def _validate_governance(rule: dict[str, Any], app_id: int) -> None:
    _require_common_ruleset(rule, GOVERNANCE_RULESET)
    expected_bypass = [
        {"actor_id": app_id, "actor_type": "Integration", "bypass_mode": "always"}
    ]
    if rule["bypass_actors"] != expected_bypass:
        raise ValueError("governance bypass must be the sole GitHub App")
    rules = rule["rules"]
    if len(rules) != 1 or not isinstance(rules[0], dict):
        raise ValueError("governance rules are not exact")
    update = rules[0]
    # GitHub 룰셋 GET 은 update 규칙의 parameters 를 값(true/false)과 무관하게
    # 생략한다(라이브 실측 2026-08-22 — true 로 설정해도 {"type":"update"} 로
    # 직렬화). 관측 가능한 계약만 요구한다: 정확히 update 단일 규칙이며,
    # parameters 가 보이는 경우에는 fetch-and-merge 허용이 꺼져 있어야 한다.
    if set(update) == {"type"}:
        if update["type"] != "update":
            raise ValueError("governance update rule is not exact")
    elif _exact_keys(update, {"type", "parameters"}, "update rule") != {
        "type": "update",
        "parameters": {"update_allows_fetch_and_merge": False},
    }:
        raise ValueError("governance update rule is not exact")


def _sole_app(value: Any, app_id: int, label: str) -> None:
    if not isinstance(value, list) or len(value) != 1:
        raise ValueError(f"classic protection {label} must contain the sole release App")
    app = value[0]
    if (
        not isinstance(app, dict)
        or app.get("id") != app_id
        or app.get("slug") != APP_SLUG
    ):
        raise ValueError(f"classic protection {label} must contain the sole release App")


def _empty_actor_lists(value: Any, app_id: int, label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"classic protection {label} is invalid")
    if value.get("users") != [] or value.get("teams") != []:
        raise ValueError(f"classic protection {label} users/teams must be empty")
    _sole_app(value.get("apps"), app_id, f"{label} apps")


def _enabled(value: Any, expected: bool, label: str) -> None:
    if not isinstance(value, dict) or value.get("enabled") is not expected:
        raise ValueError(f"classic protection {label} is not exact")


def _disabled_dismissal_restrictions(reviews: dict[str, Any]) -> None:
    if "dismissal_restrictions" not in reviews or reviews["dismissal_restrictions"] is None:
        return
    dismissal = reviews["dismissal_restrictions"]
    metadata = {
        "url": DISMISSAL_URL,
        "users_url": DISMISSAL_URL + "/users",
        "teams_url": DISMISSAL_URL + "/teams",
    }
    if (
        not isinstance(dismissal, dict)
        or not {"users", "teams", "apps"}.issubset(dismissal)
        or not set(dismissal).issubset({"users", "teams", "apps", *metadata})
        or any(dismissal[key] != [] for key in ("users", "teams", "apps"))
        or any(dismissal.get(key, expected) != expected for key, expected in metadata.items())
    ):
        raise ValueError("classic dismissal restrictions must be disabled and exact")


def _validate_classic(protection: Any, app_id: int) -> None:
    if not isinstance(protection, dict):
        raise ValueError("classic branch protection response is invalid")
    # GitHub 는 status checks 가 비활성일 때 응답에서 키를 생략한다(라이브 실측
    # 2026-08-22) — 부재와 null 을 동등한 「비활성」 실서식으로 받고, 값이 있는
    # 형태만 거부한다. 「키 존재+null」을 요구하던 이전 계약은 실제 GET 에서
    # 영원히 실패하는 잠재 결함이었다.
    if protection.get("required_status_checks") is not None:
        raise ValueError("classic required status checks must be absent or null")
    _empty_actor_lists(protection.get("restrictions"), app_id, "push restrictions")
    reviews = protection.get("required_pull_request_reviews")
    if not isinstance(reviews, dict):
        raise ValueError("classic pull-request reviews are missing")
    if (
        reviews.get("dismiss_stale_reviews") is not True
        or reviews.get("require_code_owner_reviews") is not False
        or type(reviews.get("required_approving_review_count")) is not int
        or reviews["required_approving_review_count"] != 1
        or reviews.get("require_last_push_approval") is not True
    ):
        raise ValueError("classic pull-request review parameters are not exact")
    _disabled_dismissal_restrictions(reviews)
    _empty_actor_lists(
        reviews.get("bypass_pull_request_allowances"), app_id, "PR bypass"
    )
    _enabled(protection.get("enforce_admins"), True, "administrator enforcement")
    _enabled(protection.get("required_linear_history"), True, "linear history")
    _enabled(
        protection.get("required_conversation_resolution"),
        True,
        "conversation resolution",
    )
    _enabled(protection.get("allow_force_pushes"), False, "force pushes")
    _enabled(protection.get("allow_deletions"), False, "deletions")


def validate_authority_state(
    *,
    app_slug: str,
    installation_id: int,
    repositories: dict[str, Any],
    classic_protection_status: int,
    classic_protection: dict[str, Any],
    rulesets: list[dict[str, Any]],
    rule_details: dict[int, dict[str, Any]],
    expected_app_id: int,
) -> dict[str, Any]:
    """Validate an already fetched, complete GitHub authority snapshot."""
    expected_app_id = _positive_int(expected_app_id, "expected App id")
    installation_id = _positive_int(installation_id, "installation id")
    if app_slug != APP_SLUG:
        raise ValueError("GitOps write App slug is not exact")
    if not isinstance(repositories, dict):
        raise ValueError("installation repository inventory is invalid")
    selected = repositories.get("repositories")
    if (
        type(repositories.get("total_count")) is not int
        or repositories["total_count"] != 1
        or not isinstance(selected, list)
        or len(selected) != 1
    ):
        raise ValueError("GitOps App must select the sole GitOps repository")
    repository = selected[0]
    if (
        not isinstance(repository, dict)
        or repository.get("full_name") != REPOSITORY
        or repository.get("archived") is not False
        or _positive_int(repository.get("id"), "GitOps repository id") <= 0
    ):
        raise ValueError("GitOps App must select the sole GitOps repository")
    if classic_protection_status != 200:
        raise ValueError("classic branch protection must be present")
    _validate_classic(classic_protection, expected_app_id)
    if not isinstance(rulesets, list) or len(rulesets) != 2:
        raise ValueError("exactly two active effective rulesets are required")
    summaries: dict[str, dict[str, Any]] = {}
    for summary in rulesets:
        if not isinstance(summary, dict) or summary.get("enforcement") != "active":
            raise ValueError("exactly two active effective rulesets are required")
        name = summary.get("name")
        if not isinstance(name, str) or name in summaries:
            raise ValueError("ruleset summary names are not exact")
        summaries[name] = summary
    if set(summaries) != {INTEGRITY_RULESET, GOVERNANCE_RULESET}:
        raise ValueError("exactly two active effective rulesets are required")
    expected_ids: dict[str, int] = {}
    for name, summary in summaries.items():
        for key, expected in (
            ("source_type", "Repository"),
            ("source", REPOSITORY),
        ):
            if summary.get(key) != expected:
                raise ValueError(f"{name}: summary {key} is not exact")
        expected_ids[name] = _positive_int(summary.get("id"), f"{name} summary id")
    if set(rule_details) != set(expected_ids.values()):
        raise ValueError("ruleset detail inventory is not exact")
    integrity = rule_details[expected_ids[INTEGRITY_RULESET]]
    governance = rule_details[expected_ids[GOVERNANCE_RULESET]]
    if integrity.get("id") != expected_ids[INTEGRITY_RULESET]:
        raise ValueError("integrity ruleset detail id drifted")
    if governance.get("id") != expected_ids[GOVERNANCE_RULESET]:
        raise ValueError("governance ruleset detail id drifted")
    _validate_integrity(integrity)
    _validate_governance(governance, expected_app_id)
    return {
        "app_slug": APP_SLUG,
        "app_id": expected_app_id,
        "installation_id": installation_id,
        "ruleset_ids": sorted(expected_ids.values()),
    }


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _request_json(path: str, token: str, *, allow_404: bool = False):
    if not path.startswith("/") or "\r" in path or "\n" in path:
        raise ValueError("GitHub API path is invalid")
    request = urllib.request.Request(
        API_ROOT + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "devpath-gitops-release-authority-verifier",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(_RejectRedirects())
    try:
        response = opener.open(request, timeout=20)
    except urllib.error.HTTPError as exc:
        response = exc
    status = int(response.status)
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    encoding = response.headers.get("Content-Encoding")
    length_header = response.headers.get("Content-Length")
    if content_type != "application/json" or encoding not in {None, "identity"}:
        raise ValueError("GitHub API response headers are not canonical JSON")
    if length_header is None or not length_header.isdigit():
        raise ValueError("GitHub API response Content-Length is missing")
    declared = int(length_header)
    if declared < 1 or declared > MAX_RESPONSE_BYTES:
        raise ValueError("GitHub API response size is outside the bound")
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) != declared:
        raise ValueError("GitHub API response length does not match Content-Length")
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("GitHub API response is not canonical UTF-8 JSON") from exc
    if status != 200 and not (allow_404 and status == 404):
        raise ValueError(f"GitHub API returned unexpected HTTP status {status}")
    return status, document, response.headers


def fetch_authority_state(
    token: str, expected_app_id: int, app_slug: str, installation_id: int
) -> dict[str, Any]:
    if not token or "\r" in token or "\n" in token:
        raise ValueError("GitHub App token is missing or unsafe")
    _, repositories, repo_headers = _request_json(
        "/installation/repositories?per_page=100", token
    )
    if repo_headers.get("Link"):
        raise ValueError("GitOps App repository inventory unexpectedly paginates")
    protection_status, protection, _ = _request_json(
        f"/repos/{REPOSITORY}/branches/main/protection", token
    )
    _, rulesets, ruleset_headers = _request_json(
        f"/repos/{REPOSITORY}/rulesets?includes_parents=true&per_page=100", token
    )
    if ruleset_headers.get("Link"):
        raise ValueError("GitOps ruleset inventory unexpectedly paginates")
    if not isinstance(rulesets, list):
        raise ValueError("GitOps ruleset inventory is invalid")
    details: dict[int, dict[str, Any]] = {}
    for summary in rulesets:
        if not isinstance(summary, dict):
            raise ValueError("GitOps ruleset summary is invalid")
        identifier = _positive_int(summary.get("id"), "ruleset id")
        _, detail, _ = _request_json(f"/repos/{REPOSITORY}/rulesets/{identifier}", token)
        details[identifier] = detail
    return validate_authority_state(
        app_slug=app_slug,
        installation_id=installation_id,
        repositories=repositories,
        classic_protection_status=protection_status,
        classic_protection=protection,
        rulesets=rulesets,
        rule_details=details,
        expected_app_id=expected_app_id,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-app-id", required=True, type=int)
    parser.add_argument("--app-slug", required=True)
    parser.add_argument("--installation-id", required=True, type=int)
    parser.add_argument("--github-output")
    args = parser.parse_args(argv)
    try:
        token = os.environ.get("GH_TOKEN", "")
        result = fetch_authority_state(
            token, args.expected_app_id, args.app_slug, args.installation_id
        )
        if args.github_output:
            output = os.path.realpath(args.github_output)
            with open(output, "a", encoding="utf-8", newline="\n") as handle:
                for key in ("app_slug", "app_id", "installation_id"):
                    handle.write(f"{key}={result[key]}\n")
        print("GitOps write App and exact main rulesets are authenticated")
        return 0
    except (OSError, ValueError) as exc:
        print(f"GitOps write authority verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
