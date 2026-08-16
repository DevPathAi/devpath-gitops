#!/usr/bin/env python3
"""Fail-closed Cloudflare Pages checks for Landing-last and reverse rollback."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from validate_release_manifest import resolve_release_bundle


API_ROOT = "https://api.cloudflare.com/client/v4"


def _api(token: str, method: str, path: str) -> dict:
    request = Request(
        f"{API_ROOT}{path}",
        method=method,
        data=b"{}" if method == "POST" else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "devpath-release-control/1",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        raise ValueError("Cloudflare API request failed") from exc
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("Cloudflare API rejected the release operation")
    return payload


def _paths(candidate: dict) -> tuple[str, str, str, str, str]:
    home = candidate["home"]
    account = quote(home["cloudflare_account_id"], safe="")
    project = quote(home["cloudflare_project"], safe="")
    base = f"/accounts/{account}/pages/projects/{project}/deployments"
    return (
        base,
        home["candidate_deployment_id"],
        home["prior_production_deployment_id"],
        home["source_sha"],
        candidate["environments"]["production"]["landing_origin"],
    )


def _deployment(token: str, base: str, deployment_id: str) -> dict:
    payload = _api(token, "GET", f"{base}/{quote(deployment_id, safe='')}")
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("id") != deployment_id:
        raise ValueError("Cloudflare deployment identity mismatch")
    return result


def _successful(deployment: dict, environment: str, source_sha: str | None = None) -> None:
    if deployment.get("environment") != environment:
        raise ValueError(f"Cloudflare deployment must be {environment}")
    stage = deployment.get("latest_stage") or {}
    if stage.get("status") != "success":
        raise ValueError("Cloudflare deployment is not successful")
    if source_sha is not None:
        metadata = ((deployment.get("deployment_trigger") or {}).get("metadata") or {})
        if metadata.get("commit_hash") != source_sha:
            raise ValueError("Cloudflare candidate source SHA mismatch")


def _current_production(token: str, base: str) -> dict:
    query = urlencode({"env": "production", "page": 1, "per_page": 1})
    payload = _api(token, "GET", f"{base}?{query}")
    results = payload.get("result")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise ValueError("Cloudflare current production deployment is unavailable")
    return results[0]


def _probe(origin: str) -> None:
    request = Request(f"{origin.rstrip('/')}/", headers={"User-Agent": "devpath-landing-canary/1"})
    try:
        with urlopen(request, timeout=10) as response:
            if response.status < 200 or response.status >= 400:
                raise ValueError("Landing probe returned a failing status")
    except OSError as exc:
        raise ValueError("Landing probe failed") from exc


def execute(root: Path, release_id: str, action: str) -> None:
    _, _, _, candidate, _ = resolve_release_bundle(root, release_id)
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not token:
        raise ValueError("CLOUDFLARE_API_TOKEN is required")
    base, candidate_id, prior_id, source_sha, landing_origin = _paths(candidate)

    if action == "preflight":
        candidate_deployment = _deployment(token, base, candidate_id)
        _successful(candidate_deployment, "preview", source_sha)
        prior = _deployment(token, base, prior_id)
        _successful(prior, "production")
        if _current_production(token, base).get("id") != prior_id:
            raise ValueError("recorded prior deployment is not current production")
        print("verified Landing candidate and prior production CAS")
        return

    if action == "verify-new-production":
        current = _current_production(token, base)
        if current.get("id") == prior_id:
            raise ValueError("Landing production did not advance from prior")
        _successful(current, "production", source_sha)
        _probe(landing_origin)
        print("verified new Landing production source and smoke")
        return

    if action == "rollback-prior":
        prior = _deployment(token, base, prior_id)
        _successful(prior, "production")
        current = _current_production(token, base)
        if current.get("id") != prior_id:
            # Never roll back an unrelated Landing deployment. The only permitted
            # current successor is this candidate's successful production source.
            _successful(current, "production", source_sha)
            _api(token, "POST", f"{base}/{quote(prior_id, safe='')}/rollback")
            deadline = time.monotonic() + 120
            while _current_production(token, base).get("id") != prior_id:
                if time.monotonic() >= deadline:
                    raise ValueError("Landing prior rollback was not observed within 120 seconds")
                time.sleep(10)
        _probe(landing_origin)
        print("verified Landing prior rollback before web rollback")
        return
    raise ValueError("unknown Cloudflare release action")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-id", required=True)
    parser.add_argument(
        "--action",
        choices=["preflight", "verify-new-production", "rollback-prior"],
        required=True,
    )
    args = parser.parse_args(argv)
    try:
        execute(args.root.resolve(), args.release_id, args.action)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cloudflare release gate failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
