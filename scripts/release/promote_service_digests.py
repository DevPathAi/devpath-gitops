#!/usr/bin/env python3
"""Atomically select all sealed application-service OCI digests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_release_manifest import resolve_release_bundle, validate_candidate_spec


SERVICE_NAMES = (
    "devpath-admin",
    "devpath-ai-svc",
    "devpath-community-svc",
    "devpath-gateway",
    "devpath-lcs-svc",
    "devpath-learning-svc",
    "devpath-notification-svc",
    "devpath-platform-svc",
    "devpath-sandbox-svc",
)
SERVICE_PATHS = {
    name: f"apps/{name}/base/kustomization.yaml" for name in SERVICE_NAMES
}
SAFE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _image_block(source: str, image: str, label: str) -> tuple[list[str], int, int, int]:
    if not source or "\r" in source or not source.endswith("\n"):
        raise ValueError(f"{label}: kustomization must be nonempty canonical LF text")
    lines = source.splitlines(keepends=True)
    matches = [
        index for index, line in enumerate(lines) if line.strip() == f"- name: {image}"
    ]
    if len(matches) != 1:
        raise ValueError(f"{label}: expected exactly one target image entry")
    image_headers = [
        index for index, line in enumerate(lines) if line.strip() == "images:"
    ]
    if len(image_headers) != 1:
        raise ValueError(f"{label}: kustomization must contain one images section")
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
        raise ValueError(f"{label}: image transformer must contain exactly one image entry")
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
    return lines, start, end, indent


def validate_image_selector(
    source: str,
    image: str,
    *,
    expected_kind: str,
    expected_value: str,
    label: str,
) -> None:
    """Validate one exact name/newName plus tag-or-digest image selector."""
    if expected_kind not in {"newTag", "digest"}:
        raise ValueError(f"{label}: expected image selector kind is invalid")
    lines, start, end, _ = _image_block(source, image, label)
    block = [line.strip() for line in lines[start:end] if line.strip()]
    if block != [
        f"- name: {image}",
        f"newName: {image}",
        f"{expected_kind}: {expected_value}",
    ]:
        raise ValueError(f"{label}: image selector does not match the exact expected value")


def render_kustomization(
    source: str,
    candidate: dict[str, Any],
    service_name: str,
) -> str:
    """Replace one sealed-base tag selector with one candidate-bound digest."""
    validate_candidate_spec(candidate)
    if service_name not in SERVICE_PATHS:
        raise ValueError("unknown application service")
    service = candidate["services"][service_name]
    image = service["image_repository"]
    digest = service["image_digest"]
    lines, start, end, indent = _image_block(source, image, service_name)
    block = [line for line in lines[start:end] if line.strip()]
    if len(block) != 3:
        raise ValueError(
            f"{service_name}: image entry must have only name/newName and one selector"
        )
    if block[0].strip() != f"- name: {image}":
        raise ValueError(f"{service_name}: image name is not exact")
    if block[1].strip() != f"newName: {image}":
        raise ValueError(f"{service_name}: image newName is not exact")
    selector = block[2].strip()
    if selector.startswith("newTag: "):
        tag = selector.removeprefix("newTag: ")
        if SAFE_TAG.fullmatch(tag) is None:
            raise ValueError(f"{service_name}: sealed base tag is unsafe")
    elif selector.startswith("digest: "):
        prior_digest = selector.removeprefix("digest: ")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", prior_digest) is None:
            raise ValueError(f"{service_name}: sealed base digest is invalid")
    else:
        raise ValueError(f"{service_name}: sealed base selector kind is invalid")
    child = " " * (indent + 2)
    replacement = [
        f"{' ' * indent}- name: {image}\n",
        f"{child}newName: {image}\n",
        f"{child}digest: {digest}\n",
    ]
    rendered = "".join(lines[:start] + replacement + lines[end:])
    if rendered.count(f"digest: {digest}") != 1 or "newTag:" in rendered:
        raise ValueError(f"{service_name}: failed to create one immutable digest selector")
    return rendered


def validate_rendered_output(
    rendered: str,
    candidate: dict[str, Any],
    service_name: str,
) -> None:
    """Require one exact Deployment/container at the sealed root digest."""
    validate_candidate_spec(candidate)
    if service_name not in SERVICE_PATHS:
        raise ValueError("unknown application service")
    if not rendered or "\r" in rendered or not rendered.endswith("\n"):
        raise ValueError(f"{service_name}: rendered manifest must be canonical LF text")
    service = candidate["services"][service_name]
    repository = service["image_repository"]
    expected = f"{repository}@{service['image_digest']}"
    documents: list[list[str]] = []
    current: list[str] = []
    for line in rendered.splitlines():
        if line == "---":
            if current:
                documents.append(current)
                current = []
        else:
            current.append(line)
    if current:
        documents.append(current)

    def block_end(lines: list[str], start: int, indent: int) -> int:
        for index in range(start + 1, len(lines)):
            line = lines[index]
            if line and len(line) - len(line.lstrip(" ")) <= indent:
                return index
        return len(lines)

    def unique_mapping(lines: list[str], start: int, indent: int, key: str) -> int:
        end = block_end(lines, start, indent) if start >= 0 else len(lines)
        expected_indent = indent + 2
        expected = f"{' ' * expected_indent}{key}:"
        matches = [
            index
            for index in range(start + 1, end)
            if lines[index] == expected
        ]
        if len(matches) != 1:
            raise ValueError(f"{service_name}: rendered {key} hierarchy is not exact")
        return matches[0]

    deployments: list[list[str]] = []
    for document in documents:
        if document.count("kind: Deployment") != 1:
            continue
        metadata = unique_mapping(document, -1, -2, "metadata")
        metadata_end = block_end(document, metadata, 0)
        names = [
            line.removeprefix("  name: ")
            for line in document[metadata + 1 : metadata_end]
            if line.startswith("  name: ") and not line.startswith("    ")
        ]
        if names == [service_name]:
            deployments.append(document)
    if len(deployments) != 1:
        raise ValueError(f"{service_name}: expected one exact rendered Deployment")

    document = deployments[0]
    spec = unique_mapping(document, -1, -2, "spec")
    template = unique_mapping(document, spec, 0, "template")
    pod_spec = unique_mapping(document, template, 2, "spec")
    containers = unique_mapping(document, pod_spec, 4, "containers")
    container_end = len(document)
    for index in range(containers + 1, len(document)):
        line = document[index]
        if not line:
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent < 6 or (indent == 6 and not line.startswith("      - ")):
            container_end = index
            break
    entries: list[list[str]] = []
    entry: list[str] = []
    for line in document[containers + 1 : container_end]:
        indent = len(line) - len(line.lstrip(" ")) if line else 0
        if indent == 6 and line.startswith("      - "):
            if entry:
                entries.append(entry)
            entry = [line]
        elif entry:
            entry.append(line)
    if entry:
        entries.append(entry)
    if not entries:
        raise ValueError(f"{service_name}: rendered containers list is empty")

    parsed: list[tuple[str | None, str | None]] = []
    for item in entries:
        direct: list[str] = [item[0].removeprefix("      - ")]
        direct.extend(
            line.removeprefix("        ")
            for line in item[1:]
            if line.startswith("        ") and not line.startswith("          ")
        )
        names = [line.removeprefix("name: ") for line in direct if line.startswith("name: ")]
        images = [line.removeprefix("image: ") for line in direct if line.startswith("image: ")]
        if len(names) > 1 or len(images) > 1:
            raise ValueError(f"{service_name}: rendered container fields are duplicated")
        parsed.append((names[0] if names else None, images[0] if images else None))
    targets = [image for name, image in parsed if name == service_name]
    if targets != [expected]:
        raise ValueError(f"{service_name}: target container is not the sealed digest")
    repository_images = [
        image
        for _, image in parsed
        if image is not None
        and (image.startswith(repository + "@") or image.startswith(repository + ":"))
    ]
    if repository_images != [expected]:
        raise ValueError(f"{service_name}: target image appears outside the exact container")


def _target_path(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if target != root / Path(relative) or not target.is_file() or target.is_symlink():
        raise ValueError(f"unsafe or missing service kustomization: {relative}")
    return target


def apply_service_digests(
    root: Path,
    candidate: dict[str, Any],
    kustomize_bin: Path,
    *,
    build_arguments: tuple[str, ...] = ("build",),
) -> None:
    """Validate all nine selectors first, then write and render the exact allowlist."""
    validate_candidate_spec(candidate)
    root = root.resolve()
    rendered_files: dict[str, str] = {}
    targets: dict[str, Path] = {}
    for name in SERVICE_NAMES:
        target = _target_path(root, SERVICE_PATHS[name])
        targets[name] = target
        rendered_files[name] = render_kustomization(
            target.read_text(encoding="utf-8"), candidate, name
        )
    for name in SERVICE_NAMES:
        targets[name].write_text(rendered_files[name], encoding="utf-8", newline="\n")
    for name in SERVICE_NAMES:
        command = [
            str(kustomize_bin),
            *build_arguments,
            str(targets[name].parent),
        ]
        result = subprocess.run(command, capture_output=True, check=False)
        if result.returncode != 0:
            raise ValueError(f"{name}: pinned Kustomize build failed")
        try:
            rendered = result.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{name}: rendered manifest is not UTF-8") from exc
        validate_rendered_output(rendered, candidate, name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest-root", type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--kustomize-bin", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        manifest_root = (args.manifest_root or root).resolve()
        _, _, _, candidate, _ = resolve_release_bundle(manifest_root, args.release_id)
        apply_service_digests(root, candidate, args.kustomize_bin)
        print(f"selected all nine immutable service digests for {args.release_id}")
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"service digest promotion failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
