#!/usr/bin/env python3
"""Lease the current GitHub runner's IPv4 /32 into the exact k3s API SG."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any, Callable
from urllib.request import ProxyHandler, Request, build_opener


AWS_REGION = "ap-northeast-2"
AWS_ACCOUNT_ID = "963773969059"
SECURITY_GROUP_ID = "sg-0ad7dfa8afe5d1eea"
MANAGED_BY = "devpath-mission-spine-github-actions"
CHECK_IP_URL = "https://checkip.amazonaws.com"
RULE_ID = re.compile(r"sgr-[0-9a-f]{17}")
RUN_ID = re.compile(r"[1-9][0-9]{0,19}")
ATTEMPT = re.compile(r"[1-9][0-9]{0,2}")
JOB = re.compile(r"[A-Za-z0-9_.-]{1,100}")


def _fetch_runner_ip() -> bytes:
    opener = build_opener(ProxyHandler({}))
    request = Request(
        CHECK_IP_URL,
        headers={"Accept": "text/plain", "User-Agent": "devpath-k3s-ingress/1"},
    )
    with opener.open(request, timeout=10) as response:
        if response.status != 200:
            raise ValueError("runner IPv4 discovery returned a non-200 status")
        raw = response.read(65)
    if len(raw) > 64:
        raise ValueError("runner IPv4 discovery response is oversized")
    return raw


def _global_ipv4(raw: bytes) -> str:
    try:
        text = raw.decode("ascii").strip()
        address = ipaddress.ip_address(text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("runner IPv4 is invalid") from exc
    if address.version != 4 or not address.is_global or str(address) != text:
        raise ValueError("runner IPv4 must be one canonical global address")
    return text


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _json_result(result: Any, operation: str) -> Any:
    if result.returncode != 0:
        raise ValueError(f"AWS {operation} failed")
    try:
        return json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"AWS {operation} returned invalid JSON") from exc


def _tags(rule: dict[str, Any]) -> dict[str, str]:
    raw = rule.get("Tags")
    if not isinstance(raw, list):
        raise ValueError("security-group rule tags are missing")
    tags: dict[str, str] = {}
    for item in raw:
        if (
            not isinstance(item, dict)
            or set(item) != {"Key", "Value"}
            or not isinstance(item["Key"], str)
            or not isinstance(item["Value"], str)
            or item["Key"] in tags
        ):
            raise ValueError("security-group rule tags are invalid")
        tags[item["Key"]] = item["Value"]
    return tags


def _validate_rule_shape(rule: Any) -> dict[str, Any]:
    if not isinstance(rule, dict):
        raise ValueError("security-group rule is invalid")
    rule_id = rule.get("SecurityGroupRuleId")
    cidr = rule.get("CidrIpv4")
    if not isinstance(rule_id, str) or RULE_ID.fullmatch(rule_id) is None:
        raise ValueError("security-group rule ID is invalid")
    if (
        rule.get("GroupId") != SECURITY_GROUP_ID
        or rule.get("IsEgress") is not False
        or rule.get("IpProtocol") != "tcp"
        or rule.get("FromPort") != 6443
        or rule.get("ToPort") != 6443
        or not isinstance(cidr, str)
        or not cidr.endswith("/32")
    ):
        raise ValueError("security-group rule scope is not exact")
    _global_ipv4(cidr[:-3].encode("ascii", errors="ignore"))
    return rule


def _append_github_env(path: Path, rule_id: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("GITHUB_ENV must be an existing regular non-symlink file")
    before = path.stat()
    if before.st_size > 1024 * 1024:
        raise ValueError("GITHUB_ENV exceeds its byte bound")
    row = f"KUBERNETES_API_INGRESS_RULE_ID={rule_id}\n".encode("utf-8")
    flags = os.O_APPEND | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (
            before.st_ino and opened.st_ino and before.st_ino != opened.st_ino
        ):
            raise ValueError("GITHUB_ENV identity changed during append")
        if os.write(descriptor, row) != len(row):
            raise ValueError("GITHUB_ENV append was incomplete")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _revoke(rule_id: str, command_runner: Callable[[list[str]], Any]) -> None:
    result = _json_result(
        command_runner(
            [
                "aws",
                "ec2",
                "revoke-security-group-ingress",
                "--region",
                AWS_REGION,
                "--group-id",
                SECURITY_GROUP_ID,
                "--security-group-rule-ids",
                rule_id,
                "--output",
                "json",
            ]
        ),
        "security-group revoke",
    )
    if result in (
        {"Return": True},
        {"Return": True, "UnknownIpPermissions": []},
    ):
        return
    revoked = result.get("RevokedSecurityGroupRules") if isinstance(result, dict) else None
    if (
        not isinstance(result, dict)
        or set(result) != {"Return", "RevokedSecurityGroupRules"}
        or result.get("Return") is not True
        or not isinstance(revoked, list)
        or len(revoked) != 1
        or _validate_rule_shape(revoked[0]).get("SecurityGroupRuleId") != rule_id
    ):
        raise ValueError("AWS security-group revoke was not exact")


def open_ingress(
    github_env: Path,
    run_id: str,
    run_attempt: str,
    job: str,
    *,
    ip_fetcher: Callable[[], bytes] = _fetch_runner_ip,
    command_runner: Callable[[list[str]], Any] = _run,
    now: Callable[[], float] = time.time,
) -> str:
    if RUN_ID.fullmatch(run_id) is None:
        raise ValueError("GitHub run ID is invalid")
    if ATTEMPT.fullmatch(run_attempt) is None:
        raise ValueError("GitHub run attempt is invalid")
    if JOB.fullmatch(job) is None:
        raise ValueError("GitHub job name is invalid")
    address = _global_ipv4(ip_fetcher())
    cidr = f"{address}/32"
    description = f"mission-spine-gha:{run_id}:{run_attempt}:{job}"
    expires = str(int(now()) + 3600)
    tags = [
        {"Key": "ManagedBy", "Value": MANAGED_BY},
        {"Key": "GitHubRun", "Value": f"{run_id}-{run_attempt}"},
        {"Key": "GitHubJob", "Value": job},
        {"Key": "ExpiresAtEpoch", "Value": expires},
    ]
    permissions = [
        {
            "IpProtocol": "tcp",
            "FromPort": 6443,
            "ToPort": 6443,
            "IpRanges": [{"CidrIp": cidr, "Description": description}],
        }
    ]
    tag_specifications = [{"ResourceType": "security-group-rule", "Tags": tags}]
    document = _json_result(
        command_runner(
            [
                "aws",
                "ec2",
                "authorize-security-group-ingress",
                "--region",
                AWS_REGION,
                "--group-id",
                SECURITY_GROUP_ID,
                "--ip-permissions",
                json.dumps(permissions, separators=(",", ":")),
                "--tag-specifications",
                json.dumps(tag_specifications, separators=(",", ":")),
                "--output",
                "json",
            ]
        ),
        "security-group authorize",
    )
    rules = document.get("SecurityGroupRules") if isinstance(document, dict) else None
    provisional_id = None
    if isinstance(rules, list) and len(rules) == 1 and isinstance(rules[0], dict):
        value = rules[0].get("SecurityGroupRuleId")
        if isinstance(value, str) and RULE_ID.fullmatch(value):
            provisional_id = value
    try:
        if not isinstance(document, dict) or document.get("Return") is not True:
            raise ValueError("AWS security-group authorize was not exact")
        if not isinstance(rules, list) or len(rules) != 1:
            raise ValueError("AWS security-group authorize returned an ambiguous rule set")
        rule = _validate_rule_shape(rules[0])
        expected_tags = {item["Key"]: item["Value"] for item in tags}
        if (
            rule.get("CidrIpv4") != cidr
            or rule.get("Description") != description
            or _tags(rule) != expected_tags
        ):
            raise ValueError("authorized security-group rule does not match the lease")
        rule_id = rule["SecurityGroupRuleId"]
        _append_github_env(github_env, rule_id)
        return rule_id
    except Exception:
        if provisional_id is not None:
            _revoke(provisional_id, command_runner)
        raise


def close_ingress(
    rule_id: str,
    *,
    command_runner: Callable[[list[str]], Any] = _run,
) -> bool:
    if not rule_id:
        return False
    if RULE_ID.fullmatch(rule_id) is None:
        raise ValueError("security-group rule ID is invalid")
    document = _json_result(
        command_runner(
            [
                "aws",
                "ec2",
                "describe-security-group-rules",
                "--region",
                AWS_REGION,
                "--security-group-rule-ids",
                rule_id,
                "--output",
                "json",
            ]
        ),
        "security-group describe",
    )
    rules = document.get("SecurityGroupRules") if isinstance(document, dict) else None
    if not isinstance(rules, list) or len(rules) != 1:
        raise ValueError("managed security-group rule lookup is ambiguous")
    rule = _validate_rule_shape(rules[0])
    if (
        rule.get("SecurityGroupRuleId") != rule_id
        or not isinstance(rule.get("Description"), str)
        or not rule["Description"].startswith("mission-spine-gha:")
        or _tags(rule).get("ManagedBy") != MANAGED_BY
    ):
        raise ValueError("refusing to revoke an unmanaged security-group rule")
    _revoke(rule_id, command_runner)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="action", required=True)
    open_parser = subcommands.add_parser("open")
    open_parser.add_argument("--github-env", type=Path, required=True)
    close_parser = subcommands.add_parser("close")
    close_parser.add_argument("--rule-id", default="")
    args = parser.parse_args(argv)
    try:
        if args.action == "open":
            open_ingress(
                args.github_env,
                os.environ.get("GITHUB_RUN_ID", ""),
                os.environ.get("GITHUB_RUN_ATTEMPT", ""),
                os.environ.get("GITHUB_JOB", ""),
            )
        else:
            close_ingress(args.rule_id)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"kubernetes API ingress management failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
