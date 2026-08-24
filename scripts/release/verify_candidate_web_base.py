#!/usr/bin/env python3
"""Bind a candidate's prior lineage to its exact GitOps web base."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from set_web_digest import render_kustomization
from validate_release_manifest import resolve_candidate_spec


WEB_KUSTOMIZATION = Path("apps/devpath-web/base/kustomization.yaml")


def verify_candidate_web_base(root: Path, release_id: str) -> None:
    root = root.resolve()
    _, candidate, candidate_hash = resolve_candidate_spec(root, release_id)
    relative = candidate["gitops"]["web_kustomization"]
    if relative != WEB_KUSTOMIZATION.as_posix():
        raise ValueError("candidate web kustomization path is not exact")
    target = (root / relative).resolve()
    if target != (root / WEB_KUSTOMIZATION).resolve():
        raise ValueError("candidate web kustomization escaped its exact path")
    source = target.read_text(encoding="utf-8")
    render_kustomization(
        source,
        candidate,
        "mission-off",
        "base",
        candidate_spec_sha256=candidate_hash,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args(argv)
    try:
        verify_candidate_web_base(args.root, args.release_id)
        print("verified candidate exact base web digest and prior lineage")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"candidate web base verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
