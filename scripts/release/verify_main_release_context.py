#!/usr/bin/env python3
"""Authenticate main-owned workflow code and a data-only release branch checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any
from urllib.parse import quote

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_release_manifest import (
    RELEASE_ID,
    resolve_candidate_spec,
    resolve_release_bundle,
)
from verify_release_artifacts import MAX_CANDIDATE_SPEC_BYTES, verify_candidate_artifact


REPOSITORY = "DevPathAi/devpath-gitops"
SHA40 = re.compile(r"[0-9a-f]{40}")
MAX_RELEASE_MANIFEST_BYTES = 2 * 1024 * 1024
WORKFLOW_PATHS = frozenset(
    {
        ".github/workflows/mission-spine-validate.yml",
        ".github/workflows/mission-spine-promote.yml",
        ".github/workflows/mission-spine-landing-last.yml",
        ".github/workflows/mission-spine-rollback.yml",
    }
)


def _git(root: Path, *args: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=not binary,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError("Git provenance query failed")
    if binary:
        return result.stdout
    return result.stdout.strip()


def _single_parent(root: Path, commit: str, label: str) -> str:
    row = str(_git(root, "rev-list", "--parents", "-n", "1", commit)).split()
    if len(row) != 2 or row[0] != commit or SHA40.fullmatch(row[1]) is None:
        raise ValueError(f"{label} must have exactly one parent")
    return row[1]


def _regular_blob(root: Path, commit: str, relative: str, max_bytes: int) -> bytes:
    row = str(_git(root, "ls-tree", commit, "--", relative)).split()
    if len(row) != 4 or row[0] != "100644" or row[1] != "blob":
        raise ValueError(f"{relative} must be one regular Git blob")
    size_text = str(_git(root, "cat-file", "-s", row[2]))
    if not size_text.isascii() or not size_text.isdecimal():
        raise ValueError(f"{relative} blob size is invalid")
    size = int(size_text)
    if not 2 <= size <= max_bytes:
        raise ValueError(f"{relative} blob size exceeds its frozen bound")
    path = root / relative
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode) or path.is_symlink():
        raise ValueError(f"{relative} checkout is not one regular file")
    raw = _git(root, "show", f"{commit}:{relative}", binary=True)
    assert isinstance(raw, bytes)
    if len(raw) != size or raw != path.read_bytes():
        raise ValueError(f"{relative} checkout differs from its authenticated blob")
    return raw


def validate_main_state(
    *,
    control_head: str,
    workflow_sha: str,
    repository: str,
    event_name: str,
    ref: str,
    ref_name: str,
    run_attempt: int,
    workflow_ref: str,
    workflow_path: str,
    branch: Any,
) -> None:
    if (
        SHA40.fullmatch(control_head) is None
        or workflow_sha != control_head
        or isinstance(run_attempt, bool)
        or not isinstance(run_attempt, int)
    ):
        raise ValueError("workflow code SHA must equal the exact main checkout SHA")
    if (
        repository != REPOSITORY
        or event_name != "workflow_dispatch"
        or ref != "refs/heads/main"
        or ref_name != "main"
        or run_attempt != 1
        or workflow_ref != f"{REPOSITORY}/{workflow_path}@refs/heads/main"
    ):
        raise ValueError("workflow is not an attempt-one dispatch from protected main")
    if not isinstance(branch, dict):
        raise ValueError("protected main response is invalid")
    if (
        branch.get("name") != "main"
        or branch.get("protected") is not True
        or (branch.get("commit") or {}).get("sha") != control_head
    ):
        raise ValueError("workflow checkout is not the current protected main")


def validate_control_checkout(
    control_root: Path,
    workflow_path: str,
    branch: Any,
    environment: dict[str, str],
) -> str:
    control_root = control_root.resolve()
    head = str(_git(control_root, "rev-parse", "HEAD"))
    if environment.get("GITHUB_RUN_ATTEMPT") != "1":
        raise ValueError("workflow run attempt must be canonical string 1")
    attempt = 1
    validate_main_state(
        control_head=head,
        workflow_sha=environment.get("GITHUB_WORKFLOW_SHA", ""),
        repository=environment.get("GITHUB_REPOSITORY", ""),
        event_name=environment.get("GITHUB_EVENT_NAME", ""),
        ref=environment.get("GITHUB_REF", ""),
        ref_name=environment.get("GITHUB_REF_NAME", ""),
        run_attempt=attempt,
        workflow_ref=environment.get("GITHUB_WORKFLOW_REF", ""),
        workflow_path=workflow_path,
        branch=branch,
    )
    if environment.get("GITHUB_SHA") != head:
        raise ValueError("GITHUB_SHA differs from the checked-out main commit")
    if str(_git(control_root, "status", "--porcelain=v1", "--untracked-files=all")):
        raise ValueError("trusted main checkout has modified tracked bytes")
    local = control_root / workflow_path
    raw = _git(control_root, "show", f"{head}:{workflow_path}", binary=True)
    assert isinstance(raw, bytes)
    if not local.is_file() or local.is_symlink() or local.read_bytes() != raw:
        raise ValueError("executed workflow bytes differ from protected main")
    return head


def validate_release_data_tree(
    data_root: Path,
    release_id: str,
    branch_head: str,
    *,
    sealed: bool,
) -> dict[str, str]:
    if RELEASE_ID.fullmatch(release_id) is None:
        raise ValueError("release_id is invalid")
    if SHA40.fullmatch(branch_head) is None:
        raise ValueError("release branch head is invalid")
    data_root = data_root.resolve()
    head = str(_git(data_root, "rev-parse", "HEAD"))
    if head != branch_head:
        raise ValueError("data-only checkout is not the current release branch head")
    if str(_git(data_root, "status", "--porcelain=v1", "--untracked-files=all")):
        raise ValueError("data-only release checkout is not clean")

    candidate_path = f"release-manifests/candidates/{release_id}.candidate-spec.json"
    release_path = f"release-manifests/releases/{release_id}.json"
    if sealed:
        candidate_head = _single_parent(data_root, head, "sealed release commit")
        base = _single_parent(data_root, candidate_head, "candidate commit")
        release_delta = str(
            _git(data_root, "diff", "--name-status", candidate_head, head)
        ).splitlines()
        if release_delta != [f"A\t{release_path}"]:
            raise ValueError("sealed release commit must add only the release manifest")
        release_raw = _regular_blob(
            data_root, head, release_path, MAX_RELEASE_MANIFEST_BYTES
        )
        release_obj = resolve_release_bundle(data_root, release_id)
        candidate_file, release_file, candidate = (
            release_obj[1],
            release_obj[0],
            release_obj[3],
        )
        if release_file.read_bytes() != release_raw:
            raise ValueError("sealed release bytes drifted")
    else:
        candidate_head = head
        base = _single_parent(data_root, candidate_head, "candidate commit")
        if (data_root / release_path).exists():
            raise ValueError("candidate-only checkout already contains a release manifest")
        candidate_file, candidate, _ = resolve_candidate_spec(data_root, release_id)

    if candidate["gitops"]["base_sha"] != base:
        raise ValueError("candidate gitops.base_sha differs from its actual sole parent")

    candidate_delta = str(
        _git(data_root, "diff", "--name-status", base, candidate_head)
    ).splitlines()
    if candidate_delta != [f"A\t{candidate_path}"]:
        raise ValueError("candidate commit must add only the candidate manifest")
    candidate_raw = _regular_blob(
        data_root, candidate_head, candidate_path, MAX_CANDIDATE_SPEC_BYTES
    )
    if candidate_file.read_bytes() != candidate_raw:
        raise ValueError("candidate manifest bytes drifted")
    outputs = {
        "gitops_base_sha": base,
        "candidate_head_sha": candidate_head,
        "candidate_spec_path": candidate_file.as_posix(),
        "candidate_spec_sha256": hashlib.sha256(candidate_raw).hexdigest(),
    }
    if sealed:
        outputs.update(
            {
                "sealed_release_sha": head,
                "release_manifest_path": release_file.as_posix(),
                "release_manifest_sha256": hashlib.sha256(release_raw).hexdigest(),
            }
        )
    return outputs


def _gh_json(path: str) -> Any:
    result = subprocess.run(
        ["gh", "api", "-H", "X-GitHub-Api-Version: 2026-03-10", path],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        raise ValueError("GitHub provenance request failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("GitHub provenance response is invalid JSON") from exc


def verify(
    control_root: Path,
    data_root: Path | None,
    workflow_path: str,
    release_id: str,
    mode: str,
    github_output: Path | None,
) -> dict[str, str]:
    if not os.environ.get("GH_TOKEN"):
        raise ValueError("GH_TOKEN is required")
    if mode not in {"control", "candidate", "sealed"}:
        raise ValueError("main release context mode is invalid")
    if RELEASE_ID.fullmatch(release_id) is None:
        raise ValueError("release_id is invalid")
    if workflow_path not in WORKFLOW_PATHS:
        raise ValueError("workflow path is not a protected release workflow")
    branch = _gh_json(f"repos/{REPOSITORY}/branches/main")
    control_sha = validate_control_checkout(
        control_root, workflow_path, branch, os.environ.copy()
    )
    outputs = {"control_sha": control_sha}
    if mode == "control":
        if github_output is not None:
            _write_outputs(github_output, outputs)
        return outputs
    if data_root is None:
        raise ValueError("data root is required outside control-only mode")
    release_branch = f"release/candidate-{release_id}"
    release_branch_state = _gh_json(
        f"repos/{REPOSITORY}/branches/{quote(release_branch, safe='')}"
    )
    release_head = (release_branch_state.get("commit") or {}).get("sha")
    outputs.update(validate_release_data_tree(
        data_root, release_id, str(release_head), sealed=mode == "sealed"
    ))
    release_branch_recheck = _gh_json(
        f"repos/{REPOSITORY}/branches/{quote(release_branch, safe='')}"
    )
    if (release_branch_recheck.get("commit") or {}).get("sha") != release_head:
        raise ValueError("release branch changed during authentication")
    if mode == "candidate":
        if outputs["gitops_base_sha"] != control_sha:
            raise ValueError("staging validator main must equal candidate gitops.base_sha")
        evidence_token = os.environ.get("RELEASE_EVIDENCE_TOKEN", "")
        if not evidence_token:
            raise ValueError("RELEASE_EVIDENCE_TOKEN is required for candidate authentication")
        candidate_path, candidate, _ = resolve_candidate_spec(data_root, release_id)
        command_env = os.environ.copy()
        command_env["GH_TOKEN"] = evidence_token
        verify_candidate_artifact(
            command_env, release_id, candidate, candidate_path.read_bytes()
        )
        main_recheck = _gh_json(f"repos/{REPOSITORY}/branches/main")
        if (
            not isinstance(main_recheck, dict)
            or main_recheck.get("protected") is not True
            or (main_recheck.get("commit") or {}).get("sha") != control_sha
        ):
            raise ValueError("protected main changed during candidate authentication")
        release_branch_recheck = _gh_json(
            f"repos/{REPOSITORY}/branches/{quote(release_branch, safe='')}"
        )
        if (release_branch_recheck.get("commit") or {}).get("sha") != release_head:
            raise ValueError("release branch changed during candidate artifact authentication")
    if github_output is not None:
        _write_outputs(github_output, outputs)
    return outputs


def _write_outputs(path: Path, outputs: dict[str, str]) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("GITHUB_OUTPUT must be an existing regular file")
    with path.open("a", encoding="utf-8", newline="\n") as output:
        for key, value in outputs.items():
            if re.fullmatch(r"[a-z_]+", key) is None or "\n" in value or "\r" in value:
                raise ValueError("main release context output is unsafe")
            output.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--workflow-path", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--mode", choices=("control", "candidate", "sealed"), required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        verify(
            args.control_root,
            args.data_root,
            args.workflow_path,
            args.release_id,
            args.mode,
            args.github_output,
        )
        return 0
    except (OSError, ValueError) as exc:
        print(f"main release context verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
