#!/usr/bin/env python3
"""Wait for exact sealed migration or all-nine additive-service runtime state."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from promote_service_digests import SERVICE_NAMES, SERVICE_PATHS
from validate_release_manifest import resolve_release_bundle
from verify_kubernetes_release_runtime import (
    validate_migration_runtime,
    validate_service_runtime,
)
from verify_oci_images import RegistryClient
from verify_promotion_chain import MIGRATION_PATH, inspect_chain, migration_job_name


KUBECTL_VERSION = "v1.36.2"
SHA40 = re.compile(r"[0-9a-f]{40}")
SAFE_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")
MAX_KUBECTL_BYTES = 4 * 1024 * 1024


def _kubectl_binary() -> str:
    binary = shutil.which("kubectl")
    if not binary:
        raise ValueError("pinned kubectl is unavailable")
    result = subprocess.run(
        [binary, "version", "--client", "-o", "json"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or len(result.stdout) > 64 * 1024:
        raise ValueError("kubectl client version lookup failed")
    try:
        version = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("kubectl client version is not UTF-8 JSON") from exc
    if (version.get("clientVersion") or {}).get("gitVersion") != KUBECTL_VERSION:
        raise ValueError("kubectl client version is not the frozen release version")
    return binary


def _kubectl(binary: str, context: str, namespace: str, args: list[str]) -> str:
    if not context or any(char in context for char in "\r\n"):
        raise ValueError("Kubernetes context is unsafe")
    if SAFE_NAME.fullmatch(namespace) is None:
        raise ValueError("Kubernetes namespace is unsafe")
    result = subprocess.run(
        [binary, "--context", context, "--namespace", namespace, *args],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or len(result.stdout) > MAX_KUBECTL_BYTES:
        raise ValueError("bounded kubectl read failed")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("kubectl response is not UTF-8") from exc


def _json_snapshot(binary: str, context: str, namespace: str, args: list[str]) -> Any:
    try:
        return json.loads(_kubectl(binary, context, namespace, [*args, "-o", "json"]))
    except json.JSONDecodeError as exc:
        raise ValueError("kubectl response is not JSON") from exc


def _commit_time(root: Path, commit: str) -> str:
    if SHA40.fullmatch(commit) is None:
        raise ValueError("rollout commit SHA is invalid")
    result = subprocess.run(
        ["git", "show", "-s", "--format=%cI", commit],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("rollout commit timestamp lookup failed")
    try:
        parsed = datetime.fromisoformat(result.stdout.strip())
    except ValueError as exc:
        raise ValueError("rollout commit timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("rollout commit timestamp has no timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _last_path_change(root: Path, observed_commit: str, path: str) -> str:
    if SHA40.fullmatch(observed_commit) is None or path not in {
        MIGRATION_PATH,
        *SERVICE_PATHS.values(),
    }:
        raise ValueError("rollout applied-revision lookup is invalid")
    result = subprocess.run(
        ["git", "log", "-1", "--format=%H", observed_commit, "--", path],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    applied = result.stdout.strip()
    if result.returncode != 0 or SHA40.fullmatch(applied) is None:
        raise ValueError("rollout applied revision lookup failed")
    return applied


def _registry() -> RegistryClient:
    return RegistryClient(
        os.environ.get("MISSION_SPINE_GHCR_READ_ACTOR", ""),
        os.environ.get("MISSION_SPINE_GHCR_READ_TOKEN", ""),
    )


def _trust(registry: RegistryClient, binding: dict[str, Any]) -> dict[str, Any]:
    return registry.inspect(
        repository=binding["repository"],
        source_sha=binding["source_sha"],
        image_repository=binding["image_repository"],
        expected_root_digest=binding["image_digest"],
    )


def _write(path: Path, document: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise ValueError("runtime evidence output must be a new file in an existing directory")
    raw = (json.dumps(document, separators=(",", ":"), ensure_ascii=True) + "\n").encode(
        "utf-8"
    )
    if len(raw) > 512 * 1024:
        raise ValueError("runtime evidence exceeds its byte bound")
    with path.open("xb") as output:
        output.write(raw)


def wait_migration(
    root: Path,
    candidate: dict[str, Any],
    candidate_spec_sha256: str,
    release_manifest_sha256: str,
    migration_commit: str,
    observed_commit: str,
    context: str,
    timeout_seconds: int,
    evidence_out: Path,
) -> None:
    state = inspect_chain(
        root,
        candidate,
        candidate_spec_sha256,
        release_manifest_sha256,
        observed_commit,
    )
    if state.get("migration_commit") != migration_commit:
        raise ValueError("observed chain does not retain the exact migration commit")
    if _last_path_change(root, observed_commit, MIGRATION_PATH) != migration_commit:
        raise ValueError("migration applied revision is not the exact migration commit")
    registry = _registry()
    trust = _trust(registry, candidate["shared_migration"])
    name = migration_job_name(trust["root_digest"], release_manifest_sha256)
    binary = _kubectl_binary()
    not_before = _commit_time(root, migration_commit)
    deadline = time.monotonic() + timeout_seconds
    last = "migration runtime is not yet observable"
    while True:
        try:
            application = _json_snapshot(
                binary, context, "argocd", ["get", "application/devpath-migration"]
            )
            job = _json_snapshot(binary, context, "devpath", ["get", f"job/{name}"])
            pods = _json_snapshot(
                binary, context, "devpath", ["get", "pods", "-l", f"job-name={name}"]
            )
            pod_items = pods.get("items") if isinstance(pods, dict) else None
            if not isinstance(pod_items, list) or len(pod_items) != 1:
                raise ValueError("migration Job does not own exactly one Pod")
            pod_name = (pod_items[0].get("metadata") or {}).get("name")
            if SAFE_NAME.fullmatch(pod_name or "") is None:
                raise ValueError("migration Pod name is unsafe")
            logs = _kubectl(
                binary,
                context,
                "devpath",
                ["logs", f"pod/{pod_name}", "-c", "flyway"],
            )
            result = validate_migration_runtime(
                application,
                job,
                pods,
                logs,
                migration_commit,
                release_manifest_sha256,
                trust,
                candidate["shared_migration"]["flyway_target"],
                candidate["shared_migration"]["required_migration"],
                not_before,
                observed_commit=observed_commit,
            )
            _write(evidence_out, result)
            return
        except ValueError as exc:
            last = str(exc)
        if time.monotonic() >= deadline:
            raise ValueError(f"migration runtime did not become exact: {last}")
        time.sleep(10)


def wait_services(
    root: Path,
    candidate: dict[str, Any],
    candidate_spec_sha256: str,
    release_manifest_sha256: str,
    services_commit: str,
    observed_commit: str,
    context: str,
    timeout_seconds: int,
    evidence_out: Path,
) -> None:
    state = inspect_chain(
        root,
        candidate,
        candidate_spec_sha256,
        release_manifest_sha256,
        observed_commit,
    )
    if state.get("services_commit") != services_commit:
        raise ValueError("observed chain does not retain the exact services commit")
    applied_revisions = {
        name: _last_path_change(root, observed_commit, SERVICE_PATHS[name])
        for name in SERVICE_NAMES
    }
    registry = _registry()
    trusts = {name: _trust(registry, candidate["services"][name]) for name in SERVICE_NAMES}
    binary = _kubectl_binary()
    deadline = time.monotonic() + timeout_seconds
    last = "additive service runtime is not yet observable"
    while True:
        try:
            services: dict[str, Any] = {}
            for name in SERVICE_NAMES:
                application = _json_snapshot(
                    binary, context, "argocd", ["get", f"application/{name}"]
                )
                deployment = _json_snapshot(
                    binary, context, "devpath", ["get", f"deployment/{name}"]
                )
                replicasets = _json_snapshot(
                    binary, context, "devpath", ["get", "replicasets", "-l", f"app={name}"]
                )
                pods = _json_snapshot(
                    binary, context, "devpath", ["get", "pods", "-l", f"app={name}"]
                )
                services[name] = validate_service_runtime(
                    application,
                    deployment,
                    replicasets,
                    pods,
                    name,
                    observed_commit,
                    applied_revisions[name],
                    trusts[name],
                )
            _write(
                evidence_out,
                {
                    "schema_version": 1,
                    "status": "passed",
                    "services_commit": services_commit,
                    "observed_commit": observed_commit,
                    "services": services,
                },
            )
            return
        except ValueError as exc:
            last = str(exc)
        if time.monotonic() >= deadline:
            raise ValueError(f"all-nine additive runtime did not become exact: {last}")
        time.sleep(10)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--gitops-root", type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--phase", choices=["migration", "services"], required=True)
    parser.add_argument("--introduced-commit", required=True)
    parser.add_argument("--observed-commit", required=True)
    parser.add_argument("--evidence-out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        gitops_root = (args.gitops_root or root).resolve()
        release_path, _, _, candidate, candidate_spec_sha256 = resolve_release_bundle(
            root, args.release_id
        )
        release_manifest_sha256 = hashlib.sha256(release_path.read_bytes()).hexdigest()
        if not os.environ.get("KUBECONFIG"):
            raise ValueError("KUBECONFIG is required")
        identity = candidate["environments"]["production"]
        timeout_seconds = candidate["rollout"]["sync_timeout_seconds"]
        if args.phase == "migration":
            wait_migration(
                gitops_root,
                candidate,
                candidate_spec_sha256,
                release_manifest_sha256,
                args.introduced_commit,
                args.observed_commit,
                identity["kubernetes_context"],
                timeout_seconds,
                args.evidence_out,
            )
        else:
            wait_services(
                gitops_root,
                candidate,
                candidate_spec_sha256,
                release_manifest_sha256,
                args.introduced_commit,
                args.observed_commit,
                identity["kubernetes_context"],
                timeout_seconds,
                args.evidence_out,
            )
        print(f"verified exact {args.phase} release runtime")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"release runtime verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
