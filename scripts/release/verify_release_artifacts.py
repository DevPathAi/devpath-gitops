#!/usr/bin/env python3
"""Fetch and verify sealed, sanitized release evidence from GitHub Actions."""

from __future__ import annotations

import argparse
import binascii
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from typing import Any
import zipfile

from validate_release_manifest import (
    FRONTEND_CATALOG_CONTRACTS,
    FRONTEND_EVIDENCE_FILES,
    FRONTEND_FIXTURE_IDS,
    FRONTEND_PROJECTION_CONTRACT_SHA256,
    FRONTEND_PROJECTION_MATRIX,
    MANUAL_CATALOG_CONTRACTS,
    PRODUCER_WORKFLOWS,
    QUALITY_EVIDENCE,
    SHA40,
    SHA64,
    SIGNED_MOBILE_BINDING_VERSION,
    SIGNED_MOBILE_FILES,
    SIGNED_MOBILE_WORKFLOW,
    _validate_sanitized,
    ai_eval_artifact_name,
    home_dist_artifact_name,
    privacy_approval_artifact_name,
    quality_source,
    resolve_release_bundle,
)


MAX_EVIDENCE_BYTES = 256 * 1024
MAX_CANDIDATE_ARTIFACT_ZIP_BYTES = 1024 * 1024
MAX_CANDIDATE_SPEC_BYTES = 256 * 1024
MAX_HOME_PAYLOAD_BYTES = 100 * 1024 * 1024
MAX_HOME_TAR_BYTES = 100 * 1024 * 1024
MAX_HOME_ARTIFACT_ZIP_BYTES = 105 * 1024 * 1024
MAX_HOME_ARCHIVE_FILES = 10_000
MAX_AI_ARTIFACT_ZIP_BYTES = 1024 * 1024
MAX_SIGNED_MOBILE_BINARY_BYTES = 500 * 1024 * 1024
MAX_SIGNED_MOBILE_ARCHIVE_BYTES = 550 * 1024 * 1024
CANDIDATE_REPOSITORY = "DevPathAi/devpath-gitops"
CANDIDATE_WORKFLOW = ".github/workflows/mission-spine-candidate.yml"
AI_RENDER_PATH = "apps/devpath-ai-svc/base"
AI_RUNTIME_ENVIRONMENT = {
    "MENTOR_PROVIDER": "ollama",
    "MENTOR_FALLBACK": "claude",
    "MENTOR_OLLAMA_MODEL": "qwen2.5:3b",
    "MENTOR_CLAUDE_MODEL": "claude-sonnet-4-6",
}
KUSTOMIZE_VERSION = "v5.4.3"
KUSTOMIZE_BINARY_SHA256 = (
    "1d6bae90ee8591f7a4ed5b75be3f9bf80b7609f0c785921320827cd93e7c3a9a"
)
KUSTOMIZE_BINARY_BYTES = 15_101_952
MAX_AI_RENDER_SOURCE_BYTES = 16 * 1024 * 1024
MAX_AI_RENDERED_BYTES = 32 * 1024 * 1024
MAX_AI_RENDER_SOURCE_FILES = 1_000
JOURNEY_ROW_KEYS = {"route", "step", "result", "duration_ms", "candidate_spec_sha256"}
PRODUCER_EVIDENCE_KEYS = {"producer_run_id", "producer_run_attempt"}
HOME_CASE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
JOURNEY_ALLOWLISTS = {
    "journey-activation": {
        "landing-prepermission-zero": ("/",),
        "opaque-journey-handoff": ("/diagnostic",),
        "guest-diagnostic-fifteen": ("/diagnostic",),
        "guest-preview-refresh": ("/diagnostic",),
        "oauth-callback-replay": ("/consent",),
        "required-consent-claim-replay": ("/diagnostic",),
        "explicit-path-to-today": ("/dashboard",),
        "content-linked-completion-replay": ("/dashboard",),
        "contentless-completion-replay": ("/dashboard",),
        "onboarding-analytics-ordered": ("/dashboard",),
    },
    "journey-contextual-practice": {
        "authenticated-authoritative-today": ("/dashboard",),
        "canonical-content-to-sandbox": ("/mission/1/sandbox",),
        "immediate-disconnect-timeout-recovery": ("/mission/1/sandbox",),
        "midstream-disconnect-truncated-recovery": ("/mission/1/sandbox",),
        "stale-session-reconciliation": ("/mission/1/sandbox",),
        "outbox-review-durable": ("/mission/1/sandbox",),
        "private-context-preview-commit": ("/mission/1/mentor",),
        "mentor-partial-retry-payload-parity": ("/mission/1/mentor",),
        "workspace-analytics-and-boundaries": ("/mission/1/mentor",),
    },
}
VALIDATION_SEAL_KEYS = {
    "release_id",
    "candidate_spec_sha256",
    "release_manifest_sha256",
    "status",
    "rollback_seconds",
    "validator_run_id",
    "validator_run_attempt",
    "validator_head_sha",
    "validator_workflow_sha256",
}
FRONTEND_BASELINE_AUTHENTICATION_KEYS = {
    "release_id",
    "repository",
    "workflow_path",
    "workflow_sha256",
    "run_id",
    "run_attempt",
    "head_sha",
    "artifact_id",
    "artifact_name",
    "artifact_archive_sha256",
    "approval_document_sha256",
    "approval_environment",
    "approval_environment_id",
    "approved_by_id",
    "approved_by",
    "approval_effective_at",
}
PROTECTED_APPROVAL_KEYS = {
    "approval_environment",
    "approval_environment_id",
    "approval_job_name",
    "approved_by",
    "approved_by_id",
    "approval_effective_at",
}
PROTECTED_APPROVAL_CONTRACTS = {
    "frontend-baseline": (
        "et13-baseline-approval",
        "Approve exact ET13 visual baseline",
    ),
    "signed-mobile-android": (
        "mission-spine-mobile-signing-android",
        "Sign Android release",
    ),
    "ai-release-eval": (
        "mission-spine-ai-release-eval",
        "Run AI release evaluation",
    ),
    "privacy-approval": (
        "mission-spine-privacy-approval",
        "Approve analytics privacy release",
    ),
    "manual-nvda": ("manual-at-nvda", "Approve manual NVDA evidence"),
    "manual-talkback": (
        "manual-at-talkback",
        "Approve manual TalkBack evidence",
    ),
}
SIGNED_MOBILE_PROVENANCE_KEYS = {
    "schema_version",
    "release_id",
    "repository",
    "source_sha",
    "event",
    "workflow_path",
    "workflow_sha256",
    "producer_run_id",
    "producer_run_attempt",
    "pubspec_lock_sha256",
    "toolchain",
    "build_configuration",
    "android",
    "approvals",
}
MANUAL_CATALOG_KEYS = {
    "schema_version",
    "lane",
    "assistive_technology",
    "test_provenance_path",
    "case_count",
    "cases",
}
MANUAL_CASE_KEYS = {"id", "surface", "entry_point", "procedure", "expected"}
MANUAL_PROVENANCE_KEYS = {
    "schema_version",
    "lane",
    "catalog_path",
    "catalog_sha256",
    "assistive_technology",
    "execution_mode",
    "required_platform",
    "required_client",
    "required_artifact",
    "case_ids",
    "pass_policy",
}


def _route_allowed(label: str, step: str, route: str) -> bool:
    if route in JOURNEY_ALLOWLISTS[label][step]:
        return True
    if label == "journey-activation" and step in {
        "explicit-path-to-today",
        "content-linked-completion-replay",
        "contentless-completion-replay",
        "onboarding-analytics-ordered",
    }:
        parts = route.split("/")
        return len(parts) == 4 and parts[1] == "path" and parts[2].isdigit() and parts[3] == "today"
    if label == "journey-contextual-practice":
        parts = route.split("/")
        if step == "authenticated-authoritative-today":
            return len(parts) == 4 and parts[1] == "path" and parts[2].isdigit() and parts[3] == "today"
        expected_tail = "mentor" if step in {
            "private-context-preview-commit",
            "mentor-partial-retry-payload-parity",
            "workspace-analytics-and-boundaries",
        } else "sandbox"
        return len(parts) == 4 and parts[1] == "mission" and parts[2].isdigit() and parts[3] == expected_tail
    return False


def _exact_payload(payload: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ValueError(f"{label} evidence has an invalid key set")
    return payload


def _bounded_number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{label} is outside its approved range")
    return number


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _positive_int(value: Any, label: str) -> int:
    number = _nonnegative_int(value, label)
    if number == 0:
        raise ValueError(f"{label} must be a positive integer")
    return number


def _quality_catalog(candidate: dict[str, Any], label: str) -> dict[str, Any]:
    return candidate["quality_evidence_inputs"]["catalogs"][label]


def _validate_quality_counts(value: dict[str, Any], catalog: dict[str, Any], label: str) -> None:
    case_count = _positive_int(value["case_count"], f"{label} case_count")
    passed = _nonnegative_int(value["passed_case_count"], f"{label} passed_case_count")
    failed = _nonnegative_int(value["failed_case_count"], f"{label} failed_case_count")
    if case_count != catalog["case_count"]:
        raise ValueError(f"{label} case_count does not match the bound catalog")
    if passed != case_count or failed != 0:
        raise ValueError(f"{label} all catalog cases must be passed with failed_case_count zero")


def _validate_surface_counts(value: Any, case_count: int, label: str) -> None:
    counts = _exact_payload(value, {"web", "admin", "mobile", "dp_design"}, f"{label} surfaces")
    parsed = [_positive_int(count, f"{label} surface {surface}") for surface, count in counts.items()]
    if sum(parsed) != case_count:
        raise ValueError(f"{label} surface counts must sum to the exact catalog count")
    expected = FRONTEND_CATALOG_CONTRACTS.get(label)
    if expected is not None and (
        case_count != expected["case_count"]
        or counts != expected["surface_case_counts"]
    ):
        raise ValueError(f"{label} must match the exact approved frontend surface counts")


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_frontend_baseline_authentication(
    value: Any,
    candidate: dict[str, Any],
    visual_catalog: dict[str, Any],
) -> dict[str, Any]:
    """Validate the protected baseline approval identity embedded by ET13 producers."""
    authentication = _exact_payload(
        value,
        FRONTEND_BASELINE_AUTHENTICATION_KEYS,
        "frontend baseline authentication",
    )
    release_id = candidate["release_id"]
    expected = {
        "release_id": release_id,
        "repository": "DevPathAi/devpath-frontend",
        "workflow_path": ".github/workflows/et13-baseline-approval.yml",
        "head_sha": candidate["frontend"]["source_sha"],
        "approval_document_sha256": visual_catalog["baseline_approval_sha256"],
        "approval_environment": "et13-baseline-approval",
    }
    for field, expected_value in expected.items():
        if authentication[field] != expected_value:
            raise ValueError(f"frontend baseline authentication {field} mismatch")
    for field in (
        "workflow_sha256",
        "artifact_archive_sha256",
        "approval_document_sha256",
    ):
        if not isinstance(authentication[field], str) or SHA64.fullmatch(
            authentication[field]
        ) is None:
            raise ValueError(f"frontend baseline authentication {field} is invalid")
    for field in (
        "run_id",
        "run_attempt",
        "artifact_id",
        "approval_environment_id",
        "approved_by_id",
    ):
        _positive_int(authentication[field], f"frontend baseline authentication {field}")
    if authentication["run_attempt"] != 1:
        raise ValueError(
            "frontend baseline authentication run_attempt must be 1; retry requires a fresh dispatch"
        )
    expected_artifact_name = (
        f"{release_id}-frontend-visual-approved-baseline-run-"
        f"{authentication['run_id']}-attempt-{authentication['run_attempt']}"
    )
    if authentication["artifact_name"] != expected_artifact_name:
        raise ValueError("frontend baseline authentication artifact_name mismatch")
    approved_by = authentication["approved_by"]
    if not isinstance(approved_by, str) or re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", approved_by
    ) is None:
        raise ValueError("frontend baseline authentication approved_by is invalid")
    effective_at = authentication["approval_effective_at"]
    if not isinstance(effective_at, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", effective_at
    ) is None:
        raise ValueError("frontend baseline authentication approval_effective_at is invalid")
    try:
        parsed = datetime.fromisoformat(effective_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "frontend baseline authentication approval_effective_at is invalid"
        ) from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("frontend baseline authentication approval_effective_at is not UTC")
    return authentication


def validate_atomic_frontend_authentication(
    authentications: dict[str, dict[str, Any]],
) -> None:
    expected = {"frontend-visual", "frontend-automated-a11y"}
    if set(authentications) != expected:
        raise ValueError("atomic frontend evidence requires both baseline authentications")
    if authentications["frontend-visual"] != authentications["frontend-automated-a11y"]:
        raise ValueError("atomic frontend evidence baseline authentication mismatch")


def _utc_z(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value
    ) is None:
        raise ValueError(f"{label} must be a UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid UTC timestamp") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"{label} must be UTC")
    return parsed


def validate_approval_claim(label: str, value: Any) -> dict[str, Any]:
    """Validate one producer-embedded protected-environment approval identity."""
    if label not in PROTECTED_APPROVAL_CONTRACTS:
        raise ValueError(f"{label}: unknown protected approval contract")
    claim = _exact_payload(value, PROTECTED_APPROVAL_KEYS, f"{label} approval")
    expected_environment, expected_job = PROTECTED_APPROVAL_CONTRACTS[label]
    if claim["approval_environment"] != expected_environment:
        raise ValueError(f"{label} approval_environment mismatch")
    if claim["approval_job_name"] != expected_job:
        raise ValueError(f"{label} approval_job_name mismatch")
    for field in ("approval_environment_id", "approved_by_id"):
        _positive_int(claim[field], f"{label} {field}")
    approved_by = claim["approved_by"]
    if not isinstance(approved_by, str) or re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", approved_by
    ) is None:
        raise ValueError(f"{label} approved_by is invalid")
    _utc_z(claim["approval_effective_at"], f"{label} approval_effective_at")
    return claim


def validate_signed_mobile_provenance(
    value: Any,
    candidate: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Validate the exact protected Android build-provenance document."""
    _validate_sanitized(value, "signed-mobile provenance")
    provenance = _exact_payload(
        value,
        SIGNED_MOBILE_PROVENANCE_KEYS,
        "signed-mobile provenance",
    )
    mobile = candidate["quality_evidence_inputs"]["mobile_test_artifacts"]
    expected_top = {
        "schema_version": "leva.mission-spine.signed-android-build.v2",
        "release_id": candidate["release_id"],
        "repository": mobile["repository"],
        "source_sha": mobile["source_sha"],
        "event": mobile["event"],
        "workflow_path": mobile["workflow_path"],
        "workflow_sha256": mobile["workflow_sha256"],
        "producer_run_id": mobile["workflow_run_id"],
        "producer_run_attempt": mobile["run_attempt"],
    }
    for field, expected in expected_top.items():
        if provenance[field] != expected:
            raise ValueError(f"signed-mobile provenance {field} mismatch")
    if not isinstance(provenance["pubspec_lock_sha256"], str) or SHA64.fullmatch(
        provenance["pubspec_lock_sha256"]
    ) is None:
        raise ValueError("signed-mobile provenance pubspec_lock_sha256 is invalid")

    toolchain = _exact_payload(
        provenance["toolchain"],
        {"flutter_version", "flutter_revision", "dart_sdk_version", "android"},
        "signed-mobile toolchain",
    )
    expected_toolchain = {
        "flutter_version": "3.44.1",
        "flutter_revision": "924134a44c189315be2148659913dda1671cbe99",
        "dart_sdk_version": "3.12.1",
    }
    for field, expected in expected_toolchain.items():
        if toolchain[field] != expected:
            raise ValueError(f"signed-mobile toolchain {field} mismatch")
    android_toolchain = _exact_payload(
        toolchain["android"],
        {"java_runtime", "compile_sdk"},
        "signed-mobile Android toolchain",
    )
    if android_toolchain != {
        "java_runtime": (
            "OpenJDK Runtime Environment Temurin-17.0.20+8 (build 17.0.20+8)"
        ),
        "compile_sdk": 36,
    }:
        raise ValueError("signed-mobile Android toolchain mismatch")
    configuration = _exact_payload(
        provenance["build_configuration"],
        {"use_mock", "api_base_url", "web_app_url"},
        "signed-mobile build configuration",
    )
    if configuration != {
        "use_mock": False,
        "api_base_url": "https://api.leva.ai.kr",
        "web_app_url": "https://app.leva.ai.kr",
    }:
        raise ValueError("signed-mobile build configuration is not production")

    android = _exact_payload(
        provenance["android"],
        {
            "artifact_path",
            "sha256",
            "bytes",
            "application_id",
            "version_name",
            "version_code",
            "signature_verified",
            "signing_classification",
            "play_app_signing",
            "signing_certificate_sha256",
        },
        "signed-mobile Android provenance",
    )
    android_expected = {
        "artifact_path": mobile["signed_apk_file"],
        "sha256": mobile["signed_apk_sha256"],
        "application_id": "ai.devpath.devpath_mobile",
        "signature_verified": True,
        "signing_classification": "org_keystore_release_test_distribution",
        "play_app_signing": False,
    }
    for field, expected in android_expected.items():
        if android[field] != expected:
            raise ValueError(f"signed-mobile Android {field} mismatch")
    _positive_int(android["bytes"], "signed-mobile Android bytes")
    _positive_int(android["version_code"], "signed-mobile Android version_code")
    if not isinstance(android["version_name"], str) or not android["version_name"].strip():
        raise ValueError("signed-mobile Android version_name is invalid")
    if not isinstance(android["signing_certificate_sha256"], str) or SHA64.fullmatch(
        android["signing_certificate_sha256"]
    ) is None:
        raise ValueError("signed-mobile Android signing certificate is invalid")

    approvals = _exact_payload(
        provenance["approvals"],
        {"android"},
        "signed-mobile approvals",
    )
    validated_approvals = {
        "signed-mobile-android": validate_approval_claim(
            "signed-mobile-android", approvals["android"]
        ),
    }
    return validated_approvals


def validate_manual_catalog_bundle(
    label: str,
    catalog_raw: bytes,
    provenance_raw: bytes,
    candidate: dict[str, Any],
) -> None:
    """Validate authoritative manual catalog and static protocol bytes at source."""
    contract = MANUAL_CATALOG_CONTRACTS[label]
    binding = _quality_catalog(candidate, label)
    if hashlib.sha256(catalog_raw).hexdigest() != binding["sha256"]:
        raise ValueError(f"{label} catalog raw SHA-256 mismatch")
    if hashlib.sha256(provenance_raw).hexdigest() != binding["provenance_sha256"]:
        raise ValueError(f"{label} static test-provenance raw SHA-256 mismatch")
    try:
        catalog = json.loads(catalog_raw.decode("utf-8"))
        provenance = json.loads(provenance_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} catalog/provenance is not valid UTF-8 JSON") from exc
    _validate_sanitized(catalog, f"{label} catalog")
    _validate_sanitized(provenance, f"{label} static test-provenance")
    catalog = _exact_payload(catalog, MANUAL_CATALOG_KEYS, f"{label} catalog")
    expected_catalog = {
        "schema_version": "leva.mission-spine.manual-at-catalog.v1",
        "lane": label,
        "assistive_technology": contract["assistive_technology"],
        "test_provenance_path": contract["provenance_path"],
        "case_count": contract["case_count"],
    }
    for field, expected in expected_catalog.items():
        if catalog[field] != expected:
            raise ValueError(f"{label} catalog {field} mismatch")
    cases = catalog["cases"]
    if not isinstance(cases, list) or len(cases) != contract["case_count"]:
        raise ValueError(f"{label} catalog cases mismatch")
    expected_entry_points = (
        ("today", "next_action")
        if label == "manual-nvda"
        else ("today", "next_action", "content", "offline_status")
    )
    actual_ids: list[str] = []
    for index, value in enumerate(cases):
        case = _exact_payload(value, MANUAL_CASE_KEYS, f"{label} catalog case {index}")
        if case["id"] != contract["case_ids"][index]:
            raise ValueError(f"{label} catalog case order/ID mismatch")
        actual_ids.append(case["id"])
        if case["surface"] != contract["surface"]:
            raise ValueError(f"{label} catalog case surface mismatch")
        if case["entry_point"] != expected_entry_points[index]:
            raise ValueError(f"{label} catalog case entry_point mismatch")
        for field in ("procedure", "expected"):
            rows = case[field]
            if (
                not isinstance(rows, list)
                or not rows
                or any(not isinstance(row, str) or not row.strip() for row in rows)
            ):
                raise ValueError(f"{label} catalog case {field} must be non-empty strings")
    if tuple(actual_ids) != contract["case_ids"]:
        raise ValueError(f"{label} catalog case IDs mismatch")

    provenance = _exact_payload(
        provenance,
        MANUAL_PROVENANCE_KEYS,
        f"{label} static test-provenance",
    )
    expected_provenance = {
        "schema_version": "leva.mission-spine.manual-at-test-provenance.v1",
        "lane": label,
        "catalog_path": contract["path"],
        "catalog_sha256": binding["sha256"],
        "assistive_technology": contract["assistive_technology"],
        "execution_mode": "manual_human",
        "required_platform": contract["required_platform"],
        "required_client": contract["required_client"],
        "required_artifact": contract["required_artifact"],
        "case_ids": list(contract["case_ids"]),
        "pass_policy": {
            "all_cases_required": True,
            "failed_case_count": 0,
            "synthetic_results_allowed": False,
            "emulator_allowed": False,
        },
    }
    if provenance != expected_provenance:
        raise ValueError(f"{label} static test-provenance contract mismatch")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _home_archive_path(value: str) -> str:
    if (
        not value
        or len(value) > 255
        or len(value.encode("utf-8")) != len(value)
        or value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or "\x00" in value
        or re.fullmatch(r"[A-Za-z0-9._/-]+", value) is None
    ):
        raise ValueError("home-dist: tar path is unsafe or noncanonical")
    parts = value.split("/")
    if parts[0] != "dist" or len(parts) < 2 or any(
        part in {"", ".", ".."} for part in parts
    ):
        raise ValueError("home-dist: tar entry must be a file beneath dist/")
    return value


def _home_ustar_path(value: str) -> tuple[str, str]:
    value = _home_archive_path(value)
    if len(value) <= 100:
        return value, ""
    for index in range(value.rfind("/"), 0, -1):
        if value[index] != "/":
            continue
        prefix = value[:index]
        name = value[index + 1 :]
        if len(prefix) <= 155 and len(name) <= 100:
            return name, prefix
    raise ValueError("home-dist: tar path does not fit canonical ustar fields")


def _home_octal(value: int, length: int) -> bytes:
    encoded = format(value, "o").rjust(length - 1, "0")
    if len(encoded) != length - 1:
        raise ValueError("home-dist: tar octal field overflow")
    return f"{encoded}\0".encode("ascii")


def _home_ustar_header(value: str, size: int) -> bytes:
    name, prefix = _home_ustar_path(value)
    header = bytearray(512)

    def write(offset: int, length: int, raw: bytes) -> None:
        if len(raw) > length:
            raise ValueError("home-dist: tar string field overflow")
        header[offset : offset + len(raw)] = raw

    write(0, 100, name.encode("ascii"))
    write(100, 8, _home_octal(0o644, 8))
    write(108, 8, _home_octal(0, 8))
    write(116, 8, _home_octal(0, 8))
    write(124, 12, _home_octal(size, 12))
    write(136, 12, _home_octal(0, 12))
    header[148:156] = b" " * 8
    header[156] = ord("0")
    write(257, 6, b"ustar\0")
    write(263, 2, b"00")
    write(329, 8, _home_octal(0, 8))
    write(337, 8, _home_octal(0, 8))
    write(345, 155, prefix.encode("ascii"))
    checksum = format(sum(header), "o").rjust(6, "0")
    if len(checksum) != 6:
        raise ValueError("home-dist: tar checksum field overflow")
    header[148:156] = f"{checksum}\0 ".encode("ascii")
    return bytes(header)


def _read_home_tar_string(field: bytes, label: str) -> str:
    zero = field.find(b"\0")
    end = len(field) if zero < 0 else zero
    if zero >= 0 and any(field[zero:]):
        raise ValueError(f"home-dist: {label} has bytes after NUL")
    value = field[:end]
    if any(byte < 0x20 or byte > 0x7E for byte in value):
        raise ValueError(f"home-dist: {label} must be printable ASCII")
    return value.decode("ascii")


def _read_home_tar_octal(field: bytes, label: str) -> int:
    if not field or field[-1] != 0 or re.fullmatch(rb"[0-7]+", field[:-1]) is None:
        raise ValueError(f"home-dist: {label} is not canonical octal")
    return int(field[:-1], 8)


def _decode_canonical_home_gzip(raw: bytes) -> bytes:
    expected_header = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03"
    if not 18 <= len(raw) <= MAX_HOME_PAYLOAD_BYTES:
        raise ValueError("home-dist: dist.tar.gz byte length is invalid")
    if raw[:10] != expected_header:
        raise ValueError("home-dist: dist.tar.gz header is not canonical")
    body_end = len(raw) - 8
    offset = 10
    chunks: list[bytes] = []
    total = 0
    saw_final = False
    while offset < body_end:
        if offset + 5 > body_end:
            raise ValueError("home-dist: stored DEFLATE header is truncated")
        control = raw[offset]
        if control not in {0, 1}:
            raise ValueError("home-dist: dist.tar.gz must use canonical stored DEFLATE")
        final = control == 1
        length, complement = struct.unpack_from("<HH", raw, offset + 1)
        if length <= 0 or complement != ((~length) & 0xFFFF):
            raise ValueError("home-dist: stored DEFLATE length is invalid")
        if not final and length != 65_535:
            raise ValueError("home-dist: non-final DEFLATE block is not canonical")
        offset += 5
        end = offset + length
        if end > body_end:
            raise ValueError("home-dist: stored DEFLATE block exceeds archive bounds")
        chunks.append(raw[offset:end])
        total += length
        if total > MAX_HOME_TAR_BYTES:
            raise ValueError("home-dist: dist.tar.gz expands beyond 100 MiB")
        offset = end
        if final:
            saw_final = True
            break
    if not saw_final or offset != body_end:
        raise ValueError("home-dist: dist.tar.gz has trailing or concatenated data")
    tar_bytes = b"".join(chunks)
    expected_crc, expected_size = struct.unpack_from("<II", raw, body_end)
    if (
        expected_crc != binascii.crc32(tar_bytes)
        or expected_size != (len(tar_bytes) & 0xFFFFFFFF)
    ):
        raise ValueError("home-dist: dist.tar.gz trailer mismatch")
    return tar_bytes


def validate_home_dist_archive_bytes(raw: bytes) -> None:
    """Require the producer's exact bounded canonical gzip/ustar dist package."""
    tar_bytes = _decode_canonical_home_gzip(raw)
    if (
        len(tar_bytes) < 3 * 512
        or len(tar_bytes) > MAX_HOME_TAR_BYTES
        or len(tar_bytes) % 512 != 0
    ):
        raise ValueError("home-dist: tar byte length is invalid")
    offset = 0
    previous = ""
    paths: set[str] = set()
    while offset < len(tar_bytes):
        header = tar_bytes[offset : offset + 512]
        if not any(header):
            if offset + 1024 != len(tar_bytes) or any(tar_bytes[offset:]):
                raise ValueError("home-dist: tar must end in exactly two zero blocks")
            break
        name = _read_home_tar_string(header[0:100], "tar name")
        prefix = _read_home_tar_string(header[345:500], "tar prefix")
        path = _home_archive_path(f"{prefix}/{name}" if prefix else name)
        size = _read_home_tar_octal(header[124:136], "tar size")
        if header != _home_ustar_header(path, size):
            raise ValueError("home-dist: tar header metadata is not canonical")
        if path in paths or (previous and previous >= path):
            raise ValueError("home-dist: tar paths are duplicated or out of order")
        paths.add(path)
        previous = path
        if len(paths) > MAX_HOME_ARCHIVE_FILES:
            raise ValueError("home-dist: tar contains too many files")
        content_start = offset + 512
        content_end = content_start + size
        padded_end = content_start + ((size + 511) // 512) * 512
        if content_end > len(tar_bytes) - 1024 or padded_end > len(tar_bytes):
            raise ValueError("home-dist: tar entry exceeds archive bounds")
        if any(tar_bytes[content_end:padded_end]):
            raise ValueError("home-dist: tar padding is not canonical")
        offset = padded_end
    if not paths or "dist/index.html" not in paths:
        raise ValueError("home-dist: canonical dist/index.html is required")


def validate_home_dist_archive(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError("home-dist: dist.tar.gz must be a regular file")
    size = path.stat().st_size
    if size <= 0 or size > MAX_HOME_PAYLOAD_BYTES:
        raise ValueError("home-dist: dist.tar.gz byte length is invalid")
    validate_home_dist_archive_bytes(path.read_bytes())


def _extract_exact_root_artifact_archive(
    archive_path: Path,
    destination: Path,
    label: str,
    limits: dict[str, int],
    maximum_total: int,
) -> None:
    """Safely materialize an exact flat GitHub artifact ZIP."""
    expected = set(limits)
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"{label}: artifact archive is not a valid ZIP") from exc
    with archive:
        infos = archive.infolist()
        names: set[str] = set()
        total = 0
        for info in infos:
            name = info.filename
            if (
                not name
                or "\\" in name
                or "\x00" in name
                or name.startswith("/")
                or re.match(r"^[A-Za-z]:", name)
                or PurePosixPath(name).parts != (name,)
            ):
                raise ValueError(f"{label}: artifact ZIP path is unsafe")
            if name in names:
                raise ValueError(f"{label}: artifact ZIP contains duplicate entries")
            names.add(name)
            mode = (info.external_attr >> 16) & 0xFFFF
            if info.is_dir() or stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                raise ValueError(f"{label}: artifact ZIP may contain regular files only")
            if info.flag_bits & 0x1:
                raise ValueError(f"{label}: encrypted artifact ZIP entries are forbidden")
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ValueError(f"{label}: artifact ZIP compression is unsupported")
            if name not in expected:
                raise ValueError(f"{label}: artifact ZIP file set is not canonical")
            if info.file_size <= 0 or info.file_size > limits[name]:
                raise ValueError(f"{label}: artifact ZIP entry size is invalid")
            total += info.file_size
            if total > maximum_total:
                raise ValueError(f"{label}: artifact ZIP expands beyond the safety limit")
        if names != expected:
            raise ValueError(f"{label}: artifact ZIP file set is not canonical")
        destination.mkdir(parents=True, exist_ok=False)
        for info in infos:
            target = destination / info.filename
            written = 0
            try:
                with archive.open(info, "r") as source, target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        if written > info.file_size:
                            raise ValueError(f"{label}: artifact ZIP entry size changed")
                        output.write(chunk)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise ValueError(f"{label}: artifact ZIP entry failed CRC validation") from exc
            if written != info.file_size:
                raise ValueError(f"{label}: artifact ZIP entry size mismatch")


def extract_home_artifact_archive(archive_path: Path, destination: Path) -> None:
    """Safely materialize the exact two-file Home artifact ZIP."""
    _extract_exact_root_artifact_archive(
        archive_path,
        destination,
        "home-dist",
        {
            "dist.tar.gz": MAX_HOME_PAYLOAD_BYTES,
            "evidence.json": MAX_EVIDENCE_BYTES,
        },
        MAX_HOME_PAYLOAD_BYTES + MAX_EVIDENCE_BYTES,
    )


def extract_ai_release_eval_archive(archive_path: Path, destination: Path) -> None:
    """Safely materialize the exact one-file AI evaluation artifact ZIP."""
    _extract_exact_root_artifact_archive(
        archive_path,
        destination,
        "ai-release-eval",
        {"evidence.json": MAX_EVIDENCE_BYTES},
        MAX_EVIDENCE_BYTES,
    )


def extract_privacy_approval_archive(archive_path: Path, destination: Path) -> None:
    """Safely materialize the exact one-file privacy approval artifact ZIP."""
    _extract_exact_root_artifact_archive(
        archive_path,
        destination,
        "privacy-approval",
        {"evidence.json": MAX_EVIDENCE_BYTES},
        MAX_EVIDENCE_BYTES,
    )


def extract_migration_result_archive(archive_path: Path, destination: Path) -> None:
    """Safely materialize the exact one-file Shared migration result artifact."""
    _extract_exact_root_artifact_archive(
        archive_path,
        destination,
        "shared-migration-result",
        {"evidence.json": MAX_EVIDENCE_BYTES},
        MAX_EVIDENCE_BYTES,
    )


def extract_production_canary_archive(archive_path: Path, destination: Path) -> None:
    """Safely materialize the exact one-file production canary artifact."""
    _extract_exact_root_artifact_archive(
        archive_path,
        destination,
        "production-canary",
        {"evidence.json": MAX_EVIDENCE_BYTES},
        MAX_EVIDENCE_BYTES,
    )


def extract_landing_evidence_archive(archive_path: Path, destination: Path) -> None:
    """Safely materialize the exact one-file Landing-last evidence artifact."""
    _extract_exact_root_artifact_archive(
        archive_path,
        destination,
        "landing-last",
        {"evidence.json": MAX_EVIDENCE_BYTES},
        MAX_EVIDENCE_BYTES,
    )


def extract_candidate_spec_archive(archive_path: Path, destination: Path) -> None:
    """Safely materialize the exact one-file canonical candidate artifact ZIP."""
    _extract_exact_root_artifact_archive(
        archive_path,
        destination,
        "candidate-spec",
        {"candidate-spec.json": MAX_CANDIDATE_SPEC_BYTES},
        MAX_CANDIDATE_SPEC_BYTES,
    )


def _download_exact_root_artifact_archive(
    command_env: dict[str, str],
    repository: str,
    artifact_id: int,
    metadata: dict[str, Any],
    destination: Path,
    label: str,
    maximum_zip_bytes: int,
    extractor: Any,
) -> None:
    digest = metadata.get("digest")
    match = re.fullmatch(r"sha256:([0-9a-f]{64})", digest or "")
    archive_size = metadata.get("size_in_bytes")
    if match is None:
        raise ValueError(f"{label}: artifact metadata digest is missing or invalid")
    if (
        isinstance(archive_size, bool)
        or not isinstance(archive_size, int)
        or not 0 < archive_size <= maximum_zip_bytes
    ):
        raise ValueError(f"{label}: artifact metadata size is invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f"mission-spine-{label}-archive-", dir=destination.parent
    ) as temp_dir:
        archive_path = Path(temp_dir) / "artifact.zip"
        with archive_path.open("xb") as output:
            download = subprocess.run(
                [
                    "gh",
                    "api",
                    "-H",
                    "Accept: application/vnd.github+json",
                    f"repos/{repository}/actions/artifacts/{artifact_id}/zip",
                ],
                stdout=output,
                stderr=subprocess.DEVNULL,
                env=command_env,
                check=False,
            )
        if download.returncode != 0:
            raise ValueError(f"{label}: artifact archive download failed")
        if archive_path.stat().st_size != archive_size:
            raise ValueError(f"{label}: artifact archive size differs from metadata")
        if _sha256_file(archive_path) != match.group(1):
            raise ValueError(f"{label}: artifact archive digest differs from metadata")
        extractor(archive_path, destination)


def download_home_artifact_archive(
    command_env: dict[str, str],
    repository: str,
    artifact_id: int,
    metadata: dict[str, Any],
    destination: Path,
) -> None:
    _download_exact_root_artifact_archive(
        command_env,
        repository,
        artifact_id,
        metadata,
        destination,
        "home-dist",
        MAX_HOME_ARTIFACT_ZIP_BYTES,
        extract_home_artifact_archive,
    )


def download_ai_release_eval_archive(
    command_env: dict[str, str],
    repository: str,
    artifact_id: int,
    metadata: dict[str, Any],
    destination: Path,
) -> None:
    _download_exact_root_artifact_archive(
        command_env,
        repository,
        artifact_id,
        metadata,
        destination,
        "ai-release-eval",
        MAX_AI_ARTIFACT_ZIP_BYTES,
        extract_ai_release_eval_archive,
    )


def download_privacy_approval_archive(
    command_env: dict[str, str],
    repository: str,
    artifact_id: int,
    metadata: dict[str, Any],
    destination: Path,
) -> None:
    _download_exact_root_artifact_archive(
        command_env,
        repository,
        artifact_id,
        metadata,
        destination,
        "privacy-approval",
        MAX_AI_ARTIFACT_ZIP_BYTES,
        extract_privacy_approval_archive,
    )


def download_migration_result_archive(
    command_env: dict[str, str],
    repository: str,
    artifact_id: int,
    metadata: dict[str, Any],
    destination: Path,
) -> None:
    _download_exact_root_artifact_archive(
        command_env,
        repository,
        artifact_id,
        metadata,
        destination,
        "shared-migration-result",
        MAX_AI_ARTIFACT_ZIP_BYTES,
        extract_migration_result_archive,
    )


def download_production_canary_archive(
    command_env: dict[str, str],
    repository: str,
    artifact_id: int,
    metadata: dict[str, Any],
    destination: Path,
) -> None:
    _download_exact_root_artifact_archive(
        command_env,
        repository,
        artifact_id,
        metadata,
        destination,
        "production-canary",
        MAX_AI_ARTIFACT_ZIP_BYTES,
        extract_production_canary_archive,
    )


def download_landing_evidence_archive(
    command_env: dict[str, str],
    repository: str,
    artifact_id: int,
    metadata: dict[str, Any],
    destination: Path,
) -> None:
    _download_exact_root_artifact_archive(
        command_env,
        repository,
        artifact_id,
        metadata,
        destination,
        "landing-last",
        MAX_AI_ARTIFACT_ZIP_BYTES,
        extract_landing_evidence_archive,
    )


def download_candidate_spec_archive(
    command_env: dict[str, str],
    artifact_id: int,
    metadata: dict[str, Any],
    destination: Path,
) -> None:
    _download_exact_root_artifact_archive(
        command_env,
        CANDIDATE_REPOSITORY,
        artifact_id,
        metadata,
        destination,
        "candidate-spec",
        MAX_CANDIDATE_ARTIFACT_ZIP_BYTES,
        extract_candidate_spec_archive,
    )


def _extract_signed_mobile_archive(
    archive_path: Path,
    destination: Path,
    candidate: dict[str, Any],
) -> None:
    """Inspect and extract the signed bundle without trusting ZIP paths or file types."""
    mobile = candidate["quality_evidence_inputs"]["mobile_test_artifacts"]
    expected_files = {
        mobile["build_provenance_file"],
        mobile["signed_apk_file"],
    }
    expected_directories = {
        parent.as_posix()
        for filename in expected_files
        for parent in PurePosixPath(filename).parents
        if parent != PurePosixPath(".")
    }
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("signed-mobile: artifact archive is not a valid ZIP") from exc
    with archive:
        infos = archive.infolist()
        actual_files: set[str] = set()
        seen_entries: set[str] = set()
        total_uncompressed = 0
        for info in infos:
            name = info.filename
            if (
                not name
                or "\\" in name
                or "\x00" in name
                or name.startswith("/")
                or re.match(r"^[A-Za-z]:", name)
            ):
                raise ValueError("signed-mobile: artifact archive path is unsafe")
            normalized = name[:-1] if name.endswith("/") else name
            parts = PurePosixPath(normalized).parts
            if not normalized or any(part in {"", ".", ".."} for part in parts):
                raise ValueError("signed-mobile: artifact archive path is unsafe")
            if normalized in seen_entries:
                raise ValueError("signed-mobile: artifact archive contains duplicate entries")
            seen_entries.add(normalized)
            mode = (info.external_attr >> 16) & 0xFFFF
            kind = stat.S_IFMT(mode)
            if info.is_dir():
                if kind not in {0, stat.S_IFDIR}:
                    raise ValueError("signed-mobile: artifact archive directory type is unsafe")
                if normalized not in expected_directories:
                    raise ValueError(
                        "signed-mobile: artifact archive contains an unexpected directory"
                    )
                continue
            if kind not in {0, stat.S_IFREG}:
                raise ValueError("signed-mobile: artifact archive may not contain links")
            if info.flag_bits & 0x1:
                raise ValueError("signed-mobile: encrypted artifact entries are forbidden")
            if normalized not in expected_files:
                raise ValueError("signed-mobile: artifact archive contains an unexpected file")
            limit = (
                MAX_EVIDENCE_BYTES
                if normalized == mobile["build_provenance_file"]
                else MAX_SIGNED_MOBILE_BINARY_BYTES
            )
            if info.file_size <= 0 or info.file_size > limit:
                raise ValueError("signed-mobile: artifact archive entry size is invalid")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_SIGNED_MOBILE_ARCHIVE_BYTES:
                raise ValueError("signed-mobile: artifact archive expands beyond the safety limit")
            actual_files.add(normalized)
        if actual_files != expected_files:
            raise ValueError("signed-mobile: artifact archive file set is not canonical")

        destination.mkdir(parents=True, exist_ok=False)
        for info in infos:
            if info.is_dir():
                continue
            relative = info.filename
            target = destination.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.open(info, "r") as source, target.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    written += len(chunk)
                    if written > info.file_size:
                        raise ValueError("signed-mobile: artifact entry size changed while reading")
                    output.write(chunk)
            if written != info.file_size:
                raise ValueError("signed-mobile: artifact entry size mismatch")


def _download_signed_mobile_archive(
    command_env: dict[str, str],
    repository: str,
    artifact_id: int,
    expected_sha256: str,
    destination: Path,
    candidate: dict[str, Any],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="mission-spine-signed-mobile-archive-",
        dir=destination.parent,
    ) as temp_dir:
        archive_path = Path(temp_dir) / "artifact.zip"
        with archive_path.open("xb") as output:
            download = subprocess.run(
                [
                    "gh",
                    "api",
                    "-H",
                    "Accept: application/vnd.github+json",
                    f"repos/{repository}/actions/artifacts/{artifact_id}/zip",
                ],
                stdout=output,
                stderr=subprocess.DEVNULL,
                env=command_env,
                check=False,
            )
        if download.returncode != 0:
            raise ValueError("signed-mobile: artifact archive download failed")
        size = archive_path.stat().st_size
        if size <= 0 or size > MAX_SIGNED_MOBILE_ARCHIVE_BYTES:
            raise ValueError("signed-mobile: artifact archive size is invalid")
        if _sha256_file(archive_path) != expected_sha256:
            raise ValueError("signed-mobile: downloaded artifact archive digest mismatch")
        _extract_signed_mobile_archive(archive_path, destination, candidate)


def validate_signed_mobile_bundle(
    root: Path,
    candidate: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Verify exact extracted file layout, raw hashes, sizes, and provenance."""
    mobile = candidate["quality_evidence_inputs"]["mobile_test_artifacts"]
    expected_entries = sorted(
        {
            mobile["build_provenance_file"],
            "mobile",
            "mobile/android",
            mobile["signed_apk_file"],
        }
    )
    entries = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    if entries != expected_entries:
        raise ValueError("signed-mobile artifact contains an unexpected file set")
    for relative in ("mobile", "mobile/android"):
        directory = root / relative
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError("signed-mobile artifact directories must not be links")
    for field, hash_field in (
        ("build_provenance_file", "build_provenance_sha256"),
        ("signed_apk_file", "signed_apk_sha256"),
    ):
        path = root / mobile[field]
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"signed-mobile {field} must be a regular file")
        size = path.stat().st_size
        limit = MAX_EVIDENCE_BYTES if field == "build_provenance_file" else MAX_SIGNED_MOBILE_BINARY_BYTES
        if size <= 0 or size > limit:
            raise ValueError(f"signed-mobile {field} size is invalid")
        if _sha256_file(path) != mobile[hash_field]:
            raise ValueError(f"signed-mobile {hash_field} mismatch")
    provenance_path = root / mobile["build_provenance_file"]
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("signed-mobile build provenance is not valid UTF-8 JSON") from exc
    approvals = validate_signed_mobile_provenance(provenance, candidate)
    if provenance["android"]["bytes"] != (root / mobile["signed_apk_file"]).stat().st_size:
        raise ValueError("signed-mobile Android byte count mismatch")
    return approvals


def validate_protected_approval(
    label: str,
    claim_value: Any,
    environment: Any,
    approvals: Any,
    jobs: Any,
    run: Any,
    expected_head: str,
    *,
    expected_repository: str = "DevPathAi/devpath-frontend",
    expected_branch: str = "main",
    approved_team_ids: set[int] | None = None,
) -> None:
    """Reconcile an embedded claim with live environment, review, job, and run data."""
    claim = validate_approval_claim(label, claim_value)
    if not isinstance(environment, dict):
        raise ValueError(f"{label}: protected environment response is invalid")
    if (
        environment.get("id") != claim["approval_environment_id"]
        or environment.get("name") != claim["approval_environment"]
        or environment.get("can_admins_bypass") is not False
    ):
        raise ValueError(f"{label}: protected environment identity mismatch")
    rules = environment.get("protection_rules")
    reviewer_rules = (
        [rule for rule in rules if isinstance(rule, dict) and rule.get("type") == "required_reviewers"]
        if isinstance(rules, list)
        else []
    )
    if len(reviewer_rules) != 1:
        raise ValueError(f"{label}: exactly one required-reviewers protection rule is required")
    reviewer_rule = reviewer_rules[0]
    if reviewer_rule.get("prevent_self_review") is not True:
        raise ValueError(f"{label}: protected environment must prevent self-review")
    configured_reviewers = reviewer_rule.get("reviewers")
    if not isinstance(configured_reviewers, list) or not configured_reviewers:
        raise ValueError(f"{label}: protected environment has no configured reviewers")

    approved_team_ids = approved_team_ids or set()
    reviewer_configured = False
    for entry in configured_reviewers:
        if not isinstance(entry, dict) or not isinstance(entry.get("reviewer"), dict):
            continue
        reviewer = entry["reviewer"]
        if entry.get("type") == "User" and (
            reviewer.get("id") == claim["approved_by_id"]
            and reviewer.get("login") == claim["approved_by"]
        ):
            reviewer_configured = True
        if entry.get("type") == "Team" and reviewer.get("id") in approved_team_ids:
            reviewer_configured = True
    if not reviewer_configured:
        raise ValueError(f"{label}: approver is not a configured user or team member")

    if not isinstance(run, dict):
        raise ValueError(f"{label}: protected run response is invalid")
    if run.get("run_attempt") != 1:
        raise ValueError(f"{label}: protected approval run attempt must be 1")
    if run.get("head_branch") != expected_branch:
        raise ValueError(f"{label}: protected approval run head_branch mismatch")
    if run.get("head_sha") != expected_head:
        raise ValueError(f"{label}: protected approval run head SHA mismatch")
    if (run.get("repository") or {}).get("full_name") != expected_repository:
        raise ValueError(f"{label}: protected approval run repository mismatch")
    for label_suffix, initiator in (
        ("actor", run.get("actor")),
        ("triggering actor", run.get("triggering_actor")),
    ):
        if not isinstance(initiator, dict):
            raise ValueError(f"{label}: protected run initiator identity is missing")
        _positive_int(
            initiator.get("id"),
            f"{label} protected run {label_suffix} id",
        )
        login = initiator.get("login")
        if not isinstance(login, str) or re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", login
        ) is None:
            raise ValueError(f"{label}: protected run initiator identity is invalid")

    if not isinstance(approvals, list):
        raise ValueError(f"{label}: approval history is invalid")
    environment_approvals = []
    for review in approvals:
        if not isinstance(review, dict) or review.get("state") != "approved":
            continue
        environments = review.get("environments")
        if not isinstance(environments, list):
            continue
        matching_environments = [
            item
            for item in environments
            if isinstance(item, dict)
            and item.get("id") == claim["approval_environment_id"]
            and item.get("name") == claim["approval_environment"]
        ]
        if len(matching_environments) == 1:
            environment_approvals.append(review)
    if len(environment_approvals) != 1:
        raise ValueError(f"{label}: exactly one approved review is required")
    approved_user = environment_approvals[0].get("user") or {}
    if (
        approved_user.get("id") != claim["approved_by_id"]
        or approved_user.get("login") != claim["approved_by"]
    ):
        raise ValueError(f"{label}: approved reviewer identity mismatch")

    if not isinstance(jobs, list):
        raise ValueError(f"{label}: protected job list is invalid")
    matching_jobs = [
        job
        for job in jobs
        if isinstance(job, dict) and job.get("name") == claim["approval_job_name"]
    ]
    if len(matching_jobs) != 1:
        raise ValueError(f"{label}: exactly one protected approval job is required")
    job = matching_jobs[0]
    if job.get("status") != "completed" or job.get("conclusion") != "success":
        raise ValueError(f"{label}: protected approval job must be successful")
    if job.get("head_sha") != expected_head:
        raise ValueError(f"{label}: protected approval job head SHA mismatch")
    if job.get("started_at") != claim["approval_effective_at"]:
        raise ValueError(f"{label}: approval_effective_at must equal protected job started_at")
    job_started_at = _utc_z(job.get("started_at"), f"{label} protected job started_at")
    run_started_at = _utc_z(run.get("run_started_at"), f"{label} run_started_at")
    run_updated_at = _utc_z(run.get("updated_at"), f"{label} run updated_at")
    if not run_started_at <= job_started_at <= run_updated_at:
        raise ValueError(f"{label}: protected approval job is outside the live run interval")


def validate_manual_chronology(
    label: str,
    signed_provenance: dict[str, Any],
    signed_run: dict[str, Any],
    manual_run: dict[str, Any],
    approval_claim: dict[str, Any],
) -> None:
    signed_completed = _utc_z(
        signed_run.get("updated_at"), "signed-mobile run updated_at"
    )
    manual_started = _utc_z(
        manual_run.get("run_started_at"), f"{label} run_started_at"
    )
    if signed_completed >= manual_started:
        raise ValueError(f"{label}: manual run started before signed-mobile completion")


def _frontend_surface(fixture_id: str) -> str:
    if fixture_id.startswith("web-"):
        return "web"
    if fixture_id.startswith("admin-"):
        return "admin"
    if fixture_id.startswith("mobile-"):
        return "mobile"
    return "dp_design"


def _frontend_expected_case_identity(label: str) -> list[tuple[str, str, str]]:
    kind = "visual" if label == "frontend-visual" else "a11y"
    profiles = (
        [(width, theme) for width in (320, 600, 840, 1240) for theme in ("light", "dark")]
        if kind == "visual"
        else [(320, "light"), (1240, "dark")]
    )
    result: list[tuple[str, str, str]] = []
    extension = "png" if kind == "visual" else "json"
    for fixture_id in FRONTEND_FIXTURE_IDS:
        surface = _frontend_surface(fixture_id)
        for width, theme in profiles:
            suffix = (
                f"visual--w{width}--{theme}"
                if kind == "visual"
                else f"a11y--w{width}--{theme}--text200"
            )
            case_id = f"{fixture_id}--{suffix}"
            result.append((fixture_id, case_id, f"{kind}/{surface}/{case_id}.{extension}"))
    return result


def _read_frontend_json(path: Path, label: str) -> tuple[bytes, Any]:
    raw = path.read_bytes()
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError(f"{label}: packaged metadata exceeds the sanitized size limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label}: packaged metadata is not valid UTF-8 JSON") from exc
    _validate_sanitized(value, f"{label} packaged metadata")
    return raw, value


def _frontend_expected_entries(label: str) -> list[str]:
    files = set(FRONTEND_EVIDENCE_FILES[label])
    directories: set[str] = set()
    for filename in files:
        parent = Path(filename).parent
        while parent != Path("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return sorted(files | directories)


def validate_frontend_evidence_bundle(
    label: str,
    root: Path,
    evidence: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Verify the exact sanitized four-file frontend lane package and raw bindings."""
    if label not in FRONTEND_EVIDENCE_FILES:
        raise ValueError(f"{label}: not a frontend evidence lane")
    archive_entries = list(root.rglob("*"))
    if any(path.is_symlink() for path in archive_entries):
        raise ValueError(f"{label}: packaged evidence may not contain links")
    entries = sorted(path.relative_to(root).as_posix() for path in archive_entries)
    expected_files = sorted(FRONTEND_EVIDENCE_FILES[label])
    if entries != _frontend_expected_entries(label):
        raise ValueError(f"{label}: exactly four sanitized files are required")
    for filename in expected_files:
        path = root / filename
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"{label}: packaged evidence must be regular files")

    lane = FRONTEND_CATALOG_CONTRACTS[label]
    catalog_binding = _quality_catalog(candidate, label)
    kind = "visual" if label == "frontend-visual" else "a11y"
    catalog_file = lane["path"]
    manifest_file = f"artifacts/et13/{kind}-manifest.v1.json"
    manifest_schema = f"leva.et13.{kind}-manifest.v1"

    evidence_raw, packaged_evidence = _read_frontend_json(root / "evidence.json", label)
    if packaged_evidence != evidence:
        raise ValueError(f"{label}: evidence.json does not match the verified payload")
    if len(evidence_raw) > MAX_EVIDENCE_BYTES:
        raise ValueError(f"{label}: evidence exceeds the sanitized size limit")

    catalog_raw, generated = _read_frontend_json(root / catalog_file, label)
    if hashlib.sha256(catalog_raw).hexdigest() != catalog_binding["sha256"]:
        raise ValueError(f"{label}: catalog raw SHA does not match candidate prebinding")
    generated = _exact_payload(
        generated,
        {
            "schema_version", "case_catalog_version", "catalog_sha256",
            "projection_contract_sha256", "projection_matrix", "fixture_ids", "case_count",
            "surface_case_counts", "cases",
        },
        f"{label} generated catalog",
    )
    if (
        generated["schema_version"] != lane["case_catalog_schema_version"]
        or generated["case_catalog_version"] != lane["case_catalog_version"]
        or generated["projection_contract_sha256"] != FRONTEND_PROJECTION_CONTRACT_SHA256
        or generated["projection_contract_sha256"]
        != catalog_binding["projection_contract_sha256"]
        or generated["projection_matrix"] != FRONTEND_PROJECTION_MATRIX
        or _canonical_sha256(generated["projection_matrix"])
        != generated["projection_contract_sha256"]
        or generated["fixture_ids"] != list(FRONTEND_FIXTURE_IDS)
        or generated["case_count"] != lane["case_count"]
        or generated["surface_case_counts"] != lane["surface_case_counts"]
    ):
        raise ValueError(f"{label}: generated catalog identity or exact matrix is invalid")
    if not isinstance(generated["catalog_sha256"], str) or SHA64.fullmatch(generated["catalog_sha256"]) is None:
        raise ValueError(f"{label}: source catalog SHA is invalid")
    _positive_int(generated["case_count"], f"{label} generated case_count")
    _validate_surface_counts(
        generated["surface_case_counts"], generated["case_count"], label
    )
    cases = generated["cases"]
    expected_identities = _frontend_expected_case_identity(label)
    if not isinstance(cases, list) or len(cases) != len(expected_identities):
        raise ValueError(f"{label}: generated catalog does not contain the exact ordered cases")
    generated_case_keys = {
        "case_id", "fixture_id", "owner", "distribution", "route", "ready_semantics_label",
        "surface_label", "capture_scope", "source_widget", "substitutions", "width", "height",
        "device_pixel_ratio", "theme", "text_scale_percent", "locale", "timezone",
        "reduced_motion", "artifact_path",
    }
    expected_width_theme = (
        [(width, theme, 100) for width in (320, 600, 840, 1240) for theme in ("light", "dark")]
        if kind == "visual"
        else [(320, "light", 200), (1240, "dark", 200)]
    )
    for index, (case, identity) in enumerate(zip(cases, expected_identities, strict=True)):
        row = _exact_payload(case, generated_case_keys, f"{label} generated case {index}")
        fixture_id, case_id, artifact_path = identity
        width, theme, text_scale = expected_width_theme[index % len(expected_width_theme)]
        owner = _frontend_surface(fixture_id)
        distribution = "web" if owner == "dp_design" else owner
        projection = FRONTEND_PROJECTION_MATRIX[index // len(expected_width_theme)]
        for field in ("width", "height", "device_pixel_ratio", "text_scale_percent"):
            _positive_int(row[field], f"{label} generated case {field}")
        if (
            row["fixture_id"] != fixture_id
            or row["case_id"] != case_id
            or row["artifact_path"] != artifact_path
            or row["width"] != width
            or row["theme"] != theme
            or row["text_scale_percent"] != text_scale
            or row["owner"] != owner
            or row["distribution"] != distribution
            or row["route"] != f"/?fixture={fixture_id}"
            or row["ready_semantics_label"] != f"ET13_READY:{fixture_id}"
            or row["surface_label"] != fixture_id
            or row["capture_scope"] != projection["capture_scope"]
            or row["source_widget"] != projection["source_widget"]
            or row["substitutions"] != projection["substitutions"]
            or row["height"] != 900
            or row["device_pixel_ratio"] != 1
            or row["locale"] != "ko-KR"
            or row["timezone"] != "UTC"
            or row["reduced_motion"] is not True
        ):
            raise ValueError(f"{label}: generated catalog does not contain the exact ordered cases")

    provenance_raw, provenance = _read_frontend_json(root / "artifacts/et13/provenance.v1.json", label)
    if hashlib.sha256(provenance_raw).hexdigest() != catalog_binding["input_provenance_file_sha256"]:
        raise ValueError(f"{label}: input provenance raw SHA does not match candidate prebinding")
    provenance = _exact_payload(
        provenance,
        {
            "schema_version", "kind", "source_sha", "catalog_sha256", "case_catalog_sha256",
            "projection_contract_sha256", "assets_lock_sha256", "renderer_lock_sha256",
            "renderer_image_digest", "build_marker_sha256", "baseline_authentication",
            "input_provenance_sha256",
        },
        f"{label} input provenance",
    )
    provenance_inputs = {
        key: value
        for key, value in provenance.items()
        if key != "input_provenance_sha256"
    }
    canonical_provenance = _canonical_sha256(provenance_inputs)
    if (
        provenance["schema_version"] != "leva.et13.input-provenance.v1"
        or provenance["kind"] != kind
        or provenance["source_sha"] != candidate["frontend"]["source_sha"]
        or provenance["catalog_sha256"] != generated["catalog_sha256"]
        or provenance["case_catalog_sha256"] != catalog_binding["sha256"]
        or provenance["projection_contract_sha256"]
        != FRONTEND_PROJECTION_CONTRACT_SHA256
        or provenance["projection_contract_sha256"]
        != generated["projection_contract_sha256"]
        or provenance["input_provenance_sha256"] != canonical_provenance
        or canonical_provenance != catalog_binding["input_provenance_sha256"]
        or evidence["input_provenance_sha256"] != canonical_provenance
        or evidence["input_provenance_file_sha256"] != hashlib.sha256(provenance_raw).hexdigest()
    ):
        raise ValueError(f"{label}: canonical or raw input provenance binding mismatch")
    authentication = validate_frontend_baseline_authentication(
        provenance["baseline_authentication"],
        candidate,
        _quality_catalog(candidate, "frontend-visual"),
    )
    for field in ("assets_lock_sha256", "renderer_lock_sha256", "build_marker_sha256"):
        if not isinstance(provenance[field], str) or SHA64.fullmatch(provenance[field]) is None:
            raise ValueError(f"{label}: {field} is invalid")
    if not isinstance(provenance["renderer_image_digest"], str) or re.fullmatch(
        r"sha256:[0-9a-f]{64}", provenance["renderer_image_digest"]
    ) is None:
        raise ValueError(f"{label}: renderer image digest is invalid")

    manifest_raw, manifest = _read_frontend_json(root / manifest_file, label)
    if hashlib.sha256(manifest_raw).hexdigest() != evidence["result_manifest_sha256"]:
        raise ValueError(f"{label}: result manifest raw SHA mismatch")
    manifest_keys = {
        "schema_version", "case_catalog_version", "case_catalog_schema_version", "fixture_ids",
        "source_sha", "catalog_sha256", "case_catalog_sha256", "projection_contract_sha256",
        "assets_lock_sha256",
        "renderer_lock_sha256", "input_provenance_sha256", "renderer_image",
        "renderer_image_digest", "capture_network", "unexpected_request_policy",
        "capture_surface", "device_evidence", "external_accessibility_status",
        "evidence_mode", "case_count", "surface_case_counts", "cases",
    }
    if kind == "visual":
        manifest_keys |= {"baseline_status", "baseline_set_sha256", "baseline_approval_sha256"}
    manifest = _exact_payload(manifest, manifest_keys, f"{label} result manifest")
    expected_manifest = {
        "schema_version": manifest_schema,
        "case_catalog_version": lane["case_catalog_version"],
        "case_catalog_schema_version": lane["case_catalog_schema_version"],
        "fixture_ids": list(FRONTEND_FIXTURE_IDS),
        "source_sha": candidate["frontend"]["source_sha"],
        "catalog_sha256": generated["catalog_sha256"],
        "case_catalog_sha256": catalog_binding["sha256"],
        "projection_contract_sha256": FRONTEND_PROJECTION_CONTRACT_SHA256,
        "assets_lock_sha256": provenance["assets_lock_sha256"],
        "renderer_lock_sha256": provenance["renderer_lock_sha256"],
        "input_provenance_sha256": canonical_provenance,
        "renderer_image_digest": provenance["renderer_image_digest"],
        "capture_network": "none",
        "unexpected_request_policy": "fail",
        "capture_surface": "flutter_web_release_projection",
        "device_evidence": False,
        "external_accessibility_status": "not_satisfied",
        "evidence_mode": "release_ready",
        "case_count": lane["case_count"],
        "surface_case_counts": lane["surface_case_counts"],
    }
    for field, expected in expected_manifest.items():
        if manifest[field] != expected:
            raise ValueError(f"{label}: result manifest {field} mismatch")
    if manifest["device_evidence"] is not False:
        raise ValueError(f"{label}: result manifest device_evidence mismatch")
    _positive_int(manifest["case_count"], f"{label} result manifest case_count")
    _validate_surface_counts(
        manifest["surface_case_counts"], manifest["case_count"], label
    )
    if (
        not isinstance(manifest["renderer_image"], str)
        or not manifest["renderer_image"].endswith("@" + provenance["renderer_image_digest"])
    ):
        raise ValueError(f"{label}: renderer image is not digest pinned")
    if kind == "visual":
        for field, expected in (
            ("baseline_status", "approved"),
            ("baseline_set_sha256", catalog_binding["baseline_set_sha256"]),
            ("baseline_approval_sha256", catalog_binding["baseline_approval_sha256"]),
        ):
            if manifest[field] != expected or evidence[field] != expected:
                raise ValueError(f"{label}: approved baseline binding mismatch")

    result_cases = manifest["cases"]
    if not isinstance(result_cases, list) or len(result_cases) != len(cases):
        raise ValueError(f"{label}: result manifest must contain exact ordered passed cases")
    for index, (result, generated_case) in enumerate(zip(result_cases, cases, strict=True)):
        result_keys = {"case_id", "status", "artifact_path", "sha256", "bytes"}
        if kind == "a11y":
            result_keys |= {
                "standard", "critical_violations", "serious_violations", "other_violations",
                "passes", "incomplete",
            }
        row = _exact_payload(result, result_keys, f"{label} result case {index}")
        if (
            row["case_id"] != generated_case["case_id"]
            or row["artifact_path"] != generated_case["artifact_path"]
            or row["status"] != "passed"
            or not isinstance(row["sha256"], str)
            or SHA64.fullmatch(row["sha256"]) is None
            or row["sha256"] == "0" * 64
        ):
            raise ValueError(f"{label}: result manifest must contain exact ordered passed cases")
        _positive_int(row["bytes"], f"{label} result case bytes")
        if kind == "a11y":
            if row["standard"] != "WCAG 2.2 AA":
                raise ValueError(f"{label}: standard must be WCAG 2.2 AA")
            for field in (
                "critical_violations", "serious_violations", "other_violations", "passes", "incomplete",
            ):
                _nonnegative_int(row[field], f"{label} result case {field}")
            if row["critical_violations"] != 0 or row["serious_violations"] != 0:
                raise ValueError(f"{label}: result manifest contains blocking accessibility violations")
    return authentication


def _validate_home_evidence_payload(
    label: str,
    payload: Any,
    candidate_hash: str,
    candidate: dict[str, Any],
) -> None:
    value = _exact_payload(
        payload,
        {
            "$schema", "schema_version", "document_type", "evidence_mode", "binding",
            "runtime", "theme_coverage", "baseline_review", "summary", "cases", "privacy",
        },
        label,
    )
    expected_kind = "visual" if label == "home-visual" else "a11y"
    if value["$schema"] != "https://leva.ai.kr/schemas/home-visual-a11y-evidence-v2.json":
        raise ValueError(f"{label} schema reference is not canonical")
    if value["schema_version"] != 2 or value["document_type"] != f"home-{expected_kind}-evidence":
        raise ValueError(f"{label} document identity is invalid")
    if value["evidence_mode"] != "release_ready":
        raise ValueError(f"{label} evidence is not release_ready")

    catalog = _quality_catalog(candidate, label)
    binding = _exact_payload(
        value["binding"],
        {
            "repository", "rendered_product_sha", "rendered_product_tree_sha256",
            "evidence_producer_sha", "candidate_spec_sha256", "case_catalog_sha256",
            "font_manifest_sha256",
        },
        f"{label} binding",
    )
    expected_repository, expected_source = quality_source(candidate, label)
    expected_binding = {
        "repository": expected_repository,
        "rendered_product_sha": catalog["rendered_product_sha"],
        "rendered_product_tree_sha256": catalog["rendered_product_tree_sha256"],
        "evidence_producer_sha": expected_source,
        "candidate_spec_sha256": candidate_hash,
        "case_catalog_sha256": catalog["sha256"],
        "font_manifest_sha256": catalog["font_manifest_sha256"],
    }
    for field, expected in expected_binding.items():
        if binding[field] != expected:
            raise ValueError(f"{label} {field} mismatch")
    runtime = _exact_payload(
        value["runtime"],
        {
            "browser", "playwright_version", "locale", "timezone_id", "device_scale_factor",
            "color_scheme", "reduced_motion", "animations", "clock", "network_policy",
            "workers",
        },
        f"{label} runtime",
    )
    if _canonical_sha256(runtime) != catalog["provenance_sha256"]:
        raise ValueError(f"{label} render provenance mismatch")
    if (
        runtime["browser"] != "chromium"
        or runtime["playwright_version"] != "1.61.1"
        or runtime["locale"] != "ko-KR"
        or runtime["timezone_id"] != "UTC"
        or runtime["device_scale_factor"] != 1
        or runtime["color_scheme"] != "light"
        or runtime["reduced_motion"] != "reduce"
        or runtime["animations"] != "disabled"
        or runtime["clock"] != "2026-08-16T00:00:00.000Z"
        or runtime["network_policy"] != "loopback-only"
        or runtime["workers"] != 1
    ):
        raise ValueError(f"{label} runtime provenance is not the approved deterministic render profile")

    theme = _exact_payload(value["theme_coverage"], {"light", "dark"}, f"{label} themes")
    if theme["light"] != "covered":
        raise ValueError(f"{label} light theme is not covered")
    dark = _exact_payload(theme["dark"], {"status", "reason", "approval"}, f"{label} dark theme")
    approval = _exact_payload(
        dark["approval"], {"required", "status", "owner", "artifact"}, f"{label} dark approval"
    )
    if (
        dark["status"] != "not_applicable"
        or not isinstance(dark["reason"], str)
        or not 1 <= len(dark["reason"]) <= 240
        or approval != {
            "required": True,
            "status": "pending",
            "owner": "product-design",
            "artifact": None,
        }
    ):
        raise ValueError(f"{label} dark-theme non-applicability record is invalid")

    baseline = _exact_payload(
        value["baseline_review"], {"status", "review_id"}, f"{label} baseline review"
    )
    if (
        baseline["status"] != "approved"
        or not isinstance(baseline["review_id"], str)
        or not 1 <= len(baseline["review_id"]) <= 80
    ):
        raise ValueError(f"{label} baseline review must be approved")

    summary = _exact_payload(value["summary"], {"required", "passed", "failed"}, f"{label} summary")
    required = _positive_int(summary["required"], f"{label} required cases")
    passed = _nonnegative_int(summary["passed"], f"{label} passed cases")
    failed = _nonnegative_int(summary["failed"], f"{label} failed cases")
    if required != catalog["case_count"] or passed != required or failed != 0:
        raise ValueError(f"{label} all exact catalog cases must be passed")
    cases = value["cases"]
    if not isinstance(cases, list) or len(cases) != required:
        raise ValueError(f"{label} cases do not match the exact catalog count")
    case_ids: set[str] = set()
    for index, case in enumerate(cases):
        common_keys = {
            "case_id", "status", "theme", "viewport", "check_count", "failed_check_count",
        }
        expected_keys = common_keys | ({"artifact_sha256"} if expected_kind == "visual" else {"violation_counts"})
        row = _exact_payload(case, expected_keys, f"{label} case {index}")
        case_id = row["case_id"]
        if (
            not isinstance(case_id, str)
            or len(case_id) > 80
            or HOME_CASE_ID.fullmatch(case_id) is None
            or case_id in case_ids
        ):
            raise ValueError(f"{label} case IDs must be bounded and unique")
        case_ids.add(case_id)
        if row["status"] != "passed" or row["theme"] != "light":
            raise ValueError(f"{label} case {index} did not pass")
        viewport = _exact_payload(row["viewport"], {"width", "height"}, f"{label} viewport")
        if viewport["width"] not in {320, 600, 840, 1240}:
            raise ValueError(f"{label} viewport width is not canonical")
        height = _positive_int(viewport["height"], f"{label} viewport height")
        if not 720 <= height <= 1200:
            raise ValueError(f"{label} viewport height is outside the canonical range")
        check_count = _positive_int(row["check_count"], f"{label} case check_count")
        if check_count > 1000:
            raise ValueError(f"{label} case check_count exceeds the schema bound")
        if _nonnegative_int(row["failed_check_count"], f"{label} failed_check_count") != 0:
            raise ValueError(f"{label} case {index} has failed checks")
        if expected_kind == "visual":
            if not isinstance(row["artifact_sha256"], str) or SHA64.fullmatch(row["artifact_sha256"]) is None:
                raise ValueError(f"{label} visual artifact hash is invalid")
            if row["artifact_sha256"] == "0" * 64:
                raise ValueError(f"{label} passed visual artifact hash is a failure sentinel")
        else:
            violations = _exact_payload(
                row["violation_counts"],
                {"critical", "serious", "moderate", "minor", "total"},
                f"{label} violations",
            )
            for field, count in violations.items():
                if _nonnegative_int(count, f"{label} {field} violations") > 10_000:
                    raise ValueError(f"{label} {field} violations exceed the schema bound")
            if violations["critical"] != 0:
                raise ValueError(f"{label} critical violations must be zero")
            if violations["serious"] != 0:
                raise ValueError(f"{label} serious violations must be zero")
            if violations["total"] != sum(violations[field] for field in ("critical", "serious", "moderate", "minor")):
                raise ValueError(f"{label} violation total is inconsistent")
            if violations["total"] != 0:
                raise ValueError(f"{label} passed with accessibility violations")

    privacy = _exact_payload(
        value["privacy"], {"classification", "contains_raw_content"}, f"{label} privacy"
    )
    if privacy != {"classification": "sanitized-aggregate-only", "contains_raw_content": False}:
        raise ValueError(f"{label} evidence may not contain raw content")


def validate_evidence_payload(
    label: str,
    payload: Any,
    candidate_hash: str,
    candidate: dict[str, Any],
    producer_run_id: int | None = None,
    producer_run_attempt: int | None = None,
) -> None:
    _validate_sanitized(payload, "evidence")
    if label in JOURNEY_ALLOWLISTS:
        if not isinstance(payload, list) or not payload:
            raise ValueError("journey evidence must be a non-empty JSON array")
        expected_steps = list(JOURNEY_ALLOWLISTS[label])
        actual_steps: list[str] = []
        for index, row in enumerate(payload):
            if not isinstance(row, dict) or set(row) != JOURNEY_ROW_KEYS:
                raise ValueError(f"journey evidence row {index} has an invalid key set")
            actual_steps.append(row.get("step"))
            if row["candidate_spec_sha256"] != candidate_hash:
                raise ValueError(f"journey evidence row {index} does not bind candidate-spec")
            if row["result"] != "passed":
                raise ValueError(f"journey evidence row {index} did not pass")
            if (
                isinstance(row["duration_ms"], bool)
                or not isinstance(row["duration_ms"], int)
                or not 0 <= row["duration_ms"] <= 600_000
            ):
                raise ValueError(f"journey evidence row {index} has invalid duration_ms")
            if row["step"] not in JOURNEY_ALLOWLISTS[label]:
                raise ValueError("journey evidence step sequence is not the approved allowlist")
            if not isinstance(row["route"], str) or not _route_allowed(label, row["step"], row["route"]):
                raise ValueError(f"journey evidence row {index} has a disallowed route")
        if actual_steps != expected_steps:
            raise ValueError("journey evidence step sequence is not the approved allowlist")
        return
    if label in {"home-visual", "home-axe-browser-a11y"}:
        _validate_home_evidence_payload(label, payload, candidate_hash, candidate)
        return
    if not isinstance(payload, dict) or payload.get("candidate_spec_sha256") != candidate_hash:
        raise ValueError("evidence does not bind candidate-spec")
    if payload.get("status") != "passed":
        raise ValueError("evidence status must be passed")
    for field, expected in (
        ("producer_run_id", producer_run_id),
        ("producer_run_attempt", producer_run_attempt),
    ):
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"evidence {field} must be a positive integer")
        if expected is not None and value != expected:
            label_text = field.replace("_", " ")
            raise ValueError(f"evidence {label_text} mismatch")
    if label == "home-dist":
        value = _exact_payload(
            payload,
            {
                "candidate_spec_sha256", "status", "home_source_sha", "dist_sha256",
                *PRODUCER_EVIDENCE_KEYS,
            },
            label,
        )
        if value["producer_run_attempt"] != 1:
            raise ValueError(
                "home-dist producer_run_attempt must be 1; retry requires a fresh dispatch"
            )
        if value["home_source_sha"] != candidate["home"]["source_sha"]:
            raise ValueError("home-dist source SHA mismatch")
        if value["dist_sha256"] != candidate["home"]["dist_sha256"]:
            raise ValueError("home-dist payload SHA mismatch")
        return
    if label == "privacy-approval":
        keys = {
            "candidate_spec_sha256", "status", "approved_at", "collection_mode", "region",
            "project_identity", "retention_days", "access_owner", "deletion_runbook",
            *PROTECTED_APPROVAL_KEYS,
            *PRODUCER_EVIDENCE_KEYS,
        }
        value = _exact_payload(payload, keys, label)
        if value["producer_run_attempt"] != 1:
            raise ValueError(
                "privacy-approval producer_run_attempt must be 1; retry requires a fresh dispatch"
            )
        for field in keys - {
            "candidate_spec_sha256", "status", "approved_at",
            *PROTECTED_APPROVAL_KEYS,
            *PRODUCER_EVIDENCE_KEYS,
        }:
            if value[field] != candidate["analytics_privacy"][field]:
                raise ValueError(f"privacy-approval {field} mismatch")
        if not isinstance(value["approved_at"], str) or not value["approved_at"].endswith("Z"):
            raise ValueError("privacy-approval approved_at must be UTC")
        try:
            datetime.fromisoformat(value["approved_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("privacy-approval approved_at is invalid") from exc
        validate_approval_claim(
            "privacy-approval",
            {field: value[field] for field in PROTECTED_APPROVAL_KEYS},
        )
        if value["approved_at"] != value["approval_effective_at"]:
            raise ValueError(
                "privacy-approval approved_at must equal protected approval effective time"
            )
        return
    if label == "ai-release-eval":
        config = candidate["ai_release_eval_config"]
        value = _exact_payload(
            payload,
            {
                "candidate_spec_sha256", "status", "ai_source_sha", "primary_model",
                "fallback_models", "prompt_sha256", "fixture_revision", "fixture_sha256",
                "gitops_source_sha", "rendered_config_sha256",
                "ollama_endpoint_sha256",
                "hard_invariants_percent", "usefulness_percent", "baseline_delta_points",
                *PROTECTED_APPROVAL_KEYS,
                *PRODUCER_EVIDENCE_KEYS,
            },
            label,
        )
        if value["producer_run_attempt"] != 1:
            raise ValueError(
                "ai-release-eval producer_run_attempt must be 1; retry requires a fresh dispatch"
            )
        expected = {
            "ai_source_sha": candidate["services"]["devpath-ai-svc"]["source_sha"],
            "gitops_source_sha": candidate["gitops"]["base_sha"],
            "primary_model": config["primary_model"],
            "fallback_models": config["fallback_models"],
            "prompt_sha256": config["prompt_sha256"],
            "fixture_revision": config["fixture_revision"],
            "fixture_sha256": config["fixture_sha256"],
            "rendered_config_sha256": config["rendered_config_sha256"],
            "ollama_endpoint_sha256": config["ollama_endpoint_sha256"],
        }
        for field, expected_value in expected.items():
            if value[field] != expected_value:
                raise ValueError(f"ai-release-eval {field} mismatch")
        if _bounded_number(value["hard_invariants_percent"], "hard invariants", 100, 100) != 100:
            raise ValueError("hard invariants must be 100")
        _bounded_number(value["usefulness_percent"], "usefulness", 90, 100)
        _bounded_number(value["baseline_delta_points"], "baseline delta", -5, 100)
        validate_approval_claim(
            "ai-release-eval",
            {field: value[field] for field in PROTECTED_APPROVAL_KEYS},
        )
        return
    if label in QUALITY_EVIDENCE:
        catalog = _quality_catalog(candidate, label)
        common_keys = {
            "candidate_spec_sha256", "status", "producer_run_id", "producer_run_attempt",
            "repository", "source_sha", "case_catalog_sha256", "case_count",
            "passed_case_count", "failed_case_count",
        }
        extras: set[str]
        if label == "frontend-visual":
            extras = {
                "case_catalog_version", "case_catalog_schema_version",
                "projection_contract_sha256", "fixture_ids",
                "surface_case_counts", "capture_surface", "device_evidence", "evidence_mode",
                "input_provenance_sha256", "input_provenance_file_sha256",
                "result_manifest_sha256", "baseline_status", "baseline_set_sha256",
                "baseline_approval_sha256", "pixel_diff_percent",
            }
        elif label == "frontend-automated-a11y":
            extras = {
                "case_catalog_version", "case_catalog_schema_version",
                "projection_contract_sha256", "fixture_ids",
                "surface_case_counts", "capture_surface", "device_evidence", "evidence_mode",
                "input_provenance_sha256", "input_provenance_file_sha256",
                "result_manifest_sha256", "standard",
                "critical_violations", "serious_violations",
            }
        elif label == "manual-nvda":
            extras = {
                "assistive_technology",
                "test_provenance_sha256",
                *PROTECTED_APPROVAL_KEYS,
            }
        elif label == "manual-talkback":
            extras = {
                "assistive_technology", "test_provenance_sha256",
                "build_provenance_sha256", "signed_apk_sha256",
                *PROTECTED_APPROVAL_KEYS,
            }
        else:  # Home labels return above.
            raise ValueError(f"unknown evidence kind: {label}")
        value = _exact_payload(payload, common_keys | extras, label)
        if label in MANUAL_CATALOG_CONTRACTS and value["producer_run_attempt"] != 1:
            raise ValueError(
                f"{label} producer_run_attempt must be 1; retry requires a fresh dispatch"
            )
        expected_repository, expected_source = quality_source(candidate, label)
        if value["repository"] != expected_repository:
            raise ValueError(f"{label} repository mismatch")
        if value["source_sha"] != expected_source:
            raise ValueError(f"{label} source SHA mismatch")
        if value["case_catalog_sha256"] != catalog["sha256"]:
            raise ValueError(f"{label} case catalog hash mismatch")
        _validate_quality_counts(value, catalog, label)

        if label in {"frontend-visual", "frontend-automated-a11y"}:
            if (
                value["case_catalog_version"] != catalog["case_catalog_version"]
                or value["case_catalog_schema_version"] != catalog["case_catalog_schema_version"]
                or value["projection_contract_sha256"]
                != catalog["projection_contract_sha256"]
                or value["projection_contract_sha256"]
                != FRONTEND_PROJECTION_CONTRACT_SHA256
                or value["fixture_ids"] != catalog["fixture_ids"]
                or value["fixture_ids"] != list(FRONTEND_FIXTURE_IDS)
                or value["evidence_mode"] != "release_ready"
                or value["evidence_mode"] != catalog["evidence_mode"]
            ):
                raise ValueError(f"{label} must bind the exact approved frontend catalog order")
            for field in (
                "input_provenance_sha256",
                "input_provenance_file_sha256",
            ):
                if value[field] != catalog[field]:
                    raise ValueError(f"{label} {field} mismatch")
            if not isinstance(value["result_manifest_sha256"], str) or SHA64.fullmatch(
                value["result_manifest_sha256"]
            ) is None:
                raise ValueError(f"{label} result_manifest_sha256 must be SHA-256")
            prebound_digests = {
                catalog["sha256"],
                catalog["projection_contract_sha256"],
                catalog["input_provenance_sha256"],
                catalog["input_provenance_file_sha256"],
            }
            if label == "frontend-visual":
                prebound_digests |= {
                    catalog["baseline_set_sha256"],
                    catalog["baseline_approval_sha256"],
                }
            if value["result_manifest_sha256"] in prebound_digests:
                raise ValueError(f"{label} result and prebound input digests must be distinct")
            expected = FRONTEND_CATALOG_CONTRACTS[label]
            if (
                value["capture_surface"] != expected["capture_surface"]
                or value["device_evidence"] is not expected["device_evidence"]
                or value["capture_surface"] != catalog["capture_surface"]
                or value["device_evidence"] is not catalog["device_evidence"]
            ):
                raise ValueError(
                    f"{label} is Flutter-web projection evidence, not native device evidence"
                )
            _validate_surface_counts(value["surface_case_counts"], value["case_count"], label)
        elif value["test_provenance_sha256"] != catalog["provenance_sha256"]:
            raise ValueError(f"{label} test_provenance_sha256 mismatch")
        if label == "frontend-visual":
            if (
                value["baseline_status"] != "approved"
                or value["baseline_status"] != catalog["baseline_status"]
                or value["baseline_set_sha256"] != catalog["baseline_set_sha256"]
                or value["baseline_approval_sha256"] != catalog["baseline_approval_sha256"]
            ):
                raise ValueError("frontend visual evidence must bind an approved baseline")
            if _bounded_number(value["pixel_diff_percent"], "frontend visual pixel diff", 0, 0) != 0:
                raise ValueError("frontend visual pixel diff must be zero")
        if label == "frontend-automated-a11y":
            if value["standard"] != "WCAG 2.2 AA":
                raise ValueError("frontend automated a11y standard must be WCAG 2.2 AA")
            for field in ("critical_violations", "serious_violations"):
                if isinstance(value[field], bool) or value[field] != 0:
                    raise ValueError(f"frontend automated a11y {field} must be integer zero")
        expected_at = {
            lane: contract["assistive_technology"]
            for lane, contract in MANUAL_CATALOG_CONTRACTS.items()
        }
        if label in expected_at and value["assistive_technology"] != expected_at[label]:
            raise ValueError(f"{label} assistive technology mismatch")
        if label in MANUAL_CATALOG_CONTRACTS:
            validate_approval_claim(
                label,
                {field: value[field] for field in PROTECTED_APPROVAL_KEYS},
            )
        if label == "manual-talkback":
            mobile = candidate["quality_evidence_inputs"]["mobile_test_artifacts"]
            for field in ("build_provenance_sha256",):
                if value[field] != mobile[field]:
                    raise ValueError(f"{label} {field} mismatch")
            if value["signed_apk_sha256"] != mobile["signed_apk_sha256"]:
                raise ValueError(f"{label} signed_apk_sha256 mismatch")
        return
    raise ValueError(f"unknown evidence kind: {label}")


def validate_sealed_metadata(label: str, payload: dict[str, Any], release: dict[str, Any]) -> None:
    """Prevent a manifest from claiming better approval/eval values than its evidence."""
    if label == "privacy-approval":
        expected_approval = release["analytics_privacy_approval"]["approved_at"]
        if payload.get("approved_at") != expected_approval:
            raise ValueError("privacy-approval: approved_at does not match the sealed manifest")
    if label == "ai-release-eval":
        sealed_eval = release["ai_release_eval"]
        for field in (
            "hard_invariants_percent",
            "usefulness_percent",
            "baseline_delta_points",
        ):
            if payload.get(field) != sealed_eval[field]:
                raise ValueError(f"ai-release-eval: {field} does not match the sealed manifest")


def validate_run_provenance(
    label: str,
    run: dict[str, Any],
    reference: dict[str, Any],
    expected_head: str,
    expected_workflow_path: str,
    workflow_bytes: bytes,
    expected_event: str = "workflow_dispatch",
) -> None:
    """Bind evidence to one successful producer run and its exact workflow blob."""
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ValueError(f"{label}: producer workflow did not complete successfully")
    if "workflow_run_id" in reference and run.get("id") != reference["workflow_run_id"]:
        raise ValueError(f"{label}: producer workflow run ID mismatch")
    if run.get("event") != expected_event or reference.get("event") != expected_event:
        raise ValueError(f"{label}: producer event must be {expected_event}")
    if run.get("head_sha") != expected_head or reference.get("head_sha") != expected_head:
        raise ValueError(f"{label}: producer head SHA mismatch")
    run_attempt = run.get("run_attempt")
    if (
        isinstance(run_attempt, bool)
        or not isinstance(run_attempt, int)
        or run_attempt <= 0
        or reference.get("run_attempt") != run_attempt
    ):
        raise ValueError(f"{label}: producer run attempt mismatch")
    if run.get("path") != expected_workflow_path:
        raise ValueError(f"{label}: producer workflow path mismatch")
    if reference.get("workflow_path") != expected_workflow_path:
        raise ValueError(f"{label}: sealed workflow path mismatch")
    actual_hash = hashlib.sha256(workflow_bytes).hexdigest()
    if reference.get("workflow_sha256") != actual_hash:
        raise ValueError(f"{label}: producer workflow bytes mismatch")


def validate_home_master_trust(
    branch: Any,
    run: Any,
    expected_head: str,
) -> None:
    """Require the Home producer to be the current protected master commit."""
    if not isinstance(branch, dict):
        raise ValueError("home-dist: master branch response is invalid")
    if branch.get("name") != "master" or branch.get("protected") is not True:
        raise ValueError("home-dist: master branch must be protected")
    if (branch.get("commit") or {}).get("sha") != expected_head:
        raise ValueError("home-dist: candidate source is not the current master commit")
    if not isinstance(run, dict):
        raise ValueError("home-dist: producer run response is invalid")
    if run.get("run_attempt") != 1:
        raise ValueError("home-dist: producer run attempt must be 1")
    if run.get("head_branch") != "master":
        raise ValueError("home-dist: producer run head_branch must be master")
    if run.get("head_sha") != expected_head:
        raise ValueError("home-dist: producer run head SHA mismatch")
    if (run.get("repository") or {}).get("full_name") != "DevPathAi/devpath-home-page":
        raise ValueError("home-dist: producer run repository mismatch")
    if (run.get("head_repository") or {}).get("full_name") != "DevPathAi/devpath-home-page":
        raise ValueError("home-dist: producer run head repository mismatch")


def validate_ai_release_eval_trust(
    branch: Any,
    run: Any,
    expected_head: str,
) -> None:
    """Require the AI evaluator to be the current protected main commit."""
    repository = "DevPathAi/devpath-ai-svc"
    if not isinstance(branch, dict):
        raise ValueError("ai-release-eval: main branch response is invalid")
    if branch.get("name") != "main" or branch.get("protected") is not True:
        raise ValueError("ai-release-eval: main branch must be protected")
    if (branch.get("commit") or {}).get("sha") != expected_head:
        raise ValueError("ai-release-eval: candidate source is not current main")
    if not isinstance(run, dict):
        raise ValueError("ai-release-eval: producer run response is invalid")
    if run.get("run_attempt") != 1:
        raise ValueError("ai-release-eval: producer run attempt must be 1")
    if run.get("head_branch") != "main" or run.get("head_sha") != expected_head:
        raise ValueError("ai-release-eval: producer run must bind current main")
    if (run.get("repository") or {}).get("full_name") != repository:
        raise ValueError("ai-release-eval: producer run repository mismatch")
    if (run.get("head_repository") or {}).get("full_name") != repository:
        raise ValueError("ai-release-eval: producer run head repository mismatch")


def validate_privacy_approval_trust(
    branch: Any,
    run: Any,
    expected_head: str,
) -> None:
    """Require privacy approval to run at the current protected Documents main commit."""
    repository = "DevPathAi/documents"
    if not isinstance(branch, dict):
        raise ValueError("privacy-approval: main branch response is invalid")
    if branch.get("name") != "main" or branch.get("protected") is not True:
        raise ValueError("privacy-approval: main branch must be protected")
    if (branch.get("commit") or {}).get("sha") != expected_head:
        raise ValueError("privacy-approval: candidate source is not current main")
    if not isinstance(run, dict):
        raise ValueError("privacy-approval: producer run response is invalid")
    if run.get("run_attempt") != 1:
        raise ValueError("privacy-approval: producer run attempt must be 1")
    if run.get("head_branch") != "main" or run.get("head_sha") != expected_head:
        raise ValueError("privacy-approval: producer run must bind current main")
    if (run.get("repository") or {}).get("full_name") != repository:
        raise ValueError("privacy-approval: producer run repository mismatch")
    if (run.get("head_repository") or {}).get("full_name") != repository:
        raise ValueError("privacy-approval: producer run head repository mismatch")


def _repository_file_bytes(
    repository: str,
    path: str,
    head_sha: str,
    env: dict[str, str],
) -> bytes:
    result = subprocess.run(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github.raw+json",
            f"repos/{repository}/contents/{path}?ref={head_sha}",
        ],
        capture_output=True,
        env=env,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise ValueError(f"producer source blob download failed: {path}")
    return result.stdout


def _workflow_bytes(
    repository: str,
    workflow_path: str,
    head_sha: str,
    env: dict[str, str],
) -> bytes:
    return _repository_file_bytes(repository, workflow_path, head_sha, env)


def validate_workflow_dispatch_inputs(
    workflow_bytes: bytes,
    expected_inputs: set[str],
    label: str,
) -> None:
    """Fail closed unless a canonical workflow exposes only the approved dispatch inputs."""
    try:
        text = workflow_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label}: producer workflow is not UTF-8") from exc
    if "\t" in text:
        raise ValueError(f"{label}: producer workflow may not use tab indentation")
    lines = text.splitlines()

    def content_rows(start: int, parent_indent: int) -> list[tuple[int, int, str]]:
        rows: list[tuple[int, int, str]] = []
        for index, raw in enumerate(lines[start:], start=start):
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            if indent <= parent_indent:
                break
            rows.append((index, indent, raw.strip()))
        return rows

    on_rows = [
        (index, len(match.group(1)))
        for index, line in enumerate(lines)
        if (match := re.fullmatch(r"( *)on:\s*(?:#.*)?", line))
    ]
    if len(on_rows) != 1 or on_rows[0][1] != 0:
        raise ValueError(f"{label}: workflow must contain one canonical top-level on block")
    on_index, on_indent = on_rows[0]
    on_children = content_rows(on_index + 1, on_indent)
    if not on_children:
        raise ValueError(f"{label}: workflow on block is empty")
    on_child_indent = min(indent for _, indent, _ in on_children)
    direct_events = [
        (index, indent, value)
        for index, indent, value in on_children
        if indent == on_child_indent
    ]
    if len(direct_events) != 1 or re.fullmatch(
        r"workflow_dispatch:\s*(?:#.*)?", direct_events[0][2]
    ) is None:
        raise ValueError(
            f"{label}: workflow on block must contain only canonical workflow_dispatch"
        )
    dispatch_index, dispatch_indent, _ = direct_events[0]
    dispatch_children = content_rows(dispatch_index + 1, dispatch_indent)
    if not dispatch_children:
        raise ValueError(f"{label}: workflow_dispatch block is empty")
    child_indent = min(indent for _, indent, _ in dispatch_children)
    input_rows = [
        (offset, index, indent)
        for offset, (index, indent, value) in enumerate(dispatch_children)
        if indent == child_indent and re.fullmatch(r"inputs:\s*(?:#.*)?", value)
    ]
    if len(input_rows) != 1:
        raise ValueError(f"{label}: workflow must contain one canonical dispatch inputs block")
    input_offset, _, input_indent = input_rows[0]
    nested: list[tuple[int, int, str]] = []
    for row in dispatch_children[input_offset + 1 :]:
        if row[1] <= input_indent:
            break
        nested.append(row)
    if not nested:
        actual_inputs: set[str] = set()
    else:
        entry_indent = min(indent for _, indent, _ in nested)
        actual_inputs = set()
        direct_rows = [
            (offset, value)
            for offset, (_, indent, value) in enumerate(nested)
            if indent == entry_indent
        ]
        for direct_index, (offset, value) in enumerate(direct_rows):
            match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*):\s*(?:#.*)?", value)
            if match is None:
                raise ValueError(f"{label}: dispatch input mapping is not canonical")
            name = match.group(1)
            if name in actual_inputs:
                raise ValueError(f"{label}: dispatch input is duplicated")
            actual_inputs.add(name)
            next_offset = (
                direct_rows[direct_index + 1][0]
                if direct_index + 1 < len(direct_rows)
                else len(nested)
            )
            property_rows = nested[offset + 1 : next_offset]
            if not property_rows:
                raise ValueError(f"{label}: dispatch input {name} has no properties")
            property_indent = min(indent for _, indent, _ in property_rows)
            properties: dict[str, str] = {}
            for _, indent, property_value in property_rows:
                if indent != property_indent:
                    continue
                property_match = re.fullmatch(
                    r"([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*",
                    property_value,
                )
                if property_match is None:
                    raise ValueError(f"{label}: dispatch input {name} is not canonical")
                key, raw_value = property_match.groups()
                if key in properties:
                    raise ValueError(f"{label}: dispatch input {name} property is duplicated")
                properties[key] = raw_value
            unexpected = set(properties) - {"description", "required", "type"}
            if unexpected:
                raise ValueError(
                    f"{label}: dispatch input {name} has forbidden properties {sorted(unexpected)}"
                )
            if re.fullmatch(r"true(?:\s+#.*)?", properties.get("required", "")) is None:
                raise ValueError(f"{label}: dispatch input {name} must be required")
            if re.fullmatch(r"string(?:\s+#.*)?", properties.get("type", "")) is None:
                raise ValueError(f"{label}: dispatch input {name} type must be string")
    if actual_inputs != expected_inputs:
        raise ValueError(
            f"{label}: dispatch inputs must be exactly {sorted(expected_inputs)}"
        )


def _git_output(root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ValueError("GitOps provenance git query failed")
    return result.stdout.strip()


def verify_validation_tree(
    root: Path,
    release_id: str,
    base_sha: str,
    validator_control_sha: str,
) -> None:
    """Bind B -> candidate C -> sealed release R independently of main-run H."""
    if SHA40.fullmatch(validator_control_sha) is None:
        raise ValueError("validator main control SHA is invalid")
    sealed_head = _git_output(root, ["rev-parse", "HEAD"])
    candidate_head = _git_output(root, ["rev-parse", f"{sealed_head}^"])
    if _git_output(root, ["rev-parse", f"{candidate_head}^"]) != base_sha:
        raise ValueError("candidate head is not based directly on sealed GitOps base")
    candidate_path = f"release-manifests/candidates/{release_id}.candidate-spec.json"
    release_path = f"release-manifests/releases/{release_id}.json"
    candidate_delta = _git_output(root, ["diff", "--name-status", base_sha, candidate_head])
    if candidate_delta.splitlines() != [f"A\t{candidate_path}"]:
        raise ValueError("candidate head has a disallowed candidate tree delta")
    final_delta = _git_output(root, ["diff", "--name-status", candidate_head, sealed_head])
    if final_delta.splitlines() != [f"A\t{release_path}"]:
        raise ValueError("sealed release commit has a disallowed tree delta")
    candidate_blob = subprocess.run(
        ["git", "show", f"{candidate_head}:{candidate_path}"],
        cwd=root,
        capture_output=True,
        check=False,
        timeout=30,
    )
    release_blob = subprocess.run(
        ["git", "show", f"{sealed_head}:{release_path}"],
        cwd=root,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if candidate_blob.returncode != 0 or candidate_blob.stdout != (root / candidate_path).read_bytes():
        raise ValueError("candidate blob differs from sealed release branch bytes")
    if release_blob.returncode != 0 or release_blob.stdout != (root / release_path).read_bytes():
        raise ValueError("release blob differs from sealed release branch bytes")


def validate_validation_seal_payload(
    payload: Any,
    release_id: str,
    candidate_hash: str,
    release_hash: str,
    attestation: dict[str, Any],
) -> None:
    _validate_sanitized(payload, "sealed-validation")
    if not isinstance(payload, dict) or set(payload) != VALIDATION_SEAL_KEYS:
        raise ValueError("sealed-validation evidence has an invalid key set")
    if payload["release_id"] != release_id:
        raise ValueError("sealed-validation release_id mismatch")
    if payload["candidate_spec_sha256"] != candidate_hash:
        raise ValueError("sealed-validation candidate-spec hash mismatch")
    if SHA64.fullmatch(str(payload["release_manifest_sha256"])) is None:
        raise ValueError("sealed-validation final manifest hash is invalid")
    if payload["release_manifest_sha256"] != release_hash:
        raise ValueError("sealed-validation final manifest bytes do not match")
    if payload["status"] != "passed":
        raise ValueError("sealed-validation did not pass")
    rollback_seconds = payload["rollback_seconds"]
    if isinstance(rollback_seconds, bool) or not isinstance(rollback_seconds, int):
        raise ValueError("sealed-validation rollback_seconds must be an integer")
    if not 0 <= rollback_seconds <= 600:
        raise ValueError("sealed-validation reverse rollback exceeded 600 seconds")
    if payload["validator_run_id"] != attestation["validator_run_id"]:
        raise ValueError("sealed-validation validator run mismatch")
    if payload["validator_run_attempt"] != attestation["validator_run_attempt"]:
        raise ValueError("sealed-validation validator run attempt mismatch")
    if payload["validator_head_sha"] != attestation["validator_head_sha"]:
        raise ValueError("sealed-validation validator head mismatch")
    if payload["validator_workflow_sha256"] != attestation["validator_workflow_sha256"]:
        raise ValueError("sealed-validation validator workflow mismatch")


def _run_json_value(command: list[str], env: dict[str, str]) -> Any:
    result = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        raise ValueError(f"GitHub evidence API command failed: {command[1]}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("GitHub evidence API returned invalid JSON") from exc
    return value


def _run_json(command: list[str], env: dict[str, str]) -> dict[str, Any]:
    value = _run_json_value(command, env)
    if not isinstance(value, dict):
        raise ValueError("GitHub evidence API returned an invalid object")
    return value


def _run_json_optional(command: list[str], env: dict[str, str]) -> dict[str, Any] | None:
    """Return None for an absent optional GitHub resource; never manufacture proof."""
    result = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("GitHub evidence API returned invalid optional JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("GitHub evidence API returned an invalid optional object")
    return value


def _list_protected_runs(
    command_env: dict[str, str],
    repository: str,
    expected_head: str,
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for page in range(1, 1001):
        listing = _run_json(
            [
                "gh",
                "api",
                (
                    f"repos/{repository}/actions/runs?head_sha={expected_head}"
                    "&event=workflow_dispatch&status=success&per_page=100"
                    f"&page={page}"
                ),
            ],
            command_env,
        )
        page_runs = listing.get("workflow_runs")
        if not isinstance(page_runs, list):
            raise ValueError("protected producer run query returned invalid JSON")
        runs.extend(page_runs)
        if len(page_runs) < 100:
            return runs
    raise ValueError("protected producer run query exceeded the pagination safety limit")


def _list_named_artifacts(
    command_env: dict[str, str],
    repository: str,
    artifact_name: str,
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for page in range(1, 1001):
        listing = _run_json(
            [
                "gh",
                "api",
                (
                    f"repos/{repository}/actions/artifacts?name={artifact_name}"
                    f"&per_page=100&page={page}"
                ),
            ],
            command_env,
        )
        page_artifacts = listing.get("artifacts")
        if not isinstance(page_artifacts, list):
            raise ValueError("protected artifact query returned invalid JSON")
        artifacts.extend(page_artifacts)
        if len(page_artifacts) < 100:
            return artifacts
    raise ValueError("protected artifact query exceeded the pagination safety limit")


def _candidate_branch_name(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 255
        or value == "main"
        or value.startswith(("/", "."))
        or value.endswith(("/", ".", ".lock"))
        or any(token in value for token in ("\\", "..", "//", "@{", "~", "^", ":", "?", "*", "["))
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError("candidate-spec: producer branch name is invalid")
    return value


def validate_candidate_producer_trust(
    release_id: str,
    candidate: dict[str, Any],
    candidate_raw: bytes,
    run: Any,
    branch: Any,
    commit: Any,
    comparison: Any,
    workflow_raw: bytes,
    source_raw: bytes,
    *,
    require_current_main: bool = True,
) -> None:
    """Bind a candidate artifact to the exact base -> candidate-only Git tree."""
    base_sha = candidate["gitops"]["base_sha"]
    candidate_path = f"release-manifests/candidates/{release_id}.candidate-spec.json"
    expected_branch = f"release/candidate-{release_id}"
    if not isinstance(run, dict):
        raise ValueError("candidate-spec: producer run response is invalid")
    run_id = run.get("id")
    run_attempt = run.get("run_attempt")
    if (
        isinstance(run_id, bool)
        or not isinstance(run_id, int)
        or run_id <= 0
        or isinstance(run_attempt, bool)
        or not isinstance(run_attempt, int)
        or run_attempt <= 0
    ):
        raise ValueError("candidate-spec: producer run coordinates are invalid")
    if (
        run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or run.get("event") != "workflow_dispatch"
        or run.get("path") != CANDIDATE_WORKFLOW
        or (run.get("repository") or {}).get("full_name") != CANDIDATE_REPOSITORY
        or (run.get("head_repository") or {}).get("full_name") != CANDIDATE_REPOSITORY
        or SHA40.fullmatch(str(run.get("head_sha"))) is None
    ):
        raise ValueError("candidate-spec: producer run provenance is invalid")
    if _candidate_branch_name(run.get("head_branch")) != expected_branch:
        raise ValueError("candidate-spec: producer branch is not the release branch")
    if not isinstance(branch, dict):
        raise ValueError("candidate-spec: protected main response is invalid")
    if branch.get("name") != "main" or branch.get("protected") is not True:
        raise ValueError("candidate-spec: GitOps main is not protected")
    if require_current_main and (branch.get("commit") or {}).get("sha") != base_sha:
        raise ValueError("candidate-spec: gitops.base_sha is not current protected main")
    if not isinstance(commit, dict):
        raise ValueError("candidate-spec: candidate commit response is invalid")
    parents = commit.get("parents")
    if (
        commit.get("sha") != run["head_sha"]
        or not isinstance(parents, list)
        or len(parents) != 1
        or not isinstance(parents[0], dict)
        or parents[0].get("sha") != base_sha
    ):
        raise ValueError("candidate-spec: producer head is not the sole child of base_sha")
    if not isinstance(comparison, dict):
        raise ValueError("candidate-spec: GitHub comparison response is invalid")
    files = comparison.get("files")
    if (
        comparison.get("status") != "ahead"
        or comparison.get("ahead_by") != 1
        or comparison.get("behind_by") != 0
        or comparison.get("total_commits") != 1
        or not isinstance(files, list)
        or len(files) != 1
        or not isinstance(files[0], dict)
        or files[0].get("filename") != candidate_path
        or files[0].get("status") != "added"
        or "previous_filename" in files[0]
    ):
        raise ValueError("candidate-spec: producer head has a disallowed tree delta")
    validate_workflow_dispatch_inputs(
        workflow_raw,
        {"release_id"},
        "candidate-spec",
    )
    if source_raw != candidate_raw:
        raise ValueError("candidate-spec: source blob differs from artifact bytes")


def _list_candidate_artifacts(
    command_env: dict[str, str],
    release_id: str,
) -> list[tuple[dict[str, Any], int, int]]:
    pattern = re.compile(
        rf"^{re.escape(release_id)}-candidate-spec-run-([1-9][0-9]*)-attempt-([1-9][0-9]*)$"
    )
    matches: list[tuple[dict[str, Any], int, int]] = []
    for page in range(1, 1001):
        listing = _run_json(
            [
                "gh",
                "api",
                f"repos/{CANDIDATE_REPOSITORY}/actions/artifacts?per_page=100&page={page}",
            ],
            command_env,
        )
        artifacts = listing.get("artifacts")
        if not isinstance(artifacts, list):
            raise ValueError("candidate-spec: artifact query returned invalid JSON")
        for metadata in artifacts:
            if not isinstance(metadata, dict):
                raise ValueError("candidate-spec: artifact metadata is invalid")
            match = pattern.fullmatch(str(metadata.get("name", "")))
            if match is not None:
                matches.append((metadata, int(match.group(1)), int(match.group(2))))
        if len(artifacts) < 100:
            return matches
    raise ValueError("candidate-spec: artifact query exceeded the pagination safety limit")


def verify_candidate_artifact(
    command_env: dict[str, str],
    release_id: str,
    candidate: dict[str, Any],
    candidate_raw: bytes,
    *,
    require_current_main: bool = True,
) -> dict[str, Any]:
    """Authenticate the unique raw candidate artifact and its one-file child commit."""
    if not 2 <= len(candidate_raw) <= MAX_CANDIDATE_SPEC_BYTES:
        raise ValueError("candidate-spec: raw candidate byte length is invalid")
    expected_hash = hashlib.sha256(candidate_raw).hexdigest()
    branch = _run_json(
        ["gh", "api", f"repos/{CANDIDATE_REPOSITORY}/branches/main"],
        command_env,
    )
    matches: list[dict[str, Any]] = []
    seen_coordinates: set[tuple[int, int]] = set()
    for metadata, run_id, run_attempt in _list_candidate_artifacts(
        command_env, release_id
    ):
        coordinates = (run_id, run_attempt)
        if coordinates in seen_coordinates:
            raise ValueError("candidate-spec: duplicate run-scoped artifacts are forbidden")
        seen_coordinates.add(coordinates)
        if (
            metadata.get("expired") is not False
            or isinstance(metadata.get("id"), bool)
            or not isinstance(metadata.get("id"), int)
            or metadata["id"] <= 0
            or (metadata.get("workflow_run") or {}).get("id") != run_id
        ):
            continue
        try:
            expires_at = _utc_z(
                metadata.get("expires_at"), "candidate-spec artifact expires_at"
            )
        except ValueError:
            continue
        if expires_at <= datetime.now().astimezone():
            continue
        run = _run_json(
            ["gh", "api", f"repos/{CANDIDATE_REPOSITORY}/actions/runs/{run_id}"],
            command_env,
        )
        if run.get("id") != run_id or run.get("run_attempt") != run_attempt:
            continue
        if (metadata.get("workflow_run") or {}).get("head_sha") != run.get("head_sha"):
            continue
        if not (
            run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and run.get("event") == "workflow_dispatch"
            and run.get("path") == CANDIDATE_WORKFLOW
            and (run.get("repository") or {}).get("full_name") == CANDIDATE_REPOSITORY
            and (run.get("head_repository") or {}).get("full_name") == CANDIDATE_REPOSITORY
        ):
            continue
        try:
            _candidate_branch_name(run.get("head_branch"))
        except ValueError:
            continue
        with tempfile.TemporaryDirectory(prefix="mission-spine-candidate-spec-") as temp_dir:
            destination = Path(temp_dir) / "package"
            download_candidate_spec_archive(
                command_env,
                metadata["id"],
                metadata,
                destination,
            )
            raw = (destination / "candidate-spec.json").read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_hash or raw != candidate_raw:
            continue
        head_sha = run["head_sha"]
        commit = _run_json(
            ["gh", "api", f"repos/{CANDIDATE_REPOSITORY}/commits/{head_sha}"],
            command_env,
        )
        comparison = _run_json(
            [
                "gh",
                "api",
                f"repos/{CANDIDATE_REPOSITORY}/compare/{candidate['gitops']['base_sha']}...{head_sha}",
            ],
            command_env,
        )
        workflow_raw = _repository_file_bytes(
            CANDIDATE_REPOSITORY,
            CANDIDATE_WORKFLOW,
            head_sha,
            command_env,
        )
        base_workflow_raw = _repository_file_bytes(
            CANDIDATE_REPOSITORY,
            CANDIDATE_WORKFLOW,
            candidate["gitops"]["base_sha"],
            command_env,
        )
        if workflow_raw != base_workflow_raw:
            raise ValueError("candidate-spec: workflow differs from protected base")
        candidate_path = (
            f"release-manifests/candidates/{release_id}.candidate-spec.json"
        )
        source_raw = _repository_file_bytes(
            CANDIDATE_REPOSITORY,
            candidate_path,
            head_sha,
            command_env,
        )
        validate_candidate_producer_trust(
            release_id,
            candidate,
            candidate_raw,
            run,
            branch,
            commit,
            comparison,
            workflow_raw,
            source_raw,
            require_current_main=require_current_main,
        )
        matches.append(run)
    if len(matches) != 1:
        raise ValueError("candidate-spec: exactly one eligible producer run is required")
    return matches[0]


def _materialize_git_tree(root: Path, source_sha: str, destination: Path) -> None:
    """Materialize only regular blobs beneath the frozen AI kustomize base."""
    listing = subprocess.run(
        [
            "git",
            "ls-tree",
            "-rz",
            "--full-tree",
            source_sha,
            "--",
            AI_RENDER_PATH,
        ],
        cwd=root,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if listing.returncode != 0 or not listing.stdout:
        raise ValueError("ai-release-eval: pinned GitOps render tree is unavailable")
    records = listing.stdout.split(b"\0")
    if records[-1] != b"":
        raise ValueError("ai-release-eval: pinned Git tree listing is malformed")
    records.pop()
    if not records or len(records) > MAX_AI_RENDER_SOURCE_FILES:
        raise ValueError("ai-release-eval: pinned Git tree file count is invalid")
    destination.mkdir(parents=True, exist_ok=False)
    total = 0
    previous = ""
    for record in records:
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, object_sha = metadata.decode("ascii").split(" ")
            path = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("ai-release-eval: pinned Git tree row is malformed") from exc
        relative = PurePosixPath(path)
        if (
            mode != "100644"
            or kind != "blob"
            or SHA40.fullmatch(object_sha) is None
            or path <= previous
            or "\\" in path
            or relative.is_absolute()
            or relative.parts[:3] != ("apps", "devpath-ai-svc", "base")
            or len(relative.parts) < 4
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError("ai-release-eval: pinned Git tree contains an unsafe entry")
        previous = path
        blob = subprocess.run(
            ["git", "cat-file", "blob", object_sha],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if blob.returncode != 0:
            raise ValueError("ai-release-eval: pinned Git blob is unavailable")
        total += len(blob.stdout)
        if total > MAX_AI_RENDER_SOURCE_BYTES:
            raise ValueError("ai-release-eval: pinned Git render tree is too large")
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(blob.stdout)


def validate_ai_rendered_config_bytes(raw: bytes, expected_sha256: str) -> None:
    if (
        not raw
        or len(raw) > MAX_AI_RENDERED_BYTES
        or b"\r" in raw
        or not raw.endswith(b"\n")
    ):
        raise ValueError("ai-release-eval: rendered config is not canonical LF output")
    try:
        if raw.decode("utf-8").encode("utf-8") != raw:
            raise ValueError("ai-release-eval: rendered config is not canonical UTF-8")
    except UnicodeDecodeError as exc:
        raise ValueError("ai-release-eval: rendered config is not canonical UTF-8") from exc
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("ai-release-eval: independently rendered config SHA-256 mismatch")
    validate_ai_rendered_runtime(raw)


def validate_ai_runtime_environment(environment: dict[str, str]) -> None:
    """Keep the release-evaluation model contract aligned with runtime routing."""
    for name, expected in AI_RUNTIME_ENVIRONMENT.items():
        if environment.get(name) != expected:
            raise ValueError(
                f"ai-release-eval: {name} must equal the frozen runtime value"
            )


def validate_ai_rendered_runtime(raw: bytes) -> None:
    """Extract the exact kustomize-rendered AI container environment fail closed."""
    text = raw.decode("utf-8")
    deployments: list[list[str]] = []
    for document in text.split("---\n"):
        lines = document.splitlines()
        if "kind: Deployment" not in lines:
            continue
        metadata_name = None
        try:
            metadata_index = lines.index("metadata:")
        except ValueError:
            continue
        for line in lines[metadata_index + 1 :]:
            if line and not line.startswith(" "):
                break
            if line.startswith("  name: "):
                metadata_name = line.removeprefix("  name: ")
                break
        if metadata_name == "devpath-ai-svc":
            deployments.append(lines)
    if len(deployments) != 1:
        raise ValueError(
            "ai-release-eval: rendered config must contain exactly one AI Deployment"
        )

    lines = deployments[0]

    def unique_direct_child(parent: int, parent_indent: int, key: str) -> int:
        expected_indent = parent_indent + 2
        expected = f'{" " * expected_indent}{key}'
        matches: list[int] = []
        for index in range(parent + 1, len(lines)):
            line = lines[index]
            if not line:
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent <= parent_indent:
                break
            if indent == expected_indent and line == expected:
                matches.append(index)
        if len(matches) != 1:
            raise ValueError(
                f"ai-release-eval: rendered Deployment requires one direct {key}"
            )
        return matches[0]

    root_specs = [index for index, line in enumerate(lines) if line == "spec:"]
    if len(root_specs) != 1:
        raise ValueError("ai-release-eval: rendered Deployment spec is ambiguous")
    template = unique_direct_child(root_specs[0], 0, "template:")
    pod_spec = unique_direct_child(template, 2, "spec:")
    containers = unique_direct_child(pod_spec, 4, "containers:")
    start = containers + 1
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in lines[start:]:
        if not line:
            if current:
                current.append(line)
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent < 6:
            break
        if indent == 6:
            if not line.startswith("      - "):
                break
            if current:
                chunks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        chunks.append(current)
    targets = [
        chunk
        for chunk in chunks
        if chunk.count("        name: devpath-ai-svc") == 1
    ]
    if len(targets) != 1:
        raise ValueError(
            "ai-release-eval: rendered config must contain exactly one AI container"
        )

    target = targets[0]
    images = [
        line.removeprefix("        image: ")
        for line in target
        if line.startswith("        image: ")
    ]
    if len(images) != 1 or re.fullmatch(
        r"ghcr\.io/devpathai/devpath-ai-svc:[0-9a-f]{40}", images[0]
    ) is None:
        raise ValueError(
            "ai-release-eval: rendered AI container image identity is invalid"
        )
    env_headers: list[tuple[int, int]] = []
    for index, line in enumerate(target):
        if line == "      - env:":
            env_headers.append((index, 8))
        elif line == "        env:":
            env_headers.append((index, 8))
    if len(env_headers) != 1:
        raise ValueError(
            "ai-release-eval: rendered AI container must contain exactly one env block"
        )

    header_index, item_indent = env_headers[0]
    item_prefix = " " * item_indent
    end = header_index + 1
    while end < len(target):
        line = target[end]
        if not line:
            end += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent < item_indent or (
            indent == item_indent and not line.startswith(f"{item_prefix}- ")
        ):
            break
        end += 1

    environment: dict[str, str] = {}
    seen_names: set[str] = set()
    index = header_index + 1
    while index < end:
        if not target[index]:
            index += 1
            continue
        match = re.fullmatch(
            rf"{re.escape(item_prefix)}- name: ([A-Z][A-Z0-9_]*)", target[index]
        )
        if match is None:
            raise ValueError("ai-release-eval: rendered env entry is malformed")
        name = match.group(1)
        if name in seen_names:
            raise ValueError(f"ai-release-eval: duplicate rendered environment {name}")
        seen_names.add(name)
        item_end = index + 1
        while item_end < end:
            line = target[item_end]
            if line and len(line) - len(line.lstrip(" ")) <= item_indent:
                break
            item_end += 1
        if name in AI_RUNTIME_ENVIRONMENT:
            direct_prefix = " " * (item_indent + 2)
            direct = [
                line
                for line in target[index + 1 : item_end]
                if line.startswith(direct_prefix)
                and len(line) - len(line.lstrip(" ")) == item_indent + 2
            ]
            if len(direct) != 1 or not direct[0].startswith(
                f"{direct_prefix}value: "
            ):
                raise ValueError(
                    f"ai-release-eval: {name} must have exactly one direct value"
                )
            value = direct[0].removeprefix(f"{direct_prefix}value: ")
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            environment[name] = value
        index = item_end
    validate_ai_runtime_environment(environment)


def verify_ai_rendered_config(root: Path, candidate: dict[str, Any]) -> None:
    """Re-render exact candidate GitOps bytes with the producer's pinned binary."""
    configured = os.environ.get("MISSION_SPINE_KUSTOMIZE_BIN", "")
    if not configured:
        raise ValueError("MISSION_SPINE_KUSTOMIZE_BIN is required")
    binary = Path(configured)
    if not binary.is_absolute() or not binary.is_file() or binary.is_symlink():
        raise ValueError("ai-release-eval: pinned kustomize must be an absolute regular file")
    if (
        binary.stat().st_size != KUSTOMIZE_BINARY_BYTES
        or _sha256_file(binary) != KUSTOMIZE_BINARY_SHA256
    ):
        raise ValueError("ai-release-eval: pinned kustomize binary bytes mismatch")
    version = subprocess.run(
        [str(binary), "version"],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if version.returncode != 0 or version.stdout != f"{KUSTOMIZE_VERSION}\n".encode():
        raise ValueError("ai-release-eval: pinned kustomize version mismatch")
    with tempfile.TemporaryDirectory(prefix="mission-spine-ai-render-") as temp_dir:
        source = Path(temp_dir) / "source"
        _materialize_git_tree(root, candidate["gitops"]["base_sha"], source)
        rendered = subprocess.run(
            [str(binary), "build", AI_RENDER_PATH],
            cwd=source,
            capture_output=True,
            check=False,
            timeout=60,
        )
    if rendered.returncode != 0:
        raise ValueError("ai-release-eval: pinned kustomize render failed")
    validate_ai_rendered_config_bytes(
        rendered.stdout,
        candidate["ai_release_eval_config"]["rendered_config_sha256"],
    )


def select_unique_protected_producer_run(
    command_env: dict[str, str],
    repository: str,
    expected_head: str,
    workflow_path: str,
    release_id: str,
    kind: str,
) -> dict[str, Any]:
    def names(run_id: int) -> tuple[str, ...]:
        if kind == "home-dist":
            return (home_dist_artifact_name(release_id, run_id, 1),)
        if kind == "ai-release-eval":
            return (ai_eval_artifact_name(release_id, run_id, 1),)
        if kind == "privacy-approval":
            return (privacy_approval_artifact_name(release_id, run_id, 1),)
        if kind == "signed-mobile":
            return (f"{release_id}-signed-android-build-run-{run_id}-attempt-1",)
        if kind == "frontend-baseline":
            return (
                f"{release_id}-frontend-visual-approved-baseline-run-{run_id}-attempt-1",
            )
        if kind == "manual":
            return tuple(
                f"{release_id}-{label}-run-{run_id}-attempt-1"
                for label in MANUAL_CATALOG_CONTRACTS
            )
        raise ValueError(f"unknown protected producer kind: {kind}")

    matches: dict[int, dict[str, Any]] = {}
    for run in _list_protected_runs(command_env, repository, expected_head):
        if not (
            run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and run.get("event") == "workflow_dispatch"
            and run.get("head_sha") == expected_head
            and run.get("head_branch") == (
                "master" if kind == "home-dist" else "main"
            )
            and run.get("path") == workflow_path
            and run.get("run_attempt") == 1
            and isinstance(run.get("id"), int)
            and not isinstance(run.get("id"), bool)
            and run["id"] > 0
        ):
            continue
        run_id = run["id"]
        complete = True
        for artifact_name in names(run_id):
            artifacts = [
                metadata
                for metadata in _list_named_artifacts(
                    command_env,
                    repository,
                    artifact_name,
                )
                if metadata.get("name") == artifact_name
                and metadata.get("expired") is False
                and (metadata.get("workflow_run") or {}).get("id") == run_id
            ]
            if len(artifacts) > 1:
                raise ValueError(f"{kind}: duplicate active artifacts for producer run")
            if len(artifacts) != 1:
                complete = False
                break
        if complete:
            matches[run_id] = run
    if len(matches) != 1:
        raise ValueError(f"{kind}: exactly one eligible protected producer run is required")
    return next(iter(matches.values()))


def assert_unique_protected_producer_run(
    command_env: dict[str, str],
    repository: str,
    expected_head: str,
    workflow_path: str,
    release_id: str,
    kind: str,
    expected_run_id: int,
) -> None:
    selected = select_unique_protected_producer_run(
        command_env,
        repository,
        expected_head,
        workflow_path,
        release_id,
        kind,
    )
    if selected.get("id") != expected_run_id:
        raise ValueError(f"{kind}: exactly one eligible protected producer run is required")


def _list_attempt_jobs(
    command_env: dict[str, str],
    repository: str,
    run_id: int,
    run_attempt: int,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for page in range(1, 1001):
        listing = _run_json(
            [
                "gh",
                "api",
                (
                    f"repos/{repository}/actions/runs/{run_id}/attempts/"
                    f"{run_attempt}/jobs?per_page=100&page={page}"
                ),
            ],
            command_env,
        )
        page_jobs = listing.get("jobs")
        if not isinstance(page_jobs, list):
            raise ValueError("GitHub protected job query returned invalid JSON")
        jobs.extend(page_jobs)
        if len(page_jobs) < 100:
            return jobs
    raise ValueError("GitHub protected job query exceeded the pagination safety limit")


def _configured_team_memberships(
    command_env: dict[str, str],
    repository: str,
    environment: dict[str, Any],
    approved_by: str,
    approved_by_id: int,
) -> set[int]:
    organization = repository.split("/", 1)[0]
    team_ids: set[int] = set()
    rules = environment.get("protection_rules") or []
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("type") != "required_reviewers":
            continue
        for entry in rule.get("reviewers") or []:
            reviewer = entry.get("reviewer") if isinstance(entry, dict) else None
            if (
                isinstance(entry, dict)
                and entry.get("type") == "User"
                and isinstance(reviewer, dict)
                and reviewer.get("id") == approved_by_id
                and reviewer.get("login") == approved_by
            ):
                return team_ids
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("type") != "required_reviewers":
            continue
        for entry in rule.get("reviewers") or []:
            if not isinstance(entry, dict) or entry.get("type") != "Team":
                continue
            reviewer = entry.get("reviewer") or {}
            team_id = reviewer.get("id")
            team_slug = reviewer.get("slug")
            if (
                isinstance(team_id, bool)
                or not isinstance(team_id, int)
                or team_id <= 0
                or not isinstance(team_slug, str)
                or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,98}[A-Za-z0-9])?", team_slug)
                is None
            ):
                raise ValueError("protected environment has an invalid configured team")
            membership = _run_json_optional(
                [
                    "gh",
                    "api",
                    f"orgs/{organization}/teams/{team_slug}/memberships/{approved_by}",
                ],
                command_env,
            )
            if membership is not None and membership.get("state") == "active" and membership.get("role") in {
                "member",
                "maintainer",
            }:
                team_ids.add(team_id)
    return team_ids


def verify_live_protected_approval(
    command_env: dict[str, str],
    repository: str,
    run_id: int,
    run_attempt: int,
    run: dict[str, Any],
    label: str,
    claim: dict[str, Any],
    expected_head: str,
) -> None:
    if run_attempt != 1:
        raise ValueError(f"{label}: protected approval attempt must be 1")
    environment_name = claim["approval_environment"]
    environment = _run_json(
        ["gh", "api", f"repos/{repository}/environments/{environment_name}"],
        command_env,
    )
    approvals = _run_json_value(
        ["gh", "api", f"repos/{repository}/actions/runs/{run_id}/approvals"],
        command_env,
    )
    if not isinstance(approvals, list):
        raise ValueError(f"{label}: approval history response is invalid")
    jobs = _list_attempt_jobs(command_env, repository, run_id, run_attempt)
    team_ids = _configured_team_memberships(
        command_env,
        repository,
        environment,
        claim["approved_by"],
        claim["approved_by_id"],
    )
    validate_protected_approval(
        label,
        claim,
        environment,
        approvals,
        jobs,
        run,
        expected_head,
        expected_repository=repository,
        approved_team_ids=team_ids,
    )


def verify_frontend_baseline_authentication(
    command_env: dict[str, str],
    authentication: dict[str, Any],
) -> None:
    repository = authentication["repository"]
    run_id = authentication["run_id"]
    run_attempt = authentication["run_attempt"]
    if run_attempt != 1:
        raise ValueError("frontend-baseline: protected approval attempt must be 1")
    metadata = _run_json(
        ["gh", "api", f"repos/{repository}/actions/artifacts/{authentication['artifact_id']}"],
        command_env,
    )
    if (
        metadata.get("id") != authentication["artifact_id"]
        or metadata.get("name") != authentication["artifact_name"]
        or metadata.get("expired") is not False
        or (metadata.get("workflow_run") or {}).get("id") != run_id
    ):
        raise ValueError("frontend-baseline: artifact identity or lifetime mismatch")
    if metadata.get("digest") != f"sha256:{authentication['artifact_archive_sha256']}":
        raise ValueError("frontend-baseline: artifact archive digest mismatch")
    current_run = _run_json(
        ["gh", "api", f"repos/{repository}/actions/runs/{run_id}"],
        command_env,
    )
    if current_run.get("run_attempt") != 1:
        raise ValueError("frontend-baseline: rerun attempt is not sealable")
    run = _run_json(
        ["gh", "api", f"repos/{repository}/actions/runs/{run_id}/attempts/1"],
        command_env,
    )
    workflow_raw = _workflow_bytes(
        repository,
        authentication["workflow_path"],
        authentication["head_sha"],
        command_env,
    )
    reference = {
        "event": "workflow_dispatch",
        "head_sha": authentication["head_sha"],
        "run_attempt": 1,
        "workflow_path": authentication["workflow_path"],
        "workflow_sha256": authentication["workflow_sha256"],
        "workflow_run_id": run_id,
    }
    validate_run_provenance(
        "frontend-baseline",
        run,
        reference,
        authentication["head_sha"],
        authentication["workflow_path"],
        workflow_raw,
        "workflow_dispatch",
    )
    assert_unique_protected_producer_run(
        command_env,
        repository,
        authentication["head_sha"],
        authentication["workflow_path"],
        authentication["release_id"],
        "frontend-baseline",
        run_id,
    )
    claim = {
        "approval_environment": authentication["approval_environment"],
        "approval_environment_id": authentication["approval_environment_id"],
        "approval_job_name": PROTECTED_APPROVAL_CONTRACTS["frontend-baseline"][1],
        "approved_by": authentication["approved_by"],
        "approved_by_id": authentication["approved_by_id"],
        "approval_effective_at": authentication["approval_effective_at"],
    }
    verify_live_protected_approval(
        command_env,
        repository,
        run_id,
        1,
        run,
        "frontend-baseline",
        claim,
        authentication["head_sha"],
    )


def verify_manual_catalog_inputs(
    command_env: dict[str, str],
    candidate: dict[str, Any],
) -> None:
    repository = candidate["frontend"]["repository"]
    source_sha = candidate["frontend"]["source_sha"]
    for label, contract in MANUAL_CATALOG_CONTRACTS.items():
        catalog_raw = _repository_file_bytes(
            repository,
            contract["path"],
            source_sha,
            command_env,
        )
        provenance_raw = _repository_file_bytes(
            repository,
            contract["provenance_path"],
            source_sha,
            command_env,
        )
        validate_manual_catalog_bundle(label, catalog_raw, provenance_raw, candidate)


def _parse_mobile_version(pubspec_raw: bytes) -> tuple[str, int]:
    try:
        text = pubspec_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("mobile pubspec.yaml is not UTF-8") from exc
    matches = re.findall(r"(?m)^version:\s*([0-9]+(?:\.[0-9]+){2})\+([1-9][0-9]*)\s*$", text)
    if len(matches) != 1:
        raise ValueError("mobile pubspec.yaml must contain one canonical version")
    return matches[0][0], int(matches[0][1])


def verify_signed_mobile_artifact(
    command_env: dict[str, str],
    candidate: dict[str, Any],
    destination: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Authenticate and materialize the exact candidate-prebound signed-mobile bundle."""
    mobile = candidate["quality_evidence_inputs"]["mobile_test_artifacts"]
    repository = mobile["repository"]
    run_id = mobile["workflow_run_id"]
    run_attempt = mobile["run_attempt"]
    if run_attempt != 1:
        raise ValueError("signed-mobile: protected signing attempt must be 1")
    metadata = _run_json(
        ["gh", "api", f"repos/{repository}/actions/artifacts/{mobile['artifact_id']}"],
        command_env,
    )
    if (
        metadata.get("id") != mobile["artifact_id"]
        or metadata.get("name") != mobile["artifact_name"]
        or metadata.get("expired") is not False
        or (metadata.get("workflow_run") or {}).get("id") != run_id
    ):
        raise ValueError("signed-mobile: artifact identity or lifetime mismatch")
    if metadata.get("digest") != f"sha256:{mobile['artifact_archive_sha256']}":
        raise ValueError("signed-mobile: GitHub artifact archive digest mismatch")
    current_run = _run_json(
        ["gh", "api", f"repos/{repository}/actions/runs/{run_id}"],
        command_env,
    )
    if current_run.get("run_attempt") != run_attempt:
        raise ValueError("signed-mobile: stale or rerun attempt is not sealable")
    run = _run_json(
        [
            "gh",
            "api",
            f"repos/{repository}/actions/runs/{run_id}/attempts/{run_attempt}",
        ],
        command_env,
    )
    workflow_raw = _workflow_bytes(
        repository,
        SIGNED_MOBILE_WORKFLOW,
        mobile["source_sha"],
        command_env,
    )
    validate_workflow_dispatch_inputs(
        workflow_raw,
        {"release_id"},
        "signed-mobile",
    )
    run_reference = {**mobile, "head_sha": mobile["source_sha"]}
    validate_run_provenance(
        "signed-mobile",
        run,
        run_reference,
        mobile["source_sha"],
        SIGNED_MOBILE_WORKFLOW,
        workflow_raw,
        "workflow_dispatch",
    )
    if hashlib.sha256(workflow_raw).hexdigest() != mobile["workflow_sha256"]:
        raise ValueError("signed-mobile: candidate workflow SHA-256 mismatch")
    if run.get("head_branch") != "main" or (run.get("repository") or {}).get(
        "full_name"
    ) != repository:
        raise ValueError("signed-mobile: protected run must bind frontend main")
    assert_unique_protected_producer_run(
        command_env,
        repository,
        mobile["source_sha"],
        SIGNED_MOBILE_WORKFLOW,
        candidate["release_id"],
        "signed-mobile",
        run_id,
    )

    pubspec_lock_raw = _repository_file_bytes(
        repository,
        "pubspec.lock",
        mobile["source_sha"],
        command_env,
    )
    mobile_pubspec_raw = _repository_file_bytes(
        repository,
        "apps/mobile/pubspec.yaml",
        mobile["source_sha"],
        command_env,
    )
    version_name, version_code = _parse_mobile_version(mobile_pubspec_raw)

    _download_signed_mobile_archive(
        command_env,
        repository,
        mobile["artifact_id"],
        mobile["artifact_archive_sha256"],
        destination,
        candidate,
    )
    approvals = validate_signed_mobile_bundle(destination, candidate)
    provenance = json.loads(
        (destination / mobile["build_provenance_file"]).read_text(encoding="utf-8")
    )
    if provenance["pubspec_lock_sha256"] != hashlib.sha256(pubspec_lock_raw).hexdigest():
        raise ValueError("signed-mobile: pubspec.lock SHA-256 mismatch")
    if (
        provenance["android"]["version_name"] != version_name
        or provenance["android"]["version_code"] != version_code
    ):
        raise ValueError("signed-mobile: packaged version does not match mobile pubspec.yaml")
    for label, claim in approvals.items():
        verify_live_protected_approval(
            command_env,
            repository,
            run_id,
            run_attempt,
            run,
            label,
            claim,
            mobile["source_sha"],
        )
    return provenance, run


def _artifact_entries(release: dict[str, Any]) -> list[tuple[str, dict[str, Any], bool, bool]]:
    entries = [
        ("home-dist", release["home_dist_artifact"], False, True),
        ("privacy-approval", release["analytics_privacy_approval"]["evidence"], False, False),
        ("ai-release-eval", release["ai_release_eval"]["evidence"], False, False),
        ("journey-activation", release["journeys"]["activation"], True, False),
        ("journey-contextual-practice", release["journeys"]["contextual_practice"], True, False),
    ]
    entries.extend(
        (label, release["quality_evidence"][key], False, False)
        for label, key in QUALITY_EVIDENCE.items()
    )
    return entries


def _verify_validation_seal(
    command_env: dict[str, str],
    release_path: Path,
    release_id: str,
    release: dict[str, Any],
    candidate_hash: str,
    base_sha: str,
) -> None:
    attestation = release["validation_attestation"]
    repository = attestation["validator_repository"]
    run_id = attestation["validator_run_id"]
    artifact_name = (
        f"{release_id}-sealed-validation-attempt-{attestation['validator_run_attempt']}"
    )
    listing = _run_json(
        ["gh", "api", f"repos/{repository}/actions/artifacts?name={artifact_name}&per_page=100"],
        command_env,
    )
    active = [item for item in listing.get("artifacts", []) if item.get("expired") is False]
    if len(active) != 1:
        raise ValueError("sealed-validation: exactly one active artifact is required")
    metadata = active[0]
    if metadata.get("name") != artifact_name:
        raise ValueError("sealed-validation: artifact name mismatch")
    if (metadata.get("workflow_run") or {}).get("id") != run_id:
        raise ValueError("sealed-validation: validator run mismatch")
    run = _run_json(["gh", "api", f"repos/{repository}/actions/runs/{run_id}"], command_env)
    workflow_bytes = _workflow_bytes(
        repository,
        attestation["validator_workflow_path"],
        attestation["validator_head_sha"],
        command_env,
    )
    validate_run_provenance(
        "sealed-validation",
        run,
        {
            "event": attestation["validator_event"],
            "head_sha": attestation["validator_head_sha"],
            "run_attempt": attestation["validator_run_attempt"],
            "workflow_path": attestation["validator_workflow_path"],
            "workflow_sha256": attestation["validator_workflow_sha256"],
        },
        attestation["validator_head_sha"],
        PRODUCER_WORKFLOWS["journey-activation"],
        workflow_bytes,
    )
    verify_validation_tree(
        release_path.parents[2],
        release_id,
        base_sha,
        attestation["validator_head_sha"],
    )
    with tempfile.TemporaryDirectory(prefix="mission-spine-validation-seal-") as temp_dir:
        result = subprocess.run(
            [
                "gh",
                "run",
                "download",
                str(run_id),
                "--repo",
                repository,
                "--name",
                artifact_name,
                "--dir",
                temp_dir,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=command_env,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError("sealed-validation: artifact download failed")
        entries = [path for path in Path(temp_dir).rglob("*")]
        if len(entries) != 1 or entries[0].relative_to(temp_dir).as_posix() != "evidence.json":
            raise ValueError("sealed-validation: artifact contains unexpected files")
        if not entries[0].is_file() or entries[0].is_symlink():
            raise ValueError("sealed-validation: evidence must be a regular file")
        raw = entries[0].read_bytes()
        if len(raw) > MAX_EVIDENCE_BYTES:
            raise ValueError("sealed-validation: evidence exceeds the sanitized size limit")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("sealed-validation: evidence is not valid UTF-8 JSON") from exc
    release_hash = hashlib.sha256(release_path.read_bytes()).hexdigest()
    validate_validation_seal_payload(
        payload,
        release_id,
        candidate_hash,
        release_hash,
        attestation,
    )


def verify_artifacts(root: Path, release_id: str, materialize_home: Path | None = None) -> None:
    release_path, candidate_path, release, candidate, candidate_hash = resolve_release_bundle(
        root, release_id
    )
    token = os.environ.get("RELEASE_EVIDENCE_TOKEN", "")
    if not token:
        raise ValueError("RELEASE_EVIDENCE_TOKEN is required")
    if shutil.which("gh") is None:
        raise ValueError("GitHub CLI is required")
    command_env = os.environ.copy()
    command_env["GH_TOKEN"] = token
    verify_candidate_artifact(
        command_env,
        release_id,
        candidate,
        candidate_path.read_bytes(),
        require_current_main=False,
    )
    verify_ai_rendered_config(root, candidate)

    expected_head = {
        "home-dist": candidate["home"]["source_sha"],
        "privacy-approval": candidate["analytics_privacy"]["approval_source_sha"],
        "ai-release-eval": candidate["services"]["devpath-ai-svc"]["source_sha"],
        "journey-activation": release["validation_attestation"]["validator_head_sha"],
        "journey-contextual-practice": release["validation_attestation"]["validator_head_sha"],
    }
    expected_head.update({
        label: (
            release["validation_attestation"]["validator_head_sha"]
            if label in {"home-visual", "home-axe-browser-a11y"}
            else quality_source(candidate, label)[1]
        )
        for label in QUALITY_EVIDENCE
    })

    frontend_authentications: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="mission-spine-evidence-") as temp_dir:
        temp = Path(temp_dir)
        verify_manual_catalog_inputs(command_env, candidate)
        signed_mobile_provenance, signed_mobile_run = verify_signed_mobile_artifact(
            command_env,
            candidate,
            temp / "signed-mobile",
        )
        manual_reference = release["quality_evidence"][QUALITY_EVIDENCE["manual-nvda"]]
        assert_unique_protected_producer_run(
            command_env,
            candidate["frontend"]["repository"],
            candidate["frontend"]["source_sha"],
            PRODUCER_WORKFLOWS["manual-nvda"],
            release_id,
            "manual",
            manual_reference["workflow_run_id"],
        )
        for label, artifact, journey, has_payload in _artifact_entries(release):
            repository = artifact["repository"]
            artifact_id = artifact["artifact_id"]
            run_id = artifact["workflow_run_id"]
            metadata = _run_json(
                ["gh", "api", f"repos/{repository}/actions/artifacts/{artifact_id}"],
                command_env,
            )
            if metadata.get("id") != artifact_id or metadata.get("name") != artifact["artifact_name"]:
                raise ValueError(f"{label}: artifact identity mismatch")
            if metadata.get("expired") is not False:
                raise ValueError(f"{label}: artifact is expired or expiry is unknown")
            if (metadata.get("workflow_run") or {}).get("id") != run_id:
                raise ValueError(f"{label}: workflow run identity mismatch")
            run = _run_json(
                ["gh", "api", f"repos/{repository}/actions/runs/{run_id}"],
                command_env,
            )
            workflow_path = PRODUCER_WORKFLOWS[label]
            workflow_bytes = _workflow_bytes(
                repository,
                workflow_path,
                expected_head[label],
                command_env,
            )
            if label == "home-dist":
                validate_workflow_dispatch_inputs(
                    workflow_bytes,
                    {
                        "release_id",
                        "candidate_spec_sha256",
                        "home_source_sha",
                        "dist_sha256",
                    },
                    "home-dist",
                )
            elif label == "ai-release-eval":
                validate_workflow_dispatch_inputs(
                    workflow_bytes,
                    {
                        "release_id",
                        "candidate_spec_sha256",
                        "ai_source_sha",
                        "gitops_source_sha",
                    },
                    "ai-release-eval",
                )
            elif label == "privacy-approval":
                validate_workflow_dispatch_inputs(
                    workflow_bytes,
                    {
                        "release_id",
                        "candidate_spec_sha256",
                        "approval_source_sha",
                    },
                    "privacy-approval",
                )
            if label in MANUAL_CATALOG_CONTRACTS:
                validate_workflow_dispatch_inputs(
                    workflow_bytes,
                    {
                        "release_id",
                        "candidate_run_id",
                        "candidate_run_attempt",
                        "candidate_artifact_id",
                        "candidate_spec_sha256",
                    },
                    "manual",
                )
            validate_run_provenance(
                label,
                run,
                artifact,
                expected_head[label],
                workflow_path,
                workflow_bytes,
                artifact["event"],
            )
            if label == "home-dist":
                branch = _run_json(
                    ["gh", "api", f"repos/{repository}/branches/master"],
                    command_env,
                )
                validate_home_master_trust(branch, run, expected_head[label])
                assert_unique_protected_producer_run(
                    command_env,
                    repository,
                    expected_head[label],
                    workflow_path,
                    release_id,
                    "home-dist",
                    run_id,
                )
            elif label in {"ai-release-eval", "privacy-approval"}:
                branch = _run_json(
                    ["gh", "api", f"repos/{repository}/branches/main"],
                    command_env,
                )
                if label == "ai-release-eval":
                    validate_ai_release_eval_trust(branch, run, expected_head[label])
                else:
                    validate_privacy_approval_trust(branch, run, expected_head[label])
                assert_unique_protected_producer_run(
                    command_env,
                    repository,
                    expected_head[label],
                    workflow_path,
                    release_id,
                    label,
                    run_id,
                )
            if journey:
                attestation = release["validation_attestation"]
                if repository != attestation["validator_repository"]:
                    raise ValueError(f"{label}: validator repository mismatch")
                if run_id != attestation["validator_run_id"]:
                    raise ValueError(f"{label}: validator run mismatch")
                if run.get("run_attempt") != attestation["validator_run_attempt"]:
                    raise ValueError(f"{label}: validator run attempt mismatch")

            destination = temp / label
            if label == "home-dist":
                download_home_artifact_archive(
                    command_env,
                    repository,
                    artifact_id,
                    metadata,
                    destination,
                )
            elif label == "ai-release-eval":
                download_ai_release_eval_archive(
                    command_env,
                    repository,
                    artifact_id,
                    metadata,
                    destination,
                )
            elif label == "privacy-approval":
                download_privacy_approval_archive(
                    command_env,
                    repository,
                    artifact_id,
                    metadata,
                    destination,
                )
            else:
                destination.mkdir()
                download = subprocess.run(
                    [
                        "gh",
                        "run",
                        "download",
                        str(run_id),
                        "--repo",
                        repository,
                        "--name",
                        artifact["artifact_name"],
                        "--dir",
                        str(destination),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=command_env,
                    check=False,
                )
                if download.returncode != 0:
                    raise ValueError(f"{label}: artifact download failed")
            entries = sorted(
                path.relative_to(destination).as_posix()
                for path in destination.rglob("*")
            )
            if label in {"home-visual", "home-axe-browser-a11y"}:
                expected_files = ["a11y-evidence.v2.json", "visual-evidence.v2.json"]
            elif label in FRONTEND_EVIDENCE_FILES:
                expected_files = sorted(FRONTEND_EVIDENCE_FILES[label])
            else:
                expected_files = ["dist.tar.gz", "evidence.json"] if has_payload else ["evidence.json"]
            expected_entries = (
                _frontend_expected_entries(label)
                if label in FRONTEND_EVIDENCE_FILES
                else expected_files
            )
            if entries != expected_entries:
                raise ValueError(f"{label}: artifact contains an unexpected file set")
            for filename in expected_files:
                path = destination / filename
                if not path.is_file() or path.is_symlink():
                    raise ValueError(f"{label}: artifact file must be a regular file")
            evidence_path = destination / artifact["evidence_file"]
            raw = evidence_path.read_bytes()
            if len(raw) > MAX_EVIDENCE_BYTES:
                raise ValueError(f"{label}: evidence exceeds the sanitized size limit")
            if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
                raise ValueError(f"{label}: evidence SHA-256 mismatch")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"{label}: evidence is not valid UTF-8 JSON") from exc
            validate_evidence_payload(
                label,
                payload,
                candidate_hash,
                candidate,
                run_id,
                run.get("run_attempt"),
            )
            if label in MANUAL_CATALOG_CONTRACTS:
                claim = {field: payload[field] for field in PROTECTED_APPROVAL_KEYS}
                validate_manual_chronology(
                    label,
                    signed_mobile_provenance,
                    signed_mobile_run,
                    run,
                    claim,
                )
                verify_live_protected_approval(
                    command_env,
                    repository,
                    run_id,
                    artifact["run_attempt"],
                    run,
                    label,
                    claim,
                    expected_head[label],
                )
            if label in {"ai-release-eval", "privacy-approval"}:
                claim = {field: payload[field] for field in PROTECTED_APPROVAL_KEYS}
                verify_live_protected_approval(
                    command_env,
                    repository,
                    run_id,
                    artifact["run_attempt"],
                    run,
                    label,
                    claim,
                    expected_head[label],
                )
            if label in FRONTEND_EVIDENCE_FILES:
                frontend_authentications[label] = validate_frontend_evidence_bundle(
                    label, destination, payload, candidate
                )
            if not journey:
                validate_sealed_metadata(label, payload, release)
            if has_payload:
                payload_path = destination / "dist.tar.gz"
                validate_home_dist_archive(payload_path)
                if hashlib.sha256(payload_path.read_bytes()).hexdigest() != artifact["payload_sha256"]:
                    raise ValueError("home-dist: payload SHA-256 mismatch")
                if materialize_home is not None:
                    materialize_home.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(payload_path, materialize_home)
    validate_atomic_frontend_authentication(frontend_authentications)
    verify_frontend_baseline_authentication(
        command_env,
        frontend_authentications["frontend-visual"],
    )
    _verify_validation_seal(
        command_env,
        release_path,
        release_id,
        release,
        candidate_hash,
        candidate["gitops"]["base_sha"],
    )
    print(f"verified twelve logical evidence manifests and final validation seal for {release_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--materialize-home", type=Path)
    args = parser.parse_args(argv)
    try:
        verify_artifacts(args.root.resolve(), args.release_id, args.materialize_home)
        return 0
    except (OSError, ValueError) as exc:
        print(f"release artifact verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
