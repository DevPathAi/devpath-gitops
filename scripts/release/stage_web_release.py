#!/usr/bin/env python3
"""Atomically select one sealed web phase on the staging Deployment."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_release_manifest import (
    DEDICATED_STAGING_IDENTITY,
    resolve_candidate_spec,
    resolve_release_bundle,
    validate_candidate_spec,
)


WEB_IMAGE = "ghcr.io/devpathai/devpath-web"
SHA256 = re.compile(r"[0-9a-f]{64}")
PHASE_DIGESTS = {
    "prior": ("frontend", "rollback", "prior_digest"),
    "mission-off": ("frontend", "mission_off", "image_digest"),
    "mission-on": ("frontend", "selected_on_digest"),
}


def _lookup(data: dict[str, Any], fields: tuple[str, ...]) -> str:
    value: Any = data
    for field in fields:
        value = value[field]
    if not isinstance(value, str):
        raise ValueError("staging web digest is not a string")
    return value


def _staging_identity(candidate: dict[str, Any]) -> dict[str, Any]:
    identity = candidate["environments"]["staging"]
    if any(
        identity.get(field) != expected
        for field, expected in DEDICATED_STAGING_IDENTITY.items()
    ):
        raise ValueError("candidate does not bind the dedicated staging identity")
    return identity


def build_patch(
    candidate: dict[str, Any], candidate_spec_sha256: str, phase: str
) -> dict[str, Any]:
    validate_candidate_spec(candidate)
    identity = _staging_identity(candidate)
    if SHA256.fullmatch(candidate_spec_sha256) is None:
        raise ValueError("staging candidate-spec SHA-256 is invalid")
    if phase not in PHASE_DIGESTS:
        raise ValueError("staging web phase is invalid")
    digest = _lookup(candidate, PHASE_DIGESTS[phase])
    if phase == "prior":
        prior = candidate["frontend"]["rollback"]["prior_identity"]
        ready = "true" if prior["ready"] else "false"
        release_id = prior["release_id"]
        identity_sha = prior["candidate_spec_sha256"]
        identity_digest = prior["image_digest"]
    else:
        ready = "true"
        release_id = candidate["release_id"]
        identity_sha = candidate_spec_sha256
        identity_digest = digest
    container = identity["web_container"]
    return {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": container,
                            "image": f"{WEB_IMAGE}@{digest}",
                            "imagePullPolicy": "IfNotPresent",
                            "env": [
                                {"name": "MISSION_RELEASE_READY", "value": ready},
                                {"name": "MISSION_RELEASE_ID", "value": release_id},
                                {
                                    "name": "MISSION_CANDIDATE_SPEC_SHA256",
                                    "value": identity_sha,
                                },
                                {
                                    "name": "MISSION_IMAGE_DIGEST",
                                    "value": identity_digest,
                                },
                                {
                                    "name": "MISSION_SYNTHETIC_PROBE_TOKEN",
                                    "valueFrom": {
                                        "secretKeyRef": {
                                            "name": "mission-spine-synthetic-probe",
                                            "key": "token",
                                        }
                                    },
                                },
                            ],
                            "ports": [{"containerPort": 8080, "protocol": "TCP"}],
                            "readinessProbe": {
                                "httpGet": {
                                    "path": "/",
                                    "port": 8080,
                                    "scheme": "HTTP",
                                },
                                "initialDelaySeconds": 5,
                                "periodSeconds": 10,
                                "timeoutSeconds": 1,
                                "successThreshold": 1,
                                "failureThreshold": 3,
                            },
                            "livenessProbe": {
                                "httpGet": {
                                    "path": "/",
                                    "port": 8080,
                                    "scheme": "HTTP",
                                },
                                "initialDelaySeconds": 10,
                                "periodSeconds": 10,
                                "timeoutSeconds": 1,
                                "successThreshold": 1,
                                "failureThreshold": 3,
                            },
                            "resources": {
                                "requests": {"cpu": "50m", "memory": "64Mi"},
                                "limits": {"memory": "128Mi"},
                            },
                            "terminationMessagePath": "/dev/termination-log",
                            "terminationMessagePolicy": "File",
                        }
                    ],
                    "automountServiceAccountToken": False,
                }
            }
        }
    }


def _current_phase(
    deployment: Any,
    candidate: dict[str, Any],
    candidate_spec_sha256: str,
    expected_current: str,
) -> str:
    identity = _staging_identity(candidate)
    if not isinstance(deployment, dict):
        raise ValueError("current staging Deployment is invalid")
    metadata = deployment.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("current staging Deployment metadata is invalid")
    if (
        metadata.get("name") != identity["web_deployment"]
        or metadata.get("namespace") != identity["namespace"]
    ):
        raise ValueError("current staging Deployment identity is not exact")
    resource_version = metadata.get("resourceVersion")
    if (
        not isinstance(resource_version, str)
        or not 1 <= len(resource_version) <= 256
        or any(ord(character) < 0x21 for character in resource_version)
    ):
        raise ValueError("current staging resourceVersion is invalid")
    pod_spec = ((deployment.get("spec") or {}).get("template") or {}).get("spec")
    if (
        not isinstance(pod_spec, dict)
        or pod_spec.get("automountServiceAccountToken") is not False
    ):
        raise ValueError("current staging Pod shape is not exact")
    containers = pod_spec.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        raise ValueError("current staging Deployment must have one exact container")
    container = containers[0]
    if not isinstance(container, dict) or container.get("name") != identity["web_container"]:
        raise ValueError("current staging container identity is not exact")
    phases = tuple(PHASE_DIGESTS) if expected_current == "candidate" else (expected_current,)
    for current_phase in phases:
        expected = build_patch(candidate, candidate_spec_sha256, current_phase)
        expected_container = expected["spec"]["template"]["spec"]["containers"][0]
        if container == expected_container:
            return current_phase
    raise ValueError("current staging phase or container shape is not exact")


def build_cas_patch(
    deployment: Any,
    candidate: dict[str, Any],
    candidate_spec_sha256: str,
    target: str,
    expected_current: str,
) -> dict[str, Any]:
    if expected_current not in {*PHASE_DIGESTS, "candidate"}:
        raise ValueError("expected current staging phase is invalid")
    _current_phase(
        deployment, candidate, candidate_spec_sha256, expected_current
    )
    resource_version = deployment["metadata"]["resourceVersion"]
    patch = build_patch(candidate, candidate_spec_sha256, target)
    patch["metadata"] = {"resourceVersion": resource_version}
    return patch


def stage(
    root: Path,
    release_id: str,
    phase: str,
    expected_current: str,
    *,
    candidate_only: bool = False,
) -> None:
    if not os.environ.get("KUBECONFIG"):
        raise ValueError("KUBECONFIG is required")
    binary = shutil.which("kubectl")
    if binary is None:
        raise ValueError("kubectl is required")
    if candidate_only:
        _, candidate, candidate_hash = resolve_candidate_spec(root, release_id)
    else:
        _, _, _, candidate, candidate_hash = resolve_release_bundle(root, release_id)
    identity = _staging_identity(candidate)
    target = f"deployment/{identity['web_deployment']}"
    get_result = subprocess.run(
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
    if get_result.returncode != 0:
        raise ValueError("current staging Deployment read failed")
    try:
        current = json.loads(get_result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("current staging Deployment is not JSON") from exc
    patch = json.dumps(
        build_cas_patch(current, candidate, candidate_hash, phase, expected_current),
        separators=(",", ":"),
        ensure_ascii=True,
    )
    result = subprocess.run(
        [
            binary,
            "--context",
            identity["kubernetes_context"],
            "--namespace",
            identity["namespace"],
            "patch",
            target,
            "--type=strategic",
            "--patch",
            patch,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("atomic staging web patch failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--phase", choices=tuple(PHASE_DIGESTS), required=True)
    parser.add_argument(
        "--expected-current",
        choices=(*PHASE_DIGESTS, "candidate"),
        required=True,
    )
    parser.add_argument("--candidate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        stage(
            args.root.resolve(),
            args.release_id,
            args.phase,
            args.expected_current,
            candidate_only=args.candidate_only,
        )
        print(f"staged exact {args.phase} web image and release identity")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"staging web release failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
