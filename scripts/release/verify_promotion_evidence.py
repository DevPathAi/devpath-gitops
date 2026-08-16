#!/usr/bin/env python3
"""Verify that Landing-last follows a completed exact-digest production canary."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from validate_release_manifest import SHA40, resolve_release_bundle


EVIDENCE_KEYS = {
    "release_id",
    "candidate_spec_sha256",
    "status",
    "on_commit",
    "sync_detection_seconds",
    "canary_seconds",
}


def validate_promotion_payload(payload: object, release_id: str, candidate_hash: str, main_sha: str) -> None:
    if not isinstance(payload, dict) or set(payload) != EVIDENCE_KEYS:
        raise ValueError("production canary evidence has an invalid key set")
    if payload["release_id"] != release_id or payload["candidate_spec_sha256"] != candidate_hash:
        raise ValueError("production canary evidence is not bound to this release")
    if payload["status"] != "passed" or payload["canary_seconds"] != 900:
        raise ValueError("production canary evidence did not pass the exact 15-minute gate")
    if (
        isinstance(payload["sync_detection_seconds"], bool)
        or not isinstance(payload["sync_detection_seconds"], int)
        or not 0 <= payload["sync_detection_seconds"] <= 300
    ):
        raise ValueError("production rollout detection exceeded five minutes")
    if not isinstance(payload["on_commit"], str) or SHA40.fullmatch(payload["on_commit"]) is None:
        raise ValueError("production canary evidence has an invalid ON commit")
    if payload["on_commit"] != main_sha:
        raise ValueError("production main drifted after the canary")


def _gh_json(args: list[str], env: dict[str, str]) -> dict:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        raise ValueError("GitHub promotion evidence query failed")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("GitHub promotion evidence query returned invalid JSON")
    return value


def verify(root: Path, release_id: str) -> None:
    _, _, _, _, candidate_hash = resolve_release_bundle(root, release_id)
    token = os.environ.get("RELEASE_EVIDENCE_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repository:
        raise ValueError("RELEASE_EVIDENCE_TOKEN and GITHUB_REPOSITORY are required")
    if repository != "DevPathAi/devpath-gitops":
        raise ValueError("production evidence must be verified in DevPathAi/devpath-gitops")
    if shutil.which("gh") is None:
        raise ValueError("GitHub CLI is required")
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    artifact_name = f"{release_id}-production-canary"
    listing = _gh_json(
        ["api", f"repos/{repository}/actions/artifacts?name={artifact_name}&per_page=100"],
        env,
    )
    artifacts = [item for item in listing.get("artifacts", []) if item.get("expired") is False]
    if len(artifacts) != 1:
        raise ValueError("exactly one active production canary artifact is required")
    artifact = artifacts[0]
    if artifact.get("name") != artifact_name:
        raise ValueError("production canary artifact name mismatch")
    run_id = (artifact.get("workflow_run") or {}).get("id")
    run = _gh_json(["api", f"repos/{repository}/actions/runs/{run_id}"], env)
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ValueError("production promotion workflow is not successful")
    if not str(run.get("path", "")).endswith(".github/workflows/mission-spine-promote.yml"):
        raise ValueError("production canary artifact came from an untrusted workflow")
    subprocess.run(["git", "fetch", "origin", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
    main_sha = subprocess.run(
        ["git", "rev-parse", "origin/main"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="mission-spine-canary-") as temp_dir:
        result = subprocess.run(
            [
                "gh",
                "run",
                "download",
                str(run_id),
                "--repo",
                repository,
                "--name",
                artifact_name,
                "--dir",
                temp_dir,
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError("production canary artifact download failed")
        entries = [path for path in Path(temp_dir).rglob("*")]
        if len(entries) != 1 or entries[0].relative_to(temp_dir).as_posix() != "evidence.json":
            raise ValueError("production canary artifact contains unexpected files")
        if not entries[0].is_file() or entries[0].is_symlink():
            raise ValueError("production canary evidence must be a regular file")
        if entries[0].stat().st_size > 256 * 1024:
            raise ValueError("production canary evidence exceeds 256 KiB")
        payload = json.loads(entries[0].read_text(encoding="utf-8"))
    validate_promotion_payload(payload, release_id, candidate_hash, main_sha)
    print(f"verified completed production canary for {release_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args(argv)
    try:
        verify(args.root.resolve(), args.release_id)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"promotion evidence gate failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
