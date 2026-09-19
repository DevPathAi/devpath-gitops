"""Frontend ET13 contract values derived from byte-pinned producer files.

GitOps trusts only the bytes it has approved. The files under
release-manifests/contracts/frontend-et13/ are byte copies of the frontend producer's
catalog at one source commit; every value the release validators need is derived from
them after their SHA-256 has been checked against the literals below. Rebinding to a new
frontend catalog means copying the files and updating these hashes — nothing else here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


CONTRACT_DIRECTORY = (
    Path(__file__).resolve().parents[2]
    / "release-manifests"
    / "contracts"
    / "frontend-et13"
)
PINNED_SHA256 = {
    "catalog.v1.json": "c5acc346a770f5890c6dd06ce616ffc1105eba12b7605e8ad985897e91b00c96",
    "visual-cases.v1.json": "acd368d92e9850cb51d67dc2d3cc9a6ae7c96e48f58da28f0e353ca0edc741ce",
    "a11y-cases.v1.json": "cf664d46f5e0b0ea9ab789dbf4afca4dbc71778eff0db05779c3a48392622929",
    "catalog.schema.json": "8e0bee6a1f99f2293c7fed5a4b19d89f855cc51da47793c9f88336daf7013726",
    "evidence.schema.json": "acf5b914d1a45838772bac5f9e912534343931e36be51f742cdf6bc3172032c1",
    "generated-cases.schema.json": "86d26364027e1e3a2bf178754ede042096f738c203220b83e6fc0d9c4ff1e8a7",
    "manifest.schema.json": "e201fd195299ed7eddea7ceeaa2ee15eadfab634a49a284b4c23d13defdff5eb",
    "release-bundle.v1.json": "b46cbc3903924267dfa9d44ea66d138ecceee22195c96c0f4fc29b0db6cfd411",
}
GENERATED_FILES = {
    "frontend-visual": "visual-cases.v1.json",
    "frontend-automated-a11y": "a11y-cases.v1.json",
}
_SURFACE_BY_OWNER = {"web": "web", "admin": "admin", "dp_design": "dp_design"}


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_pinned(directory: Path, name: str, expected_sha256: str) -> tuple[bytes, Any]:
    raw = (directory / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError(f"pinned frontend ET13 contract file {name} SHA-256 mismatch")
    return raw, json.loads(raw.decode("utf-8"))


def load_contract(directory: Path, pinned_sha256: dict[str, str]) -> dict[str, Any]:
    """Verify every pinned file and derive the contract; any inconsistency fails closed."""
    documents = {
        name: _read_pinned(directory, name, expected)
        for name, expected in pinned_sha256.items()
    }
    source_pin = json.loads((directory / "source-pin.v1.json").read_text(encoding="utf-8"))
    recorded = {name: entry["sha256"] for name, entry in source_pin["files"].items()}
    if recorded != pinned_sha256:
        raise ValueError("source-pin.v1.json does not record the pinned file hashes")

    catalog_raw, catalog = documents["catalog.v1.json"]
    catalog_sha256 = hashlib.sha256(catalog_raw).hexdigest()
    matrix = catalog["projection_matrix"]
    projection_sha256 = catalog["projection_contract_sha256"]
    if _canonical_sha256(matrix) != projection_sha256:
        raise ValueError("pinned projection matrix does not hash to its contract SHA-256")
    fixture_ids = tuple(row["fixture_id"] for row in matrix)
    if len(set(fixture_ids)) != len(fixture_ids):
        raise ValueError("pinned projection matrix repeats a fixture")

    cases: dict[str, list[dict[str, Any]]] = {}
    case_counts: dict[str, int] = {}
    surface_case_counts: dict[str, dict[str, int]] = {}
    generated_sha256: dict[str, str] = {}
    for label, name in GENERATED_FILES.items():
        raw, generated = documents[name]
        if (
            generated["catalog_sha256"] != catalog_sha256
            or generated["projection_contract_sha256"] != projection_sha256
            or generated["projection_matrix"] != matrix
            or tuple(generated["fixture_ids"]) != fixture_ids
        ):
            raise ValueError(f"pinned {name} disagrees with the pinned catalog")
        lane_cases = generated["cases"]
        counted: dict[str, int] = {}
        for case in lane_cases:
            surface = _SURFACE_BY_OWNER[case["owner"]]
            counted[surface] = counted.get(surface, 0) + 1
        if (
            generated["case_count"] != len(lane_cases)
            or generated["surface_case_counts"] != counted
        ):
            raise ValueError(f"pinned {name} case counts are not self-consistent")
        cases[label] = lane_cases
        case_counts[label] = len(lane_cases)
        surface_case_counts[label] = dict(generated["surface_case_counts"])
        generated_sha256[label] = hashlib.sha256(raw).hexdigest()

    surfaces = frozenset().union(*(set(counts) for counts in surface_case_counts.values()))
    return {
        "source_repository": source_pin["repository"],
        "source_sha": source_pin["source_sha"],
        "fixture_ids": fixture_ids,
        "projection_matrix": matrix,
        "projection_contract_sha256": projection_sha256,
        "catalog_sha256": catalog_sha256,
        "generated_sha256": generated_sha256,
        "cases": cases,
        "case_counts": case_counts,
        "surface_case_counts": surface_case_counts,
        "surfaces": surfaces,
    }


_CONTRACT = load_contract(CONTRACT_DIRECTORY, PINNED_SHA256)
SOURCE_REPOSITORY: str = _CONTRACT["source_repository"]
SOURCE_SHA: str = _CONTRACT["source_sha"]
FIXTURE_IDS: tuple[str, ...] = _CONTRACT["fixture_ids"]
PROJECTION_MATRIX: list[dict[str, Any]] = _CONTRACT["projection_matrix"]
PROJECTION_CONTRACT_SHA256: str = _CONTRACT["projection_contract_sha256"]
CATALOG_SHA256: str = _CONTRACT["catalog_sha256"]
GENERATED_SHA256: dict[str, str] = _CONTRACT["generated_sha256"]
CASES: dict[str, list[dict[str, Any]]] = _CONTRACT["cases"]
CASE_COUNTS: dict[str, int] = _CONTRACT["case_counts"]
SURFACE_CASE_COUNTS: dict[str, dict[str, int]] = _CONTRACT["surface_case_counts"]
SURFACES: frozenset[str] = _CONTRACT["surfaces"]
GOLDEN_SHA256: dict[str, str] = {
    name: PINNED_SHA256[name]
    for name in (
        "catalog.schema.json",
        "evidence.schema.json",
        "generated-cases.schema.json",
        "manifest.schema.json",
        "release-bundle.v1.json",
    )
}
