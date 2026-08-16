#!/usr/bin/env python3
"""Fail-closed Cloudflare Pages checks for Landing-last and reverse rollback."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from validate_release_manifest import CF_ID, resolve_release_bundle


API_ROOT = "https://api.cloudflare.com/client/v4"
MARKER_KEYS = {"release_id", "candidate_spec_sha256", "dist_sha256"}


class _NoRedirectHandler(HTTPRedirectHandler):
    """Keep API credentials and public identity probes on their exact origins."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


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
        with _NO_REDIRECT_OPENER.open(request, timeout=20) as response:
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
    suffix = "/deployments"
    if not base.endswith(suffix):
        raise ValueError("Cloudflare deployment collection path is invalid")
    payload = _api(token, "GET", base[: -len(suffix)])
    project = payload.get("result")
    current = project.get("canonical_deployment") if isinstance(project, dict) else None
    if (
        not isinstance(current, dict)
        or not isinstance(current.get("id"), str)
        or CF_ID.fullmatch(current["id"]) is None
    ):
        raise ValueError("Cloudflare current production deployment is unavailable")
    return current


def _production_deployments(token: str, base: str) -> list[dict]:
    """Return a bounded production deployment census for exact creation CAS."""
    query = urlencode({"env": "production", "page": 1, "per_page": 100})
    payload = _api(token, "GET", f"{base}?{query}")
    results = payload.get("result")
    if not isinstance(results, list) or not results:
        raise ValueError("Cloudflare production deployment census is unavailable")
    if any(not isinstance(item, dict) for item in results):
        raise ValueError("Cloudflare production deployment census is invalid")
    return results


def _wait_production_quiescent(token: str, base: str, deadline: float) -> None:
    """Wait until no server-side production deployment can outlive rollback."""
    terminal = {"success", "failure", "canceled"}
    allowed = terminal | {"idle", "active"}
    stable_polls = 0
    while stable_polls < 2:
        active: list[str] = []
        for deployment in _production_deployments(token, base):
            deployment_id = deployment.get("id")
            if not isinstance(deployment_id, str) or CF_ID.fullmatch(deployment_id) is None:
                raise ValueError("Cloudflare production deployment ID is invalid")
            if deployment.get("environment") != "production":
                raise ValueError("Cloudflare production census returned a non-production deployment")
            stage = deployment.get("latest_stage")
            status = stage.get("status") if isinstance(stage, dict) else None
            if status not in allowed:
                raise ValueError("Cloudflare production deployment status is invalid")
            if status not in terminal:
                active.append(deployment_id)
        stable_polls = stable_polls + 1 if not active else 0
        if stable_polls >= 2:
            return
        if time.monotonic() >= deadline:
            raise ValueError("Cloudflare production deployments did not quiesce before rollback")
        time.sleep(10)


def _created_production(
    token: str,
    base: str,
    prior_id: str,
    source_sha: str,
    not_before_epoch: int,
) -> dict:
    """Identify exactly one successful deployment created by this deploy window."""
    matches: list[dict] = []
    for deployment in _production_deployments(token, base):
        deployment_id = deployment.get("id")
        if (
            not isinstance(deployment_id, str)
            or CF_ID.fullmatch(deployment_id) is None
            or deployment_id == prior_id
        ):
            continue
        if _created_epoch(deployment) < not_before_epoch:
            continue
        metadata = ((deployment.get("deployment_trigger") or {}).get("metadata") or {})
        if metadata.get("commit_hash") != source_sha:
            continue
        _successful(deployment, "production", source_sha)
        matches.append(deployment)
    if len(matches) != 1:
        raise ValueError("exactly one newly created Cloudflare production deployment is required")
    created = matches[0]
    if _current_production(token, base).get("id") != created.get("id"):
        raise ValueError("newly created Cloudflare deployment is not exact current production")
    return created


def _probe(origin: str) -> None:
    request = Request(f"{origin.rstrip('/')}/", headers={"User-Agent": "devpath-landing-canary/1"})
    try:
        with _NO_REDIRECT_OPENER.open(request, timeout=10) as response:
            if response.status < 200 or response.status >= 400:
                raise ValueError("Landing probe returned a failing status")
    except OSError as exc:
        raise ValueError("Landing probe failed") from exc


def validate_public_marker(
    payload: object,
    release_id: str,
    candidate_hash: str,
    dist_sha256: str,
) -> None:
    expected = {
        "release_id": release_id,
        "candidate_spec_sha256": candidate_hash,
        "dist_sha256": dist_sha256,
    }
    if not isinstance(payload, dict) or set(payload) != MARKER_KEYS or payload != expected:
        raise ValueError("public dist marker does not bind the exact release artifact")


def _marker_relative_path(dist_sha256: str) -> Path:
    return Path(".well-known") / "devpath-release" / f"{dist_sha256}.json"


def _write_marker(
    dist_root: Path,
    release_id: str,
    candidate_hash: str,
    dist_sha256: str,
) -> Path:
    dist_root = dist_root.resolve()
    if not (dist_root / "index.html").is_file():
        raise ValueError("Landing dist root is missing index.html")
    marker = (dist_root / _marker_relative_path(dist_sha256)).resolve()
    if dist_root not in marker.parents or marker.exists():
        raise ValueError("public dist marker target is unsafe or already exists")
    marker.parent.mkdir(parents=True, exist_ok=False)
    payload = {
        "candidate_spec_sha256": candidate_hash,
        "dist_sha256": dist_sha256,
        "release_id": release_id,
    }
    with marker.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        output.write("\n")
    return marker


def _probe_marker(
    origin: str,
    release_id: str,
    candidate_hash: str,
    dist_sha256: str,
) -> None:
    relative = _marker_relative_path(dist_sha256).as_posix()
    request = Request(
        f"{origin.rstrip('/')}/{relative}",
        headers={"Accept": "application/json", "User-Agent": "devpath-landing-canary/2"},
    )
    try:
        with _NO_REDIRECT_OPENER.open(request, timeout=10) as response:
            if response.status != 200:
                raise ValueError("public dist marker returned a non-200 status")
            raw = response.read(4097)
    except OSError as exc:
        raise ValueError("public dist marker probe failed") from exc
    if len(raw) > 4096:
        raise ValueError("public dist marker is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("public dist marker is not UTF-8 JSON") from exc
    validate_public_marker(payload, release_id, candidate_hash, dist_sha256)


def _created_epoch(deployment: dict) -> float:
    value = deployment.get("created_on")
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("Cloudflare deployment created_on is unavailable")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise ValueError("Cloudflare deployment created_on is invalid") from exc


def execute(
    root: Path,
    release_id: str,
    action: str,
    deployment_id: str | None = None,
    not_before_epoch: int | None = None,
    github_output: Path | None = None,
    dist_root: Path | None = None,
) -> None:
    _, _, _, candidate, candidate_hash = resolve_release_bundle(root, release_id)
    base, candidate_id, prior_id, source_sha, landing_origin = _paths(candidate)
    dist_sha256 = candidate["home"]["dist_sha256"]

    if action == "write-marker":
        if dist_root is None:
            raise ValueError("--dist-root is required for write-marker")
        marker = _write_marker(
            dist_root,
            release_id,
            candidate_hash,
            dist_sha256,
        )
        print(f"wrote immutable public dist marker: {marker}")
        return

    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not token:
        raise ValueError("CLOUDFLARE_API_TOKEN is required")

    if action == "preflight":
        candidate_deployment = _deployment(token, base, candidate_id)
        _successful(candidate_deployment, "preview", source_sha)
        prior = _deployment(token, base, prior_id)
        _successful(prior, "production")
        if _current_production(token, base).get("id") != prior_id:
            raise ValueError("recorded prior deployment is not current production")
        print("verified Landing candidate and prior production CAS")
        return

    if action == "capture-new-production":
        if (
            isinstance(not_before_epoch, bool)
            or not isinstance(not_before_epoch, int)
            or not_before_epoch <= 0
        ):
            raise ValueError("--not-before-epoch must be a positive integer")
        created = _created_production(token, base, prior_id, source_sha, not_before_epoch)
        created_id = created["id"]
        _probe_marker(landing_origin, release_id, candidate_hash, dist_sha256)
        if github_output is None:
            raise ValueError("--github-output is required to capture deployment ID")
        with github_output.open("a", encoding="utf-8", newline="\n") as output:
            output.write(f"deployment_id={created_id}\n")
        print("captured exact newly created Landing deployment ID")
        return

    if action == "verify-new-production":
        if deployment_id is None:
            raise ValueError("--deployment-id is required for exact production verification")
        current = _current_production(token, base)
        if current.get("id") != deployment_id:
            raise ValueError("current Cloudflare production deployment failed exact CAS")
        deployed = _deployment(token, base, deployment_id)
        _successful(deployed, "production", source_sha)
        _probe_marker(landing_origin, release_id, candidate_hash, dist_sha256)
        _probe(landing_origin)
        print("verified exact new Landing deployment, public dist marker, and smoke")
        return

    if action == "rollback-prior":
        prior = _deployment(token, base, prior_id)
        _successful(prior, "production")
        deadline = time.monotonic() + 300
        while True:
            _wait_production_quiescent(token, base, deadline)
            current = _current_production(token, base)
            if current.get("id") == prior_id:
                break
            # Never roll back an unrelated Landing deployment. The only permitted
            # current successor is this candidate's successful production source.
            _successful(current, "production", source_sha)
            _probe_marker(landing_origin, release_id, candidate_hash, dist_sha256)
            if _current_production(token, base).get("id") != current.get("id"):
                raise ValueError("Landing production drifted before rollback CAS")
            _api(token, "POST", f"{base}/{quote(prior_id, safe='')}/rollback")
            while _current_production(token, base).get("id") != prior_id:
                if time.monotonic() >= deadline:
                    raise ValueError("Landing prior rollback was not observed within 300 seconds")
                time.sleep(10)
        _probe(landing_origin)
        print("verified Landing prior rollback before web rollback")
        return
    if action == "verify-prior":
        prior = _deployment(token, base, prior_id)
        _successful(prior, "production")
        _wait_production_quiescent(token, base, time.monotonic() + 120)
        if _current_production(token, base).get("id") != prior_id:
            raise ValueError("Landing prior deployment drifted during web rollback")
        _probe(landing_origin)
        print("reverified Landing prior CAS after web rollback")
        return
    raise ValueError("unknown Cloudflare release action")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-id", required=True)
    parser.add_argument(
        "--action",
        choices=[
            "preflight",
            "write-marker",
            "capture-new-production",
            "verify-new-production",
            "rollback-prior",
            "verify-prior",
        ],
        required=True,
    )
    parser.add_argument("--deployment-id")
    parser.add_argument("--not-before-epoch", type=int)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--dist-root", type=Path)
    args = parser.parse_args(argv)
    try:
        execute(
            args.root.resolve(),
            args.release_id,
            args.action,
            args.deployment_id,
            args.not_before_epoch,
            args.github_output,
            args.dist_root,
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cloudflare release gate failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
