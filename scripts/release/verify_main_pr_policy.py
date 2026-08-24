#!/usr/bin/env python3
"""Reject PR changes that bypass protected Mission Spine promotion commits."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable

SERVICE_NAMES = (
    "devpath-admin",
    "devpath-ai-svc",
    "devpath-community-svc",
    "devpath-gateway",
    "devpath-lcs-svc",
    "devpath-learning-svc",
    "devpath-notification-svc",
    "devpath-platform-svc",
    "devpath-sandbox-svc",
)
PROMOTION_MANAGED_PATHS = {
    *(f"apps/{name}/base/kustomization.yaml" for name in SERVICE_NAMES),
    "apps/devpath-web/base/kustomization.yaml",
    "apps/devpath-migration/base/kustomization.yaml",
}
MIGRATION_JOB_PATH = "apps/devpath-migration/base/job.yaml"
POLICY_WORKFLOW_PATH = ".github/workflows/mission-spine-main-pr-policy.yml"
POLICY_SCRIPT_PATH = "scripts/release/verify_main_pr_policy.py"
POLICY_MANAGED_PATHS = (POLICY_WORKFLOW_PATH, POLICY_SCRIPT_PATH)
CONTROL_PLANE_PREFIXES = (
    ".github/workflows/",
    ".github/actions/",
    "scripts/release/",
    "tools/release-wrangler/",
    "release-manifests/",
)
SHA40 = re.compile(r"[0-9a-f]{40}")


def _git_bytes(root: Path, revision: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"main PR policy cannot read {path} at {revision}")
    return result.stdout


def _allow_inert_migration_bootstrap(before: bytes, after: bytes) -> bool:
    if not before or b"\r" in before or not before.endswith(b"\n"):
        return False
    if b"suspend:" in before or before.count(b"spec:\n") != 1:
        return False
    expected = before.replace(b"spec:\n", b"spec:\n  suspend: true\n", 1)
    return after == expected


def validate_main_pr_delta(
    changed_paths: list[str],
    read_blob: Callable[[str, str], bytes],
) -> None:
    policy = sorted(
        path
        for path in set(changed_paths)
        if path in POLICY_MANAGED_PATHS
        or path.startswith(CONTROL_PLANE_PREFIXES)
    )
    if policy:
        raise ValueError(
            "main PR may not change the base-owned policy implementation: "
            + ", ".join(policy)
        )
    managed = sorted(set(changed_paths) & PROMOTION_MANAGED_PATHS)
    if managed:
        raise ValueError(
            "main PR may not change promotion-managed image selectors: "
            + ", ".join(managed)
        )
    argo_or_apps = sorted(
        path
        for path in set(changed_paths)
        if path.startswith(("apps/", "argocd/", "staging/"))
        and path != MIGRATION_JOB_PATH
    )
    if argo_or_apps:
        raise ValueError(
            "main PR may not change an Argo-managed production or sealed staging path: "
            + ", ".join(argo_or_apps)
        )
    if MIGRATION_JOB_PATH in changed_paths:
        before = read_blob("base", MIGRATION_JOB_PATH)
        after = read_blob("head", MIGRATION_JOB_PATH)
        if not _allow_inert_migration_bootstrap(before, after):
            raise ValueError(
                "main PR may only apply the one-time inert migration suspend bootstrap"
            )


def verify(root: Path, base: str, head: str) -> None:
    root = root.resolve()
    if SHA40.fullmatch(base) is None or SHA40.fullmatch(head) is None:
        raise ValueError("main PR policy Git coordinates are invalid")
    result = subprocess.run(
        [
            "git",
            "diff",
            "--no-renames",
            "--name-only",
            "-z",
            "--diff-filter=ACDMRTUXB",
            base,
            head,
            "--",
        ],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("main PR policy cannot compute the exact delta")
    raw = result.stdout
    if raw and not raw.endswith(b"\0"):
        raise ValueError("main PR policy delta is not NUL terminated")
    try:
        paths = [item.decode("utf-8") for item in raw.removesuffix(b"\0").split(b"\0")] if raw else []
    except UnicodeDecodeError as exc:
        raise ValueError("main PR policy delta paths are not UTF-8") from exc
    if any(
        not path
        or path.startswith(("/", "../"))
        or "/../" in path
        or "\\" in path
        or "\r" in path
        or "\n" in path
        for path in paths
    ):
        raise ValueError("main PR policy delta contains an unsafe path")
    validate_main_pr_delta(
        paths,
        lambda revision, path: _git_bytes(
            root, base if revision == "base" else head, path
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        verify(args.root, args.base, args.head)
        return 0
    except (OSError, ValueError) as exc:
        print(f"main PR promotion policy failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
