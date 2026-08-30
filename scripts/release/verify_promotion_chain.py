#!/usr/bin/env python3
"""Validate the exact Mission Spine GitOps promotion/rollback commit grammar."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from promote_service_digests import (
    SAFE_TAG,
    SERVICE_NAMES,
    SERVICE_PATHS,
    render_kustomization as render_service_kustomization,
    validate_image_selector,
)
from set_web_digest import (
    render_kustomization as render_web_kustomization,
    validate_release_identity as validate_web_release_identity,
)
from validate_release_manifest import resolve_release_bundle, validate_candidate_spec


SHA40 = re.compile(r"[0-9a-f]{40}")
MIGRATION_PATH = "apps/devpath-migration/base/kustomization.yaml"
MIGRATION_JOB_PATH = "apps/devpath-migration/base/job.yaml"
WEB_PATH = "apps/devpath-web/base/kustomization.yaml"
MIGRATION_IMAGE = "ghcr.io/devpathai/devpath-migration"
WEB_IMAGE = "ghcr.io/devpathai/devpath-web"
MAX_CHAIN_COMMITS = 32
MIGRATION_JOB_PREFIX = "devpath-flyway-migrate-"
WRITE_ACTOR = "devpath-gitops-release[bot]"
SHARED_MIGRATION_APPROVAL_FIX_SUBJECT = (
    "fix(release): authenticate shared migration approval"
)
SHARED_MIGRATION_APPROVAL_FIX_PATHS = (
    "scripts/release/verify_promotion_chain.py",
    "scripts/release/verify_release_artifacts.py",
    "tests/release/test_migration_result_trust.py",
    "tests/release/test_promotion_chain.py",
)


def _git(root: Path, args: list[str], *, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise ValueError("promotion chain Git object lookup failed")
    if binary:
        return result.stdout
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("promotion chain Git metadata is not UTF-8") from exc


def _parents(root: Path, commit: str) -> list[str]:
    row = str(_git(root, ["rev-list", "--parents", "-n", "1", commit])).split()
    if not row or row[0] != commit:
        raise ValueError("promotion chain commit lookup drifted")
    return row[1:]


def _subject(root: Path, commit: str) -> str:
    subject = str(_git(root, ["show", "-s", "--format=%s", commit]))
    if "\n" in subject or "\r" in subject or not subject:
        raise ValueError("promotion chain commit subject is invalid")
    return subject


def _require_write_actor(root: Path, commit: str) -> None:
    author = str(_git(root, ["show", "-s", "--format=%an", commit]))
    committer = str(_git(root, ["show", "-s", "--format=%cn", commit]))
    if author != WRITE_ACTOR or committer != WRITE_ACTOR:
        raise ValueError("promotion chain commit was not created by the release App")


def _require_delta(root: Path, commit: str, expected_paths: tuple[str, ...]) -> None:
    rows = str(
        _git(root, ["diff-tree", "--no-commit-id", "--name-status", "-r", commit])
    ).splitlines()
    actual: list[str] = []
    for row in rows:
        fields = row.split("\t")
        if len(fields) != 2 or fields[0] != "M":
            raise ValueError("promotion chain commit may only modify existing exact paths")
        actual.append(fields[1])
    if tuple(actual) != tuple(sorted(expected_paths)):
        raise ValueError("promotion chain commit path set is not exact")


def _blob(root: Path, commit: str, path: str) -> str:
    raw = _git(root, ["show", f"{commit}:{path}"], binary=True)
    assert isinstance(raw, bytes)
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("promotion chain kustomization is not UTF-8") from exc
    if "\r" in source or not source.endswith("\n"):
        raise ValueError("promotion chain kustomization is not canonical LF text")
    return source


def migration_job_name(image_digest: str, release_manifest_sha256: str) -> str:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest) is None:
        raise ValueError("migration image digest is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", release_manifest_sha256) is None:
        raise ValueError("release manifest hash is invalid for migration Job identity")
    return (
        MIGRATION_JOB_PREFIX
        + image_digest.removeprefix("sha256:")[:12]
        + "-"
        + release_manifest_sha256[:24]
    )


def _migration_patch_for_name(job_name: str) -> str:
    return (
        "patches:\n"
        "- target:\n"
        "    group: batch\n"
        "    version: v1\n"
        "    kind: Job\n"
        "    name: devpath-flyway-migrate\n"
        "  patch: |-\n"
        "    - op: replace\n"
        "      path: /metadata/name\n"
        f"      value: {job_name}\n"
        "    - op: replace\n"
        "      path: /spec/suspend\n"
        "      value: false\n"
    )


def _migration_patch(image_digest: str, release_manifest_sha256: str) -> str:
    return _migration_patch_for_name(
        migration_job_name(image_digest, release_manifest_sha256)
    )


def _migration_base_parts(base_source: str) -> tuple[str, str, str]:
    """Return exact canonical base body plus its tag/digest selector."""
    if (
        not base_source
        or "\r" in base_source
        or not base_source.endswith("\n")
        or "sync-options" in base_source
        or "Force=true" in base_source
        or "Replace=true" in base_source
        or "patchesJson6902:" in base_source
        or "patchesStrategicMerge:" in base_source
    ):
        raise ValueError("sealed base migration selector is not canonical")
    patch_keys = re.findall(
        r"^[ \t]*(patches(?:Json6902|StrategicMerge)?):[^\r\n]*$",
        base_source,
        flags=re.MULTILINE,
    )
    if patch_keys not in ([], ["patches"]):
        raise ValueError("sealed base migration patch key is not canonical")
    if patch_keys == ["patches"] and "\npatches:\n" not in "\n" + base_source:
        raise ValueError("sealed base migration patches key must be exact at document root")
    if base_source.count("patches:\n") > 1:
        raise ValueError("sealed base migration patch is duplicated")
    marker = base_source.find("patches:\n")
    body = base_source if marker < 0 else base_source[:marker]
    patch = "" if marker < 0 else base_source[marker:]
    selectors = [
        (kind, line.removeprefix(f"  {kind}: "))
        for line in body.splitlines()
        for kind in ("newTag", "digest")
        if line.startswith(f"  {kind}: ")
    ]
    if len(selectors) != 1:
        raise ValueError("sealed base migration selector is not unique")
    kind, value = selectors[0]
    if kind == "newTag":
        if SAFE_TAG.fullmatch(value) is None or patch:
            raise ValueError("legacy migration base tag/patch is not exact")
    else:
        if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
            raise ValueError("prior migration digest is invalid")
        expected_prefix = (
            MIGRATION_JOB_PREFIX + value.removeprefix("sha256:")[:12] + "-"
        )
        names = re.findall(
            r"^      value: (devpath-flyway-migrate-[0-9a-f]{12}-[0-9a-f]{24})$",
            patch,
            flags=re.MULTILINE,
        )
        if (
            len(names) != 1
            or not names[0].startswith(expected_prefix)
            or patch != _migration_patch_for_name(names[0])
        ):
            raise ValueError("prior migration patch is not the exact digest-derived patch")
    validate_image_selector(
        body,
        MIGRATION_IMAGE,
        expected_kind=kind,
        expected_value=value,
        label="shared-migration-base",
    )
    return body, kind, value


def render_migration_kustomization(
    base_source: str, image_digest: str, release_manifest_sha256: str
) -> str:
    """Create the only permitted legacy/prior-B to M transformation."""
    migration_job_name(image_digest, release_manifest_sha256)
    body, kind, prior_value = _migration_base_parts(base_source)
    lines = body.splitlines(keepends=True)
    selector = [
        index
        for index, line in enumerate(lines)
        if line.startswith(("  newTag: ", "  digest: "))
    ]
    if len(selector) != 1:
        raise ValueError("sealed base migration selector is not unique")
    lines[selector[0]] = f"  digest: {image_digest}\n"
    rendered = "".join(lines) + _migration_patch(
        image_digest, release_manifest_sha256
    )
    if rendered == base_source:
        raise ValueError("migration release identity is already selected")
    return rendered


def _require_migration(
    root: Path,
    commit: str,
    candidate: dict[str, Any],
    release_manifest_sha256: str,
) -> None:
    expected = render_migration_kustomization(
        _blob(root, candidate["gitops"]["base_sha"], MIGRATION_PATH),
        candidate["shared_migration"]["image_digest"],
        release_manifest_sha256,
    )
    actual = _blob(root, commit, MIGRATION_PATH)
    if actual != expected:
        raise ValueError("migration commit is not the exact digest/name transformation")
    validate_image_selector(
        actual,
        MIGRATION_IMAGE,
        expected_kind="digest",
        expected_value=candidate["shared_migration"]["image_digest"],
        label="shared-migration",
    )


def _require_inert_migration_base(root: Path, base: str) -> None:
    raw = _git(root, ["show", f"{base}:{MIGRATION_JOB_PATH}"], binary=True)
    assert isinstance(raw, bytes)
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("sealed base migration Job is not UTF-8") from exc
    if (
        "\r" in source
        or not source.endswith("\n")
        or source.count("suspend:") != 1
        or len(re.findall(r"^spec:$", source, flags=re.MULTILINE)) != 1
        or re.search(
            r"^spec:\n(?:  #[^\r\n]*\n)+  suspend: true\n  backoffLimit: 3\n",
            source,
            flags=re.MULTILINE,
        )
        is None
        or "sync-options" in source
        or "Force=true" in source
    ):
        raise ValueError("sealed base migration Job is not inert")


def _require_migration_base_selector(root: Path, base: str) -> None:
    _migration_base_parts(_blob(root, base, MIGRATION_PATH))


def _require_service_base_selectors(
    root: Path, base: str, candidate: dict[str, Any]
) -> None:
    for name in SERVICE_NAMES:
        # Rendering performs the exact one-entry/tag-or-digest structural check.
        render_service_kustomization(
            _blob(root, base, SERVICE_PATHS[name]), candidate, name
        )


def _require_services(
    root: Path,
    commit: str,
    candidate: dict[str, Any],
    *,
    transformed_from: str | None = None,
) -> None:
    for name in SERVICE_NAMES:
        service = candidate["services"][name]
        actual = _blob(root, commit, SERVICE_PATHS[name])
        if transformed_from is not None:
            expected = render_service_kustomization(
                _blob(root, transformed_from, SERVICE_PATHS[name]), candidate, name
            )
            if actual != expected:
                raise ValueError(f"{name}: service commit changed more than its image selector")
        validate_image_selector(
            actual,
            service["image_repository"],
            expected_kind="digest",
            expected_value=service["image_digest"],
            label=name,
        )


def _service_delta_paths(
    root: Path, parent: str, candidate: dict[str, Any]
) -> tuple[str, ...]:
    changed: list[str] = []
    for name in SERVICE_NAMES:
        source = _blob(root, parent, SERVICE_PATHS[name])
        if render_service_kustomization(source, candidate, name) != source:
            changed.append(SERVICE_PATHS[name])
    return tuple(sorted(changed))


def _require_web(
    root: Path,
    commit: str,
    candidate: dict[str, Any],
    candidate_spec_sha256: str,
    phase: str,
) -> None:
    source = _blob(root, commit, WEB_PATH)
    if phase == "base":
        try:
            validate_image_selector(
                source,
                WEB_IMAGE,
                expected_kind="newTag",
                expected_value=candidate["gitops"]["base_web_tag"],
                label="web-base-tag",
            )
        except ValueError:
            validate_image_selector(
                source,
                WEB_IMAGE,
                expected_kind="digest",
                expected_value=candidate["gitops"]["base_web_digest"],
                label="web-base-digest",
            )
        validate_web_release_identity(
            source, candidate, candidate_spec_sha256, phase
        )
        return
    elif phase == "prior":
        kind = "digest"
        value = candidate["frontend"]["rollback"]["prior_digest"]
    elif phase == "mission-off":
        kind = "digest"
        value = candidate["frontend"]["mission_off"]["image_digest"]
    elif phase == "mission-on":
        kind = "digest"
        value = candidate["frontend"]["selected_on_digest"]
    else:
        raise ValueError("promotion chain web phase is unknown")
    validate_image_selector(
        source,
        WEB_IMAGE,
        expected_kind=kind,
        expected_value=value,
        label=f"web-{phase}",
    )
    validate_web_release_identity(source, candidate, candidate_spec_sha256, phase)


def _require_web_transform(
    root: Path,
    commit: str,
    parent: str,
    candidate: dict[str, Any],
    candidate_spec_sha256: str,
    target: str,
    expected_current: str,
) -> None:
    expected = render_web_kustomization(
        _blob(root, parent, WEB_PATH),
        candidate,
        target,
        expected_current,
        candidate_spec_sha256=candidate_spec_sha256,
    )
    actual = _blob(root, commit, WEB_PATH)
    if actual != expected:
        raise ValueError("web commit changed more than the exact image selector")
    _require_web(root, commit, candidate, candidate_spec_sha256, target)


def inspect_chain(
    root: Path,
    candidate: dict[str, Any],
    candidate_spec_sha256: str,
    release_manifest_sha256: str,
    current_sha: str,
) -> dict[str, str]:
    """Return the current exact phase or reject every unrelated main state."""
    validate_candidate_spec(candidate)
    root = root.resolve()
    if SHA40.fullmatch(current_sha) is None:
        current_sha = str(_git(root, ["rev-parse", current_sha]))
    if SHA40.fullmatch(current_sha) is None:
        raise ValueError("promotion chain current SHA is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", candidate_spec_sha256) is None:
        raise ValueError("promotion chain candidate-spec hash is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", release_manifest_sha256) is None:
        raise ValueError("promotion chain release manifest hash is invalid")
    base = candidate["gitops"]["base_sha"]
    release_id = candidate["release_id"]
    migration_subject = (
        f"deploy(devpath-migration): {release_id} sealed {release_manifest_sha256}"
    )
    services_subject = f"release(services): promote {release_id} additive-services"
    off_subject = f"release(web): promote {release_id} mission-off"
    on_subject = f"release(web): promote {release_id} mission-on"
    rollback_off_subject = f"rollback(web): {release_id} frontend-mission-off"
    rollback_prior_subject = f"rollback(web): {release_id} frontend-prior"
    visited: set[str] = set()

    def walk(commit: str, depth: int) -> dict[str, str]:
        if depth > MAX_CHAIN_COMMITS or commit in visited:
            raise ValueError("promotion chain is cyclic or exceeds its bound")
        visited.add(commit)
        if commit == base:
            _require_inert_migration_base(root, base)
            _require_migration_base_selector(root, base)
            _require_service_base_selectors(root, base, candidate)
            _require_web(root, commit, candidate, candidate_spec_sha256, "base")
            return {
                "phase": "base",
                "web_phase": "base",
                "current_commit": commit,
                "base_commit": base,
                "migration_commit": "",
                "services_commit": "",
                "off_commit": "",
                "on_commit": "",
                "rollback_off_commit": "",
                "rollback_prior_commit": "",
            }
        parents = _parents(root, commit)
        if len(parents) != 1:
            raise ValueError("promotion chain commits must have exactly one parent")
        parent = parents[0]
        subject = _subject(root, commit)
        prior = walk(parent, depth + 1)

        if subject == migration_subject:
            _require_write_actor(root, commit)
            if prior["phase"] != "base" or parent != base:
                raise ValueError("migration commit must be the sole child of sealed base")
            _require_delta(root, commit, (MIGRATION_PATH,))
            _require_migration(root, commit, candidate, release_manifest_sha256)
            return {
                **prior,
                "phase": "migration",
                "current_commit": commit,
                "migration_commit": commit,
            }
        if subject == SHARED_MIGRATION_APPROVAL_FIX_SUBJECT:
            _require_write_actor(root, commit)
            if (
                prior["phase"] != "migration"
                or parent != prior["migration_commit"]
            ):
                raise ValueError(
                    "shared migration approval fix must directly follow migration"
                )
            _require_delta(root, commit, SHARED_MIGRATION_APPROVAL_FIX_PATHS)
            _require_migration(root, commit, candidate, release_manifest_sha256)
            _require_service_base_selectors(root, commit, candidate)
            _require_web(root, commit, candidate, candidate_spec_sha256, "base")
            return {
                **prior,
                "current_commit": commit,
            }
        if subject == services_subject:
            _require_write_actor(root, commit)
            if prior["phase"] != "migration":
                raise ValueError("services commit must directly follow migration")
            _require_delta(root, commit, _service_delta_paths(root, parent, candidate))
            _require_migration(root, commit, candidate, release_manifest_sha256)
            _require_services(root, commit, candidate, transformed_from=parent)
            _require_web(root, commit, candidate, candidate_spec_sha256, "base")
            return {
                **prior,
                "phase": "services",
                "web_phase": "base",
                "current_commit": commit,
                "services_commit": commit,
            }
        if subject == off_subject:
            _require_write_actor(root, commit)
            if prior["phase"] != "services" or prior["web_phase"] not in {"base", "prior"}:
                raise ValueError("mission-OFF commit must follow an exact services/prior phase")
            _require_delta(root, commit, (WEB_PATH,))
            _require_migration(root, commit, candidate, release_manifest_sha256)
            _require_services(root, commit, candidate)
            _require_web_transform(
                root,
                commit,
                parent,
                candidate,
                candidate_spec_sha256,
                "mission-off",
                prior["web_phase"],
            )
            return {
                **prior,
                "phase": "mission-off",
                "web_phase": "mission-off",
                "current_commit": commit,
                "off_commit": commit,
                "on_commit": "",
                "rollback_off_commit": "",
                "rollback_prior_commit": "",
            }
        if subject == on_subject:
            _require_write_actor(root, commit)
            if prior["phase"] != "mission-off":
                raise ValueError("mission-ON commit must directly follow mission-OFF")
            _require_delta(root, commit, (WEB_PATH,))
            _require_migration(root, commit, candidate, release_manifest_sha256)
            _require_services(root, commit, candidate)
            _require_web_transform(
                root,
                commit,
                parent,
                candidate,
                candidate_spec_sha256,
                "mission-on",
                "mission-off",
            )
            return {
                **prior,
                "phase": "mission-on",
                "web_phase": "mission-on",
                "current_commit": commit,
                "on_commit": commit,
                "rollback_off_commit": "",
                "rollback_prior_commit": "",
            }
        if subject == rollback_off_subject:
            _require_write_actor(root, commit)
            if prior["phase"] != "mission-on":
                raise ValueError("rollback OFF commit must directly follow mission-ON")
            _require_delta(root, commit, (WEB_PATH,))
            _require_migration(root, commit, candidate, release_manifest_sha256)
            _require_services(root, commit, candidate)
            _require_web_transform(
                root,
                commit,
                parent,
                candidate,
                candidate_spec_sha256,
                "mission-off",
                "mission-on",
            )
            return {
                **prior,
                "phase": "rollback-off",
                "web_phase": "mission-off",
                "current_commit": commit,
                "rollback_off_commit": commit,
                "rollback_prior_commit": "",
            }
        if subject == rollback_prior_subject:
            _require_write_actor(root, commit)
            if prior["phase"] not in {"mission-off", "rollback-off"}:
                raise ValueError("rollback prior commit must follow an exact OFF phase")
            _require_delta(root, commit, (WEB_PATH,))
            _require_migration(root, commit, candidate, release_manifest_sha256)
            _require_services(root, commit, candidate)
            _require_web_transform(
                root,
                commit,
                parent,
                candidate,
                candidate_spec_sha256,
                "prior",
                "mission-off",
            )
            return {
                **prior,
                "phase": "services",
                "web_phase": "prior",
                "current_commit": commit,
                "off_commit": "",
                "on_commit": "",
                "rollback_prior_commit": commit,
            }
        raise ValueError("current main contains an unrelated promotion commit")

    return walk(current_sha, 0)


def _write_outputs(path: Path, state: dict[str, str]) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("GITHUB_OUTPUT must be an existing regular file")
    with path.open("a", encoding="utf-8", newline="\n") as output:
        for key, value in state.items():
            if re.fullmatch(r"[a-z_]+", key) is None or "\n" in value or "\r" in value:
                raise ValueError("promotion chain output is unsafe")
            output.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-root", type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        release_root = (args.release_root or root).resolve()
        release_path, _, _, candidate, candidate_spec_sha256 = resolve_release_bundle(
            release_root, args.release_id
        )
        release_hash = hashlib.sha256(release_path.read_bytes()).hexdigest()
        current = str(_git(root, ["rev-parse", args.current]))
        state = inspect_chain(
            root,
            candidate,
            candidate_spec_sha256,
            release_hash,
            current,
        )
        if args.github_output is not None:
            _write_outputs(args.github_output, state)
        print(json.dumps(state, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"promotion chain verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
