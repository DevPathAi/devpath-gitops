import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "release"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
CONTRACT = ROOT / "release-manifests" / "contracts" / "frontend-et13"
SCHEMA = ROOT / "release-manifests" / "schema-v1.json"
CANDIDATE_FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-candidate-spec.json"
RELEASE_FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-release.json"
LABELS = {
    "frontend-visual": ("visual", "visual-cases.v1.json", "frontendVisualCatalog"),
    "frontend-automated-a11y": ("a11y", "a11y-cases.v1.json", "frontendAutomatedA11yCatalog"),
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def canonical_sha256(value) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def find_const(node, key):
    """Yield every `{key: {"const": ...}}` value found anywhere under node."""
    if isinstance(node, dict):
        holder = node.get(key)
        if isinstance(holder, dict) and "const" in holder:
            yield holder["const"]
        for value in node.values():
            yield from find_const(value, key)
    elif isinstance(node, list):
        for value in node:
            yield from find_const(value, key)


class FrontendEt13PinnedContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = load_module(
            SCRIPTS / "frontend_et13_contract.py", "pinned_frontend_et13_contract"
        )
        cls.validator = load_module(
            SCRIPTS / "validate_release_manifest.py", "pinned_contract_validator"
        )
        cls.verifier = load_module(
            SCRIPTS / "verify_release_artifacts.py", "pinned_contract_verifier"
        )
        cls.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        cls.candidate = json.loads(CANDIDATE_FIXTURE.read_text(encoding="utf-8"))
        cls.release = json.loads(RELEASE_FIXTURE.read_text(encoding="utf-8"))
        cls.source_pin = json.loads(
            (CONTRACT / "source-pin.v1.json").read_text(encoding="utf-8")
        )

    def test_every_pinned_file_matches_the_recorded_source_pin(self):
        self.assertEqual(self.source_pin["schema_version"], "leva.et13.source-pin.v1")
        self.assertEqual(self.source_pin["repository"], "DevPathAi/devpath-frontend")
        self.assertRegex(self.source_pin["source_sha"], r"^[0-9a-f]{40}$")
        self.assertEqual(self.source_pin["source_sha"], self.contract.SOURCE_SHA)
        self.assertEqual(
            set(self.source_pin["files"]),
            {
                "catalog.v1.json",
                "visual-cases.v1.json",
                "a11y-cases.v1.json",
                "catalog.schema.json",
                "evidence.schema.json",
                "generated-cases.schema.json",
                "manifest.schema.json",
                "release-bundle.v1.json",
            },
        )
        for name, entry in self.source_pin["files"].items():
            with self.subTest(name=name):
                self.assertEqual(set(entry), {"source_path", "sha256"})
                self.assertTrue(entry["source_path"].startswith("evidence/et13/"))
                raw = (CONTRACT / name).read_bytes()
                self.assertNotIn(b"\r", raw)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["sha256"])

    def test_pinned_catalog_and_generated_cases_agree(self):
        contract = self.contract
        catalog = json.loads((CONTRACT / "catalog.v1.json").read_text(encoding="utf-8"))
        self.assertEqual(
            hashlib.sha256((CONTRACT / "catalog.v1.json").read_bytes()).hexdigest(),
            contract.CATALOG_SHA256,
        )
        self.assertEqual(catalog["projection_matrix"], contract.PROJECTION_MATRIX)
        self.assertEqual(
            canonical_sha256(contract.PROJECTION_MATRIX),
            contract.PROJECTION_CONTRACT_SHA256,
        )
        self.assertEqual(
            [row["fixture_id"] for row in contract.PROJECTION_MATRIX],
            list(contract.FIXTURE_IDS),
        )
        self.assertNotIn("mobile", contract.SURFACES)
        self.assertFalse([f for f in contract.FIXTURE_IDS if f.startswith("mobile-")])
        for label, (_, filename, _) in LABELS.items():
            with self.subTest(label=label):
                generated = json.loads((CONTRACT / filename).read_text(encoding="utf-8"))
                self.assertEqual(generated["catalog_sha256"], contract.CATALOG_SHA256)
                self.assertEqual(generated["fixture_ids"], list(contract.FIXTURE_IDS))
                self.assertEqual(generated["cases"], contract.CASES[label])
                self.assertEqual(len(generated["cases"]), contract.CASE_COUNTS[label])
                self.assertEqual(
                    sum(contract.SURFACE_CASE_COUNTS[label].values()),
                    contract.CASE_COUNTS[label],
                )
                self.assertEqual(
                    set(contract.SURFACE_CASE_COUNTS[label]), set(contract.SURFACES)
                )

    def test_validator_constants_are_derived_from_the_pinned_files(self):
        contract, validator = self.contract, self.validator
        self.assertEqual(tuple(validator.FRONTEND_FIXTURE_IDS), contract.FIXTURE_IDS)
        self.assertEqual(validator.FRONTEND_PROJECTION_MATRIX, contract.PROJECTION_MATRIX)
        self.assertEqual(
            validator.FRONTEND_PROJECTION_CONTRACT_SHA256,
            contract.PROJECTION_CONTRACT_SHA256,
        )
        for label in LABELS:
            with self.subTest(label=label):
                lane = validator.FRONTEND_CATALOG_CONTRACTS[label]
                self.assertEqual(lane["case_count"], contract.CASE_COUNTS[label])
                self.assertEqual(
                    lane["surface_case_counts"], contract.SURFACE_CASE_COUNTS[label]
                )
                self.assertEqual(
                    lane["projection_contract_sha256"],
                    contract.PROJECTION_CONTRACT_SHA256,
                )

    def test_verifier_reconstructs_the_pinned_case_identities_independently(self):
        for label in LABELS:
            with self.subTest(label=label):
                pinned = [
                    (case["fixture_id"], case["case_id"], case["artifact_path"])
                    for case in self.contract.CASES[label]
                ]
                self.assertEqual(
                    self.verifier._frontend_expected_case_identity(label), pinned
                )
                owners = {
                    self.verifier._frontend_surface(case["fixture_id"])
                    for case in self.contract.CASES[label]
                }
                self.assertEqual(owners, set(self.contract.SURFACES))

    def test_json_schema_consts_equal_the_pinned_values(self):
        defs = self.schema["$defs"]
        self.assertEqual(
            list(find_const(self.schema, "projection_matrix")),
            [self.contract.PROJECTION_MATRIX],
        )
        shas = list(find_const(self.schema, "projection_contract_sha256"))
        self.assertGreaterEqual(len(shas), 3)
        self.assertEqual(set(shas), {self.contract.PROJECTION_CONTRACT_SHA256})
        for label, (_, _, definition) in LABELS.items():
            with self.subTest(label=label):
                lane = defs[definition]
                self.assertEqual(
                    list(find_const(lane, "fixture_ids")), [list(self.contract.FIXTURE_IDS)]
                )
                self.assertEqual(
                    list(find_const(lane, "case_count")), [self.contract.CASE_COUNTS[label]]
                )
                self.assertEqual(
                    list(find_const(lane, "surface_case_counts")),
                    [self.contract.SURFACE_CASE_COUNTS[label]],
                )

    def test_synthetic_fixtures_bind_the_pinned_source_and_catalogs(self):
        contract = self.contract
        self.assertEqual(self.candidate["frontend"]["source_sha"], contract.SOURCE_SHA)
        inputs = self.candidate["quality_evidence_inputs"]
        projection = inputs["frontend_projection_contract"]
        self.assertEqual(projection["projection_matrix"], contract.PROJECTION_MATRIX)
        self.assertEqual(
            projection["projection_contract_sha256"], contract.PROJECTION_CONTRACT_SHA256
        )
        for label in LABELS:
            with self.subTest(label=label):
                catalog = inputs["catalogs"][label]
                self.assertEqual(catalog["source_sha"], contract.SOURCE_SHA)
                self.assertEqual(catalog["sha256"], contract.GENERATED_SHA256[label])
                self.assertEqual(catalog["fixture_ids"], list(contract.FIXTURE_IDS))
                self.assertEqual(catalog["case_count"], contract.CASE_COUNTS[label])
                self.assertEqual(
                    catalog["surface_case_counts"], contract.SURFACE_CASE_COUNTS[label]
                )
        for key, artifact in self.release["quality_evidence"].items():
            if key.startswith(("frontend_", "manual_")):
                self.assertEqual(artifact["head_sha"], contract.SOURCE_SHA, key)

    def test_a_tampered_pinned_file_fails_closed_on_import(self):
        source = (SCRIPTS / "frontend_et13_contract.py").read_text(encoding="utf-8")
        self.assertIn("hashlib.sha256", source)
        tampered = dict(self.contract.PINNED_SHA256)
        tampered["catalog.v1.json"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "catalog.v1.json"):
            self.contract.load_contract(CONTRACT, tampered)


if __name__ == "__main__":
    unittest.main()
