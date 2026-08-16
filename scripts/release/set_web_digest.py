#!/usr/bin/env python3
"""Set the web Kustomize image from a validated release manifest only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_release_manifest import resolve_release_bundle, validate_candidate_spec


WEB_IMAGE = "ghcr.io/devpathai/devpath-web"
TARGET_FIELDS = {
    "mission-off": ("frontend", "mission_off", "image_digest"),
    "mission-on": ("frontend", "selected_on_digest"),
    "prior": ("frontend", "rollback", "prior_digest"),
}
EXPECTED_FIELDS = {
    "base": ("gitops", "base_web_digest"),
    "mission-off": ("frontend", "mission_off", "image_digest"),
    "mission-on": ("frontend", "selected_on_digest"),
}


def _lookup(data: dict, fields: tuple[str, ...]) -> str:
    value = data
    for field in fields:
        value = value[field]
    return value


def render_kustomization(
    source: str,
    manifest: dict,
    target: str,
    expected_current: str = "base",
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
    if sum(1 for line in lines if line.lstrip().startswith("- name:")) != 1:
        raise ValueError("web kustomization image transformer must contain exactly one image")

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
        _, _, _, manifest, _ = resolve_release_bundle(manifest_root, args.release_id)
        relative_target = manifest["gitops"]["web_kustomization"]
        target_path = (root / relative_target).resolve()
        if target_path != (root / "apps" / "devpath-web" / "base" / "kustomization.yaml").resolve():
            raise ValueError("manifest attempted to mutate a non-web path")
        source = target_path.read_text(encoding="utf-8")
        rendered = render_kustomization(source, manifest, args.target, args.expected_current)
        target_path.write_text(rendered, encoding="utf-8", newline="\n")
        print(f"selected {args.target} immutable digest for {args.release_id}")
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"web digest selection failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
