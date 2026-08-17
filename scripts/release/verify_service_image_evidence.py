#!/usr/bin/env python3
"""Authenticate exact immutable-image producer artifacts for all nine services."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import verify_release_artifacts as evidence
from promote_service_digests import SERVICE_NAMES
from validate_release_manifest import resolve_release_bundle
from verify_oci_images import RegistryClient


WORKFLOW_PATH = ".github/workflows/ci.yml"
COMMON_SERVICES = tuple(
    name for name in SERVICE_NAMES if name not in {"devpath-admin", "devpath-ai-svc"}
)
COMMON_KEYS = (
    "schema_version",
    "status",
    "repository",
    "source_sha",
    "image_repository",
    "image_digest",
    "manifest_digest",
    "config_digest",
    "platform",
    "rootfs_diff_ids",
    "oci_labels",
    "producer_workflow_path",
    "producer_workflow_sha256",
    "producer_run_id",
    "producer_run_attempt",
)
ADMIN_KEYS = (
    "schema_version",
    "source_sha",
    "image_repository",
    "image_tag",
    "image_digest",
    "image_config_digest",
    "publish_mode",
    "image_labels",
)
AI_KEYS = (
    "schema_version",
    "state",
    "source_sha",
    "image_repository",
    "image_tag",
    "image_digest",
    "manifest_digest",
    "config_digest",
    "platform",
    "oci_labels",
)
PLATFORM_KEYS = ("os", "architecture")
COMMON_LABEL_KEYS = (
    "org.opencontainers.image.source",
    "org.opencontainers.image.revision",
)
LEGACY_LABEL_KEYS = (
    "org.opencontainers.image.revision",
    "org.opencontainers.image.source",
)
SHA64 = re.compile(r"[0-9a-f]{64}")
MAX_EVIDENCE_BYTES = 256 * 1024
MAX_ARTIFACT_ZIP_BYTES = 1024 * 1024


def _positive(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _ordered(value: Any, keys: tuple[str, ...], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or tuple(value) != keys:
        raise ValueError(f"{label} keys/order are not exact")
    return value


def _parse(raw: bytes, label: str) -> dict[str, Any]:
    if not raw or len(raw) > MAX_EVIDENCE_BYTES or not raw.endswith(b"\n") or b"\r" in raw:
        raise ValueError(f"{label} bytes are not bounded UTF-8 JSON+LF")

    def pairs(values):  # noqa: ANN001
        document: dict[str, Any] = {}
        for key, value in values:
            if key in document:
                raise ValueError(f"{label} JSON contains duplicate keys")
            document[key] = value
        return document

    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{label} is not a JSON object")
    return document


def artifact_contract(service: str, source: str, run_id: int) -> tuple[str, str]:
    _positive(run_id, "producer run ID")
    if service == "devpath-admin":
        return (
            f"leva-admin-{source}-registry-evidence-run-{run_id}-attempt-1",
            "leva-admin.registry.json",
        )
    if service == "devpath-ai-svc":
        return (
            f"devpath-ai-svc-{source}-{run_id}-1-registry-evidence",
            "devpath-ai-svc.registry.json",
        )
    if service in COMMON_SERVICES:
        return (
            f"{service}-immutable-image-{source}-run-{run_id}-attempt-1",
            "evidence.json",
        )
    raise ValueError("immutable-image service is outside the exact allowlist")


def validate_service_image_payload(
    service: str,
    raw: bytes,
    binding: dict[str, Any],
    trust: dict[str, Any],
    run_id: int,
    workflow_sha256: str,
) -> dict[str, Any]:
    """Validate one of three non-interchangeable producer evidence schemas."""
    payload = _parse(raw, f"{service} immutable-image evidence")
    source = binding["source_sha"]
    repository = binding["repository"]
    image_repository = binding["image_repository"]
    root_digest = binding["image_digest"]
    expected_labels = {
        "org.opencontainers.image.source": f"https://github.com/{repository}",
        "org.opencontainers.image.revision": source,
    }
    for key, expected in (
        ("source_sha", source),
        ("image_repository", image_repository),
        ("image_digest", root_digest),
    ):
        if payload.get(key) != expected:
            raise ValueError(f"{service} immutable-image {key} differs from candidate")
    if trust.get("root_digest") != root_digest:
        raise ValueError(f"{service} independently fetched OCI root differs from candidate")

    if service in COMMON_SERVICES:
        _ordered(payload, COMMON_KEYS, f"{service} common evidence")
        _ordered(payload["platform"], PLATFORM_KEYS, f"{service} platform")
        _ordered(payload["oci_labels"], COMMON_LABEL_KEYS, f"{service} OCI labels")
        if (
            payload["schema_version"] != "devpath.immutable-image.v1"
            or payload["status"] != "passed"
            or payload["repository"] != repository
            or payload["manifest_digest"] != trust["manifest_digest"]
            or payload["config_digest"] != trust["config_digest"]
            or payload["platform"] != trust["platform"]
            or payload["rootfs_diff_ids"] != trust["rootfs_diff_ids"]
            or payload["oci_labels"] != expected_labels
            or payload["producer_workflow_path"] != WORKFLOW_PATH
            or payload["producer_workflow_sha256"] != workflow_sha256
            or payload["producer_run_id"] != run_id
            or payload["producer_run_attempt"] != 1
        ):
            raise ValueError(f"{service} common evidence differs from authenticated OCI/run")
    elif service == "devpath-admin":
        _ordered(payload, ADMIN_KEYS, "devpath-admin evidence")
        _ordered(payload["image_labels"], LEGACY_LABEL_KEYS, "devpath-admin image labels")
        if (
            payload["schema_version"] != "mission-spine.admin-artifact.v1"
            or payload["image_tag"] != source
            or payload["image_config_digest"] != trust["config_digest"]
            or payload["publish_mode"] not in {"created", "reused"}
            or payload["image_labels"] != expected_labels
        ):
            raise ValueError("devpath-admin evidence differs from authenticated OCI identity")
    elif service == "devpath-ai-svc":
        _ordered(payload, AI_KEYS, "devpath-ai-svc evidence")
        _ordered(payload["oci_labels"], LEGACY_LABEL_KEYS, "devpath-ai-svc OCI labels")
        if (
            payload["schema_version"] != "devpath.immutable-image.v1"
            or payload["state"] != "present"
            or payload["image_tag"] != source
            or payload["manifest_digest"] != trust["manifest_digest"]
            or payload["config_digest"] != trust["config_digest"]
            or payload["platform"] != "linux/amd64"
            or payload["oci_labels"] != expected_labels
        ):
            raise ValueError("devpath-ai-svc evidence differs from authenticated OCI identity")
    else:  # pragma: no cover - guarded by artifact_contract
        raise ValueError("immutable-image service is unsupported")
    return payload


def _list_push_runs(
    command_env: dict[str, str], repository: str, expected_source: str
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for page in range(1, 1001):
        listing = evidence._run_json(
            [
                "gh",
                "api",
                (
                    f"repos/{repository}/actions/runs?head_sha={expected_source}"
                    f"&event=push&status=success&per_page=100&page={page}"
                ),
            ],
            command_env,
        )
        page_runs = listing.get("workflow_runs")
        if not isinstance(page_runs, list):
            raise ValueError("immutable-image run query returned invalid JSON")
        runs.extend(page_runs)
        if len(page_runs) < 100:
            return runs
    raise ValueError("immutable-image run query exceeded pagination bound")


def _eligible_run(run: Any, repository: str, source: str) -> bool:
    return bool(
        isinstance(run, dict)
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("event") == "push"
        and run.get("head_sha") == source
        and run.get("head_branch") == "main"
        and run.get("path") == WORKFLOW_PATH
        and run.get("run_attempt") == 1
        and isinstance(run.get("id"), int)
        and not isinstance(run.get("id"), bool)
        and run["id"] > 0
        and (run.get("repository") or {}).get("full_name") == repository
        and (run.get("head_repository") or {}).get("full_name") == repository
    )


def select_unique_service_run(
    service: str,
    binding: dict[str, Any],
    runs: list[dict[str, Any]],
    artifact_lookup: Any,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    matches: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for run in runs:
        if not _eligible_run(run, binding["repository"], binding["source_sha"]):
            continue
        artifact_name, filename = artifact_contract(service, binding["source_sha"], run["id"])
        artifacts = [
            artifact
            for artifact in artifact_lookup(artifact_name)
            if isinstance(artifact, dict)
            and artifact.get("name") == artifact_name
            and artifact.get("expired") is False
            and (artifact.get("workflow_run") or {}).get("id") == run["id"]
        ]
        if len(artifacts) > 1:
            raise ValueError(f"{service}: duplicate active immutable-image artifacts")
        if len(artifacts) == 1:
            matches.append((run, artifacts[0], filename))
    if len(matches) != 1:
        raise ValueError(f"{service}: exactly one eligible immutable-image run is required")
    return matches[0]


def _download(
    command_env: dict[str, str],
    repository: str,
    artifact: dict[str, Any],
    filename: str,
    destination: Path,
    service: str,
) -> None:
    def extractor(archive: Path, output: Path) -> None:
        evidence._extract_exact_root_artifact_archive(
            archive,
            output,
            f"{service}-immutable-image",
            {filename: MAX_EVIDENCE_BYTES},
            MAX_EVIDENCE_BYTES,
        )

    evidence._download_exact_root_artifact_archive(
        command_env,
        repository,
        artifact["id"],
        artifact,
        destination,
        f"{service}-immutable-image",
        MAX_ARTIFACT_ZIP_BYTES,
        extractor,
    )


def verify_all_service_images(root: Path, release_id: str) -> dict[str, Any]:
    root = root.resolve()
    _, _, _, candidate, _ = resolve_release_bundle(root, release_id)
    token = os.environ.get("RELEASE_EVIDENCE_TOKEN", "")
    if not token or shutil.which("gh") is None:
        raise ValueError("RELEASE_EVIDENCE_TOKEN and GitHub CLI are required")
    command_env = os.environ.copy()
    command_env["GH_TOKEN"] = token
    registry = RegistryClient(
        os.environ.get("MISSION_SPINE_GHCR_READ_ACTOR", ""),
        os.environ.get("MISSION_SPINE_GHCR_READ_TOKEN", ""),
    )
    result: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="mission-spine-service-images-") as temp_dir:
        temp = Path(temp_dir)
        for service in SERVICE_NAMES:
            binding = candidate["services"][service]
            repository = binding["repository"]
            source = binding["source_sha"]
            branch = evidence._run_json(
                ["gh", "api", f"repos/{repository}/branches/main"], command_env
            )
            if (
                branch.get("name") != "main"
                or branch.get("protected") is not True
                or (branch.get("commit") or {}).get("sha") != source
            ):
                raise ValueError(f"{service}: source is not current protected main")
            run, listed_artifact, filename = select_unique_service_run(
                service,
                binding,
                _list_push_runs(command_env, repository, source),
                lambda name: evidence._list_named_artifacts(command_env, repository, name),
            )
            run_id = run["id"]
            current = evidence._run_json(
                ["gh", "api", f"repos/{repository}/actions/runs/{run_id}"], command_env
            )
            attempt = evidence._run_json(
                ["gh", "api", f"repos/{repository}/actions/runs/{run_id}/attempts/1"],
                command_env,
            )
            if not _eligible_run(current, repository, source) or not _eligible_run(
                attempt, repository, source
            ):
                raise ValueError(f"{service}: current producer run drifted")
            artifact_id = _positive(listed_artifact.get("id"), f"{service} artifact ID")
            artifact = evidence._run_json(
                ["gh", "api", f"repos/{repository}/actions/artifacts/{artifact_id}"],
                command_env,
            )
            expected_name, _ = artifact_contract(service, source, run_id)
            if (
                artifact.get("id") != artifact_id
                or artifact.get("name") != expected_name
                or artifact.get("expired") is not False
                or (artifact.get("workflow_run") or {}).get("id") != run_id
            ):
                raise ValueError(f"{service}: immutable-image artifact metadata drifted")
            workflow_raw = evidence._workflow_bytes(
                repository, WORKFLOW_PATH, source, command_env
            )
            workflow_sha = hashlib.sha256(workflow_raw).hexdigest()
            evidence.validate_run_provenance(
                f"{service}-immutable-image",
                attempt,
                {
                    "event": "push",
                    "head_sha": source,
                    "run_attempt": 1,
                    "workflow_path": WORKFLOW_PATH,
                    "workflow_sha256": workflow_sha,
                    "workflow_run_id": run_id,
                },
                source,
                WORKFLOW_PATH,
                workflow_raw,
                "push",
            )
            trust = registry.inspect(
                repository=repository,
                source_sha=source,
                image_repository=binding["image_repository"],
                expected_root_digest=binding["image_digest"],
            )
            destination = temp / service
            _download(command_env, repository, artifact, filename, destination, service)
            payload = validate_service_image_payload(
                service,
                (destination / filename).read_bytes(),
                binding,
                trust,
                run_id,
                workflow_sha,
            )
            result[service] = {
                "producer_run_id": run_id,
                "producer_run_attempt": 1,
                "artifact_id": artifact_id,
                "workflow_sha256": workflow_sha,
                "root_digest": trust["root_digest"],
                "manifest_digest": trust["manifest_digest"],
                "config_digest": trust["config_digest"],
                "rootfs_diff_ids": trust["rootfs_diff_ids"],
                "evidence_schema_version": payload["schema_version"],
            }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--evidence-out", type=Path)
    args = parser.parse_args(argv)
    try:
        document = {
            "schema_version": 1,
            "status": "passed",
            "release_id": args.release_id,
            "services": verify_all_service_images(args.root, args.release_id),
        }
        raw = (json.dumps(document, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
        if args.evidence_out is not None:
            if args.evidence_out.exists() or not args.evidence_out.parent.is_dir():
                raise ValueError("service image evidence output must be a new file")
            args.evidence_out.write_bytes(raw)
        print(raw.decode("utf-8"), end="")
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"service immutable-image verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
