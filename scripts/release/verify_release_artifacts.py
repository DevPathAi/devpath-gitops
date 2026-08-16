#!/usr/bin/env python3
"""Fetch and verify sealed, sanitized release evidence from GitHub Actions."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from validate_release_manifest import (
    FRONTEND_CATALOG_CONTRACTS,
    FRONTEND_FIXTURE_IDS,
    PRODUCER_WORKFLOWS,
    QUALITY_EVIDENCE,
    SHA64,
    _validate_sanitized,
    quality_source,
    resolve_release_bundle,
)


MAX_EVIDENCE_BYTES = 256 * 1024
MAX_HOME_PAYLOAD_BYTES = 100 * 1024 * 1024
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
        if value["home_source_sha"] != candidate["home"]["source_sha"]:
            raise ValueError("home-dist source SHA mismatch")
        if value["dist_sha256"] != candidate["home"]["dist_sha256"]:
            raise ValueError("home-dist payload SHA mismatch")
        return
    if label == "privacy-approval":
        keys = {
            "candidate_spec_sha256", "status", "approved_at", "collection_mode", "region",
            "project_identity", "retention_days", "access_owner", "deletion_runbook",
            *PRODUCER_EVIDENCE_KEYS,
        }
        value = _exact_payload(payload, keys, label)
        for field in keys - {
            "candidate_spec_sha256", "status", "approved_at", *PRODUCER_EVIDENCE_KEYS,
        }:
            if value[field] != candidate["analytics_privacy"][field]:
                raise ValueError(f"privacy-approval {field} mismatch")
        if not isinstance(value["approved_at"], str) or not value["approved_at"].endswith("Z"):
            raise ValueError("privacy-approval approved_at must be UTC")
        try:
            datetime.fromisoformat(value["approved_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("privacy-approval approved_at is invalid") from exc
        return
    if label == "ai-release-eval":
        config = candidate["ai_release_eval_config"]
        value = _exact_payload(
            payload,
            {
                "candidate_spec_sha256", "status", "ai_source_sha", "primary_model",
                "fallback_models", "prompt_sha256", "fixture_revision", "fixture_sha256",
                "hard_invariants_percent", "usefulness_percent", "baseline_delta_points",
                *PRODUCER_EVIDENCE_KEYS,
            },
            label,
        )
        expected = {
            "ai_source_sha": candidate["services"]["devpath-ai-svc"]["source_sha"],
            "primary_model": config["primary_model"],
            "fallback_models": config["fallback_models"],
            "prompt_sha256": config["prompt_sha256"],
            "fixture_revision": config["fixture_revision"],
            "fixture_sha256": config["fixture_sha256"],
        }
        for field, expected_value in expected.items():
            if value[field] != expected_value:
                raise ValueError(f"ai-release-eval {field} mismatch")
        if _bounded_number(value["hard_invariants_percent"], "hard invariants", 100, 100) != 100:
            raise ValueError("hard invariants must be 100")
        _bounded_number(value["usefulness_percent"], "usefulness", 90, 100)
        _bounded_number(value["baseline_delta_points"], "baseline delta", -5, 100)
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
                "case_catalog_version", "fixture_ids", "surface_case_counts",
                "capture_surface", "device_evidence",
                "render_provenance_sha256", "pixel_diff_percent",
            }
        elif label == "frontend-automated-a11y":
            extras = {
                "case_catalog_version", "fixture_ids", "surface_case_counts",
                "capture_surface", "device_evidence",
                "test_provenance_sha256", "standard",
                "critical_violations", "serious_violations",
            }
        elif label == "manual-nvda":
            extras = {"assistive_technology", "test_provenance_sha256"}
        elif label == "manual-voiceover":
            extras = {
                "assistive_technology", "test_provenance_sha256",
                "build_provenance_sha256", "signed_ipa_sha256",
            }
        elif label == "manual-talkback":
            extras = {
                "assistive_technology", "test_provenance_sha256",
                "build_provenance_sha256", "signed_apk_sha256",
            }
        else:  # Home labels return above.
            raise ValueError(f"unknown evidence kind: {label}")
        value = _exact_payload(payload, common_keys | extras, label)
        expected_repository, expected_source = quality_source(candidate, label)
        if value["repository"] != expected_repository:
            raise ValueError(f"{label} repository mismatch")
        if value["source_sha"] != expected_source:
            raise ValueError(f"{label} source SHA mismatch")
        if value["case_catalog_sha256"] != catalog["sha256"]:
            raise ValueError(f"{label} case catalog hash mismatch")
        _validate_quality_counts(value, catalog, label)

        # Frontend release evidence binds pre-run inputs only. Post-run manifest and
        # artifact-set hashes stay in the producer's separate detailed artifact.
        provenance_field = "render_provenance_sha256" if label == "frontend-visual" else "test_provenance_sha256"
        if value[provenance_field] != catalog["provenance_sha256"]:
            raise ValueError(f"{label} {provenance_field} mismatch")
        if label in {"frontend-visual", "frontend-automated-a11y"}:
            if (
                value["case_catalog_version"] != catalog["case_catalog_version"]
                or value["fixture_ids"] != catalog["fixture_ids"]
                or value["fixture_ids"] != list(FRONTEND_FIXTURE_IDS)
            ):
                raise ValueError(f"{label} must bind the exact approved frontend catalog order")
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
        if label == "frontend-visual":
            if _bounded_number(value["pixel_diff_percent"], "frontend visual pixel diff", 0, 0) != 0:
                raise ValueError("frontend visual pixel diff must be zero")
        if label == "frontend-automated-a11y":
            if value["standard"] != "WCAG2.2AA":
                raise ValueError("frontend automated a11y standard must be WCAG2.2AA")
            for field in ("critical_violations", "serious_violations"):
                if isinstance(value[field], bool) or value[field] != 0:
                    raise ValueError(f"frontend automated a11y {field} must be integer zero")
        expected_at = {
            "manual-nvda": "NVDA+Chromium",
            "manual-voiceover": "VoiceOver+Safari+iOS",
            "manual-talkback": "TalkBack+Android",
        }
        if label in expected_at and value["assistive_technology"] != expected_at[label]:
            raise ValueError(f"{label} assistive technology mismatch")
        if label in {"manual-voiceover", "manual-talkback"}:
            mobile = candidate["quality_evidence_inputs"]["mobile_test_artifacts"]
            for field in ("build_provenance_sha256",):
                if value[field] != mobile[field]:
                    raise ValueError(f"{label} {field} mismatch")
            signed_field = "signed_ipa_sha256" if label == "manual-voiceover" else "signed_apk_sha256"
            if value[signed_field] != mobile[signed_field]:
                raise ValueError(f"{label} {signed_field} mismatch")
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


def _workflow_bytes(
    repository: str,
    workflow_path: str,
    head_sha: str,
    env: dict[str, str],
) -> bytes:
    result = subprocess.run(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github.raw+json",
            f"repos/{repository}/contents/{workflow_path}?ref={head_sha}",
        ],
        capture_output=True,
        env=env,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise ValueError("producer workflow blob download failed")
    return result.stdout


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
    validator_head_sha: str,
) -> None:
    """Reject a validation run from a look-alike branch or extra tree delta."""
    current_head = _git_output(root, ["rev-parse", "HEAD"])
    if _git_output(root, ["rev-parse", "HEAD^"]) != validator_head_sha:
        raise ValueError("validator head is not the sole parent of the sealed release commit")
    if _git_output(root, ["rev-parse", f"{validator_head_sha}^"]) != base_sha:
        raise ValueError("validator head is not based directly on sealed GitOps base")
    candidate_path = f"release-manifests/candidates/{release_id}.candidate-spec.json"
    release_path = f"release-manifests/releases/{release_id}.json"
    candidate_delta = _git_output(root, ["diff", "--name-only", f"{base_sha}...{validator_head_sha}"])
    if candidate_delta.splitlines() != [candidate_path]:
        raise ValueError("validator head has a disallowed candidate tree delta")
    final_delta = _git_output(root, ["diff", "--name-only", f"{validator_head_sha}...{current_head}"])
    if final_delta.splitlines() != [release_path]:
        raise ValueError("sealed release commit has a disallowed tree delta")
    if _git_output(root, ["show", f"{validator_head_sha}:{candidate_path}"]) != (
        root / candidate_path
    ).read_text(encoding="utf-8").strip():
        raise ValueError("validator candidate blob differs from sealed branch bytes")


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


def _run_json(command: list[str], env: dict[str, str]) -> dict[str, Any]:
    result = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        raise ValueError(f"GitHub evidence API command failed: {command[1]}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("GitHub evidence API returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("GitHub evidence API returned an invalid object")
    return value


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
    release_path, _, release, candidate, candidate_hash = resolve_release_bundle(root, release_id)
    token = os.environ.get("RELEASE_EVIDENCE_TOKEN", "")
    if not token:
        raise ValueError("RELEASE_EVIDENCE_TOKEN is required")
    if shutil.which("gh") is None:
        raise ValueError("GitHub CLI is required")
    command_env = os.environ.copy()
    command_env["GH_TOKEN"] = token

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

    with tempfile.TemporaryDirectory(prefix="mission-spine-evidence-") as temp_dir:
        temp = Path(temp_dir)
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
            validate_run_provenance(
                label,
                run,
                artifact,
                expected_head[label],
                workflow_path,
                workflow_bytes,
                artifact["event"],
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
            else:
                expected_files = ["dist.tar.gz", "evidence.json"] if has_payload else ["evidence.json"]
            if entries != expected_files:
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
            if not journey:
                validate_sealed_metadata(label, payload, release)
            if has_payload:
                payload_path = destination / "dist.tar.gz"
                if payload_path.stat().st_size > MAX_HOME_PAYLOAD_BYTES:
                    raise ValueError("home-dist: payload exceeds 100 MiB")
                if hashlib.sha256(payload_path.read_bytes()).hexdigest() != artifact["payload_sha256"]:
                    raise ValueError("home-dist: payload SHA-256 mismatch")
                if materialize_home is not None:
                    materialize_home.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(payload_path, materialize_home)
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
