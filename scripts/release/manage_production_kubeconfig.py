#!/usr/bin/env python3
"""Create and remove the protected production kubeconfig without path races."""

from __future__ import annotations

import argparse
import base64
import binascii
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Callable


SCOPES = {
    "production": (
        "mission-spine-production-kubeconfig-",
        "PRODUCTION_KUBECONFIG_B64",
    ),
    "staging": ("mission-spine-staging-kubeconfig-", "STAGING_KUBECONFIG_B64"),
}
PREFIX = SCOPES["production"][0]
NAMES = tuple(
    re.compile(rf"{re.escape(prefix)}[0-9a-f]{{32}}")
    for prefix, _environment_name in SCOPES.values()
)
MAX_ENCODED_BYTES = 2 * 1024 * 1024
MAX_DECODED_BYTES = 1024 * 1024


def _root(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ValueError("RUNNER_TEMP must be an existing absolute non-symlink directory")
    return path.resolve(strict=True)


def _open_exclusive(path: Path) -> int:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags, 0o600)


def _append_github_env(path: Path, row: bytes) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("GITHUB_ENV must be an existing regular non-symlink file")
    before = path.stat()
    if before.st_size > 1024 * 1024:
        raise ValueError("GITHUB_ENV exceeds its byte bound")
    flags = os.O_APPEND | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (
            before.st_ino and opened.st_ino and before.st_ino != opened.st_ino
        ):
            raise ValueError("GITHUB_ENV identity changed during append")
        if os.write(descriptor, row) != len(row):
            raise ValueError("GITHUB_ENV append was incomplete")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create(
    runner_temp: Path,
    github_env: Path,
    encoded: str,
    *,
    scope: str = "production",
    token_factory: Callable[[int], str] = secrets.token_hex,
) -> Path:
    if scope not in SCOPES:
        raise ValueError("kubeconfig scope is invalid")
    prefix, _environment_name = SCOPES[scope]
    root = _root(runner_temp)
    if (
        not isinstance(encoded, str)
        or not encoded
        or len(encoded.encode("ascii", errors="ignore")) != len(encoded)
        or len(encoded) > MAX_ENCODED_BYTES
    ):
        raise ValueError("production kubeconfig base64 input is invalid")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("production kubeconfig base64 input is invalid") from exc
    if not decoded or len(decoded) > MAX_DECODED_BYTES or b"\x00" in decoded:
        raise ValueError("decoded production kubeconfig is invalid or oversized")

    descriptor: int | None = None
    target: Path | None = None
    for _ in range(16):
        token = token_factory(16)
        if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None:
            raise ValueError("kubeconfig name entropy is invalid")
        candidate = root / f"{prefix}{token}"
        try:
            descriptor = _open_exclusive(candidate)
            target = candidate
            break
        except FileExistsError:
            continue
    if descriptor is None or target is None:
        raise ValueError("could not allocate a unique production kubeconfig")
    try:
        os.fchmod(descriptor, 0o600)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (
            os.name != "nt" and stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise ValueError("production kubeconfig file mode is not exact")
        written = 0
        while written < len(decoded):
            count = os.write(descriptor, decoded[written:])
            if count <= 0:
                raise ValueError("production kubeconfig write was incomplete")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        _append_github_env(
            github_env,
            f"KUBECONFIG={target.as_posix()}\n".encode("utf-8"),
        )
        return target
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        try:
            if target.exists() and not target.is_symlink():
                target.unlink()
        except OSError:
            pass
        raise


def cleanup(runner_temp: Path, target_text: str) -> None:
    root = _root(runner_temp)
    if not target_text:
        return
    target = Path(target_text)
    if (
        not target.is_absolute()
        or target.parent.resolve(strict=True) != root
        or not any(name.fullmatch(target.name) is not None for name in NAMES)
        or target.is_symlink()
        or not target.is_file()
    ):
        raise ValueError("production kubeconfig cleanup target is not exact")
    target.unlink()
    if target.exists() or target.is_symlink():
        raise ValueError("production kubeconfig cleanup did not complete")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="action", required=True)
    create_parser = subcommands.add_parser("create")
    create_parser.add_argument("--runner-temp", type=Path, required=True)
    create_parser.add_argument("--github-env", type=Path, required=True)
    create_parser.add_argument("--scope", choices=tuple(SCOPES), default="production")
    cleanup_parser = subcommands.add_parser("cleanup")
    cleanup_parser.add_argument("--runner-temp", type=Path, required=True)
    cleanup_parser.add_argument("--path", default="")
    args = parser.parse_args(argv)
    try:
        if args.action == "create":
            _prefix, environment_name = SCOPES[args.scope]
            create(
                args.runner_temp,
                args.github_env,
                os.environ.get(environment_name, ""),
                scope=args.scope,
            )
        else:
            cleanup(args.runner_temp, args.path)
        return 0
    except (OSError, ValueError) as exc:
        print(f"production kubeconfig management failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
