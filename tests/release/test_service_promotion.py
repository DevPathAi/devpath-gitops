import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "release"
FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-candidate-spec.json"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_module(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ServicePromotionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.promoter = load_module("promote_service_digests.py", "service_promoter")
        cls.candidate = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_exact_nine_service_allowlist_is_frozen(self):
        expected = (
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
        self.assertEqual(self.promoter.SERVICE_NAMES, expected)
        self.assertEqual(
            tuple(self.promoter.SERVICE_PATHS.values()),
            tuple(f"apps/{name}/base/kustomization.yaml" for name in expected),
        )

    def test_each_tag_selector_becomes_only_its_candidate_digest(self):
        for name in self.promoter.SERVICE_NAMES:
            source = (ROOT / self.promoter.SERVICE_PATHS[name]).read_text(encoding="utf-8")
            rendered = self.promoter.render_kustomization(source, self.candidate, name)
            service = self.candidate["services"][name]
            self.assertEqual(rendered.count(f"digest: {service['image_digest']}"), 1)
            self.assertNotIn("newTag:", rendered)
            self.assertNotIn("\r", rendered)

    def test_prior_digest_selector_is_exactly_replaced_for_the_next_release(self):
        name = "devpath-admin"
        source = (ROOT / self.promoter.SERVICE_PATHS[name]).read_text(encoding="utf-8")
        prior = "sha256:" + "9" * 64
        selector = next(line for line in source.splitlines() if line.startswith("  newTag: "))
        source = source.replace(selector + "\n", f"  digest: {prior}\n")
        rendered = self.promoter.render_kustomization(source, self.candidate, name)
        current = self.candidate["services"][name]["image_digest"]
        self.assertEqual(rendered.count(f"  digest: {current}\n"), 1)
        self.assertNotIn(prior, rendered)
        for mutation in (
            source.replace(prior, "sha256:" + "A" * 64),
            source.replace(
                f"  digest: {prior}",
                "  digest: sha256:" + "8" * 64 + f"\n  digest: {prior}",
            ),
        ):
            with self.subTest(mutation=mutation[-120:]), self.assertRaises(ValueError):
                self.promoter.render_kustomization(mutation, self.candidate, name)

    def test_selector_rejects_wrong_duplicate_or_malformed_base(self):
        name = "devpath-admin"
        source = (ROOT / self.promoter.SERVICE_PATHS[name]).read_text(encoding="utf-8")
        mutations = (
            source.replace("- name: ghcr.io/devpathai/devpath-admin", "- name: evil/admin"),
            source.replace(
                "  newName: ghcr.io/devpathai/devpath-admin",
                "  newName: evil/admin",
            ),
            source + "- name: ghcr.io/devpathai/extra\n  newName: ghcr.io/devpathai/extra\n  newTag: main\n",
            re.sub(r"  newTag: [^\n]+", "  digest: not-a-digest", source),
            source.replace("  newTag:", "  unexpected:\n  newTag:"),
            source.replace("\n", "\r\n"),
        )
        for mutated in mutations:
            with self.subTest(mutated=mutated[-100:]):
                with self.assertRaises(ValueError):
                    self.promoter.render_kustomization(mutated, self.candidate, name)

    def test_image_entry_count_is_scoped_to_the_images_section(self):
        name = "devpath-admin"
        source = (ROOT / self.promoter.SERVICE_PATHS[name]).read_text(encoding="utf-8")
        with_generator = (
            "configMapGenerator:\n"
            "- name: unrelated-runtime-config\n"
            "  literals:\n"
            "  - MODE=exact\n"
            + source
        )
        rendered = self.promoter.render_kustomization(
            with_generator, self.candidate, name
        )
        self.assertIn("- name: unrelated-runtime-config", rendered)
        duplicate_image = source.replace(
            "images:\n",
            "images:\n"
            "- name: ghcr.io/devpathai/extra\n"
            "  newName: ghcr.io/devpathai/extra\n"
            "  newTag: main\n",
            1,
        )
        with self.assertRaisesRegex(ValueError, "exactly one image entry"):
            self.promoter.render_kustomization(
                duplicate_image, self.candidate, name
            )

    def test_rendered_output_requires_one_exact_target_image_and_no_tag_form(self):
        name = "devpath-ai-svc"
        service = self.candidate["services"][name]
        exact = (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            f"  name: {name}\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      containers:\n"
            f"      - image: {service['image_repository']}@{service['image_digest']}\n"
            f"        name: {name}\n"
        )
        self.promoter.validate_rendered_output(exact, self.candidate, name)
        for mutated in (
            exact.replace(service["image_digest"], "sha256:" + "0" * 64),
            exact + exact,
            exact + f"      - image: {service['image_repository']}:main\n",
            exact.replace("image:", "command:"),
            exact.replace("\n", "\r\n"),
            exact.replace("    spec:\n      containers:", "    bogus:\n      containers:"),
            exact.replace("      containers:", "      initContainers:"),
            exact.replace(f"        name: {name}", "        name: sidecar"),
        ):
            with self.assertRaises(ValueError):
                self.promoter.validate_rendered_output(mutated, self.candidate, name)

    def test_apply_is_all_or_nothing_before_build_and_touches_only_nine_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in self.promoter.SERVICE_NAMES:
                target = root / self.promoter.SERVICE_PATHS[name]
                target.parent.mkdir(parents=True)
                shutil.copy2(ROOT / self.promoter.SERVICE_PATHS[name], target)
            original = {
                name: (root / self.promoter.SERVICE_PATHS[name]).read_bytes()
                for name in self.promoter.SERVICE_NAMES
            }
            broken = root / self.promoter.SERVICE_PATHS[self.promoter.SERVICE_NAMES[-1]]
            broken.write_text("images: []\n", encoding="utf-8", newline="\n")
            with self.assertRaises(ValueError):
                self.promoter.apply_service_digests(root, self.candidate, Path("kustomize"))
            for name in self.promoter.SERVICE_NAMES[:-1]:
                self.assertEqual(
                    (root / self.promoter.SERVICE_PATHS[name]).read_bytes(), original[name]
                )

    def test_real_kustomize_renders_exact_candidate_digest_for_all_nine(self):
        binary = shutil.which("kubectl")
        if binary is None:
            self.skipTest("kubectl is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "apps", root / "apps")
            self.promoter.apply_service_digests(
                root,
                self.candidate,
                Path(binary),
                build_arguments=("kustomize",),
            )


if __name__ == "__main__":
    unittest.main()
