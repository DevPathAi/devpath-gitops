#!/usr/bin/env python3
"""Set the web Kustomize image from a validated release manifest only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_release_manifest import resolve_release_bundle, validate_candidate_spec


WEB_IMAGE = "ghcr.io/devpathai/devpath-web"
SHA256 = re.compile(r"[0-9a-f]{64}")
IDENTITY_BLOCK = re.compile(
    r"configMapGenerator:\n"
    r"- name: devpath-web-release-identity\n"
    r"  literals:\n"
    r"  - MISSION_RELEASE_READY=([^\n]+)\n"
    r"  - MISSION_RELEASE_ID=([^\n]+)\n"
    r"  - MISSION_CANDIDATE_SPEC_SHA256=([^\n]+)\n"
    r"  - MISSION_IMAGE_DIGEST=([^\n]+)\n"
)
TARGET_FIELDS = {
    "mission-off": ("frontend", "mission_off", "image_digest"),
    "mission-on": ("frontend", "selected_on_digest"),
    "prior": ("frontend", "rollback", "prior_digest"),
}
EXPECTED_FIELDS = {
    "base": ("gitops", "base_web_digest"),
    "mission-off": ("frontend", "mission_off", "image_digest"),
    "mission-on": ("frontend", "selected_on_digest"),
    "prior": ("frontend", "rollback", "prior_digest"),
}


def _lookup(data: dict, fields: tuple[str, ...]) -> str:
    value = data
    for field in fields:
        value = value[field]
    return value


def _identity_values(
    manifest: dict,
    candidate_spec_sha256: str,
    phase: str,
) -> tuple[str, str, str, str]:
    if SHA256.fullmatch(candidate_spec_sha256) is None:
        raise ValueError("candidate-spec SHA-256 is invalid")
    if phase in {"base", "prior"}:
        prior = manifest["frontend"]["rollback"]["prior_identity"]
        return (
            "true" if prior["ready"] else "false",
            prior["release_id"],
            prior["candidate_spec_sha256"],
            prior["image_digest"],
        )
    if phase not in EXPECTED_FIELDS:
        raise ValueError(f"unknown release identity phase: {phase}")
    return (
        "true",
        manifest["release_id"],
        candidate_spec_sha256,
        _lookup(manifest, EXPECTED_FIELDS[phase]),
    )


def _identity_block(values: tuple[str, str, str, str]) -> str:
    ready, release_id, candidate_sha, image_digest = values
    return (
        "configMapGenerator:\n"
        "- name: devpath-web-release-identity\n"
        "  literals:\n"
        f"  - MISSION_RELEASE_READY={ready}\n"
        f"  - MISSION_RELEASE_ID={release_id}\n"
        f"  - MISSION_CANDIDATE_SPEC_SHA256={candidate_sha}\n"
        f"  - MISSION_IMAGE_DIGEST={image_digest}\n"
    )


def _parse_identity(source: str) -> tuple[str, tuple[str, str, str, str]]:
    matches = list(IDENTITY_BLOCK.finditer(source))
    if source.count("configMapGenerator:\n") != 1 or len(matches) != 1:
        raise ValueError("current web release identity block is not exact")
    for key in (
        "MISSION_RELEASE_READY",
        "MISSION_RELEASE_ID",
        "MISSION_CANDIDATE_SPEC_SHA256",
        "MISSION_IMAGE_DIGEST",
    ):
        if source.count(f"  - {key}=") != 1:
            raise ValueError(f"web release identity field {key} is not unique")
    match = matches[0]
    return match.group(0), tuple(match.groups())


def validate_release_identity(
    source: str,
    manifest: dict,
    candidate_spec_sha256: str,
    phase: str,
) -> None:
    block, actual = _parse_identity(source)
    expected = _identity_values(manifest, candidate_spec_sha256, phase)
    if actual != expected:
        raise ValueError(f"web {phase} release identity is not exact")
    if block != _identity_block(actual):
        raise ValueError("web release identity serialization is not canonical")


def _render_identity(
    source: str,
    manifest: dict,
    candidate_spec_sha256: str,
    target: str,
    expected_current: str,
) -> str:
    validate_release_identity(
        source, manifest, candidate_spec_sha256, expected_current
    )
    current, _ = _parse_identity(source)
    replacement = _identity_block(
        _identity_values(manifest, candidate_spec_sha256, target)
    )
    return source.replace(current, replacement, 1)


def render_kustomization(
    source: str,
    manifest: dict,
    target: str,
    expected_current: str = "base",
    *,
    candidate_spec_sha256: str,
) -> str:
    validate_candidate_spec(manifest)
    if target not in TARGET_FIELDS:
        raise ValueError(f"unknown release target: {target}")
    if expected_current not in EXPECTED_FIELDS:
        raise ValueError(f"unknown current phase: {expected_current}")
    if "\r\n" in source:
        raise ValueError("kustomization must use LF line endings")

    lines = source.splitlines(keepends=True)
    matches = [index for index, line in enumerate(lines) if line.strip() == f"- name: {WEB_IMAGE}"]
    if len(matches) != 1:
        raise ValueError("web kustomization must contain exactly one devpath-web image entry")
    image_headers = [
        index for index, line in enumerate(lines) if line == "images:\n"
    ]
    if len(image_headers) != 1:
        raise ValueError("web kustomization must contain one top-level images section")
    section_start = image_headers[0] + 1
    section_end = len(lines)
    for index in range(section_start, len(lines)):
        line = lines[index]
        if line and not line.startswith((" ", "\t", "-", "#", "\n")):
            section_end = index
            break
    image_entries = [
        index
        for index in range(section_start, section_end)
        if lines[index].lstrip().startswith("- name:")
    ]
    if len(image_entries) != 1 or matches[0] != image_entries[0]:
        raise ValueError(
            "web kustomization images section must contain exactly one image entry"
        )

    start = matches[0]
    indent = len(lines[start]) - len(lines[start].lstrip(" "))
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped:
            continue
        current_indent = len(lines[index]) - len(lines[index].lstrip(" "))
        if current_indent <= indent:
            end = index
            break

    block = lines[start:end]
    new_name = [line for line in block if line.strip().startswith("newName:")]
    tags = [line for line in block if line.strip().startswith("newTag:")]
    digests = [line for line in block if line.strip().startswith("digest:")]
    allowed = {"- name:", "newName:", "newTag:", "digest:"}
    for line in block:
        stripped = line.strip()
        if stripped and not any(stripped.startswith(prefix) for prefix in allowed):
            raise ValueError(f"unexpected field in web image transformer: {stripped}")
    if len(new_name) != 1 or new_name[0].strip() != f"newName: {WEB_IMAGE}":
        raise ValueError("web image newName must be exact")
    if len(tags) + len(digests) != 1:
        raise ValueError("web image must have exactly one current tag or digest")

    expected_digest = _lookup(manifest, EXPECTED_FIELDS[expected_current])
    if digests:
        current_digest = digests[0].strip().split("digest:", 1)[1].strip()
        if current_digest != expected_digest:
            raise ValueError(
                f"current digest is {current_digest}; expected {expected_current} digest {expected_digest}"
            )
    elif expected_current != "base":
        raise ValueError(f"current digest is a tag; expected {expected_current} digest {expected_digest}")
    else:
        current_tag = tags[0].strip().split("newTag:", 1)[1].strip()
        if current_tag != manifest["gitops"]["base_web_tag"]:
            raise ValueError("current tag is not the sealed trusted base tag")

    target_digest = _lookup(manifest, TARGET_FIELDS[target])
    child_indent = " " * (indent + 2)
    replacement = [
        f"{' ' * indent}- name: {WEB_IMAGE}\n",
        f"{child_indent}newName: {WEB_IMAGE}\n",
        f"{child_indent}digest: {target_digest}\n",
    ]
    rendered = "".join(lines[:start] + replacement + lines[end:])
    rendered = _render_identity(
        rendered,
        manifest,
        candidate_spec_sha256,
        target,
        expected_current,
    )
    if rendered.count(f"digest: {target_digest}") != 1 or "newTag:" in rendered:
        raise ValueError("failed to create one immutable digest selector")
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest-root", type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--target", choices=sorted(TARGET_FIELDS), required=True)
    parser.add_argument("--expected-current", choices=sorted(EXPECTED_FIELDS), required=True)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        manifest_root = (args.manifest_root or root).resolve()
        _, _, _, manifest, candidate_spec_sha256 = resolve_release_bundle(
            manifest_root, args.release_id
        )
        relative_target = manifest["gitops"]["web_kustomization"]
        target_path = (root / relative_target).resolve()
        if target_path != (root / "apps" / "devpath-web" / "base" / "kustomization.yaml").resolve():
            raise ValueError("manifest attempted to mutate a non-web path")
        source = target_path.read_text(encoding="utf-8")
        rendered = render_kustomization(
            source,
            manifest,
            args.target,
            args.expected_current,
            candidate_spec_sha256=candidate_spec_sha256,
        )
        target_path.write_text(rendered, encoding="utf-8", newline="\n")
        print(f"selected {args.target} immutable digest for {args.release_id}")
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"web digest selection failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
