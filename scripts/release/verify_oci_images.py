#!/usr/bin/env python3
"""Authenticate candidate OCI roots, linux/amd64 manifests, configs, and runtime IDs."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request


DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
SHA40 = re.compile(r"[0-9a-f]{40}")
ACTOR = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
IMAGE_REPOSITORY = re.compile(r"ghcr\.io/devpathai/[a-z0-9][a-z0-9-]{0,62}")
GITHUB_REPOSITORY = re.compile(r"DevPathAi/[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
INDEX_MEDIA_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
MANIFEST_MEDIA_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
CONFIG_MEDIA_TYPES = {
    "application/vnd.oci.image.config.v1+json",
    "application/vnd.docker.container.image.v1+json",
}
LAYER_MEDIA_TYPES = {
    "application/vnd.oci.image.layer.v1.tar",
    "application/vnd.oci.image.layer.v1.tar+gzip",
    "application/vnd.oci.image.layer.v1.tar+zstd",
    "application/vnd.docker.image.rootfs.diff.tar.gzip",
}
MAX_TOKEN_BYTES = 16 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_CONFIG_BYTES = 16 * 1024 * 1024
MAX_DESCRIPTORS = 128


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _reject_duplicate_pairs(pairs):  # noqa: ANN001
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("OCI JSON contains a duplicate key")
        result[key] = value
    return result


def _parse_json(raw: bytes, label: str) -> Any:
    if not raw:
        raise ValueError(f"{label} is empty")
    try:
        text = raw.decode("utf-8")
        return json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not JSON") from exc


def _descriptor_digest(document: Any, label: str) -> tuple[str, int, str]:
    if not isinstance(document, dict):
        raise ValueError(f"{label} descriptor is invalid")
    digest = document.get("digest")
    size = document.get("size")
    media_type = document.get("mediaType")
    if DIGEST.fullmatch(digest or "") is None:
        raise ValueError(f"{label} descriptor digest is invalid")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError(f"{label} descriptor size is invalid")
    if not isinstance(media_type, str):
        raise ValueError(f"{label} descriptor media type is invalid")
    return digest, size, media_type


def _validate_manifest(
    raw: bytes,
    content_type: str,
    digest_header: str,
    expected_digest: str,
) -> tuple[dict[str, Any], str, int]:
    if len(raw) < 2 or len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("linux/amd64 manifest size is invalid")
    if _sha256(raw) != expected_digest or digest_header != expected_digest:
        raise ValueError("linux/amd64 manifest digest is not exact")
    document = _parse_json(raw, "linux/amd64 image manifest")
    if (
        not isinstance(document, dict)
        or document.get("schemaVersion") != 2
        or document.get("mediaType") not in MANIFEST_MEDIA_TYPES
        or content_type != document.get("mediaType")
    ):
        raise ValueError("linux/amd64 manifest media type is invalid")
    config_digest, config_size, config_media = _descriptor_digest(
        document.get("config"), "config"
    )
    if config_media not in CONFIG_MEDIA_TYPES or set(document["config"]) != {
        "mediaType",
        "digest",
        "size",
    }:
        raise ValueError("config descriptor media type is invalid")
    layers = document.get("layers")
    if not isinstance(layers, list) or not layers or len(layers) > MAX_DESCRIPTORS:
        raise ValueError("image layer descriptor list is invalid")
    layer_digests: set[str] = set()
    for layer in layers:
        layer_digest, _, layer_media = _descriptor_digest(layer, "layer")
        if (
            layer_digest in layer_digests
            or layer_media not in LAYER_MEDIA_TYPES
            or not set(layer).issubset({"mediaType", "digest", "size", "annotations"})
            or "urls" in layer
        ):
            raise ValueError("image layer descriptors are duplicated or invalid")
        layer_digests.add(layer_digest)
    return document, config_digest, config_size


def validate_oci_image(
    *,
    repository: str,
    source_sha: str,
    image_repository: str,
    expected_root_digest: str,
    root_bytes: bytes,
    root_content_type: str,
    root_digest_header: str,
    manifest_bytes: bytes,
    manifest_content_type: str,
    manifest_digest_header: str,
    config_bytes: bytes,
) -> dict[str, Any]:
    """Validate independently fetched root/manifest/config bytes."""
    if GITHUB_REPOSITORY.fullmatch(repository) is None:
        raise ValueError("source repository is invalid")
    if SHA40.fullmatch(source_sha) is None:
        raise ValueError("source SHA is invalid")
    if IMAGE_REPOSITORY.fullmatch(image_repository) is None:
        raise ValueError("image repository is invalid")
    if DIGEST.fullmatch(expected_root_digest) is None:
        raise ValueError("candidate root digest is invalid")
    if len(root_bytes) < 2 or len(root_bytes) > MAX_MANIFEST_BYTES:
        raise ValueError("OCI root manifest size is invalid")
    if _sha256(root_bytes) != expected_root_digest or root_digest_header != expected_root_digest:
        raise ValueError("OCI root digest does not equal the sealed candidate")
    root = _parse_json(root_bytes, "OCI root manifest")
    if not isinstance(root, dict) or root.get("schemaVersion") != 2:
        raise ValueError("OCI root manifest schema is invalid")
    media_type = root.get("mediaType")
    if root_content_type != media_type:
        raise ValueError("OCI root Content-Type does not match its mediaType")
    if media_type in INDEX_MEDIA_TYPES:
        descriptors = root.get("manifests")
        if (
            not isinstance(descriptors, list)
            or not descriptors
            or len(descriptors) > MAX_DESCRIPTORS
        ):
            raise ValueError("OCI index descriptors are invalid")
        linux_amd64: list[dict[str, Any]] = []
        seen: set[str] = set()
        for descriptor in descriptors:
            child_digest, _, child_media = _descriptor_digest(descriptor, "index child")
            if child_digest in seen or child_media not in MANIFEST_MEDIA_TYPES:
                raise ValueError("OCI index child descriptors are duplicated or invalid")
            seen.add(child_digest)
            platform = descriptor.get("platform")
            if platform == {"architecture": "amd64", "os": "linux"}:
                linux_amd64.append(descriptor)
        if len(linux_amd64) != 1:
            raise ValueError("OCI index must contain exactly one linux/amd64 child")
        descriptor = linux_amd64[0]
        manifest_digest, manifest_size, descriptor_media = _descriptor_digest(
            descriptor, "linux/amd64 manifest"
        )
        if manifest_size != len(manifest_bytes):
            raise ValueError("linux/amd64 manifest descriptor size drifted")
        if manifest_content_type != descriptor_media:
            raise ValueError("linux/amd64 manifest descriptor media type drifted")
    elif media_type in MANIFEST_MEDIA_TYPES:
        manifest_digest = expected_root_digest
        if manifest_bytes != root_bytes:
            raise ValueError("single-manifest root bytes were not reused exactly")
        if manifest_content_type != root_content_type:
            raise ValueError("single-manifest root media type drifted")
    else:
        raise ValueError("OCI root must be an index or image manifest")
    manifest_document, config_digest, config_size = _validate_manifest(
        manifest_bytes,
        manifest_content_type,
        manifest_digest_header,
        manifest_digest,
    )
    if len(config_bytes) != config_size or len(config_bytes) > MAX_CONFIG_BYTES:
        raise ValueError("OCI config descriptor size drifted")
    if _sha256(config_bytes) != config_digest:
        raise ValueError("OCI config digest does not match its descriptor")
    config = _parse_json(config_bytes, "OCI image config")
    if (
        not isinstance(config, dict)
        or config.get("architecture") != "amd64"
        or config.get("os") != "linux"
    ):
        raise ValueError("OCI config platform is not linux/amd64")
    config_section = config.get("config")
    labels = config_section.get("Labels") if isinstance(config_section, dict) else None
    expected_source = f"https://github.com/{repository}"
    if (
        not isinstance(labels, dict)
        or labels.get("org.opencontainers.image.revision") != source_sha
        or labels.get("org.opencontainers.image.source") != expected_source
    ):
        raise ValueError("OCI source labels do not equal the sealed source")
    rootfs = config.get("rootfs")
    diff_ids = rootfs.get("diff_ids") if isinstance(rootfs, dict) else None
    if (
        rootfs is None
        or rootfs.get("type") != "layers"
        or not isinstance(diff_ids, list)
        or not diff_ids
        or len(diff_ids) > MAX_DESCRIPTORS
        or len(set(diff_ids)) != len(diff_ids)
        or any(DIGEST.fullmatch(value or "") is None for value in diff_ids)
        or len(diff_ids) != len(manifest_document["layers"])
    ):
        raise ValueError("OCI rootfs diff IDs are invalid")
    if config_digest in {expected_root_digest, manifest_digest}:
        raise ValueError("OCI root, manifest, and config identity are not independent")
    return {
        "repository": repository,
        "source_sha": source_sha,
        "image_repository": image_repository,
        "root_digest": expected_root_digest,
        "manifest_digest": manifest_digest,
        "config_digest": config_digest,
        "platform": {"os": "linux", "architecture": "amd64"},
        "rootfs_diff_ids": list(diff_ids),
        "oci_labels": {
            "org.opencontainers.image.source": expected_source,
            "org.opencontainers.image.revision": source_sha,
        },
    }


def normalize_runtime_image_id(image_id: str, trust: Mapping[str, Any]) -> dict[str, str]:
    """Accept only the authenticated linux/amd64 manifest or config identity."""
    if not isinstance(image_id, str) or not image_id or "\r" in image_id or "\n" in image_id:
        raise ValueError("runtime imageID is missing or unsafe")
    repository = trust.get("image_repository")
    manifest = trust.get("manifest_digest")
    config = trust.get("config_digest")
    value = image_id
    for prefix in ("docker-pullable://", "containerd://", "docker://"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    if "@" in value:
        parts = value.split("@")
        if len(parts) != 2 or parts[0] != repository:
            raise ValueError("runtime imageID repository is not exact")
        value = parts[1]
    elif ":" in value and not value.startswith("sha256:"):
        raise ValueError("runtime imageID may not be tag-form")
    if value == manifest:
        return {"digest": value, "form": "linux-amd64-manifest"}
    if value == config:
        return {"digest": value, "form": "config"}
    raise ValueError("runtime imageID is not the authenticated manifest/config")


def runtime_image_matches(
    digest: Any, form: Any, trust: Mapping[str, Any]
) -> bool:
    """Match runtime evidence against the canonical OCI identity form contract."""
    key = {
        "linux-amd64-manifest": "manifest_digest",
        "config": "config_digest",
    }.get(form)
    if key is None or not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
        return False
    expected = trust.get(key)
    return (
        isinstance(expected, str)
        and DIGEST.fullmatch(expected) is not None
        and digest == expected
    )


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class RegistryClient:
    """Small bounded GHCR reader; Basic auth is used only for token exchange."""

    def __init__(self, actor: str, token: str) -> None:
        if ACTOR.fullmatch(actor or "") is None or not token or any(
            char in token for char in "\r\n"
        ):
            raise ValueError("GHCR read credentials are missing or unsafe")
        self.actor = actor
        self.token = token
        self.opener = urllib.request.build_opener(_RejectRedirect())

    def _request(
        self, url: str, headers: Mapping[str, str], max_bytes: int
    ) -> tuple[bytes, dict[str, str]]:
        if not url.startswith("https://"):
            raise ValueError("registry URL is not HTTPS")
        request_headers = dict(headers)
        request_headers["Accept-Encoding"] = "identity"
        request = urllib.request.Request(url, headers=request_headers, method="GET")
        try:
            response = self.opener.open(request, timeout=30)
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as exc:
            raise ValueError("registry request failed") from exc
        with response:
            if response.status != 200 or response.geturl() != url:
                raise ValueError("registry response status or URL drifted")
            encoding = response.headers.get("Content-Encoding")
            if encoding not in {None, "identity"}:
                raise ValueError("registry response Content-Encoding is invalid")
            length = response.headers.get("Content-Length")
            if length is not None and (
                not length.isdecimal() or int(length) < 1 or int(length) > max_bytes
            ):
                raise ValueError("registry response Content-Length is invalid")
            body = response.read(max_bytes + 1)
            if not body or len(body) > max_bytes:
                raise ValueError("registry response size is invalid")
            if length is not None and len(body) != int(length):
                raise ValueError("registry response length drifted")
            return body, {key.lower(): value.strip() for key, value in response.headers.items()}

    def _bearer(self, repository_path: str) -> str:
        query = urllib.parse.urlencode(
            {"service": "ghcr.io", "scope": f"repository:{repository_path}:pull"}
        )
        basic = base64.b64encode(f"{self.actor}:{self.token}".encode()).decode("ascii")
        raw, headers = self._request(
            "https://ghcr.io/token?" + query,
            {
                "Authorization": "Basic " + basic,
                "Accept": "application/json",
                "User-Agent": "devpath-gitops-oci-verifier/1",
            },
            MAX_TOKEN_BYTES,
        )
        if headers.get("content-type") != "application/json":
            raise ValueError("registry token Content-Type is invalid")
        document = _parse_json(raw, "registry token")
        bearer = document.get("token") if isinstance(document, dict) else None
        if not isinstance(bearer, str) or not bearer or len(bearer) > 8192 or any(
            char in bearer for char in "\r\n"
        ):
            raise ValueError("registry bearer token is invalid")
        return bearer

    @staticmethod
    def _storage_redirect(exc: urllib.error.HTTPError, original: str, digest: str) -> str:
        try:
            if exc.code != 307 or exc.geturl() != original:
                raise ValueError("registry blob redirect status or source drifted")
            if exc.headers is None or exc.headers.get("Content-Length") != "0" or exc.read(1):
                raise ValueError("registry blob redirect body is not empty")
            location = exc.headers.get("Location")
            parsed = urllib.parse.urlsplit(location or "")
            match = re.fullmatch(r"/ghcrblobs[0-9]+/blobs/(sha256:[0-9a-f]{64})", parsed.path)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "pkg-containers.githubusercontent.com"
                or parsed.port not in {None, 443}
                or parsed.username is not None
                or parsed.password is not None
                or match is None
                or match.group(1) != digest
                or not parsed.query
                or parsed.fragment
            ):
                raise ValueError("registry blob redirect target is not exact")
            return location or ""
        finally:
            exc.close()

    def _blob(self, repository_path: str, digest: str, bearer: str) -> bytes:
        url = f"https://ghcr.io/v2/{repository_path}/blobs/{digest}"
        try:
            body, _ = self._request(
                url,
                {
                    "Authorization": "Bearer " + bearer,
                    "Accept": ", ".join(sorted(CONFIG_MEDIA_TYPES)),
                    "User-Agent": "devpath-gitops-oci-verifier/1",
                },
                MAX_CONFIG_BYTES,
            )
            return body
        except urllib.error.HTTPError as exc:
            storage = self._storage_redirect(exc, url, digest)
        body, _ = self._request(
            storage,
            {
                "Accept": "application/octet-stream",
                "User-Agent": "devpath-gitops-oci-verifier/1",
            },
            MAX_CONFIG_BYTES,
        )
        return body

    def _manifest(
        self, repository_path: str, digest: str, bearer: str
    ) -> tuple[bytes, str, str]:
        url = f"https://ghcr.io/v2/{repository_path}/manifests/{digest}"
        try:
            raw, headers = self._request(
                url,
                {
                    "Authorization": "Bearer " + bearer,
                    "Accept": ", ".join(sorted(INDEX_MEDIA_TYPES | MANIFEST_MEDIA_TYPES)),
                    "User-Agent": "devpath-gitops-oci-verifier/1",
                },
                MAX_MANIFEST_BYTES,
            )
        except urllib.error.HTTPError as exc:
            exc.close()
            raise ValueError(f"registry manifest request returned HTTP {exc.code}") from exc
        content_type = headers.get("content-type", "").split(";", 1)[0]
        return raw, content_type, headers.get("docker-content-digest", "")

    def inspect(
        self,
        *,
        repository: str,
        source_sha: str,
        image_repository: str,
        expected_root_digest: str,
    ) -> dict[str, Any]:
        if IMAGE_REPOSITORY.fullmatch(image_repository) is None:
            raise ValueError("image repository is invalid")
        repository_path = image_repository.removeprefix("ghcr.io/")
        bearer = self._bearer(repository_path)
        root, root_type, root_header = self._manifest(
            repository_path, expected_root_digest, bearer
        )
        root_document = _parse_json(root, "OCI root manifest")
        if isinstance(root_document, dict) and root_document.get("mediaType") in INDEX_MEDIA_TYPES:
            descriptors = root_document.get("manifests")
            linux = [
                item
                for item in descriptors or []
                if isinstance(item, dict)
                and item.get("platform") == {"architecture": "amd64", "os": "linux"}
            ]
            if len(linux) != 1 or DIGEST.fullmatch(linux[0].get("digest", "")) is None:
                raise ValueError("OCI index must contain exactly one linux/amd64 child")
            manifest, manifest_type, manifest_header = self._manifest(
                repository_path, linux[0]["digest"], bearer
            )
        else:
            manifest, manifest_type, manifest_header = root, root_type, root_header
        manifest_document = _parse_json(manifest, "linux/amd64 manifest")
        config_descriptor = (
            manifest_document.get("config") if isinstance(manifest_document, dict) else None
        )
        config_digest = (
            config_descriptor.get("digest") if isinstance(config_descriptor, dict) else ""
        )
        if DIGEST.fullmatch(config_digest or "") is None:
            raise ValueError("OCI config descriptor digest is invalid")
        config = self._blob(repository_path, config_digest, bearer)
        return validate_oci_image(
            repository=repository,
            source_sha=source_sha,
            image_repository=image_repository,
            expected_root_digest=expected_root_digest,
            root_bytes=root,
            root_content_type=root_type,
            root_digest_header=root_header,
            manifest_bytes=manifest,
            manifest_content_type=manifest_type,
            manifest_digest_header=manifest_header,
            config_bytes=config,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--image-repository", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args(argv)
    try:
        trust = RegistryClient(
            os.environ.get("MISSION_SPINE_GHCR_READ_ACTOR", ""),
            os.environ.get("MISSION_SPINE_GHCR_READ_TOKEN", ""),
        ).inspect(
            repository=args.repository,
            source_sha=args.source_sha,
            image_repository=args.image_repository,
            expected_root_digest=args.image_digest,
        )
        if args.github_output:
            with open(args.github_output, "a", encoding="utf-8", newline="\n") as output:
                for key in ("root_digest", "manifest_digest", "config_digest"):
                    output.write(f"{key}={trust[key]}\n")
        print(json.dumps(trust, separators=(",", ":"), ensure_ascii=True))
        return 0
    except (OSError, ValueError) as exc:
        print(f"OCI verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
