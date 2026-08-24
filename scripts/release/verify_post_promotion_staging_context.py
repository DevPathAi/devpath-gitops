#!/usr/bin/env python3
"""Authenticate a production transition before its run rebaselines staging."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote
import re
import subprocess
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_release_manifest import RELEASE_ID, resolve_release_bundle
from verify_main_release_context import _gh_json, validate_release_data_tree
from verify_promotion_chain import inspect_chain


REPOSITORY = "DevPathAi/devpath-gitops"
WORKFLOW_PATHS = {
    "mission-on": ".github/workflows/mission-spine-promote.yml",
    "prior": ".github/workflows/mission-spine-rollback.yml",
}
SHA40 = re.compile(r"[0-9a-f]{40}")


def _git(root: Path, *args: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=not binary,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("post-promotion Git authentication failed")
    return result.stdout if binary else result.stdout.strip()


def validate_run_coordinates(
    environment: dict[str, str],
    control_head: str,
    workflow_path: str,
    expected_phase: str,
) -> None:
    if (
        SHA40.fullmatch(control_head) is None
        or WORKFLOW_PATHS.get(expected_phase) != workflow_path
    ):
        raise ValueError("post-promotion control coordinate is invalid")
    expected = {
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_NAME": "main",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": control_head,
        "GITHUB_WORKFLOW_SHA": control_head,
        "GITHUB_WORKFLOW_REF": f"{REPOSITORY}/{workflow_path}@refs/heads/main",
    }
    if any(environment.get(key) != value for key, value in expected.items()):
        raise ValueError("post-promotion run is not the exact original main dispatch")


def validate_transition_state(
    control_state: dict[str, str],
    current_state: dict[str, str],
    current_head: str,
    expected_phase: str,
) -> None:
    if current_state.get("current_commit") != current_head:
        raise ValueError("current main is not the exact authenticated transition commit")
    if expected_phase == "mission-on":
        if (
            current_state.get("phase") != "mission-on"
            or current_state.get("web_phase") != "mission-on"
            or current_state.get("on_commit") != current_head
        ):
            raise ValueError("current main is not the exact sealed mission-ON chain")
    elif expected_phase == "prior":
        if current_state.get("web_phase") not in {"base", "prior"}:
            raise ValueError("current main is not the exact sealed prior rollback chain")
    else:
        raise ValueError("post-promotion expected phase is invalid")
    if not control_state.get("current_commit"):
        raise ValueError("original control chain is invalid")


def validate_live_refs(
    main_branch: Any,
    release_branch: Any,
    expected_commit: str,
    expected_release_head: str,
) -> None:
    if (
        not isinstance(main_branch, dict)
        or main_branch.get("name") != "main"
        or main_branch.get("protected") is not True
        or (main_branch.get("commit") or {}).get("sha") != expected_commit
    ):
        raise ValueError("protected main is not the exact production transition commit")
    if (
        not isinstance(release_branch, dict)
        or (release_branch.get("commit") or {}).get("sha") != expected_release_head
    ):
        raise ValueError("sealed release branch changed after production transition")


def _require_clean(root: Path, label: str) -> str:
    root = root.resolve()
    head = str(_git(root, "rev-parse", "HEAD"))
    if SHA40.fullmatch(head) is None:
        raise ValueError(f"{label} checkout head is invalid")
    if str(_git(root, "status", "--porcelain=v1", "--untracked-files=all")):
        raise ValueError(f"{label} checkout is not clean")
    return head


def verify(
    control_root: Path,
    gitops_root: Path,
    data_root: Path,
    release_id: str,
    expected_commit: str,
    expected_phase: str,
    workflow_path: str,
) -> None:
    if not os.environ.get("GH_TOKEN"):
        raise ValueError("GH_TOKEN is required")
    if RELEASE_ID.fullmatch(release_id) is None:
        raise ValueError("release_id is invalid")
    if SHA40.fullmatch(expected_commit) is None:
        raise ValueError("expected production transition commit is invalid")

    control_root = control_root.resolve()
    gitops_root = gitops_root.resolve()
    data_root = data_root.resolve()
    control_head = _require_clean(control_root, "control")
    current_head = _require_clean(gitops_root, "current main")
    release_head = _require_clean(data_root, "sealed release")
    validate_run_coordinates(
        os.environ.copy(), control_head, workflow_path, expected_phase
    )
    if current_head != expected_commit:
        raise ValueError("current main checkout is not the expected transition commit")

    control_workflow = _git(
        control_root, "show", f"{control_head}:{workflow_path}", binary=True
    )
    current_workflow = _git(
        gitops_root, "show", f"{current_head}:{workflow_path}", binary=True
    )
    assert isinstance(control_workflow, bytes) and isinstance(current_workflow, bytes)
    if (
        control_workflow != current_workflow
        or (control_root / workflow_path).read_bytes() != control_workflow
        or (gitops_root / workflow_path).read_bytes() != current_workflow
    ):
        raise ValueError("promotion changed the executing workflow bytes")

    validate_release_data_tree(data_root, release_id, release_head, sealed=True)
    release_path, _, _, candidate, candidate_hash = resolve_release_bundle(
        data_root, release_id
    )
    release_hash = hashlib.sha256(release_path.read_bytes()).hexdigest()
    control_state = inspect_chain(
        control_root,
        candidate,
        candidate_hash,
        release_hash,
        control_head,
    )
    current_state = inspect_chain(
        gitops_root,
        candidate,
        candidate_hash,
        release_hash,
        current_head,
    )
    _git(gitops_root, "merge-base", "--is-ancestor", control_head, current_head)
    validate_transition_state(
        control_state, current_state, current_head, expected_phase
    )

    release_branch_name = f"release/candidate-{release_id}"
    main_ref = _gh_json(f"repos/{REPOSITORY}/branches/main")
    release_ref = _gh_json(
        f"repos/{REPOSITORY}/branches/{quote(release_branch_name, safe='')}"
    )
    validate_live_refs(main_ref, release_ref, current_head, release_head)
    main_recheck = _gh_json(f"repos/{REPOSITORY}/branches/main")
    release_recheck = _gh_json(
        f"repos/{REPOSITORY}/branches/{quote(release_branch_name, safe='')}"
    )
    validate_live_refs(main_recheck, release_recheck, current_head, release_head)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--gitops-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument(
        "--expected-phase", choices=tuple(WORKFLOW_PATHS), required=True
    )
    parser.add_argument("--workflow-path", required=True)
    args = parser.parse_args(argv)
    try:
        verify(
            args.control_root,
            args.gitops_root,
            args.data_root,
            args.release_id,
            args.expected_commit,
            args.expected_phase,
            args.workflow_path,
        )
        print("authenticated exact post-transition staging context")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"post-transition staging context failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
