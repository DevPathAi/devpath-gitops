#!/usr/bin/env python3
"""Observe exact runtime image identity and a release-bound synthetic probe."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from validate_release_manifest import resolve_candidate_spec, resolve_release_bundle
from stage_web_release import build_patch as build_staging_patch
from verify_kubernetes_release_runtime import validate_application


WEB_IMAGE = "ghcr.io/devpathai/devpath-web"
SYNTHETIC_KEYS = {"release_id", "candidate_spec_sha256", "image_digest", "status"}


class _NoRedirectHandler(HTTPRedirectHandler):
    """Reject redirects so bearer credentials never cross the sealed origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


# Release probe credentials must travel directly to the pinned origin.  Do not
# inherit runner-level proxy configuration: a proxy would resolve the hostname
# outside the runner and bypass the exact /etc/hosts pin installed by the
# staging validation workflow.
_NO_REDIRECT_OPENER = build_opener(ProxyHandler({}), _NoRedirectHandler())


def _expected_digest(candidate: dict[str, Any], phase: str) -> str:
    if phase == "mission-off":
        return candidate["frontend"]["mission_off"]["image_digest"]
    if phase == "mission-on":
        return candidate["frontend"]["selected_on_digest"]
    if phase == "prior":
        return candidate["frontend"]["rollback"]["prior_digest"]
    raise ValueError("unknown rollout phase")


def _allowed_spec_images(candidate: dict[str, Any], phase: str, expected_image: str) -> set[str]:
    allowed = {expected_image}
    if phase == "prior":
        # The first production transition may start from the one sealed legacy
        # source tag. Runtime imageID must still resolve to the prior digest.
        allowed.add(f"{WEB_IMAGE}:{candidate['gitops']['base_web_tag']}")
    return allowed


def _kubectl(args: list[str], context: str, namespace: str) -> str:
    command = ["kubectl", "--context", context, "--namespace", namespace, *args]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ValueError("kubectl rollout verification failed")
    return result.stdout


def _last_web_path_change(gitops_root: Path, observed_commit: str) -> str:
    result = subprocess.run(
        [
            "git",
            "log",
            "-1",
            "--format=%H",
            observed_commit,
            "--",
            "apps/devpath-web/base",
        ],
        cwd=gitops_root,
        capture_output=True,
        text=True,
        check=False,
    )
    applied_commit = result.stdout.strip()
    if (
        result.returncode != 0
        or len(applied_commit) != 40
        or any(character not in "0123456789abcdef" for character in applied_commit)
    ):
        raise ValueError("web applied revision could not be resolved from Git history")
    return applied_commit


def _single_container(items: Any, container: str, path: str) -> dict[str, Any]:
    if not isinstance(items, list) or len(items) != 1:
        raise ValueError(f"{path} must contain only the exact app container")
    matches = [item for item in items if isinstance(item, dict) and item.get("name") == container]
    if len(matches) != 1:
        raise ValueError(f"{path} must contain exactly one {container} container")
    return matches[0]


def _owner(metadata: Any, kind: str, name: str, uid: str, api_version: str) -> None:
    references = metadata.get("ownerReferences") if isinstance(metadata, dict) else None
    expected = {
        "apiVersion": api_version,
        "kind": kind,
        "name": name,
        "uid": uid,
        "controller": True,
        "blockOwnerDeletion": True,
    }
    if not isinstance(references, list) or references != [expected]:
        raise ValueError("web runtime owner reference is not exact")


def _no_auxiliary_containers(spec: Any, status: Any, label: str) -> None:
    if not isinstance(spec, dict) or not isinstance(status, dict):
        raise ValueError(f"{label} spec/status is invalid")
    for key in ("initContainers", "ephemeralContainers"):
        if spec.get(key) not in (None, []):
            raise ValueError(f"{label} may not contain {key}")
    for key in ("initContainerStatuses", "ephemeralContainerStatuses"):
        if status.get(key) not in (None, []):
            raise ValueError(f"{label} may not contain {key}")


def _validate_staging_pod_shape(spec: dict[str, Any], label: str) -> None:
    if spec.get("automountServiceAccountToken") is not False:
        raise ValueError(f"{label} service-account token automount is not disabled")


def _runtime_image(image_id: Any) -> str:
    if not isinstance(image_id, str) or not image_id:
        raise ValueError("Pod imageID is missing")
    for prefix in ("docker-pullable://", "containerd://"):
        if image_id.startswith(prefix):
            image_id = image_id[len(prefix):]
            break
    return image_id


def validate_rollout_snapshot(
    application: Any,
    deployment: Any,
    replicasets: Any,
    pods: Any,
    container: str,
    allowed_spec_images: set[str],
    expected_runtime_image: str,
    restart_baseline: dict[tuple[str, str], int],
    expected_revision: str | None,
    *,
    expected_applied_revision: str | None = None,
    expected_deployment: str | None = None,
    expected_namespace: str = "devpath",
    expected_container_spec: dict[str, Any] | None = None,
    require_automount_disabled: bool = False,
) -> None:
    """Require a fully available Deployment and stable exact-digest Pods.

    ``restart_baseline`` is populated on the first healthy poll, then both Pod
    identity and restart counts are required to remain unchanged on every poll.
    """
    if not isinstance(deployment, dict) or not isinstance(pods, dict):
        raise ValueError("Kubernetes rollout documents must be objects")
    metadata = deployment.get("metadata") or {}
    spec = deployment.get("spec") or {}
    status = deployment.get("status") or {}
    generation = metadata.get("generation")
    desired = spec.get("replicas")
    deployment_name = metadata.get("name")
    deployment_uid = metadata.get("uid")
    namespace = metadata.get("namespace")
    expected_deployment = expected_deployment or container
    if (
        deployment_name != expected_deployment
        or namespace != expected_namespace
        or not isinstance(deployment_uid, str)
        or not deployment_uid
    ):
        raise ValueError("web Deployment identity is not exact")
    if expected_revision is not None:
        applied_revision = expected_applied_revision or expected_revision
        validate_application(
            application,
            deployment_name,
            f"apps/{deployment_name}/base",
            expected_revision,
            applied_revision,
        )
    if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
        raise ValueError("Deployment generation is invalid")
    if isinstance(desired, bool) or not isinstance(desired, int) or desired <= 0:
        raise ValueError("Deployment desired replicas must be positive")
    template = spec.get("template") or {}
    deployment_pod_spec = template.get("spec") or {}
    _no_auxiliary_containers(deployment_pod_spec, {}, "Deployment template")
    if require_automount_disabled:
        _validate_staging_pod_shape(deployment_pod_spec, "Deployment template")
    spec_container = _single_container(
        deployment_pod_spec.get("containers"),
        container,
        "Deployment containers",
    )
    if spec_container.get("image") not in allowed_spec_images:
        raise ValueError("Deployment image is not an allowed sealed reference")
    if expected_container_spec is not None and spec_container != expected_container_spec:
        raise ValueError("Deployment staging container shape is not exact")
    if status.get("observedGeneration") != generation:
        raise ValueError("Deployment controller has not observed the desired generation")
    for field, label in (
        ("replicas", "desired"),
        ("updatedReplicas", "updated"),
        ("readyReplicas", "ready"),
        ("availableReplicas", "available"),
    ):
        if status.get(field) != desired:
            raise ValueError(f"Deployment {label} replicas do not equal desired replicas")
    if status.get("unavailableReplicas", 0) not in (None, 0):
        raise ValueError("Deployment has unavailable replicas")

    rs_items = replicasets.get("items") if isinstance(replicasets, dict) else None
    if not isinstance(rs_items, list):
        raise ValueError("web ReplicaSet inventory is invalid")
    active = [
        item
        for item in rs_items
        if isinstance(item, dict)
        and isinstance((item.get("spec") or {}).get("replicas", 0), int)
        and not isinstance((item.get("spec") or {}).get("replicas", 0), bool)
        and (item.get("spec") or {}).get("replicas", 0) > 0
    ]
    if len(active) != 1:
        raise ValueError("exactly one active web ReplicaSet is required")
    rs = active[0]
    rs_metadata = rs.get("metadata") or {}
    rs_name = rs_metadata.get("name")
    rs_uid = rs_metadata.get("uid")
    if not isinstance(rs_name, str) or not rs_name or not isinstance(rs_uid, str) or not rs_uid:
        raise ValueError("web ReplicaSet identity is invalid")
    _owner(rs_metadata, "Deployment", deployment_name, deployment_uid, "apps/v1")
    rs_spec = rs.get("spec") or {}
    rs_status = rs.get("status") or {}
    if rs_spec.get("replicas") != desired or any(
        rs_status.get(field) != desired
        for field in ("replicas", "readyReplicas", "availableReplicas")
    ):
        raise ValueError("web ReplicaSet is not fully available")
    rs_pod_spec = ((rs_spec.get("template") or {}).get("spec") or {})
    _no_auxiliary_containers(rs_pod_spec, {}, "ReplicaSet template")
    if require_automount_disabled:
        _validate_staging_pod_shape(rs_pod_spec, "ReplicaSet template")
    rs_container = _single_container(
        rs_pod_spec.get("containers"), container, "ReplicaSet containers"
    )
    if rs_container.get("image") not in allowed_spec_images:
        raise ValueError("ReplicaSet image is not an allowed sealed reference")
    if expected_container_spec is not None and rs_container != expected_container_spec:
        raise ValueError("ReplicaSet staging container shape is not exact")

    items = pods.get("items")
    if not isinstance(items, list) or len(items) != desired:
        raise ValueError("Pod count does not equal desired replicas")
    observed: dict[tuple[str, str], int] = {}
    for pod in items:
        if not isinstance(pod, dict):
            raise ValueError("Pod entry must be an object")
        pod_metadata = pod.get("metadata") or {}
        uid = pod_metadata.get("uid")
        if not isinstance(uid, str) or not uid:
            raise ValueError("Pod UID is missing")
        if pod_metadata.get("deletionTimestamp") is not None:
            raise ValueError("terminating Pod cannot satisfy rollout readiness")
        _owner(pod_metadata, "ReplicaSet", rs_name, rs_uid, "apps/v1")
        pod_spec = pod.get("spec") or {}
        pod_status = pod.get("status") or {}
        _no_auxiliary_containers(pod_spec, pod_status, "Pod")
        if require_automount_disabled:
            _validate_staging_pod_shape(pod_spec, "Pod")
        pod_container = _single_container(
            pod_spec.get("containers"), container, "Pod containers"
        )
        if pod_container.get("image") not in allowed_spec_images:
            raise ValueError("Pod image is not an allowed sealed reference")
        if expected_container_spec is not None and pod_container != expected_container_spec:
            raise ValueError("Pod staging container shape is not exact")
        if pod_status.get("phase") != "Running":
            raise ValueError("Pod must be Running")
        ready_conditions = [
            condition
            for condition in pod_status.get("conditions", [])
            if isinstance(condition, dict) and condition.get("type") == "Ready"
        ]
        if len(ready_conditions) != 1 or ready_conditions[0].get("status") != "True":
            raise ValueError("Pod Ready condition must be True")
        runtime = _single_container(
            pod_status.get("containerStatuses"), container, "Pod containerStatuses"
        )
        if runtime.get("ready") is not True:
            raise ValueError("Pod container readiness must be true")
        state = runtime.get("state")
        if not isinstance(state, dict) or set(state) != {"running"} or not isinstance(
            state.get("running"), dict
        ):
            raise ValueError("Pod container must be in one nonterminating running state")
        if _runtime_image(runtime.get("imageID")) != expected_runtime_image:
            raise ValueError("Pod imageID does not equal the sealed runtime digest")
        restart_count = runtime.get("restartCount")
        if isinstance(restart_count, bool) or not isinstance(restart_count, int) or restart_count < 0:
            raise ValueError("Pod restartCount is invalid")
        key = (uid, container)
        observed[key] = restart_count

    if restart_baseline:
        if set(observed) != set(restart_baseline):
            raise ValueError("healthy Pod identity changed after restart baseline")
        for key, count in observed.items():
            if count != restart_baseline[key]:
                raise ValueError("Pod restart count changed after baseline")
    else:
        restart_baseline.update(observed)


def _selector(deployment: dict[str, Any]) -> str:
    selector = (deployment.get("spec") or {}).get("selector")
    if not isinstance(selector, dict) or set(selector) != {"matchLabels"}:
        raise ValueError("Deployment selector must use only exact matchLabels")
    labels = selector.get("matchLabels")
    if not isinstance(labels, dict) or not labels:
        raise ValueError("Deployment selector matchLabels are missing")
    pairs: list[str] = []
    for key, value in sorted(labels.items()):
        if not isinstance(key, str) or not isinstance(value, str) or not key or not value:
            raise ValueError("Deployment selector labels are invalid")
        if any(character in key + value for character in ",=\n\r"):
            raise ValueError("Deployment selector labels are unsafe")
        pairs.append(f"{key}={value}")
    return ",".join(pairs)


def _snapshot(
    context: str,
    namespace: str,
    deployment_name: str,
    expected_revision: str | None,
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
    application = None
    if expected_revision is not None:
        application = json.loads(
            _kubectl(
                ["get", f"application/{deployment_name}", "-o", "json"],
                context,
                "argocd",
            )
        )
    deployment = json.loads(
        _kubectl(["get", f"deployment/{deployment_name}", "-o", "json"], context, namespace)
    )
    pods = json.loads(
        _kubectl(["get", "pods", "-l", _selector(deployment), "-o", "json"], context, namespace)
    )
    replicasets = json.loads(
        _kubectl(
            ["get", "replicasets", "-l", _selector(deployment), "-o", "json"],
            context,
            namespace,
        )
    )
    return application, deployment, replicasets, pods


def validate_synthetic_identity(
    payload: Any,
    release_id: str,
    candidate_hash: str,
    expected_digest: str,
) -> None:
    if not isinstance(payload, dict) or set(payload) != SYNTHETIC_KEYS:
        raise ValueError("synthetic identity has an invalid key set")
    expected = {
        "release_id": release_id,
        "candidate_spec_sha256": candidate_hash,
        "image_digest": expected_digest,
        "status": "ready",
    }
    if payload != expected:
        raise ValueError("synthetic identity does not bind this exact release digest")


def _synthetic_identity(
    candidate: dict[str, Any], candidate_hash: str, phase: str
) -> tuple[str, str, str] | None:
    if phase == "prior":
        prior = candidate["frontend"]["rollback"]["prior_identity"]
        if not prior["ready"]:
            return None
        return (
            prior["release_id"],
            prior["candidate_spec_sha256"],
            prior["image_digest"],
        )
    return (candidate["release_id"], candidate_hash, _expected_digest(candidate, phase))


def _probe_synthetic(
    origin: str,
    path: str,
    token: str,
    release_id: str,
    candidate_hash: str,
    expected_digest: str,
) -> None:
    if not token:
        raise ValueError("MISSION_SYNTHETIC_PROBE_TOKEN is required")
    request = Request(
        f"{origin.rstrip('/')}{path}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "devpath-release-canary/2",
            "X-DevPath-Release-ID": release_id,
        },
    )
    try:
        with _NO_REDIRECT_OPENER.open(request, timeout=10) as response:
            if response.status != 200:
                raise ValueError("synthetic probe returned a non-200 status")
            raw = response.read(16 * 1024 + 1)
    except OSError as exc:
        raise ValueError("synthetic probe request failed") from exc
    if len(raw) > 16 * 1024:
        raise ValueError("synthetic probe response is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("synthetic probe response is not UTF-8 JSON") from exc
    validate_synthetic_identity(payload, release_id, candidate_hash, expected_digest)


def wait_rollout(
    root: Path,
    release_id: str,
    environment: str,
    phase: str,
    canary_seconds: int,
    github_output: Path | None = None,
    commit: str | None = None,
    *,
    gitops_root: Path | None = None,
    candidate_only: bool = False,
) -> None:
    if not os.environ.get("KUBECONFIG"):
        raise ValueError("KUBECONFIG is required")
    if environment not in {"staging", "production"}:
        raise ValueError("environment must be staging or production")
    if candidate_only and environment != "staging":
        raise ValueError("candidate-only rollout is restricted to staging")
    if canary_seconds not in {0, 900} or (canary_seconds == 900 and phase != "mission-on"):
        raise ValueError("only the mission-ON phase may run the exact 900-second canary")
    if environment == "production" and (
        not isinstance(commit, str) or len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit)
    ):
        raise ValueError("production web rollout requires the exact GitOps commit")
    expected_revision = commit if environment == "production" else None
    if environment == "production" and gitops_root is None:
        raise ValueError("production web rollout requires the GitOps history root")
    expected_applied_revision = (
        _last_web_path_change(gitops_root, commit)
        if environment == "production" and gitops_root is not None and commit is not None
        else None
    )
    if candidate_only:
        _, candidate, candidate_hash = resolve_candidate_spec(root, release_id)
    else:
        _, _, _, candidate, candidate_hash = resolve_release_bundle(root, release_id)
    identity = candidate["environments"][environment]
    context = identity["kubernetes_context"]
    namespace = identity["namespace"]
    deployment_name = identity["web_deployment"]
    container = identity["web_container"]
    expected_digest = _expected_digest(candidate, phase)
    expected_image = f"{WEB_IMAGE}@{expected_digest}"
    allowed_images = _allowed_spec_images(candidate, phase, expected_image)
    expected_container_spec = None
    require_automount_disabled = environment == "staging"
    if require_automount_disabled:
        expected_container_spec = build_staging_patch(
            candidate, candidate_hash, phase
        )["spec"]["template"]["spec"]["containers"][0]
    revision_validation = (
        {"expected_applied_revision": expected_applied_revision}
        if expected_applied_revision is not None
        else {}
    )
    restart_baseline: dict[tuple[str, str], int] = {}

    started = time.monotonic()
    deadline = started + candidate["rollout"]["sync_timeout_seconds"]
    last_error = "rollout not yet observed"
    while True:
        try:
            application, deployment, replicasets, pods = _snapshot(
                context, namespace, deployment_name, expected_revision
            )
            validate_rollout_snapshot(
                application,
                deployment,
                replicasets,
                pods,
                container,
                allowed_images,
                expected_image,
                restart_baseline,
                expected_revision,
                expected_deployment=deployment_name,
                expected_namespace=namespace,
                expected_container_spec=expected_container_spec,
                require_automount_disabled=require_automount_disabled,
                **revision_validation,
            )
            break
        except ValueError as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            raise ValueError(f"exact ready runtime digest was not observed within 300 seconds: {last_error}")
        time.sleep(10)
    detection_seconds = int(time.monotonic() - started)
    if detection_seconds > 300:
        raise ValueError("rollout detection exceeded 300 seconds")

    token = os.environ.get("MISSION_SYNTHETIC_PROBE_TOKEN", "")
    synthetic_identity = _synthetic_identity(candidate, candidate_hash, phase)
    if synthetic_identity is not None and not token:
        raise ValueError("MISSION_SYNTHETIC_PROBE_TOKEN is required")
    probe_path = candidate["rollout"]["synthetic_probe_path"]
    canary_started = time.monotonic()
    while True:
        application, deployment, replicasets, pods = _snapshot(
            context, namespace, deployment_name, expected_revision
        )
        validate_rollout_snapshot(
            application,
            deployment,
            replicasets,
            pods,
            container,
            allowed_images,
            expected_image,
            restart_baseline,
            expected_revision,
            expected_deployment=deployment_name,
            expected_namespace=namespace,
            expected_container_spec=expected_container_spec,
            require_automount_disabled=require_automount_disabled,
            **revision_validation,
        )
        # Only the one sealed legacy prior predates release-aware identity.
        # Every candidate and released prior otherwise proves its exact lineage.
        if synthetic_identity is not None:
            _probe_synthetic(
                identity["web_origin"],
                probe_path,
                token,
                *synthetic_identity,
            )
        elapsed = time.monotonic() - canary_started
        if elapsed >= canary_seconds:
            break
        time.sleep(min(30, max(1, canary_seconds - int(elapsed))))
    print(
        f"verified {environment} {phase} runtime digest and release identity; "
        f"sync_detection_seconds={detection_seconds}; canary_seconds={canary_seconds}"
    )
    if github_output is not None:
        with github_output.open("a", encoding="utf-8", newline="\n") as output:
            output.write(f"sync_detection_seconds={detection_seconds}\n")
            output.write(f"canary_seconds={canary_seconds}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--environment", choices=["staging", "production"], required=True)
    parser.add_argument("--phase", choices=["mission-off", "mission-on", "prior"], required=True)
    parser.add_argument("--canary-seconds", type=int, choices=[0, 900], required=True)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--gitops-root", type=Path)
    parser.add_argument("--candidate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        wait_rollout(
            args.root.resolve(),
            args.release_id,
            args.environment,
            args.phase,
            args.canary_seconds,
            args.github_output,
            args.commit,
            gitops_root=args.gitops_root.resolve() if args.gitops_root else None,
            candidate_only=args.candidate_only,
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"rollout verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
