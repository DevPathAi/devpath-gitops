#!/usr/bin/env python3
"""Install the one approved kustomize binary after byte-level verification."""

from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import subprocess
import sys
import tarfile
from urllib.request import Request, urlopen


KUSTOMIZE_VERSION = "v5.4.3"
KUSTOMIZE_URL = (
    "https://github.com/kubernetes-sigs/kustomize/releases/download/"
    "kustomize%2Fv5.4.3/kustomize_v5.4.3_linux_amd64.tar.gz"
)
KUSTOMIZE_ARCHIVE_SHA256 = (
    "3669470b454d865c8184d6bce78df05e977c9aea31c30df3c669317d43bcc7a7"
)
KUSTOMIZE_BINARY_SHA256 = (
    "1d6bae90ee8591f7a4ed5b75be3f9bf80b7609f0c785921320827cd93e7c3a9a"
)
KUSTOMIZE_BINARY_BYTES = 15_101_952
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024


def validate_archive(raw: bytes) -> bytes:
    if not raw or len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("pinned kustomize archive byte length is invalid")
    if sha256(raw).hexdigest() != KUSTOMIZE_ARCHIVE_SHA256:
        raise ValueError("pinned kustomize archive SHA-256 mismatch")
    try:
        archive = tarfile.open(fileobj=BytesIO(raw), mode="r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise ValueError("pinned kustomize archive is invalid") from exc
    with archive:
        members = archive.getmembers()
        if len(members) != 1:
            raise ValueError("pinned kustomize archive must have exactly one member")
        member = members[0]
        if (
            member.name != "kustomize"
            or not member.isreg()
            or member.size != KUSTOMIZE_BINARY_BYTES
            or member.mode != 0o755
        ):
            raise ValueError("pinned kustomize archive member metadata mismatch")
        source = archive.extractfile(member)
        if source is None:
            raise ValueError("pinned kustomize binary is unavailable")
        binary = source.read(KUSTOMIZE_BINARY_BYTES + 1)
    if len(binary) != KUSTOMIZE_BINARY_BYTES:
        raise ValueError("pinned kustomize binary byte length mismatch")
    if sha256(binary).hexdigest() != KUSTOMIZE_BINARY_SHA256:
        raise ValueError("pinned kustomize binary SHA-256 mismatch")
    return binary


def install(destination: Path) -> Path:
    if destination.exists() or destination.is_symlink():
        raise ValueError("pinned kustomize destination must not already exist")
    request = Request(KUSTOMIZE_URL, headers={"User-Agent": "mission-spine-release"})
    with urlopen(request, timeout=60) as response:  # noqa: S310 - exact HTTPS URL + digest
        if response.geturl().split(":", 1)[0].lower() != "https":
            raise ValueError("pinned kustomize download redirected away from HTTPS")
        raw = response.read(MAX_ARCHIVE_BYTES + 1)
    binary = validate_archive(raw)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    target = destination / "kustomize"
    with target.open("xb") as output:
        output.write(binary)
    target.chmod(0o755)
    version = subprocess.run(
        [str(target), "version"],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if version.returncode != 0 or version.stdout != b"v5.4.3\n":
        raise ValueError("pinned kustomize version output mismatch")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        target = install(args.destination.resolve())
        print(target)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"pinned kustomize install failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
