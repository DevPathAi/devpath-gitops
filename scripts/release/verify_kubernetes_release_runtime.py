#!/usr/bin/env python3
"""Pure fail-closed validators for protected Mission Spine Kubernetes rollout gates."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from typing import Any, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from verify_oci_images import normalize_runtime_image_id
from verify_promotion_chain import migration_job_name


GITOPS_REPO_URL = "https://github.com/DevPathAi/devpath-gitops.git"
NAMESPACE = "devpath"
MIGRATION_PREFLIGHT_IMAGE = (
    "postgres:17-alpine@sha256:"
    "979c4379dd698aba0b890599a6104e082035f98ef31d9b9291ec22f2b13059ca"
)
MIGRATION_PREFLIGHT_ROOT_DIGEST = MIGRATION_PREFLIGHT_IMAGE.rsplit("@", 1)[1]
MIGRATION_PREFLIGHT_COMMAND = ["/bin/sh", "/opt/devpath/preflight.sh"]
MIGRATION_PREFLIGHT_MANIFEST_DIGEST = (
    "sha256:5a6fcbc5d93831991d2386fa634509b3c49a1ac5ffb70c13c2322840f821d7e7"
)
MIGRATION_PREFLIGHT_CONFIG_DIGEST = (
    "sha256:cc4c61127125de9f69aa50f7d78b54686576d5d3d835de03128b064d12b97154"
)
ARGO_NAMESPACE = "argocd"
SHA40 = re.compile(r"[0-9a-f]{40}")
SERVICE_ACCOUNT_VOLUME = re.compile(r"kube-api-access-[a-z0-9]{5}")
SERVICE_ACCOUNT_MOUNT_PATH = "/var/run/secrets/kubernetes.io/serviceaccount"


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _single_named(items: Any, name: str, label: str) -> dict[str, Any]:
    if not isinstance(items, list):
        raise ValueError(f"{label} must be an array")
    matches = [item for item in items if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"{label} must contain exactly one {name}")
    return matches[0]


def _only_named(items: Any, name: str, label: str) -> dict[str, Any]:
    if not isinstance(items, list) or len(items) != 1:
        raise ValueError(f"{label} must be the sole exact {name}")
    return _single_named(items, name, label)


def _no_ephemeral(spec: Any, status: Any, label: str) -> None:
    if not isinstance(spec, dict) or not isinstance(status, dict):
        raise ValueError(f"{label} spec/status is invalid")
    if spec.get("ephemeralContainers") not in (None, []) or status.get(
        "ephemeralContainerStatuses"
    ) not in (None, []):
        raise ValueError(f"{label} may not contain ephemeral containers")


def _without_admitted_service_account_projection(
    runtime_pod_spec: dict[str, Any], template_pod_spec: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Remove only the exact service-account projection injected into a Pod."""
    runtime = copy.deepcopy(runtime_pod_spec)
    template_volumes = template_pod_spec.get("volumes") or []
    runtime_volumes = runtime.get("volumes") or []
    if not isinstance(template_volumes, list) or not isinstance(runtime_volumes, list):
        raise ValueError("migration Pod volumes are invalid")
    template_names = {
        item.get("name") for item in template_volumes if isinstance(item, dict)
    }
    admitted = [
        item
        for item in runtime_volumes
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and SERVICE_ACCOUNT_VOLUME.fullmatch(item["name"])
        and item["name"] not in template_names
    ]
    if not admitted:
        return runtime, False
    if len(admitted) != 1:
        raise ValueError("migration Pod service account projection is not singular")
    volume = admitted[0]
    volume_name = volume["name"]
    projected = volume.get("projected")
    if (
        set(volume) != {"name", "projected"}
        or not isinstance(projected, dict)
        or set(projected) != {"defaultMode", "sources"}
        or projected.get("defaultMode") != 420
    ):
        raise ValueError("migration Pod service account projection is not exact")
    sources = projected.get("sources")
    if not isinstance(sources, list) or len(sources) != 3:
        raise ValueError("migration Pod service account projection is not exact")
    token = sources[0]
    token_projection = token.get("serviceAccountToken") if isinstance(token, dict) else None
    expiration = (
        token_projection.get("expirationSeconds")
        if isinstance(token_projection, dict)
        else None
    )
    if (
        not isinstance(token, dict)
        or set(token) != {"serviceAccountToken"}
        or not isinstance(token_projection, dict)
        or set(token_projection) != {"expirationSeconds", "path"}
        or isinstance(expiration, bool)
        or not isinstance(expiration, int)
        or not 3600 <= expiration <= 7200
        or token_projection.get("path") != "token"
        or sources[1]
        != {
            "configMap": {
                "items": [{"key": "ca.crt", "path": "ca.crt"}],
                "name": "kube-root-ca.crt",
            }
        }
        or sources[2]
        != {
            "downwardAPI": {
                "items": [
                    {
                        "fieldRef": {
                            "apiVersion": "v1",
                            "fieldPath": "metadata.namespace",
                        },
                        "path": "namespace",
                    }
                ]
            }
        }
    ):
        raise ValueError("migration Pod service account projection is not exact")
    expected_mount = {
        "mountPath": SERVICE_ACCOUNT_MOUNT_PATH,
        "name": volume_name,
        "readOnly": True,
    }
    for section in ("initContainers", "containers"):
        items = runtime.get(section)
        if not isinstance(items, list) or not items:
            raise ValueError("migration Pod service account mounts are invalid")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("migration Pod service account mounts are invalid")
            mounts = item.get("volumeMounts")
            if not isinstance(mounts, list) or mounts.count(expected_mount) != 1:
                raise ValueError("migration Pod service account mount is not exact")
            remaining = [mount for mount in mounts if mount != expected_mount]
            if remaining:
                item["volumeMounts"] = remaining
            else:
                item.pop("volumeMounts")
    remaining_volumes = [item for item in runtime_volumes if item is not volume]
    if remaining_volumes:
        runtime["volumes"] = remaining_volumes
    else:
        runtime.pop("volumes", None)
    return runtime, True


def _metadata(document: Any, kind: str, name: str, namespace: str) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError(f"{kind} document is invalid")
    metadata = document.get("metadata")
    if (
        not isinstance(metadata, dict)
        or metadata.get("name") != name
        or metadata.get("namespace") != namespace
    ):
        raise ValueError(f"{kind} identity is not exact")
    return metadata


def _owner(
    metadata: dict[str, Any], kind: str, name: str, uid: str, api_version: str
) -> None:
    references = metadata.get("ownerReferences")
    if not isinstance(references, list) or len(references) != 1:
        raise ValueError("runtime owner reference is not singular")
    owner = references[0]
    if (
        not isinstance(owner, dict)
        or owner.get("apiVersion") != api_version
        or owner.get("kind") != kind
        or owner.get("name") != name
        or owner.get("uid") != uid
        or owner.get("controller") is not True
    ):
        raise ValueError("runtime owner reference is not exact")


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be an exact UTC-Z timestamp")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    if parsed.tzinfo != timezone.utc:
        raise ValueError(f"{label} is not UTC")
    return parsed


def validate_application(
    application: Any,
    name: str,
    path: str,
    observed_revision: str,
    applied_revision: str,
) -> dict[str, Any]:
    if (
        SHA40.fullmatch(observed_revision) is None
        or SHA40.fullmatch(applied_revision) is None
    ):
        raise ValueError("Argo Application expected revisions are invalid")
    _metadata(application, "Argo Application", name, ARGO_NAMESPACE)
    spec = application.get("spec")
    source = spec.get("source") if isinstance(spec, dict) else None
    expected_source = {
        "repoURL": GITOPS_REPO_URL,
        "targetRevision": "main",
        "path": path,
    }
    if (
        not isinstance(spec, dict)
        or spec.get("project") != "devpath"
        or spec.get("destination")
        != {"server": "https://kubernetes.default.svc", "namespace": "devpath"}
        or source != expected_source
        or "sources" in spec
    ):
        raise ValueError("Argo Application source is not exact")
    status = application.get("status")
    sync = status.get("sync") if isinstance(status, dict) else None
    health = status.get("health") if isinstance(status, dict) else None
    operation = status.get("operationState") if isinstance(status, dict) else None
    sync_result = operation.get("syncResult") if isinstance(operation, dict) else None
    if (
        not isinstance(sync, dict)
        or sync.get("status") != "Synced"
        or sync.get("revision") != observed_revision
        or not isinstance(health, dict)
        or health.get("status") != "Healthy"
        or not isinstance(operation, dict)
        or operation.get("phase") != "Succeeded"
        or not isinstance(sync_result, dict)
        or sync_result.get("revision") != applied_revision
    ):
        raise ValueError("Argo Application revision is not exact, healthy, and synced")
    return {
        "application": name,
        "observed_revision": observed_revision,
        "applied_revision": applied_revision,
        "path": path,
    }


def _validate_deployment_status(deployment: dict[str, Any]) -> int:
    metadata = deployment["metadata"]
    spec = deployment.get("spec")
    status = deployment.get("status")
    if not isinstance(spec, dict) or not isinstance(status, dict):
        raise ValueError("Deployment spec/status is invalid")
    generation = _positive_int(metadata.get("generation"), "Deployment generation")
    desired = _positive_int(spec.get("replicas"), "Deployment replicas")
    if status.get("observedGeneration") != generation:
        raise ValueError("Deployment observed generation drifted")
    for field in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas"):
        if status.get(field) != desired:
            raise ValueError("Deployment replicas are not fully available")
    if status.get("unavailableReplicas", 0) not in {None, 0}:
        raise ValueError("Deployment replicas include unavailable instances")
    return desired


def validate_service_runtime(
    application: Any,
    deployment: Any,
    replicasets: Any,
    pods: Any,
    service_name: str,
    observed_commit: str,
    applied_commit: str,
    trust: Mapping[str, Any],
) -> dict[str, Any]:
    validate_application(
        application,
        service_name,
        f"apps/{service_name}/base",
        observed_commit,
        applied_commit,
    )
    metadata = _metadata(deployment, "Deployment", service_name, NAMESPACE)
    deployment_uid = metadata.get("uid")
    if not isinstance(deployment_uid, str) or not deployment_uid:
        raise ValueError("Deployment UID is missing")
    desired = _validate_deployment_status(deployment)
    expected_image = f"{trust['image_repository']}@{trust['root_digest']}"
    spec = deployment["spec"]
    selector = spec.get("selector")
    if selector != {"matchLabels": {"app": service_name}}:
        raise ValueError("Deployment selector is not exact")
    template = spec.get("template")
    if not isinstance(template, dict) or (template.get("metadata") or {}).get("labels", {}).get(
        "app"
    ) != service_name:
        raise ValueError("Deployment template labels are not exact")
    deployment_pod_spec = template.get("spec") or {}
    deployment_containers = deployment_pod_spec.get("containers")
    if (
        not isinstance(deployment_containers, list)
        or len(deployment_containers) != 1
        or deployment_pod_spec.get("initContainers") not in (None, [])
    ):
        raise ValueError("Deployment may contain only the exact app container")
    if deployment_pod_spec.get("ephemeralContainers") not in (None, []):
        raise ValueError("Deployment may not contain ephemeral containers")
    target = _single_named(
        deployment_containers, service_name, "Deployment containers"
    )
    if target.get("image") != expected_image:
        raise ValueError("Deployment target is not the sealed root digest")
    rs_items = replicasets.get("items") if isinstance(replicasets, dict) else None
    if not isinstance(rs_items, list):
        raise ValueError("ReplicaSet inventory is invalid")
    active: list[dict[str, Any]] = []
    for item in rs_items:
        if not isinstance(item, dict):
            raise ValueError("ReplicaSet inventory entry is invalid")
        replicas = (item.get("spec") or {}).get("replicas", 0)
        if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 0:
            raise ValueError("ReplicaSet desired replicas are invalid")
        if replicas > 0:
            active.append(item)
    if len(active) != 1:
        raise ValueError("exactly one active ReplicaSet is required")
    rs = active[0]
    rs_metadata = rs.get("metadata")
    if not isinstance(rs_metadata, dict):
        raise ValueError("active ReplicaSet metadata is invalid")
    rs_name = rs_metadata.get("name")
    rs_uid = rs_metadata.get("uid")
    if not isinstance(rs_name, str) or not rs_name or not isinstance(rs_uid, str) or not rs_uid:
        raise ValueError("active ReplicaSet identity is invalid")
    _owner(rs_metadata, "Deployment", service_name, deployment_uid, "apps/v1")
    rs_spec = rs.get("spec") or {}
    rs_status = rs.get("status") or {}
    if rs_spec.get("replicas") != desired or any(
        rs_status.get(field) != desired for field in ("replicas", "readyReplicas", "availableReplicas")
    ):
        raise ValueError("active ReplicaSet replicas are not fully ready")
    rs_pod_spec = ((rs_spec.get("template") or {}).get("spec") or {})
    rs_containers = rs_pod_spec.get("containers")
    if (
        not isinstance(rs_containers, list)
        or len(rs_containers) != 1
        or rs_pod_spec.get("initContainers") not in (None, [])
    ):
        raise ValueError("ReplicaSet may contain only the exact app container")
    if rs_pod_spec.get("ephemeralContainers") not in (None, []):
        raise ValueError("ReplicaSet may not contain ephemeral containers")
    rs_container = _single_named(
        rs_containers,
        service_name,
        "ReplicaSet containers",
    )
    if rs_container.get("image") != expected_image:
        raise ValueError("active ReplicaSet image is not the sealed root digest")
    pod_items = pods.get("items") if isinstance(pods, dict) else None
    if not isinstance(pod_items, list) or len(pod_items) != desired:
        raise ValueError("Pod count does not equal desired replicas")
    sanitized: list[dict[str, str]] = []
    seen_uids: set[str] = set()
    for pod in pod_items:
        pod_metadata = pod.get("metadata") if isinstance(pod, dict) else None
        if not isinstance(pod_metadata, dict):
            raise ValueError("Pod metadata is invalid")
        uid = pod_metadata.get("uid")
        if (
            not isinstance(uid, str)
            or not uid
            or uid in seen_uids
            or pod_metadata.get("deletionTimestamp") is not None
            or (pod_metadata.get("labels") or {}).get("app") != service_name
        ):
            raise ValueError("Pod identity is stale or duplicated")
        seen_uids.add(uid)
        _owner(pod_metadata, "ReplicaSet", rs_name, rs_uid, "apps/v1")
        pod_spec = pod.get("spec") or {}
        pod_containers = pod_spec.get("containers")
        if (
            not isinstance(pod_containers, list)
            or len(pod_containers) != 1
            or pod_spec.get("initContainers") not in (None, [])
        ):
            raise ValueError("Pod may contain only the exact app container")
        if _single_named(pod_containers, service_name, "Pod containers").get(
            "image"
        ) != expected_image:
            raise ValueError("Pod spec image is not the sealed root digest")
        pod_status = pod.get("status") or {}
        _no_ephemeral(pod_spec, pod_status, "Pod")
        ready = [
            condition
            for condition in pod_status.get("conditions", [])
            if isinstance(condition, dict) and condition.get("type") == "Ready"
        ]
        if pod_status.get("phase") != "Running" or len(ready) != 1 or ready[0].get(
            "status"
        ) != "True":
            raise ValueError("Pod is not exactly Running and Ready")
        runtime = _only_named(
            pod_status.get("containerStatuses"), service_name, "Pod containerStatuses"
        )
        state = runtime.get("state")
        if (
            runtime.get("ready") is not True
            or runtime.get("restartCount") != 0
            or not isinstance(state, dict)
            or set(state) != {"running"}
        ):
            raise ValueError("Pod target container is not a clean running instance")
        _authenticated_status_image(
            runtime.get("image"),
            expected_image,
            {
                trust["root_digest"],
                trust["manifest_digest"],
                trust["config_digest"],
            },
            "service Pod runtime image",
        )
        runtime_id = normalize_runtime_image_id(runtime.get("imageID"), trust)
        sanitized.append(
            {
                "pod_uid": uid,
                "runtime_image_digest": runtime_id["digest"],
                "runtime_image_form": runtime_id["form"],
            }
        )
    sanitized.sort(key=lambda item: item["pod_uid"])
    return {
        "service": service_name,
        "application_observed_revision": observed_commit,
        "application_applied_revision": applied_commit,
        "deployment_uid": deployment_uid,
        "root_digest": trust["root_digest"],
        "manifest_digest": trust["manifest_digest"],
        "config_digest": trust["config_digest"],
        "pods": sanitized,
    }


def _terminated(status: dict[str, Any], label: str) -> None:
    state = status.get("state")
    terminated = state.get("terminated") if isinstance(state, dict) else None
    if (
        status.get("restartCount") != 0
        or not isinstance(terminated, dict)
        or terminated.get("exitCode") != 0
        or terminated.get("reason") != "Completed"
    ):
        raise ValueError(f"{label} did not terminate successfully")


def _authenticated_status_image(
    value: Any,
    exact_reference: str,
    authenticated_digests: set[str],
    label: str,
) -> None:
    """Accept the submitted image reference or a kubelet-normalized trusted digest."""
    if value == exact_reference:
        return
    if not isinstance(value, str) or value not in authenticated_digests:
        raise ValueError(f"{label} is not authenticated")


def validate_migration_runtime(
    application: Any,
    job: Any,
    pods: Any,
    logs: str,
    migration_commit: str,
    release_manifest_sha256: str,
    trust: Mapping[str, Any],
    flyway_target: str,
    required_migration: str,
    not_before: str,
    observed_commit: str | None = None,
    application_applied_commit: str | None = None,
) -> dict[str, Any]:
    observed_commit = observed_commit or migration_commit
    application_applied_commit = application_applied_commit or migration_commit
    validate_application(
        application,
        "devpath-migration",
        "apps/devpath-migration/base",
        observed_commit,
        application_applied_commit,
    )
    expected_name = migration_job_name(
        trust["root_digest"], release_manifest_sha256
    )
    metadata = _metadata(job, "migration Job", expected_name, NAMESPACE)
    uid = metadata.get("uid")
    resource_version = metadata.get("resourceVersion")
    if (
        not isinstance(uid, str)
        or not uid
        or not isinstance(resource_version, str)
        or not resource_version.isdecimal()
        or _positive_int(metadata.get("generation"), "migration Job generation") != 1
        or _utc(metadata.get("creationTimestamp"), "migration Job creation") < _utc(
            not_before, "migration commit time"
        )
    ):
        raise ValueError("migration Job identity is stale or invalid")
    expected_image = f"{trust['image_repository']}@{trust['root_digest']}"
    spec = job.get("spec")
    pod_spec = (((spec or {}).get("template") or {}).get("spec") or {})
    if (
        not isinstance(spec, dict)
        or spec.get("suspend") is not False
        or spec.get("backoffLimit") != 3
        or pod_spec.get("restartPolicy") != "Never"
    ):
        raise ValueError("migration Job spec is not exact")
    if pod_spec.get("ephemeralContainers") not in (None, []):
        raise ValueError("migration Job may not contain ephemeral containers")
    preflight = _only_named(
        pod_spec.get("initContainers"),
        "sandbox-low-lock-preflight",
        "migration initContainers",
    )
    if (
        preflight.get("image") != MIGRATION_PREFLIGHT_IMAGE
        or preflight.get("command") != MIGRATION_PREFLIGHT_COMMAND
    ):
        raise ValueError("migration preflight image/command is not exact")
    container = _only_named(pod_spec.get("containers"), "flyway", "migration containers")
    if container.get("image") != expected_image:
        raise ValueError("migration Job image is not the sealed root digest")
    target_env = [
        item
        for item in container.get("env", [])
        if isinstance(item, dict) and item.get("name") == "TARGET_FLYWAY_VERSION"
    ]
    args = container.get("args")
    if (
        target_env != [{"name": "TARGET_FLYWAY_VERSION", "value": flyway_target}]
        or not isinstance(args, list)
        or len(args) != 1
        or not isinstance(args[0], str)
        or f"test -f /flyway/sql/{required_migration}" not in args[0]
        or "mission-spine-flyway-target=%s status=validated" not in args[0]
    ):
        raise ValueError("migration Job target/required migration contract drifted")
    status = job.get("status")
    conditions = status.get("conditions") if isinstance(status, dict) else None
    complete = [
        item
        for item in conditions or []
        if isinstance(item, dict) and item.get("type") == "Complete"
    ]
    failed = [
        item
        for item in conditions or []
        if isinstance(item, dict)
        and item.get("type") == "Failed"
        and item.get("status") == "True"
    ]
    if (
        not isinstance(status, dict)
        or status.get("succeeded") != 1
        or status.get("failed", 0) not in {None, 0}
        or status.get("active", 0) not in {None, 0}
        or len(complete) != 1
        or complete[0].get("status") != "True"
        or failed
        or _utc(status.get("completionTime"), "migration completion")
        < _utc(metadata.get("creationTimestamp"), "migration creation")
    ):
        raise ValueError("migration Job completion is not exact and successful")
    pod_items = pods.get("items") if isinstance(pods, dict) else None
    if not isinstance(pod_items, list) or len(pod_items) != 1:
        raise ValueError("migration Job must own exactly one completed Pod")
    pod = pod_items[0]
    pod_metadata = pod.get("metadata") if isinstance(pod, dict) else None
    if not isinstance(pod_metadata, dict):
        raise ValueError("migration Pod metadata is invalid")
    pod_uid = pod_metadata.get("uid")
    if not isinstance(pod_uid, str) or not pod_uid:
        raise ValueError("migration Pod UID is missing")
    _owner(pod_metadata, "Job", expected_name, uid, "batch/v1")
    runtime_pod_spec = pod.get("spec") or {}
    if not isinstance(runtime_pod_spec, dict):
        raise ValueError("migration Pod spec is invalid")
    normalized_runtime_pod_spec, service_account_projection_admitted = (
        _without_admitted_service_account_projection(runtime_pod_spec, pod_spec)
    )
    if (
        normalized_runtime_pod_spec.get("containers") != pod_spec.get("containers")
        or normalized_runtime_pod_spec.get("initContainers")
        != pod_spec.get("initContainers")
        or normalized_runtime_pod_spec.get("restartPolicy")
        != pod_spec.get("restartPolicy")
        or normalized_runtime_pod_spec.get("volumes") != pod_spec.get("volumes")
    ):
        raise ValueError("migration Pod spec differs from the authenticated Job template")
    pod_status = pod.get("status") or {}
    _no_ephemeral(runtime_pod_spec, pod_status, "migration Pod")
    if pod_status.get("phase") != "Succeeded":
        raise ValueError("migration Pod did not succeed")
    init = _only_named(
        pod_status.get("initContainerStatuses"),
        "sandbox-low-lock-preflight",
        "migration initContainerStatuses",
    )
    _terminated(init, "migration preflight")
    _authenticated_status_image(
        init.get("image"),
        MIGRATION_PREFLIGHT_IMAGE,
        {
            MIGRATION_PREFLIGHT_MANIFEST_DIGEST,
            MIGRATION_PREFLIGHT_CONFIG_DIGEST,
        },
        "migration preflight runtime image",
    )
    preflight_image_id = init.get("imageID")
    if not isinstance(preflight_image_id, str):
        raise ValueError("migration preflight runtime imageID is missing")
    normalized_preflight = preflight_image_id.removeprefix("docker-pullable://").removeprefix(
        "containerd://"
    )
    if "@" in normalized_preflight:
        normalized_preflight = normalized_preflight.rsplit("@", 1)[1]
    if normalized_preflight not in {
        MIGRATION_PREFLIGHT_ROOT_DIGEST,
        MIGRATION_PREFLIGHT_MANIFEST_DIGEST,
        MIGRATION_PREFLIGHT_CONFIG_DIGEST,
    }:
        raise ValueError("migration preflight runtime imageID is not authenticated")
    runtime = _only_named(
        pod_status.get("containerStatuses"), "flyway", "migration containerStatuses"
    )
    _terminated(runtime, "migration flyway container")
    _authenticated_status_image(
        runtime.get("image"),
        expected_image,
        {
            trust["root_digest"],
            trust["manifest_digest"],
            trust["config_digest"],
        },
        "migration Pod runtime image",
    )
    runtime_id = normalize_runtime_image_id(runtime.get("imageID"), trust)
    marker = f"mission-spine-flyway-target={flyway_target} status=validated"
    if (
        not isinstance(logs, str)
        or not logs.endswith("\n")
        or "\r" in logs
        or len(logs.encode("utf-8")) > 64 * 1024
        or logs.splitlines().count(marker) != 1
    ):
        raise ValueError("migration validated target marker is missing or duplicated")
    return {
        "schema_version": 1,
        "status": "passed",
        "migration_commit": migration_commit,
        "observed_commit": observed_commit,
        "application_observed_revision": observed_commit,
        "application_applied_revision": application_applied_commit,
        "service_account_projection_admitted": service_account_projection_admitted,
        "namespace": NAMESPACE,
        "application": "devpath-migration",
        "job": expected_name,
        "job_uid": uid,
        "job_resource_version": resource_version,
        "job_generation": 1,
        "pod_uid": pod_uid,
        "source_sha": trust["source_sha"],
        "image_repository": trust["image_repository"],
        "root_digest": trust["root_digest"],
        "manifest_digest": trust["manifest_digest"],
        "config_digest": trust["config_digest"],
        "runtime_image_digest": runtime_id["digest"],
        "runtime_image_form": runtime_id["form"],
        "flyway_target": flyway_target,
        "required_migration": required_migration,
        "complete": True,
        "succeeded": 1,
        "failed": 0,
        "active": 0,
        "reconcile_mode": "digest-derived-job-name",
    }
