#!/usr/bin/env python3
"""Build canonical, sanitized, run-scoped production canary evidence."""

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

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from promote_service_digests import SERVICE_NAMES, SERVICE_PATHS
from validate_release_manifest import resolve_release_bundle
from verify_promotion_chain import inspect_chain


SHA40 = re.compile(r"[0-9a-f]{40}")
SHA64 = re.compile(r"[0-9a-f]{64}")
WORKFLOW_PATH = ".github/workflows/mission-spine-promote.yml"


def _positive(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ValueError("production canary Git provenance lookup failed")
    return result.stdout.strip()


def _write_new(path: Path, document: dict[str, Any]) -> None:
    if (
        path.exists()
        or path.is_symlink()
        or not path.parent.is_dir()
        or path.parent.is_symlink()
    ):
        raise ValueError("production canary output must be a new regular file")
    raw = (json.dumps(document, separators=(",", ":"), ensure_ascii=True) + "\n").encode(
        "utf-8"
    )
    if len(raw) > 512 * 1024:
        raise ValueError("production canary output exceeds its byte bound")
    with path.open("xb") as output:
        mode = os.fstat(output.fileno()).st_mode
        if not stat.S_ISREG(mode):
            raise ValueError("production canary output is not a regular file")
        output.write(raw)


def build_canary(
    root: Path,
    release_id: str,
    on_commit: str,
    producer_evidence: dict[str, Any],
    runtime_evidence: dict[str, Any],
    sync_seconds: int,
    canary_seconds: int,
    run_id: int,
    run_attempt: int,
    *,
    gitops_root: Path | None = None,
    control_root: Path | None = None,
) -> dict[str, Any]:
    _positive(run_id, "production canary run ID")
    _positive(run_attempt, "production canary run attempt")
    if run_attempt != 1:
        raise ValueError("production canary protected run attempt must equal 1")
    release_path, candidate_path, _, candidate, candidate_hash = resolve_release_bundle(
        root, release_id
    )
    if SHA40.fullmatch(on_commit) is None:
        raise ValueError("production canary ON commit is invalid")
    release_hash = hashlib.sha256(release_path.read_bytes()).hexdigest()
    chain_root = (gitops_root or root).resolve()
    trusted_root = (control_root or root).resolve()
    state = inspect_chain(chain_root, candidate, release_hash, on_commit)
    if state["phase"] != "mission-on" or state["on_commit"] != on_commit:
        raise ValueError("production canary commit is not exact mission-ON")
    if (
        not isinstance(producer_evidence, dict)
        or producer_evidence.get("schema_version") != 1
        or producer_evidence.get("status") != "passed"
        or producer_evidence.get("release_id") != release_id
        or not isinstance(producer_evidence.get("services"), dict)
        or tuple(producer_evidence["services"]) != SERVICE_NAMES
    ):
        raise ValueError("production canary service producer evidence is invalid")
    services: dict[str, Any] = {}
    for name in SERVICE_NAMES:
        authenticated = producer_evidence["services"][name]
        binding = candidate["services"][name]
        if (
            not isinstance(authenticated, dict)
            or authenticated.get("root_digest") != binding["image_digest"]
            or re.fullmatch(r"sha256:[0-9a-f]{64}", str(authenticated.get("manifest_digest")))
            is None
            or re.fullmatch(r"sha256:[0-9a-f]{64}", str(authenticated.get("config_digest")))
            is None
        ):
            raise ValueError(f"production canary {name} OCI evidence is invalid")
        services[name] = {
            "source_sha": binding["source_sha"],
            "image_repository": binding["image_repository"],
            "image_digest": binding["image_digest"],
            "manifest_digest": authenticated["manifest_digest"],
            "config_digest": authenticated["config_digest"],
        }
    if (
        not isinstance(runtime_evidence, dict)
        or tuple(runtime_evidence)
        != (
            "schema_version",
            "status",
            "services_commit",
            "observed_commit",
            "services",
        )
        or runtime_evidence["schema_version"] != 1
        or runtime_evidence["status"] != "passed"
        or runtime_evidence["services_commit"] != state["services_commit"]
        or runtime_evidence["observed_commit"] != on_commit
        or not isinstance(runtime_evidence["services"], dict)
        or tuple(runtime_evidence["services"]) != SERVICE_NAMES
    ):
        raise ValueError("production canary service runtime evidence is invalid")
    applied_revisions = {
        name: _git(
            chain_root,
            "log",
            "-1",
            "--format=%H",
            on_commit,
            "--",
            SERVICE_PATHS[name],
        )
        for name in SERVICE_NAMES
    }
    if any(SHA40.fullmatch(value) is None for value in applied_revisions.values()):
        raise ValueError("production canary service applied revision is invalid")
    for name in SERVICE_NAMES:
        runtime = runtime_evidence["services"][name]
        authenticated = services[name]
        if (
            not isinstance(runtime, dict)
            or tuple(runtime)
            != (
                "service",
                "application_observed_revision",
                "application_applied_revision",
                "deployment_uid",
                "root_digest",
                "manifest_digest",
                "config_digest",
                "pods",
            )
            or runtime["service"] != name
            or runtime["application_observed_revision"] != on_commit
            or runtime["application_applied_revision"] != applied_revisions[name]
            or runtime["root_digest"] != authenticated["image_digest"]
            or runtime["manifest_digest"] != authenticated["manifest_digest"]
            or runtime["config_digest"] != authenticated["config_digest"]
            or not isinstance(runtime["deployment_uid"], str)
            or not runtime["deployment_uid"]
            or not isinstance(runtime["pods"], list)
            or not runtime["pods"]
        ):
            raise ValueError(f"production canary {name} runtime evidence is invalid")
        seen_pods: set[str] = set()
        for pod in runtime["pods"]:
            if (
                not isinstance(pod, dict)
                or tuple(pod)
                != ("pod_uid", "runtime_image_digest", "runtime_image_form")
                or not isinstance(pod["pod_uid"], str)
                or not pod["pod_uid"]
                or pod["pod_uid"] in seen_pods
                or re.fullmatch(
                    r"sha256:[0-9a-f]{64}", str(pod["runtime_image_digest"])
                )
                is None
                or pod["runtime_image_form"] not in {"manifest", "config"}
                or pod["runtime_image_digest"]
                != authenticated[
                    "manifest_digest"
                    if pod["runtime_image_form"] == "manifest"
                    else "config_digest"
                ]
            ):
                raise ValueError(f"production canary {name} Pod runtime evidence is invalid")
            seen_pods.add(pod["pod_uid"])
    if (
        isinstance(sync_seconds, bool)
        or not isinstance(sync_seconds, int)
        or not 0 <= sync_seconds <= candidate["rollout"]["sync_timeout_seconds"]
        or canary_seconds != candidate["rollout"]["canary_seconds"]
    ):
        raise ValueError("production canary timing is outside the sealed policy")
    head = _git(trusted_root, "rev-parse", "HEAD")
    actual_blob = subprocess.run(
        ["git", "show", f"{head}:{WORKFLOW_PATH}"],
        cwd=trusted_root,
        capture_output=True,
        check=False,
    ).stdout
    if not actual_blob or b"\r" in actual_blob:
        raise ValueError("production canary workflow blob is invalid")
    return {
        "schema_version": 1,
        "document_type": "mission-spine-production-canary",
        "release_id": release_id,
        "candidate_spec_sha256": candidate_hash,
        "status": "passed",
        "base_commit": state["base_commit"],
        "migration_commit": state["migration_commit"],
        "services_commit": state["services_commit"],
        "off_commit": state["off_commit"],
        "on_commit": state["on_commit"],
        "migration_image": {
            "source_sha": candidate["shared_migration"]["source_sha"],
            "image_repository": candidate["shared_migration"]["image_repository"],
            "image_digest": candidate["shared_migration"]["image_digest"],
        },
        "services": services,
        "service_runtime": runtime_evidence,
        "sync_detection_seconds": sync_seconds,
        "canary_seconds": canary_seconds,
        "promoter_repository": "DevPathAi/devpath-gitops",
        "promoter_workflow_path": WORKFLOW_PATH,
        "promoter_workflow_sha256": hashlib.sha256(actual_blob).hexdigest(),
        "promoter_run_id": run_id,
        "promoter_run_attempt": 1,
        "promoter_head_sha": head,
        "release_manifest_sha256": release_hash,
        "candidate_spec_path": candidate_path.relative_to(root).as_posix(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--gitops-root", type=Path)
    parser.add_argument("--control-root", type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--on-commit", required=True)
    parser.add_argument("--service-evidence", type=Path, required=True)
    parser.add_argument("--service-runtime", type=Path, required=True)
    parser.add_argument("--sync-seconds", type=int, required=True)
    parser.add_argument("--canary-seconds", type=int, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        producer = json.loads(args.service_evidence.read_text(encoding="utf-8"))
        runtime = json.loads(args.service_runtime.read_text(encoding="utf-8"))
        document = build_canary(
            args.root.resolve(),
            args.release_id,
            args.on_commit,
            producer,
            runtime,
            args.sync_seconds,
            args.canary_seconds,
            args.run_id,
            args.run_attempt,
            gitops_root=args.gitops_root,
            control_root=args.control_root,
        )
        _write_new(args.output, document)
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"production canary evidence failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
