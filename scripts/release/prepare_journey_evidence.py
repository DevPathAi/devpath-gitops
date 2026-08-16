#!/usr/bin/env python3
"""Validate Home journey outputs and byte-copy each to an artifact root."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

from validate_release_manifest import resolve_candidate_spec
from verify_release_artifacts import validate_evidence_payload


JOURNEYS = {
    "activation": "mission-spine-onboarding",
    "contextual": "mission-spine-workspace",
}


def prepare(root: Path, release_id: str, evidence_dir: Path, output_dir: Path) -> dict[str, str]:
    _, _, candidate_hash = resolve_candidate_spec(root, release_id)
    evidence_dir = evidence_dir.resolve()
    output_dir = output_dir.resolve()
    if not evidence_dir.is_dir():
        raise ValueError("Home journey evidence directory is missing")
    actual_entries = sorted(
        path.relative_to(evidence_dir).as_posix()
        for path in evidence_dir.rglob("*")
    )
    expected_entries = sorted(
        [*JOURNEYS.values(), *(f"{directory}/evidence.json" for directory in JOURNEYS.values())]
    )
    if actual_entries != expected_entries:
        raise ValueError("Home harness must produce exactly two canonical evidence files")
    for directory in JOURNEYS.values():
        if not (evidence_dir / directory).is_dir() or (evidence_dir / directory).is_symlink():
            raise ValueError("Home journey evidence directories must be real directories")
    if output_dir.exists():
        raise ValueError("artifact preparation directory must be fresh")
    outputs: dict[str, str] = {}
    for label, directory in JOURNEYS.items():
        source = evidence_dir / directory / "evidence.json"
        raw = source.read_bytes()
        if len(raw) > 256 * 1024:
            raise ValueError(f"{label} journey evidence exceeds sanitized size limit")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{label} journey evidence is not UTF-8 JSON") from exc
        validate_evidence_payload(payload, candidate_hash, journey=True)
        destination = output_dir / label
        destination.mkdir(parents=True)
        shutil.copyfile(source, destination / "evidence.json")
        if (destination / "evidence.json").read_bytes() != raw:
            raise ValueError(f"{label} journey evidence was not copied byte-for-byte")
        outputs[f"{label}_sha256"] = hashlib.sha256(raw).hexdigest()
        outputs[f"{label}_path"] = (destination / "evidence.json").as_posix()
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        outputs = prepare(
            args.root.resolve(),
            args.release_id,
            args.evidence_dir,
            args.output_dir,
        )
        with args.github_output.open("a", encoding="utf-8", newline="\n") as output:
            for key, value in outputs.items():
                output.write(f"{key}={value}\n")
        print("validated and byte-copied exactly two Home journey evidence files")
        return 0
    except (OSError, ValueError) as exc:
        print(f"journey evidence preparation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
