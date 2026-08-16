#!/usr/bin/env python3
"""Validate and safely resolve immutable Mission Spine release manifests.

This intentionally uses only the Python standard library.  GitHub release jobs
must fail closed even when a package index is unavailable.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable
from urllib.parse import urlsplit


RELEASE_ID = re.compile(r"^ms-[0-9]{8}-[a-z0-9][a-z0-9-]{2,40}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{1,127}$")
CF_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
WORKFLOW_PATH = re.compile(r"^\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml$")

PRODUCER_WORKFLOWS = {
    "home-dist": ".github/workflows/mission-spine-home-dist.yml",
    "privacy-approval": ".github/workflows/mission-spine-privacy-approval.yml",
    "ai-release-eval": ".github/workflows/mission-spine-release-eval.yml",
    "journey-activation": ".github/workflows/mission-spine-validate.yml",
    "journey-contextual-practice": ".github/workflows/mission-spine-validate.yml",
    "frontend-visual": ".github/workflows/et13-evidence.yml",
    "home-visual": ".github/workflows/mission-spine-validate.yml",
    "frontend-automated-a11y": ".github/workflows/et13-evidence.yml",
    "home-axe-browser-a11y": ".github/workflows/mission-spine-validate.yml",
    "manual-nvda": ".github/workflows/mission-spine-manual-at-evidence.yml",
    "manual-voiceover": ".github/workflows/mission-spine-manual-at-evidence.yml",
    "manual-talkback": ".github/workflows/mission-spine-manual-at-evidence.yml",
}

QUALITY_EVIDENCE = {
    "frontend-visual": "frontend_visual",
    "home-visual": "home_visual",
    "frontend-automated-a11y": "frontend_automated_a11y",
    "home-axe-browser-a11y": "home_axe_browser_a11y",
    "manual-nvda": "manual_nvda",
    "manual-voiceover": "manual_voiceover",
    "manual-talkback": "manual_talkback",
}
QUALITY_EVIDENCE_LABELS = tuple(QUALITY_EVIDENCE)
QUALITY_EVIDENCE_KEYS = tuple(QUALITY_EVIDENCE.values())

QUALITY_EVIDENCE_FILES = {
    "frontend-visual": "evidence.json",
    "home-visual": "visual-evidence.v2.json",
    "frontend-automated-a11y": "evidence.json",
    "home-axe-browser-a11y": "a11y-evidence.v2.json",
    "manual-nvda": "evidence.json",
    "manual-voiceover": "evidence.json",
    "manual-talkback": "evidence.json",
}

QUALITY_EVIDENCE_EVENTS = {
    label: "workflow_dispatch" for label in QUALITY_EVIDENCE_LABELS
}

FRONTEND_EVIDENCE_FILES = {
    "frontend-visual": (
        "evidence.json",
        "evidence/et13/generated/visual-cases.v1.json",
        "artifacts/et13/provenance.v1.json",
        "artifacts/et13/visual-manifest.v1.json",
    ),
    "frontend-automated-a11y": (
        "evidence.json",
        "evidence/et13/generated/a11y-cases.v1.json",
        "artifacts/et13/provenance.v1.json",
        "artifacts/et13/a11y-manifest.v1.json",
    ),
}

FRONTEND_FIXTURE_IDS = (
    "web-today-available",
    "web-path-current-week",
    "web-content-reading",
    "web-workspace-idle",
    "web-review-loaded",
    "web-mentor-context-preview",
    "admin-kpi-dashboard",
    "admin-support-long-wire",
    "mobile-today-available",
    "mobile-content-reading",
    "dp-design-mission-ledger",
    "dp-design-context-payload-preview",
)

FRONTEND_PROJECTION_CONTRACT_VERSION = "leva.et13.projection-contract.v1"
FRONTEND_PROJECTION_CONTRACT_SHA256 = (
    "c66d08b6425628a06b27d07e08d648cfb3568d9db7c8d8aca2371172ccf4bde3"
)
FRONTEND_PROJECTION_MATRIX = [
    {
        "fixture_id": "web-today-available",
        "capture_scope": "body_projection",
        "source_widget": "TodayMissionSection",
        "substitutions": [
            "controller/provider state replaced by approved deterministic fixture"
        ],
    },
    {
        "fixture_id": "web-path-current-week",
        "capture_scope": "body_projection",
        "source_widget": "MissionPathPlanView",
        "substitutions": [
            "controller/provider state replaced by approved deterministic fixture"
        ],
    },
    {
        "fixture_id": "web-content-reading",
        "capture_scope": "body_projection",
        "source_widget": "WebContentProjection",
        "substitutions": [
            "AdSense slot replaced by explicit offline blocked-network evidence slot"
        ],
    },
    {
        "fixture_id": "web-workspace-idle",
        "capture_scope": "body_projection",
        "source_widget": "SandboxLayout",
        "substitutions": [
            "controller state replaced by approved deterministic fixture",
            "Monaco callbacks disabled except production readiness",
        ],
    },
    {
        "fixture_id": "web-review-loaded",
        "capture_scope": "component_projection",
        "source_widget": "WebReviewProjection",
        "substitutions": [
            "controller state replaced by approved deterministic fixture"
        ],
    },
    {
        "fixture_id": "web-mentor-context-preview",
        "capture_scope": "body_projection",
        "source_widget": "WebMentorContextProjection",
        "substitutions": [
            "controller/provider state replaced by approved deterministic fixture"
        ],
    },
    {
        "fixture_id": "admin-kpi-dashboard",
        "capture_scope": "body_projection",
        "source_widget": "AdminKpiDashboardProjection",
        "substitutions": [
            "controller/provider state replaced by approved deterministic fixture"
        ],
    },
    {
        "fixture_id": "admin-support-long-wire",
        "capture_scope": "component_projection",
        "source_widget": "AdminSupportDetailProjection",
        "substitutions": [
            "live provider and dialog shell replaced by deterministic AlertDialog host"
        ],
    },
    {
        "fixture_id": "mobile-today-available",
        "capture_scope": "body_projection",
        "source_widget": "MobileTodayProjection",
        "substitutions": [
            "native AppBar and route shell omitted",
            "controller/provider state replaced by approved deterministic fixture",
        ],
    },
    {
        "fixture_id": "mobile-content-reading",
        "capture_scope": "body_projection",
        "source_widget": "MobileContentProjection",
        "substitutions": [
            "native AppBar and route shell omitted",
            "periodic dwell timer frozen",
            "controller/provider state replaced by approved deterministic fixture",
        ],
    },
    {
        "fixture_id": "dp-design-mission-ledger",
        "capture_scope": "component_projection",
        "source_widget": "DpEt13MissionLedgerFixture",
        "substitutions": ["hosted by the Flutter Web production distribution"],
    },
    {
        "fixture_id": "dp-design-context-payload-preview",
        "capture_scope": "component_projection",
        "source_widget": "DpEt13ContextPayloadPreviewFixture",
        "substitutions": ["hosted by the Flutter Web production distribution"],
    },
]

FRONTEND_CATALOG_CONTRACTS = {
    "frontend-visual": {
        "path": "evidence/et13/generated/visual-cases.v1.json",
        "case_catalog_version": "leva.et13.catalog.v1",
        "case_catalog_schema_version": "leva.et13.visual-cases.v1",
        "projection_contract_sha256": FRONTEND_PROJECTION_CONTRACT_SHA256,
        "case_count": 96,
        "surface_case_counts": {"web": 48, "admin": 16, "mobile": 16, "dp_design": 16},
        "capture_surface": "flutter_web_release_projection",
        "device_evidence": False,
        "evidence_mode": "release_ready",
    },
    "frontend-automated-a11y": {
        "path": "evidence/et13/generated/a11y-cases.v1.json",
        "case_catalog_version": "leva.et13.catalog.v1",
        "case_catalog_schema_version": "leva.et13.a11y-cases.v1",
        "projection_contract_sha256": FRONTEND_PROJECTION_CONTRACT_SHA256,
        "case_count": 24,
        "surface_case_counts": {"web": 12, "admin": 4, "mobile": 4, "dp_design": 4},
        "capture_surface": "flutter_web_release_projection",
        "device_evidence": False,
        "evidence_mode": "release_ready",
    },
}

SIGNED_MOBILE_BINDING_KEYS = {
    "schema_version",
    "repository",
    "source_sha",
    "event",
    "workflow_path",
    "workflow_sha256",
    "workflow_run_id",
    "run_attempt",
    "artifact_id",
    "artifact_name",
    "artifact_archive_sha256",
    "build_provenance_file",
    "build_provenance_sha256",
    "signed_apk_file",
    "signed_apk_sha256",
    "signed_ipa_file",
    "signed_ipa_sha256",
}
SIGNED_MOBILE_BINDING_VERSION = "leva.mission-spine.signed-mobile-build-binding.v1"
SIGNED_MOBILE_WORKFLOW = ".github/workflows/mission-spine-signed-mobile-build.yml"
SIGNED_MOBILE_FILES = {
    "build_provenance_file": "build-provenance.v1.json",
    "signed_apk_file": "mobile/android/leva-release.apk",
    "signed_ipa_file": "mobile/ios/leva-release.ipa",
}

MANUAL_CATALOG_CONTRACTS = {
    "manual-nvda": {
        "path": "tool/release-evidence/catalogs/manual-nvda.v1.json",
        "provenance_path": "tool/release-evidence/provenance/manual-nvda.v1.json",
        "case_count": 2,
        "assistive_technology": "NVDA+Chromium",
        "surface": "web",
        "case_ids": (
            "nvda-web-today-mission-spine",
            "nvda-web-next-action-navigation",
        ),
        "required_platform": "windows_physical_host",
        "required_client": "chromium",
        "required_artifact": "exact_source_web_release_build",
    },
    "manual-voiceover": {
        "path": "tool/release-evidence/catalogs/manual-voiceover.v1.json",
        "provenance_path": "tool/release-evidence/provenance/manual-voiceover.v1.json",
        "case_count": 4,
        "assistive_technology": "VoiceOver+Safari+iOS",
        "surface": "ios",
        "case_ids": (
            "voiceover-ios-today-mission-spine",
            "voiceover-ios-next-action-navigation",
            "voiceover-ios-content-reading",
            "voiceover-ios-offline-status",
        ),
        "required_platform": "ios_physical_device",
        "required_client": "native_flutter_ios",
        "required_artifact": "candidate_signed_ipa",
    },
    "manual-talkback": {
        "path": "tool/release-evidence/catalogs/manual-talkback.v1.json",
        "provenance_path": "tool/release-evidence/provenance/manual-talkback.v1.json",
        "case_count": 4,
        "assistive_technology": "TalkBack+Android",
        "surface": "android",
        "case_ids": (
            "talkback-android-today-mission-spine",
            "talkback-android-next-action-navigation",
            "talkback-android-content-reading",
            "talkback-android-offline-status",
        ),
        "required_platform": "android_physical_device",
        "required_client": "native_flutter_android",
        "required_artifact": "candidate_signed_apk",
    },
}

REQUIRED_SERVICES = {
    "devpath-admin",
    "devpath-ai-svc",
    "devpath-community-svc",
    "devpath-gateway",
    "devpath-lcs-svc",
    "devpath-learning-svc",
    "devpath-notification-svc",
    "devpath-platform-svc",
    "devpath-sandbox-svc",
}

PRODUCTION_ORDER = [
    "shared-migration",
    "additive-services",
    "frontend-mission-off",
    "compatibility-smoke",
    "frontend-mission-on",
    "canary",
    "landing-last",
]

ROLLBACK_ORDER = [
    "landing-prior",
    "frontend-mission-off",
    "frontend-prior",
    "retain-additive-services-and-schema",
]

CANDIDATE_TOP_LEVEL = {
    "$schema",
    "schema_version",
    "document_type",
    "release_id",
    "created_at",
    "gitops",
    "services",
    "shared_migration",
    "frontend",
    "home",
    "analytics_privacy",
    "ai_release_eval_config",
    "environments",
    "journey_harness",
    "quality_evidence_inputs",
    "rollout",
}

RELEASE_TOP_LEVEL = {
    "$schema",
    "schema_version",
    "document_type",
    "release_id",
    "created_at",
    "candidate_spec",
    "validation_attestation",
    "home_dist_artifact",
    "analytics_privacy_approval",
    "ai_release_eval",
    "journeys",
    "quality_evidence",
}

FORBIDDEN_KEYS = {
    "answers",
    "code",
    "diagnostic_answers",
    "email",
    "error",
    "error_text",
    "guest_token",
    "output",
    "prompt",
    "prompt_text",
    "raw",
    "raw_code",
    "raw_error",
    "raw_output",
    "snapshot",
    "snapshot_content",
    "token",
    "user_content",
}


def _fail(path: str, message: str) -> None:
    raise ValueError(f"{path}: {message}")


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    return value


def _exact_keys(value: dict[str, Any], keys: Iterable[str], path: str) -> None:
    expected = set(keys)
    missing = expected - value.keys()
    extra = value.keys() - expected
    if missing:
        _fail(path, f"missing fields: {', '.join(sorted(missing))}")
    if extra:
        _fail(path, f"unknown fields: {', '.join(sorted(extra))}")


def _string(value: Any, path: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        _fail(path, "must be a non-empty string")
    if "\n" in value or "\r" in value:
        _fail(path, "multiline values are forbidden")
    if pattern is not None and pattern.fullmatch(value) is None:
        _fail(path, "has an invalid format")
    return value


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, "must be a number")
    return float(value)


def _positive_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail(path, "must be a positive integer")
    return value


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _timestamp(value: Any, path: str) -> str:
    text = _string(value, path)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        _fail(path, f"must be an ISO-8601 timestamp ({exc})")
    if not text.endswith("Z"):
        _fail(path, "must use UTC Z notation")
    return text


def _https_url(value: Any, path: str) -> str:
    text = _string(value, path)
    parts = urlsplit(text)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
        _fail(path, "must be an absolute credential-free HTTPS URL")
    if parts.query or parts.fragment or parts.path not in {"", "/"}:
        _fail(path, "origins cannot contain paths, query strings, or fragments")
    return text.rstrip("/")


def _require_artifact_identity(
    artifact: dict[str, Any],
    path: str,
    repository: str,
    artifact_name: str,
    workflow_path: str,
    head_sha: str,
) -> None:
    if artifact["repository"] != repository:
        _fail(f"{path}.repository", f"must be {repository}")
    if artifact["artifact_name"] != artifact_name:
        _fail(f"{path}.artifact_name", f"must be {artifact_name}")
    if artifact["workflow_path"] != workflow_path:
        _fail(f"{path}.workflow_path", f"must be {workflow_path}")
    if artifact["head_sha"] != head_sha:
        _fail(f"{path}.head_sha", "must bind the exact producer source SHA")


def quality_artifact_name(
    label: str,
    release_id: str,
    run_attempt: int | None = None,
    workflow_run_id: int | None = None,
) -> str:
    if label in FRONTEND_CATALOG_CONTRACTS:
        for value in (workflow_run_id, run_attempt):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(
                    "Frontend quality evidence requires exact positive run ID and attempt"
                )
        return f"{release_id}-{label}-run-{workflow_run_id}-attempt-{run_attempt}"
    if label in {"home-visual", "home-axe-browser-a11y"}:
        if isinstance(run_attempt, bool) or not isinstance(run_attempt, int) or run_attempt <= 0:
            raise ValueError("Home quality evidence requires an exact positive run attempt")
        return f"{release_id}-home-visual-a11y-attempt-{run_attempt}"
    if label in MANUAL_CATALOG_CONTRACTS:
        for value in (workflow_run_id, run_attempt):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(
                    "Manual quality evidence requires exact positive run ID and attempt"
                )
        return f"{release_id}-{label}-run-{workflow_run_id}-attempt-{run_attempt}"
    raise ValueError(f"unknown quality evidence label: {label}")


def home_dist_artifact_name(
    release_id: str,
    workflow_run_id: int,
    run_attempt: int,
) -> str:
    for value in (workflow_run_id, run_attempt):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("Home dist requires exact positive run ID and attempt")
    return f"{release_id}-home-dist-run-{workflow_run_id}-attempt-{run_attempt}"


def ai_eval_artifact_name(
    release_id: str,
    workflow_run_id: int,
    run_attempt: int,
) -> str:
    for value in (workflow_run_id, run_attempt):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("AI release evaluation requires exact positive run ID and attempt")
    return f"{release_id}-ai-eval-run-{workflow_run_id}-attempt-{run_attempt}"


def privacy_approval_artifact_name(
    release_id: str,
    workflow_run_id: int,
    run_attempt: int,
) -> str:
    for value in (workflow_run_id, run_attempt):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("Privacy approval requires exact positive run ID and attempt")
    return (
        f"{release_id}-privacy-approval-run-{workflow_run_id}-attempt-{run_attempt}"
    )


def quality_source(candidate: dict[str, Any], label: str) -> tuple[str, str]:
    if label in {"home-visual", "home-axe-browser-a11y"}:
        return candidate["home"]["repository"], candidate["home"]["source_sha"]
    return candidate["frontend"]["repository"], candidate["frontend"]["source_sha"]


def _validate_sanitized(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized in FORBIDDEN_KEYS:
                _fail(f"{path}.{key}", f"forbidden unsanitized field {key}")
            _validate_sanitized(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _validate_sanitized(nested, f"{path}[{index}]")
    elif isinstance(value, str):
        if "\n" in value or "\r" in value:
            _fail(path, "multiline values are forbidden")
        lowered = value.lower()
        if lowered.startswith(("data:", "file:", "javascript:")):
            _fail(path, "embedded or local payload URIs are forbidden")


def _validate_artifact(
    value: Any,
    path: str,
    release_id: str,
    candidate_sha256: str,
    *,
    expected_event: str = "workflow_dispatch",
    expected_evidence_file: str = "evidence.json",
    require_release_scope: bool = True,
) -> None:
    obj = _object(value, path)
    _exact_keys(
        obj,
        {
            "candidate_spec_sha256",
            "repository",
            "event",
            "head_sha",
            "run_attempt",
            "workflow_path",
            "workflow_sha256",
            "workflow_run_id",
            "artifact_id",
            "artifact_name",
            "evidence_file",
            "sha256",
        },
        path,
    )
    bound_sha = _string(obj["candidate_spec_sha256"], f"{path}.candidate_spec_sha256", SHA64)
    if bound_sha != candidate_sha256:
        _fail(f"{path}.candidate_spec_sha256", "must exactly bind the immutable candidate-spec bytes")
    _string(obj["repository"], f"{path}.repository", REPOSITORY)
    if obj["event"] != expected_event:
        _fail(f"{path}.event", f"must be {expected_event}")
    _string(obj["head_sha"], f"{path}.head_sha", SHA40)
    _positive_int(obj["run_attempt"], f"{path}.run_attempt")
    _string(obj["workflow_path"], f"{path}.workflow_path", WORKFLOW_PATH)
    _string(obj["workflow_sha256"], f"{path}.workflow_sha256", SHA64)
    _positive_int(obj["workflow_run_id"], f"{path}.workflow_run_id")
    _positive_int(obj["artifact_id"], f"{path}.artifact_id")
    name = _string(obj["artifact_name"], f"{path}.artifact_name", SAFE_IDENTIFIER)
    if require_release_scope and not name.startswith(f"{release_id}-"):
        _fail(f"{path}.artifact_name", "must be release-id scoped")
    if obj["evidence_file"] != expected_evidence_file:
        _fail(f"{path}.evidence_file", f"must be exactly {expected_evidence_file}")
    _string(obj["sha256"], f"{path}.sha256", SHA64)


def _validate_home_artifact(
    value: Any,
    path: str,
    release_id: str,
    candidate_sha256: str,
    expected_dist_sha256: str,
) -> None:
    obj = _object(value, path)
    _exact_keys(
        obj,
        {
            "candidate_spec_sha256",
            "repository",
            "event",
            "head_sha",
            "run_attempt",
            "workflow_path",
            "workflow_sha256",
            "workflow_run_id",
            "artifact_id",
            "artifact_name",
            "evidence_file",
            "sha256",
            "payload_file",
            "payload_sha256",
        },
        path,
    )
    base = {key: value for key, value in obj.items() if key not in {"payload_file", "payload_sha256"}}
    _validate_artifact(base, path, release_id, candidate_sha256)
    if obj["payload_file"] != "dist.tar.gz":
        _fail(f"{path}.payload_file", "must be exactly dist.tar.gz")
    payload_hash = _string(obj["payload_sha256"], f"{path}.payload_sha256", SHA64)
    if payload_hash != expected_dist_sha256:
        _fail(f"{path}.payload_sha256", "must exactly bind candidate-spec home.dist_sha256")


def _validate_component(value: Any, path: str, expected_name: str) -> None:
    obj = _object(value, path)
    _exact_keys(obj, {"repository", "source_sha", "image_repository", "image_digest"}, path)
    repository = _string(obj["repository"], f"{path}.repository", REPOSITORY)
    expected_repository = (
        "DevPathAi/devpath-frontend"
        if expected_name == "devpath-admin"
        else f"DevPathAi/{expected_name}"
    )
    if repository != expected_repository:
        _fail(f"{path}.repository", f"must be {expected_repository}")
    _string(obj["source_sha"], f"{path}.source_sha", SHA40)
    image_repository = _string(obj["image_repository"], f"{path}.image_repository")
    expected_image = f"ghcr.io/devpathai/{expected_name}"
    if image_repository != expected_image:
        _fail(f"{path}.image_repository", f"must be {expected_image}")
    _string(obj["image_digest"], f"{path}.image_digest", DIGEST)


def _validate_environment(value: Any, path: str) -> None:
    obj = _object(value, path)
    _exact_keys(
        obj,
        {
            "github_environment",
            "kubernetes_context",
            "namespace",
            "web_deployment",
            "web_container",
            "web_origin",
            "landing_origin",
        },
        path,
    )
    for key in ("github_environment", "kubernetes_context", "namespace", "web_deployment", "web_container"):
        _string(obj[key], f"{path}.{key}", SAFE_IDENTIFIER)
    _https_url(obj["web_origin"], f"{path}.web_origin")
    _https_url(obj["landing_origin"], f"{path}.landing_origin")


def _validate_journey_harness(value: Any, path: str, environments: dict[str, Any]) -> None:
    obj = _object(value, path)
    _exact_keys(
        obj,
        {
            "landing_origin",
            "app_origin",
            "control_origin",
            "oauth_origin",
            "analytics_spy_origin",
            "dns_overrides",
        },
        path,
    )
    origins = {
        field: _https_url(obj[field], f"{path}.{field}")
        for field in ("landing_origin", "app_origin", "control_origin", "oauth_origin", "analytics_spy_origin")
    }
    if origins["landing_origin"] != environments["production"]["landing_origin"]:
        _fail(f"{path}.landing_origin", "must exactly match the canonical production Landing origin")
    if origins["app_origin"] != environments["production"]["web_origin"]:
        _fail(f"{path}.app_origin", "must exactly match the canonical production app origin")
    if len(set(origins.values())) != len(origins):
        _fail(path, "journey harness origins must be distinct")

    overrides = obj["dns_overrides"]
    if not isinstance(overrides, list) or len(overrides) != 2:
        _fail(f"{path}.dns_overrides", "must bind exactly the Landing and app DNS overrides")
    actual_hosts: set[str] = set()
    for index, override in enumerate(overrides):
        item_path = f"{path}.dns_overrides[{index}]"
        item = _object(override, item_path)
        _exact_keys(item, {"hostname", "address"}, item_path)
        hostname = _string(item["hostname"], f"{item_path}.hostname", re.compile(r"^[a-z0-9.-]+$"))
        try:
            ipaddress.ip_address(_string(item["address"], f"{item_path}.address"))
        except ValueError as exc:
            _fail(f"{item_path}.address", f"must be an IP literal ({exc})")
        if hostname in actual_hosts:
            _fail(f"{path}.dns_overrides", "hostnames must be unique")
        actual_hosts.add(hostname)
    expected_hosts = {
        urlsplit(origins["landing_origin"]).hostname,
        urlsplit(origins["app_origin"]).hostname,
    }
    if actual_hosts != expected_hosts:
        _fail(f"{path}.dns_overrides", "must cover exactly the canonical Landing and app hostnames")


def _validate_quality_evidence_inputs(value: Any, candidate: dict[str, Any]) -> None:
    path = "$.quality_evidence_inputs"
    obj = _object(value, path)
    _exact_keys(
        obj,
        {"catalogs", "frontend_projection_contract", "mobile_test_artifacts"},
        path,
    )

    projection_path = f"{path}.frontend_projection_contract"
    projection = _object(obj["frontend_projection_contract"], projection_path)
    _exact_keys(
        projection,
        {"schema_version", "projection_contract_sha256", "projection_matrix"},
        projection_path,
    )
    if projection["schema_version"] != FRONTEND_PROJECTION_CONTRACT_VERSION:
        _fail(projection_path, "projection contract schema version is not approved")
    projection_sha = _string(
        projection["projection_contract_sha256"],
        f"{projection_path}.projection_contract_sha256",
        SHA64,
    )
    matrix = projection["projection_matrix"]
    if matrix != FRONTEND_PROJECTION_MATRIX:
        _fail(projection_path, "projection contract must contain the exact ordered 12-row matrix")
    if (
        projection_sha != FRONTEND_PROJECTION_CONTRACT_SHA256
        or _canonical_sha256(matrix) != projection_sha
    ):
        _fail(projection_path, "projection contract canonical SHA-256 mismatch")

    catalogs = _object(obj["catalogs"], f"{path}.catalogs")
    _exact_keys(catalogs, QUALITY_EVIDENCE_LABELS, f"{path}.catalogs")
    for label in QUALITY_EVIDENCE_LABELS:
        catalog_path = f"{path}.catalogs.{label}"
        catalog = _object(catalogs[label], catalog_path)
        fields = {
            "repository",
            "source_sha",
            "path",
            "sha256",
            "case_count",
        }
        if label in FRONTEND_CATALOG_CONTRACTS:
            fields |= {
                "case_catalog_version",
                "case_catalog_schema_version",
                "projection_contract_sha256",
                "capture_surface",
                "device_evidence",
                "evidence_mode",
                "fixture_ids",
                "input_provenance_sha256",
                "input_provenance_file_sha256",
                "surface_case_counts",
            }
            if label == "frontend-visual":
                fields |= {
                    "baseline_status",
                    "baseline_set_sha256",
                    "baseline_approval_sha256",
                }
        else:
            fields.add("provenance_sha256")
        if label in {"home-visual", "home-axe-browser-a11y"}:
            fields |= {
                "rendered_product_sha",
                "rendered_product_tree_sha256",
                "font_manifest_sha256",
            }
        _exact_keys(
            catalog,
            fields,
            catalog_path,
        )
        expected_repository, expected_source = quality_source(candidate, label)
        if catalog["repository"] != expected_repository:
            _fail(f"{catalog_path}.repository", f"must be {expected_repository}")
        if catalog["source_sha"] != expected_source:
            _fail(f"{catalog_path}.source_sha", "must bind the exact candidate producer source")
        relative = Path(_string(catalog["path"], f"{catalog_path}.path"))
        if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".json":
            _fail(f"{catalog_path}.path", "must be a safe repository-relative JSON path")
        if label in FRONTEND_CATALOG_CONTRACTS:
            expected = FRONTEND_CATALOG_CONTRACTS[label]
            exact = {
                "path": catalog["path"],
                "case_catalog_version": catalog["case_catalog_version"],
                "case_catalog_schema_version": catalog["case_catalog_schema_version"],
                "projection_contract_sha256": catalog["projection_contract_sha256"],
                "capture_surface": catalog["capture_surface"],
                "device_evidence": catalog["device_evidence"],
                "evidence_mode": catalog["evidence_mode"],
                "fixture_ids": catalog["fixture_ids"],
                "case_count": catalog["case_count"],
                "surface_case_counts": catalog["surface_case_counts"],
            }
            expected_exact = {
                "path": expected["path"],
                "case_catalog_version": expected["case_catalog_version"],
                "case_catalog_schema_version": expected["case_catalog_schema_version"],
                "projection_contract_sha256": projection_sha,
                "capture_surface": expected["capture_surface"],
                "device_evidence": expected["device_evidence"],
                "evidence_mode": expected["evidence_mode"],
                "fixture_ids": list(FRONTEND_FIXTURE_IDS),
                "case_count": expected["case_count"],
                "surface_case_counts": expected["surface_case_counts"],
            }
            if exact != expected_exact:
                _fail(catalog_path, "must match the exact approved frontend catalog contract")
            if catalog["device_evidence"] is not False:
                _fail(
                    f"{catalog_path}.device_evidence",
                    "must be boolean false for Flutter-web projection evidence",
                )
            surface_counts = _object(
                catalog["surface_case_counts"],
                f"{catalog_path}.surface_case_counts",
            )
            _exact_keys(
                surface_counts,
                {"web", "admin", "mobile", "dp_design"},
                f"{catalog_path}.surface_case_counts",
            )
            for surface, count in surface_counts.items():
                _positive_int(
                    count,
                    f"{catalog_path}.surface_case_counts.{surface}",
                )
            if label == "frontend-visual" and catalog.get("baseline_status") != "approved":
                _fail(
                    catalog_path,
                    "must be release-ready with an approved baseline",
                )
        if (
            label in {"home-visual", "home-axe-browser-a11y"}
            and catalog["path"] != "e2e/visual/case-catalog.v2.json"
        ):
            _fail(f"{catalog_path}.path", "must bind the canonical Home v2 case catalog")
        _string(catalog["sha256"], f"{catalog_path}.sha256", SHA64)
        _positive_int(catalog["case_count"], f"{catalog_path}.case_count")
        if label in FRONTEND_CATALOG_CONTRACTS:
            for field in (
                "projection_contract_sha256",
                "input_provenance_sha256",
                "input_provenance_file_sha256",
            ):
                _string(catalog[field], f"{catalog_path}.{field}", SHA64)
            if label == "frontend-visual":
                for field in ("baseline_set_sha256", "baseline_approval_sha256"):
                    _string(catalog[field], f"{catalog_path}.{field}", SHA64)
        else:
            _string(catalog["provenance_sha256"], f"{catalog_path}.provenance_sha256", SHA64)
            if label in MANUAL_CATALOG_CONTRACTS:
                expected_manual = MANUAL_CATALOG_CONTRACTS[label]
                if catalog["path"] != expected_manual["path"]:
                    _fail(f"{catalog_path}.path", "must bind the authoritative manual catalog")
                if catalog["case_count"] != expected_manual["case_count"]:
                    _fail(
                        f"{catalog_path}.case_count",
                        "must match the authoritative manual catalog count",
                    )
                if catalog["sha256"] == catalog["provenance_sha256"]:
                    _fail(
                        catalog_path,
                        "manual catalog and static test-provenance hashes must be distinct",
                    )
        if label in {"home-visual", "home-axe-browser-a11y"}:
            _string(catalog["rendered_product_sha"], f"{catalog_path}.rendered_product_sha", SHA40)
            _string(
                catalog["rendered_product_tree_sha256"],
                f"{catalog_path}.rendered_product_tree_sha256",
                SHA64,
            )
            _string(catalog["font_manifest_sha256"], f"{catalog_path}.font_manifest_sha256", SHA64)

    frontend_digests = []
    for label in FRONTEND_CATALOG_CONTRACTS:
        catalog = catalogs[label]
        frontend_digests.extend([
            catalog["sha256"],
            catalog["input_provenance_sha256"],
            catalog["input_provenance_file_sha256"],
        ])
    frontend_digests.extend([
        projection_sha,
        catalogs["frontend-visual"]["baseline_set_sha256"],
        catalogs["frontend-visual"]["baseline_approval_sha256"],
    ])
    if len(frontend_digests) != len(set(frontend_digests)):
        _fail(
            f"{path}.catalogs",
            "frontend generated, input-provenance, and baseline digests must be distinct",
        )

    manual_catalog_hashes = [
        catalogs[label]["sha256"] for label in MANUAL_CATALOG_CONTRACTS
    ]
    manual_provenance_hashes = [
        catalogs[label]["provenance_sha256"] for label in MANUAL_CATALOG_CONTRACTS
    ]
    manual_input_hashes = manual_catalog_hashes + manual_provenance_hashes
    if len(manual_input_hashes) != len(set(manual_input_hashes)):
        _fail(
            f"{path}.catalogs",
            "manual catalog and static test-provenance raw hashes must all be distinct",
        )

    home_visual = catalogs["home-visual"]
    home_a11y = catalogs["home-axe-browser-a11y"]
    if (
        home_visual["path"] != home_a11y["path"]
        or home_visual["sha256"] != home_a11y["sha256"]
        or home_visual["provenance_sha256"] != home_a11y["provenance_sha256"]
        or home_visual["rendered_product_sha"] != home_a11y["rendered_product_sha"]
        or home_visual["rendered_product_tree_sha256"]
        != home_a11y["rendered_product_tree_sha256"]
        or home_visual["font_manifest_sha256"] != home_a11y["font_manifest_sha256"]
    ):
        _fail(
            f"{path}.catalogs",
            "Home visual and axe/browser evidence must bind the same combined catalog and render provenance",
        )

    mobile_path = f"{path}.mobile_test_artifacts"
    mobile = _object(obj["mobile_test_artifacts"], mobile_path)
    _exact_keys(mobile, SIGNED_MOBILE_BINDING_KEYS, mobile_path)
    if mobile["schema_version"] != SIGNED_MOBILE_BINDING_VERSION:
        _fail(f"{mobile_path}.schema_version", "signed-mobile binding schema is not approved")
    if mobile["repository"] != candidate["frontend"]["repository"]:
        _fail(f"{mobile_path}.repository", "must be DevPathAi/devpath-frontend")
    if mobile["source_sha"] != candidate["frontend"]["source_sha"]:
        _fail(f"{mobile_path}.source_sha", "must bind the exact frontend source")
    if mobile["event"] != "workflow_dispatch":
        _fail(f"{mobile_path}.event", "must be workflow_dispatch")
    if mobile["workflow_path"] != SIGNED_MOBILE_WORKFLOW:
        _fail(f"{mobile_path}.workflow_path", f"must be {SIGNED_MOBILE_WORKFLOW}")
    for field in ("workflow_run_id", "run_attempt", "artifact_id"):
        _positive_int(mobile[field], f"{mobile_path}.{field}")
    if mobile["run_attempt"] != 1:
        _fail(
            f"{mobile_path}.run_attempt",
            "protected signing approval is sealable only on attempt 1; retry with a fresh dispatch",
        )
    expected_name = (
        f"{candidate['release_id']}-signed-mobile-build-run-"
        f"{mobile['workflow_run_id']}-attempt-{mobile['run_attempt']}"
    )
    if mobile["artifact_name"] != expected_name:
        _fail(f"{mobile_path}.artifact_name", f"must be {expected_name}")
    for field, expected_file in SIGNED_MOBILE_FILES.items():
        if mobile[field] != expected_file:
            _fail(f"{mobile_path}.{field}", f"must be {expected_file}")
    for field in (
        "workflow_sha256",
        "artifact_archive_sha256",
        "build_provenance_sha256",
        "signed_apk_sha256",
        "signed_ipa_sha256",
    ):
        _string(mobile[field], f"{mobile_path}.{field}", SHA64)
    mobile_hashes = {
        mobile["build_provenance_sha256"],
        mobile["signed_apk_sha256"],
        mobile["signed_ipa_sha256"],
    }
    if len(mobile_hashes) != 3:
        _fail(mobile_path, "build provenance, signed APK, and signed IPA hashes must be distinct")


def validate_candidate_spec(data: Any, source: Path | None = None) -> dict[str, Any]:
    """Validate an immutable pre-execution candidate specification."""
    root = _object(data, "$")
    _validate_sanitized(root)
    _exact_keys(root, CANDIDATE_TOP_LEVEL, "$")

    if root["schema_version"] != 1:
        _fail("$.schema_version", "must be 1")
    if root["document_type"] != "candidate-spec":
        _fail("$.document_type", "must be candidate-spec")
    schema_ref = _string(root["$schema"], "$.$schema")
    if schema_ref != "../schema-v1.json" and not schema_ref.endswith("release-manifests/schema-v1.json"):
        _fail("$.$schema", "must reference release-manifests/schema-v1.json")
    release_id = _string(root["release_id"], "$.release_id", RELEASE_ID)
    _timestamp(root["created_at"], "$.created_at")

    gitops = _object(root["gitops"], "$.gitops")
    _exact_keys(
        gitops,
        {"repository", "base_sha", "base_web_tag", "base_web_digest", "web_kustomization"},
        "$.gitops",
    )
    if gitops["repository"] != "DevPathAi/devpath-gitops":
        _fail("$.gitops.repository", "must be DevPathAi/devpath-gitops")
    _string(gitops["base_sha"], "$.gitops.base_sha", SHA40)
    _string(gitops["base_web_tag"], "$.gitops.base_web_tag", SHA40)
    _string(gitops["base_web_digest"], "$.gitops.base_web_digest", DIGEST)
    if gitops["web_kustomization"] != "apps/devpath-web/base/kustomization.yaml":
        _fail("$.gitops.web_kustomization", "must target only the web base kustomization")

    services = _object(root["services"], "$.services")
    if set(services) != REQUIRED_SERVICES:
        missing = REQUIRED_SERVICES - services.keys()
        extra = services.keys() - REQUIRED_SERVICES
        _fail("$.services", f"must bind the exact application service set; missing={sorted(missing)}, extra={sorted(extra)}")
    for name, component in services.items():
        _validate_component(component, f"$.services.{name}", name)

    migration = _object(root["shared_migration"], "$.shared_migration")
    _exact_keys(
        migration,
        {
            "repository", "source_sha", "shared_version", "shared_jar_sha256",
            "image_repository", "image_digest", "flyway_target", "required_migration",
            "rollback_policy",
        },
        "$.shared_migration",
    )
    if migration["repository"] != "DevPathAi/devpath-shared":
        _fail("$.shared_migration.repository", "must be DevPathAi/devpath-shared")
    _string(migration["source_sha"], "$.shared_migration.source_sha", SHA40)
    if migration["shared_version"] != "0.0.1-et9.20260816":
        _fail("$.shared_migration.shared_version", "must bind the immutable ET9 Shared version")
    shared_jar_hash = _string(
        migration["shared_jar_sha256"], "$.shared_migration.shared_jar_sha256", SHA64
    )
    if shared_jar_hash != "94e2adb769790d813a872163347ede20ad4c75ae88e5811df2ec6625a340f21f":
        _fail("$.shared_migration.shared_jar_sha256", "must bind the verified immutable Shared jar")
    if migration["image_repository"] != "ghcr.io/devpathai/devpath-migration":
        _fail("$.shared_migration.image_repository", "must be ghcr.io/devpathai/devpath-migration")
    _string(migration["image_digest"], "$.shared_migration.image_digest", DIGEST)
    if migration["flyway_target"] != "202608161011":
        _fail("$.shared_migration.flyway_target", "must bind V202608161011")
    if migration["required_migration"] != "V202608161011__validate_lcs_mentor_snapshot_contract.sql":
        _fail("$.shared_migration.required_migration", "must bind V202608161011 validation")
    if migration["rollback_policy"] != "additive-retained":
        _fail("$.shared_migration.rollback_policy", "must retain additive schema")

    frontend = _object(root["frontend"], "$.frontend")
    _exact_keys(
        frontend,
        {
            "repository",
            "source_sha",
            "app_version",
            "analytics_contract_version",
            "flag_contract_version",
            "mission_off",
            "mission_on",
            "selected_on_digest",
            "rollback",
        },
        "$.frontend",
    )
    if frontend["repository"] != "DevPathAi/devpath-frontend":
        _fail("$.frontend.repository", "must be DevPathAi/devpath-frontend")
    source_sha = _string(frontend["source_sha"], "$.frontend.source_sha", SHA40)
    _string(frontend["app_version"], "$.frontend.app_version", SAFE_IDENTIFIER)
    if frontend["analytics_contract_version"] != "mission-spine.analytics.v1":
        _fail("$.frontend.analytics_contract_version", "must be mission-spine.analytics.v1")
    if frontend["flag_contract_version"] != "mission-spine.flag.v1":
        _fail("$.frontend.flag_contract_version", "must be mission-spine.flag.v1")
    variants: dict[str, str] = {}
    for field, suffix in (("mission_off", "mission-off"), ("mission_on", "mission-on")):
        variant = _object(frontend[field], f"$.frontend.{field}")
        _exact_keys(variant, {"tag", "image_digest"}, f"$.frontend.{field}")
        if variant["tag"] != f"{source_sha}-{suffix}":
            _fail(f"$.frontend.{field}.tag", f"must encode source SHA and {suffix}")
        variants[field] = _string(variant["image_digest"], f"$.frontend.{field}.image_digest", DIGEST)
    if variants["mission_off"] == variants["mission_on"]:
        _fail("$.frontend", "mission-OFF and mission-ON digests must be distinct")
    selected = _string(frontend["selected_on_digest"], "$.frontend.selected_on_digest", DIGEST)
    if selected != variants["mission_on"]:
        _fail("$.frontend.selected_on_digest", "must exactly equal mission_on.image_digest")
    rollback = _object(frontend["rollback"], "$.frontend.rollback")
    _exact_keys(rollback, {"mission_off_digest", "prior_digest", "final_target"}, "$.frontend.rollback")
    if rollback["mission_off_digest"] != variants["mission_off"]:
        _fail("$.frontend.rollback.mission_off_digest", "must exactly equal mission_off.image_digest")
    prior = _string(rollback["prior_digest"], "$.frontend.rollback.prior_digest", DIGEST)
    if prior != gitops["base_web_digest"]:
        _fail("$.frontend.rollback.prior_digest", "must exactly equal gitops.base_web_digest")
    if prior in variants.values():
        _fail("$.frontend.rollback.prior_digest", "must be distinct from both candidate digests")
    if rollback["final_target"] != "prior":
        _fail("$.frontend.rollback.final_target", "must be prior")

    home = _object(root["home"], "$.home")
    _exact_keys(
        home,
        {
            "repository",
            "source_sha",
            "dist_sha256",
            "cloudflare_account_id",
            "cloudflare_project",
            "candidate_deployment_id",
            "prior_production_deployment_id",
        },
        "$.home",
    )
    if home["repository"] != "DevPathAi/devpath-home-page":
        _fail("$.home.repository", "must be DevPathAi/devpath-home-page")
    _string(home["source_sha"], "$.home.source_sha", SHA40)
    _string(home["dist_sha256"], "$.home.dist_sha256", SHA64)
    _string(home["cloudflare_account_id"], "$.home.cloudflare_account_id", re.compile(r"^[0-9a-f]{32}$"))
    _string(home["cloudflare_project"], "$.home.cloudflare_project", SAFE_IDENTIFIER)
    candidate_id = _string(home["candidate_deployment_id"], "$.home.candidate_deployment_id", CF_ID)
    prior_id = _string(home["prior_production_deployment_id"], "$.home.prior_production_deployment_id", CF_ID)
    if candidate_id == prior_id:
        _fail("$.home", "candidate and prior Cloudflare deployment IDs must be distinct")

    _validate_quality_evidence_inputs(root["quality_evidence_inputs"], root)

    privacy = _object(root["analytics_privacy"], "$.analytics_privacy")
    _exact_keys(
        privacy,
        {
            "collection_mode",
            "approval_source_sha",
            "region",
            "project_identity",
            "retention_days",
            "access_owner",
            "deletion_runbook",
        },
        "$.analytics_privacy",
    )
    if privacy["collection_mode"] not in {"explicit-consent", "approved-cookieless"}:
        _fail("$.analytics_privacy.collection_mode", "must be an approved collection mode")
    _string(privacy["approval_source_sha"], "$.analytics_privacy.approval_source_sha", SHA40)
    if privacy["region"] != "EU":
        _fail("$.analytics_privacy.region", "must be EU")
    for field in ("project_identity", "access_owner", "deletion_runbook"):
        _string(privacy[field], f"$.analytics_privacy.{field}", SAFE_IDENTIFIER)
    retention = _positive_int(privacy["retention_days"], "$.analytics_privacy.retention_days")
    if retention > 365:
        _fail("$.analytics_privacy.retention_days", "must be no more than 365")

    evaluation = _object(root["ai_release_eval_config"], "$.ai_release_eval_config")
    _exact_keys(
        evaluation,
        {
            "primary_model",
            "fallback_models",
            "prompt_sha256",
            "fixture_revision",
            "fixture_sha256",
            "rendered_config_sha256",
            "ollama_endpoint_sha256",
        },
        "$.ai_release_eval_config",
    )
    primary = _string(evaluation["primary_model"], "$.ai_release_eval_config.primary_model", SAFE_IDENTIFIER)
    fallbacks = evaluation["fallback_models"]
    if not isinstance(fallbacks, list) or not fallbacks:
        _fail("$.ai_release_eval_config.fallback_models", "must name at least one actual fallback model")
    parsed_fallbacks = [_string(item, f"$.ai_release_eval_config.fallback_models[{index}]", SAFE_IDENTIFIER) for index, item in enumerate(fallbacks)]
    if len(set(parsed_fallbacks)) != len(parsed_fallbacks) or primary in parsed_fallbacks:
        _fail("$.ai_release_eval_config.fallback_models", "must be unique and distinct from primary_model")
    _string(evaluation["prompt_sha256"], "$.ai_release_eval_config.prompt_sha256", SHA64)
    _string(evaluation["fixture_revision"], "$.ai_release_eval_config.fixture_revision", SAFE_IDENTIFIER)
    _string(evaluation["fixture_sha256"], "$.ai_release_eval_config.fixture_sha256", SHA64)
    _string(
        evaluation["rendered_config_sha256"],
        "$.ai_release_eval_config.rendered_config_sha256",
        SHA64,
    )
    _string(
        evaluation["ollama_endpoint_sha256"],
        "$.ai_release_eval_config.ollama_endpoint_sha256",
        SHA64,
    )

    environments = _object(root["environments"], "$.environments")
    _exact_keys(environments, {"staging", "production"}, "$.environments")
    _validate_environment(environments["staging"], "$.environments.staging")
    _validate_environment(environments["production"], "$.environments.production")
    if environments["staging"]["github_environment"] != "mission-spine-staging":
        _fail("$.environments.staging.github_environment", "must be mission-spine-staging")
    if environments["production"]["github_environment"] != "mission-spine-production":
        _fail("$.environments.production.github_environment", "must be mission-spine-production")
    for field in ("github_environment", "kubernetes_context", "web_origin", "landing_origin"):
        if environments["staging"][field] == environments["production"][field]:
            _fail(f"$.environments.{field}", "staging and production identities must be distinct")

    _validate_journey_harness(root["journey_harness"], "$.journey_harness", environments)

    rollout = _object(root["rollout"], "$.rollout")
    _exact_keys(
        rollout,
        {
            "sync_timeout_seconds",
            "canary_seconds",
            "rollback_budget_seconds",
            "synthetic_probe_path",
            "production_order",
            "rollback_order",
        },
        "$.rollout",
    )
    if rollout["sync_timeout_seconds"] != 300:
        _fail("$.rollout.sync_timeout_seconds", "must be exactly 300")
    if rollout["canary_seconds"] != 900:
        _fail("$.rollout.canary_seconds", "must be exactly 900")
    if rollout["rollback_budget_seconds"] != 600:
        _fail("$.rollout.rollback_budget_seconds", "must be exactly 600")
    if rollout["synthetic_probe_path"] != "/internal/release/ready":
        _fail("$.rollout.synthetic_probe_path", "must be the canonical release identity endpoint")
    if rollout["production_order"] != PRODUCTION_ORDER:
        _fail("$.rollout.production_order", "must encode producer/OFF/ON/canary/Landing-last order")
    if rollout["rollback_order"] != ROLLBACK_ORDER:
        _fail("$.rollout.rollback_order", "must encode Landing-first reverse rollback and retained schema")

    return root


def validate_release_manifest(
    data: Any,
    candidate_spec: dict[str, Any],
    candidate_spec_sha256: str,
    source: Path | None = None,
) -> dict[str, Any]:
    """Validate the post-execution attestation against immutable candidate bytes."""
    candidate = validate_candidate_spec(candidate_spec)
    candidate_hash = _string(candidate_spec_sha256, "candidate_spec_sha256", SHA64)
    root = _object(data, "$")
    _validate_sanitized(root)
    _exact_keys(root, RELEASE_TOP_LEVEL, "$")
    if root["schema_version"] != 1:
        _fail("$.schema_version", "must be 1")
    if root["document_type"] != "release-manifest":
        _fail("$.document_type", "must be release-manifest")
    schema_ref = _string(root["$schema"], "$.$schema")
    if schema_ref != "../schema-v1.json" and not schema_ref.endswith("release-manifests/schema-v1.json"):
        _fail("$.$schema", "must reference release-manifests/schema-v1.json")
    release_id = _string(root["release_id"], "$.release_id", RELEASE_ID)
    if release_id != candidate["release_id"]:
        _fail("$.release_id", "must match candidate-spec release_id")
    _timestamp(root["created_at"], "$.created_at")

    candidate_ref = _object(root["candidate_spec"], "$.candidate_spec")
    _exact_keys(candidate_ref, {"path", "sha256"}, "$.candidate_spec")
    relative_path = _string(candidate_ref["path"], "$.candidate_spec.path")
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".json":
        _fail("$.candidate_spec.path", "must be a safe repository-relative JSON path")
    bound_hash = _string(candidate_ref["sha256"], "$.candidate_spec.sha256", SHA64)
    if bound_hash != candidate_hash:
        _fail("$.candidate_spec.sha256", "must match the immutable candidate-spec bytes")

    _validate_home_artifact(
        root["home_dist_artifact"],
        "$.home_dist_artifact",
        release_id,
        candidate_hash,
        candidate["home"]["dist_sha256"],
    )
    if root["home_dist_artifact"]["run_attempt"] != 1:
        _fail(
            "$.home_dist_artifact.run_attempt",
            "Home dist is sealable only on attempt 1; retry with a fresh dispatch",
        )
    _require_artifact_identity(
        root["home_dist_artifact"],
        "$.home_dist_artifact",
        candidate["home"]["repository"],
        home_dist_artifact_name(
            release_id,
            root["home_dist_artifact"]["workflow_run_id"],
            root["home_dist_artifact"]["run_attempt"],
        ),
        PRODUCER_WORKFLOWS["home-dist"],
        candidate["home"]["source_sha"],
    )

    approval = _object(root["analytics_privacy_approval"], "$.analytics_privacy_approval")
    _exact_keys(approval, {"approved_at", "evidence"}, "$.analytics_privacy_approval")
    _timestamp(approval["approved_at"], "$.analytics_privacy_approval.approved_at")
    _validate_artifact(
        approval["evidence"],
        "$.analytics_privacy_approval.evidence",
        release_id,
        candidate_hash,
    )
    if approval["evidence"]["run_attempt"] != 1:
        _fail(
            "$.analytics_privacy_approval.evidence.run_attempt",
            "protected privacy approval requires attempt 1",
        )
    _require_artifact_identity(
        approval["evidence"],
        "$.analytics_privacy_approval.evidence",
        "DevPathAi/documents",
        privacy_approval_artifact_name(
            release_id,
            approval["evidence"]["workflow_run_id"],
            approval["evidence"]["run_attempt"],
        ),
        PRODUCER_WORKFLOWS["privacy-approval"],
        candidate["analytics_privacy"]["approval_source_sha"],
    )

    evaluation = _object(root["ai_release_eval"], "$.ai_release_eval")
    _exact_keys(
        evaluation,
        {"hard_invariants_percent", "usefulness_percent", "baseline_delta_points", "evidence"},
        "$.ai_release_eval",
    )
    if _number(evaluation["hard_invariants_percent"], "$.ai_release_eval.hard_invariants_percent") != 100:
        _fail("$.ai_release_eval.hard_invariants_percent", "hard_invariants must be 100%")
    if _number(evaluation["usefulness_percent"], "$.ai_release_eval.usefulness_percent") < 90:
        _fail("$.ai_release_eval.usefulness_percent", "usefulness must be at least 90%")
    if _number(evaluation["baseline_delta_points"], "$.ai_release_eval.baseline_delta_points") < -5:
        _fail("$.ai_release_eval.baseline_delta_points", "must not regress more than five points")
    _validate_artifact(evaluation["evidence"], "$.ai_release_eval.evidence", release_id, candidate_hash)
    if evaluation["evidence"]["run_attempt"] != 1:
        _fail(
            "$.ai_release_eval.evidence.run_attempt",
            "protected AI release evaluation requires attempt 1",
        )
    _require_artifact_identity(
        evaluation["evidence"],
        "$.ai_release_eval.evidence",
        candidate["services"]["devpath-ai-svc"]["repository"],
        ai_eval_artifact_name(
            release_id,
            evaluation["evidence"]["workflow_run_id"],
            evaluation["evidence"]["run_attempt"],
        ),
        PRODUCER_WORKFLOWS["ai-release-eval"],
        candidate["services"]["devpath-ai-svc"]["source_sha"],
    )

    journeys = _object(root["journeys"], "$.journeys")
    _exact_keys(journeys, {"activation", "contextual_practice"}, "$.journeys")
    _validate_artifact(journeys["activation"], "$.journeys.activation", release_id, candidate_hash)
    _validate_artifact(
        journeys["contextual_practice"],
        "$.journeys.contextual_practice",
        release_id,
        candidate_hash,
    )
    _require_artifact_identity(
        journeys["activation"],
        "$.journeys.activation",
        "DevPathAi/devpath-gitops",
        f"{release_id}-journey-activation-attempt-{journeys['activation']['run_attempt']}",
        PRODUCER_WORKFLOWS["journey-activation"],
        journeys["activation"]["head_sha"],
    )
    _require_artifact_identity(
        journeys["contextual_practice"],
        "$.journeys.contextual_practice",
        "DevPathAi/devpath-gitops",
        (
            f"{release_id}-journey-contextual-practice-attempt-"
            f"{journeys['contextual_practice']['run_attempt']}"
        ),
        PRODUCER_WORKFLOWS["journey-contextual-practice"],
        journeys["contextual_practice"]["head_sha"],
    )
    if journeys["activation"]["sha256"] == journeys["contextual_practice"]["sha256"]:
        _fail("$.journeys", "the two journey evidence hashes must be distinct")
    if journeys["activation"]["artifact_id"] == journeys["contextual_practice"]["artifact_id"]:
        _fail("$.journeys", "the two journey artifact IDs must be distinct")

    quality = _object(root["quality_evidence"], "$.quality_evidence")
    _exact_keys(quality, QUALITY_EVIDENCE_KEYS, "$.quality_evidence")
    artifacts: dict[str, dict[str, Any]] = {}
    for label, key in QUALITY_EVIDENCE.items():
        artifact_path = f"$.quality_evidence.{key}"
        artifact = _object(quality[key], artifact_path)
        _validate_artifact(
            artifact,
            artifact_path,
            release_id,
            candidate_hash,
            expected_event=QUALITY_EVIDENCE_EVENTS[label],
            expected_evidence_file=QUALITY_EVIDENCE_FILES[label],
            require_release_scope=label in {"frontend-visual", "frontend-automated-a11y"},
        )
        if label in {"home-visual", "home-axe-browser-a11y"}:
            repository = "DevPathAi/devpath-gitops"
            head_sha = journeys["activation"]["head_sha"]
            artifact_name = quality_artifact_name(label, release_id, artifact["run_attempt"])
        elif label in FRONTEND_CATALOG_CONTRACTS:
            repository, head_sha = quality_source(candidate, label)
            artifact_name = quality_artifact_name(
                label,
                release_id,
                artifact["run_attempt"],
                artifact["workflow_run_id"],
            )
        else:
            repository, head_sha = quality_source(candidate, label)
            artifact_name = quality_artifact_name(
                label,
                release_id,
                artifact["run_attempt"],
                artifact["workflow_run_id"],
            )
        _require_artifact_identity(
            artifact,
            artifact_path,
            repository,
            artifact_name,
            PRODUCER_WORKFLOWS[label],
            head_sha,
        )
        if label in MANUAL_CATALOG_CONTRACTS and artifact["run_attempt"] != 1:
            _fail(
                f"{artifact_path}.run_attempt",
                "protected manual approval is sealable only on attempt 1; retry with a fresh dispatch",
            )
        artifacts[label] = artifact

    frontend_pair_fields = {
        "repository",
        "event",
        "head_sha",
        "run_attempt",
        "workflow_path",
        "workflow_sha256",
        "workflow_run_id",
    }
    for field in frontend_pair_fields:
        if artifacts["frontend-visual"][field] != artifacts["frontend-automated-a11y"][field]:
            _fail(
                "$.quality_evidence",
                f"atomic frontend evidence pair {field} must match",
            )

    manual_pair_fields = {
        "repository",
        "event",
        "head_sha",
        "run_attempt",
        "workflow_path",
        "workflow_sha256",
        "workflow_run_id",
    }
    manual_labels = tuple(MANUAL_CATALOG_CONTRACTS)
    for field in manual_pair_fields:
        values = {artifacts[label][field] for label in manual_labels}
        if len(values) != 1:
            _fail(
                "$.quality_evidence",
                f"atomic manual evidence trio {field} must match",
            )

    shared_home_fields = {
        "candidate_spec_sha256",
        "repository",
        "event",
        "head_sha",
        "run_attempt",
        "workflow_path",
        "workflow_sha256",
        "workflow_run_id",
        "artifact_id",
        "artifact_name",
    }
    for field in shared_home_fields:
        if artifacts["home-visual"][field] != artifacts["home-axe-browser-a11y"][field]:
            _fail("$.quality_evidence", f"Home combined artifact {field} must match")
    if artifacts["home-visual"]["sha256"] == artifacts["home-axe-browser-a11y"]["sha256"]:
        _fail("$.quality_evidence", "Home visual and axe/browser manifest hashes must be distinct")

    logical_hashes = [artifact["sha256"] for artifact in artifacts.values()]
    if len(set(logical_hashes)) != len(logical_hashes):
        _fail("$.quality_evidence", "all seven logical evidence manifest hashes must be distinct")
    physical_ids = {
        label: (artifact["repository"], artifact["artifact_id"])
        for label, artifact in artifacts.items()
        if label != "home-axe-browser-a11y"
    }
    if len(set(physical_ids.values())) != len(physical_ids):
        _fail("$.quality_evidence", "quality evidence artifacts must be distinct except Home's two manifests")
    signed_mobile = candidate["quality_evidence_inputs"]["mobile_test_artifacts"]
    for label, artifact in artifacts.items():
        if (
            artifact["repository"] == signed_mobile["repository"]
            and artifact["artifact_id"] == signed_mobile["artifact_id"]
        ):
            _fail(
                f"$.quality_evidence.{QUALITY_EVIDENCE[label]}.artifact_id",
                "must be distinct from the prebound signed-mobile artifact ID",
            )
        if (
            artifact["repository"] == signed_mobile["repository"]
            and artifact["artifact_name"] == signed_mobile["artifact_name"]
        ):
            _fail(
                f"$.quality_evidence.{QUALITY_EVIDENCE[label]}.artifact_name",
                "must be distinct from the prebound signed-mobile artifact name",
            )

    attestation = _object(root["validation_attestation"], "$.validation_attestation")
    _exact_keys(
        attestation,
        {
            "validator_repository",
            "validator_run_id",
            "validator_run_attempt",
            "validator_event",
            "validator_head_sha",
            "validator_workflow_path",
            "validator_workflow_sha256",
            "home_source_sha",
            "candidate_spec_sha256",
            "activation_sha256",
            "contextual_practice_sha256",
        },
        "$.validation_attestation",
    )
    if attestation["validator_repository"] != "DevPathAi/devpath-gitops":
        _fail("$.validation_attestation.validator_repository", "must be DevPathAi/devpath-gitops")
    _positive_int(attestation["validator_run_id"], "$.validation_attestation.validator_run_id")
    _positive_int(attestation["validator_run_attempt"], "$.validation_attestation.validator_run_attempt")
    if attestation["validator_event"] != "workflow_dispatch":
        _fail("$.validation_attestation.validator_event", "must be workflow_dispatch")
    _string(attestation["validator_head_sha"], "$.validation_attestation.validator_head_sha", SHA40)
    if attestation["validator_workflow_path"] != PRODUCER_WORKFLOWS["journey-activation"]:
        _fail("$.validation_attestation.validator_workflow_path", "must be the exact validator workflow")
    _string(
        attestation["validator_workflow_sha256"],
        "$.validation_attestation.validator_workflow_sha256",
        SHA64,
    )
    if attestation["home_source_sha"] != candidate["home"]["source_sha"]:
        _fail("$.validation_attestation.home_source_sha", "must bind the exact checked-out Home source")
    if attestation["candidate_spec_sha256"] != candidate_hash:
        _fail("$.validation_attestation.candidate_spec_sha256", "must bind candidate-spec")
    if attestation["activation_sha256"] != journeys["activation"]["sha256"]:
        _fail("$.validation_attestation.activation_sha256", "must bind activation evidence bytes")
    if attestation["contextual_practice_sha256"] != journeys["contextual_practice"]["sha256"]:
        _fail("$.validation_attestation.contextual_practice_sha256", "must bind contextual evidence bytes")
    for name in ("activation", "contextual_practice"):
        artifact = journeys[name]
        if artifact["repository"] != attestation["validator_repository"]:
            _fail(f"$.journeys.{name}.repository", "must be owned by the GitOps validator")
        if artifact["workflow_run_id"] != attestation["validator_run_id"]:
            _fail(f"$.journeys.{name}.workflow_run_id", "must match validation attestation run")
        if artifact["run_attempt"] != attestation["validator_run_attempt"]:
            _fail(f"$.journeys.{name}.run_attempt", "must match validation attestation attempt")
        if artifact["event"] != attestation["validator_event"]:
            _fail(f"$.journeys.{name}.event", "must match validation attestation event")
        if artifact["head_sha"] != attestation["validator_head_sha"]:
            _fail(f"$.journeys.{name}.head_sha", "must match validation attestation head")
        if artifact["workflow_path"] != attestation["validator_workflow_path"]:
            _fail(f"$.journeys.{name}.workflow_path", "must match validation workflow path")
        if artifact["workflow_sha256"] != attestation["validator_workflow_sha256"]:
            _fail(f"$.journeys.{name}.workflow_sha256", "must match validation workflow bytes")
    for label in ("home-visual", "home-axe-browser-a11y"):
        key = QUALITY_EVIDENCE[label]
        artifact = quality[key]
        artifact_path = f"$.quality_evidence.{key}"
        expected = {
            "repository": attestation["validator_repository"],
            "workflow_run_id": attestation["validator_run_id"],
            "run_attempt": attestation["validator_run_attempt"],
            "event": attestation["validator_event"],
            "head_sha": attestation["validator_head_sha"],
            "workflow_path": attestation["validator_workflow_path"],
            "workflow_sha256": attestation["validator_workflow_sha256"],
        }
        for field, expected_value in expected.items():
            if artifact[field] != expected_value:
                _fail(
                    f"{artifact_path}.{field}",
                    "must match the candidate validation run that produced Home evidence",
                )
    journey_ids = {journeys["activation"]["artifact_id"], journeys["contextual_practice"]["artifact_id"]}
    if artifacts["home-visual"]["artifact_id"] in journey_ids:
        _fail("$.quality_evidence.home_visual.artifact_id", "must be distinct from journey artifacts")
    return root


def validate_manifest(data: Any, source: Path | None = None) -> dict[str, Any]:
    """Dispatch candidate documents; final manifests require bundle validation."""
    root = _object(data, "$")
    if root.get("document_type") == "candidate-spec":
        return validate_candidate_spec(root, source)
    if root.get("document_type") == "release-manifest":
        _fail("$.document_type", "release-manifest requires its bound candidate-spec")
    _fail("$.document_type", "must be candidate-spec or release-manifest")


def _load_json(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON document {path}: {exc}") from exc
    return raw, _object(data, str(path))


def resolve_candidate_spec(root: Path, release_id: str) -> tuple[Path, dict[str, Any], str]:
    if RELEASE_ID.fullmatch(release_id) is None:
        raise ValueError("release_id has an invalid format")
    root = root.resolve()
    candidate_path = (
        root / "release-manifests" / "candidates" / f"{release_id}.candidate-spec.json"
    ).resolve()
    if candidate_path.parent != (root / "release-manifests" / "candidates").resolve():
        raise ValueError("candidate-spec escaped the candidates directory")
    if not candidate_path.is_file():
        raise ValueError("immutable candidate-spec does not exist")
    candidate_raw, candidate = _load_json(candidate_path)
    validate_candidate_spec(candidate, candidate_path)
    if candidate["release_id"] != release_id:
        raise ValueError("release_id must exactly match candidate-spec filename")
    return candidate_path, candidate, hashlib.sha256(candidate_raw).hexdigest()


def resolve_release_bundle(
    root: Path,
    release_id: str,
) -> tuple[Path, Path, dict[str, Any], dict[str, Any], str]:
    """Resolve release_id to the only allowed final/candidate document pair."""
    if RELEASE_ID.fullmatch(release_id) is None:
        raise ValueError("release_id has an invalid format")
    root = root.resolve()
    release_path = (root / "release-manifests" / "releases" / f"{release_id}.json").resolve()
    candidate_path, candidate, candidate_hash = resolve_candidate_spec(root, release_id)
    if release_path.parent != (root / "release-manifests" / "releases").resolve():
        raise ValueError("release manifest escaped the releases directory")
    if not release_path.is_file():
        raise ValueError("both immutable candidate-spec and final release-manifest must exist")
    _, release = _load_json(release_path)
    expected_path = candidate_path.relative_to(root).as_posix()
    if release.get("candidate_spec", {}).get("path") != expected_path:
        raise ValueError("release-manifest must reference the canonical candidate-spec path")
    validate_release_manifest(release, candidate, candidate_hash, release_path)
    if candidate["release_id"] != release_id or release["release_id"] != release_id:
        raise ValueError("release_id must exactly match both document filenames")
    return release_path, candidate_path, release, candidate, candidate_hash


def resolve_manifest(root: Path, release_id: str) -> Path:
    """Backward-compatible safe resolver returning only the sealed final path."""
    return resolve_release_bundle(root, release_id)[0]


def _github_outputs(
    candidate: dict[str, Any],
    release: dict[str, Any],
    release_path: Path,
    candidate_path: Path,
    candidate_hash: str,
) -> dict[str, str]:
    staging = candidate["environments"]["staging"]
    production = candidate["environments"]["production"]
    return {
        "manifest_path": release_path.as_posix(),
        "release_manifest_sha256": hashlib.sha256(release_path.read_bytes()).hexdigest(),
        "candidate_spec_path": candidate_path.as_posix(),
        "candidate_spec_sha256": candidate_hash,
        "release_id": release["release_id"],
        "gitops_base_sha": candidate["gitops"]["base_sha"],
        "web_base_digest": candidate["gitops"]["base_web_digest"],
        "web_off_digest": candidate["frontend"]["mission_off"]["image_digest"],
        "web_on_digest": candidate["frontend"]["selected_on_digest"],
        "web_prior_digest": candidate["frontend"]["rollback"]["prior_digest"],
        "staging_context": staging["kubernetes_context"],
        "staging_namespace": staging["namespace"],
        "staging_deployment": staging["web_deployment"],
        "staging_container": staging["web_container"],
        "staging_web_origin": staging["web_origin"],
        "production_context": production["kubernetes_context"],
        "production_namespace": production["namespace"],
        "production_deployment": production["web_deployment"],
        "production_container": production["web_container"],
        "production_web_origin": production["web_origin"],
        "production_landing_origin": production["landing_origin"],
        "home_account_id": candidate["home"]["cloudflare_account_id"],
        "home_project": candidate["home"]["cloudflare_project"],
        "home_source_sha": candidate["home"]["source_sha"],
        "home_dist_sha256": candidate["home"]["dist_sha256"],
        "home_candidate_id": candidate["home"]["candidate_deployment_id"],
        "home_prior_id": candidate["home"]["prior_production_deployment_id"],
        "home_dist_artifact_id": str(release["home_dist_artifact"]["artifact_id"]),
    }


def _candidate_github_outputs(candidate: dict[str, Any], candidate_path: Path, candidate_hash: str) -> dict[str, str]:
    return {
        "candidate_spec_path": candidate_path.as_posix(),
        "candidate_spec_sha256": candidate_hash,
        "release_id": candidate["release_id"],
        "gitops_base_sha": candidate["gitops"]["base_sha"],
        "home_source_sha": candidate["home"]["source_sha"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest", type=Path)
    source.add_argument("--release-id")
    source.add_argument("--candidate-id")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--emit-github-output", type=Path)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args(argv)

    try:
        if args.release_id:
            release_path, candidate_path, release, candidate, candidate_hash = resolve_release_bundle(
                args.root,
                args.release_id,
            )
        elif args.candidate_id:
            candidate_path, candidate, candidate_hash = resolve_candidate_spec(args.root, args.candidate_id)
            release = None
            release_path = None
        else:
            document_path = args.manifest.resolve()
            raw, document = _load_json(document_path)
            if document.get("document_type") == "candidate-spec":
                candidate = validate_candidate_spec(document, document_path)
                candidate_path = document_path
                candidate_hash = hashlib.sha256(raw).hexdigest()
                release = None
                release_path = None
            elif document.get("document_type") == "release-manifest":
                root = args.root.resolve()
                relative = _string(document.get("candidate_spec", {}).get("path"), "$.candidate_spec.path")
                candidate_path = (root / relative).resolve()
                if root not in candidate_path.parents:
                    raise ValueError("candidate-spec path escaped repository root")
                candidate_raw, candidate = _load_json(candidate_path)
                candidate_hash = hashlib.sha256(candidate_raw).hexdigest()
                release = validate_release_manifest(document, candidate, candidate_hash, document_path)
                release_path = document_path
            else:
                raise ValueError("document_type must be candidate-spec or release-manifest")
        if args.expected_sha256:
            expected = _string(args.expected_sha256, "--expected-sha256", SHA64)
            if candidate_hash != expected:
                raise ValueError(
                    f"candidate-spec SHA-256 mismatch: expected {expected}, got {candidate_hash}"
                )
        if args.emit_github_output:
            if release is None or release_path is None:
                outputs = _candidate_github_outputs(candidate, candidate_path, candidate_hash)
            else:
                outputs = _github_outputs(candidate, release, release_path, candidate_path, candidate_hash)
            with args.emit_github_output.open("a", encoding="utf-8", newline="\n") as output:
                for key, value in outputs.items():
                    output.write(f"{key}={value}\n")
        kind = "release bundle" if release is not None else "candidate-spec"
        print(f"validated {kind}: {candidate['release_id']} ({candidate_hash})")
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"release manifest validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
