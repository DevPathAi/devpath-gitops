#!/usr/bin/env python3
"""Identify one exact sealed phase on the dedicated staging web Deployment."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from stage_web_release import (
    PHASE_DIGESTS,
    _current_phase,
    _staging_identity,
    build_patch,
)
from validate_release_manifest import resolve_release_bundle


def validate_allowed_phases(phases: tuple[str, ...]) -> tuple[str, ...]:
    if (
        not phases
        or len(phases) != len(set(phases))
        or any(phase not in PHASE_DIGESTS for phase in phases)
    ):
        raise ValueError("allowed staging phases must be unique sealed phases")
    return phases


def write_output(path: Path, phase: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("staging phase GITHUB_OUTPUT must be an existing regular file")
    if phase not in PHASE_DIGESTS:
        raise ValueError("staging phase output is invalid")
    with path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"current_phase={phase}\n")


def inspect(
    root: Path,
    release_id: str,
    allowed_phases: tuple[str, ...],
    github_output: Path,
) -> str:
    allowed_phases = validate_allowed_phases(allowed_phases)
    if not os.environ.get("KUBECONFIG"):
        raise ValueError("KUBECONFIG is required")
    binary = shutil.which("kubectl")
    if binary is None:
        raise ValueError("kubectl is required")
    _, _, _, candidate, candidate_hash = resolve_release_bundle(root, release_id)
    identity = _staging_identity(candidate)
    target = f"deployment/{identity['web_deployment']}"
    result = subprocess.run(
        [
            binary,
            "--context",
            identity["kubernetes_context"],
            "--namespace",
            identity["namespace"],
            "get",
            target,
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("current staging Deployment read failed")
    try:
        deployment: Any = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("current staging Deployment is not JSON") from exc
    phase = _current_phase(deployment, candidate, candidate_hash, "candidate")
    if phase not in allowed_phases:
        raise ValueError("current staging phase is not allowed for this operation")
    write_output(github_output, phase)
    print(f"identified exact staging web phase: {phase}")
    return phase


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-id", required=True)
    parser.add_argument(
        "--allowed-phase",
        action="append",
        choices=tuple(PHASE_DIGESTS),
        required=True,
    )
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        inspect(
            args.root.resolve(),
            args.release_id,
            tuple(args.allowed_phase),
            args.github_output,
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"staging web phase inspection failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
