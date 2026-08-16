#!/usr/bin/env python3
"""Wait for the exact manifest digest and run a bounded sanitized HTTP canary."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, urlopen

from validate_release_manifest import resolve_release_bundle


def _expected_digest(candidate: dict, phase: str) -> str:
    if phase == "mission-off":
        return candidate["frontend"]["mission_off"]["image_digest"]
    if phase == "mission-on":
        return candidate["frontend"]["selected_on_digest"]
    if phase == "prior":
        return candidate["frontend"]["rollback"]["prior_digest"]
    raise ValueError("unknown rollout phase")


def _kubectl(args: list[str], context: str, namespace: str, capture: bool = False) -> str:
    command = ["kubectl", "--context", context, "--namespace", namespace, *args]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ValueError("kubectl rollout verification failed")
    return result.stdout.strip() if capture else ""


def _current_deployment_image(
    context: str,
    namespace: str,
    deployment: str,
    container: str,
) -> str | None:
    raw = _kubectl(
        ["get", f"deployment/{deployment}", "-o", "json"],
        context,
        namespace,
        capture=True,
    )
    document = json.loads(raw)
    images = [
        item.get("image")
        for item in document.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        if item.get("name") == container
    ]
    return images[0] if len(images) == 1 else None


def wait_rollout(
    root: Path,
    release_id: str,
    environment: str,
    phase: str,
    canary_seconds: int,
    github_output: Path | None = None,
) -> None:
    if not os.environ.get("KUBECONFIG"):
        raise ValueError("KUBECONFIG is required")
    if environment not in {"staging", "production"}:
        raise ValueError("environment must be staging or production")
    if canary_seconds not in {0, 900} or (canary_seconds == 900 and phase != "mission-on"):
        raise ValueError("only the mission-ON phase may run the exact 900-second canary")
    _, _, _, candidate, _ = resolve_release_bundle(root, release_id)
    identity = candidate["environments"][environment]
    context = identity["kubernetes_context"]
    namespace = identity["namespace"]
    deployment = identity["web_deployment"]
    container = identity["web_container"]
    expected_image = f"ghcr.io/devpathai/devpath-web@{_expected_digest(candidate, phase)}"

    started = time.monotonic()
    deadline = started + candidate["rollout"]["sync_timeout_seconds"]
    while True:
        if _current_deployment_image(context, namespace, deployment, container) == expected_image:
            break
        if time.monotonic() >= deadline:
            raise ValueError("exact digest was not observed within 300 seconds")
        time.sleep(10)
    remaining = max(1, int(deadline - time.monotonic()))
    _kubectl(
        ["rollout", "status", f"deployment/{deployment}", f"--timeout={remaining}s"],
        context,
        namespace,
    )
    detection_seconds = int(time.monotonic() - started)
    if detection_seconds > 300:
        raise ValueError("rollout detection exceeded 300 seconds")

    probe_url = f"{identity['web_origin'].rstrip('/')}/"
    canary_started = time.monotonic()
    while True:
        if _current_deployment_image(context, namespace, deployment, container) != expected_image:
            raise ValueError("exact digest changed during the canary")
        request = Request(probe_url, headers={"User-Agent": "devpath-release-canary/1"})
        try:
            with urlopen(request, timeout=10) as response:
                if response.status < 200 or response.status >= 400:
                    raise ValueError("canary returned a failing status")
        except OSError as exc:
            raise ValueError("canary request failed") from exc
        if time.monotonic() - canary_started >= canary_seconds:
            break
        time.sleep(min(30, max(1, canary_seconds - int(time.monotonic() - canary_started))))
    print(
        f"verified {environment} {phase} digest; sync_detection_seconds={detection_seconds}; "
        f"canary_seconds={canary_seconds}"
    )
    if github_output is not None:
        with github_output.open("a", encoding="utf-8", newline="\n") as output:
            output.write(f"sync_detection_seconds={detection_seconds}\n")
            output.write(f"canary_seconds={canary_seconds}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--environment", choices=["staging", "production"], required=True)
    parser.add_argument("--phase", choices=["mission-off", "mission-on", "prior"], required=True)
    parser.add_argument("--canary-seconds", type=int, choices=[0, 900], required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        wait_rollout(
            args.root.resolve(),
            args.release_id,
            args.environment,
            args.phase,
            args.canary_seconds,
            args.github_output,
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"rollout verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
