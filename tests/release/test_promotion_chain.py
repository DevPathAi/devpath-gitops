import copy
import importlib.util
import json
from pathlib import Path
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


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


class PromotionChainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chain = load_module("verify_promotion_chain.py", "promotion_chain")
        cls.services = load_module("promote_service_digests.py", "chain_services")
        cls.web = load_module("set_web_digest.py", "chain_web")
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "apps", self.root / "apps")
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "devpath-gitops-release[bot]")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "add", "apps")
        git(self.root, "commit", "-m", "base")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.candidate = copy.deepcopy(self.fixture)
        self.candidate["gitops"]["base_sha"] = self.base
        self.candidate_hash = "c" * 64
        self.release_hash = "a" * 64

    def tearDown(self):
        self.temp.cleanup()

    def commit(self, subject: str, *, allow_empty: bool = False) -> str:
        git(self.root, "add", "apps")
        arguments = ["commit"]
        if allow_empty:
            arguments.append("--allow-empty")
        git(self.root, *arguments, "-m", subject)
        return git(self.root, "rev-parse", "HEAD")

    def set_migration(self):
        path = self.root / "apps/devpath-migration/base/kustomization.yaml"
        digest = self.candidate["shared_migration"]["image_digest"]
        path.write_text(
            self.chain.render_migration_kustomization(
                path.read_text(encoding="utf-8"), digest, self.release_hash
            ),
            encoding="utf-8",
            newline="\n",
        )

    def set_services(self):
        binary = shutil.which("kubectl")
        if binary is None:
            self.skipTest("kubectl is unavailable")
        self.services.apply_service_digests(
            self.root,
            self.candidate,
            Path(binary),
            build_arguments=("kustomize",),
        )

    def set_web(self, target: str, expected: str):
        path = self.root / "apps/devpath-web/base/kustomization.yaml"
        path.write_text(
            self.web.render_kustomization(
                path.read_text(encoding="utf-8"),
                self.candidate,
                target,
                expected,
                candidate_spec_sha256=self.candidate_hash,
            ),
            encoding="utf-8",
            newline="\n",
        )

    def inspect(self, commit: str):
        return self.chain.inspect_chain(
            self.root,
            self.candidate,
            self.candidate_hash,
            self.release_hash,
            commit,
        )

    def set_next_release_candidate(self, base: str, base_web_digest: str):
        candidate = copy.deepcopy(self.fixture)
        candidate["gitops"]["base_sha"] = base
        candidate["gitops"]["base_web_digest"] = base_web_digest
        candidate["frontend"]["rollback"]["prior_digest"] = base_web_digest
        candidate["frontend"]["mission_off"]["image_digest"] = "sha256:" + "d" * 64
        candidate["frontend"]["rollback"]["mission_off_digest"] = "sha256:" + "d" * 64
        candidate["frontend"]["selected_on_digest"] = "sha256:" + "e" * 64
        candidate["frontend"]["mission_on"]["image_digest"] = "sha256:" + "e" * 64
        candidate["shared_migration"]["image_digest"] = "sha256:" + "f" * 64
        for index, name in enumerate(self.services.SERVICE_NAMES, start=1):
            candidate["services"][name]["image_digest"] = (
                "sha256:" + f"{index:x}a" * 32
            )
        self.candidate = candidate
        self.candidate_hash = "d" * 64
        self.release_hash = "b" * 64

    def test_full_chain_and_completed_rollback_cycle_are_exact_and_resumable(self):
        self.assertEqual(self.inspect(self.base)["phase"], "base")
        self.set_migration()
        migration = self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        state = self.inspect(migration)
        self.assertEqual(state["phase"], "migration")
        self.assertEqual(state["migration_commit"], migration)

        self.set_services()
        services = self.commit(
            f"release(services): promote {self.candidate['release_id']} additive-services"
        )
        state = self.inspect(services)
        self.assertEqual(state["phase"], "services")
        self.assertEqual(state["web_phase"], "base")

        self.set_web("mission-off", "base")
        off = self.commit(
            f"release(web): promote {self.candidate['release_id']} mission-off"
        )
        self.assertEqual(self.inspect(off)["phase"], "mission-off")
        self.set_web("mission-on", "mission-off")
        on = self.commit(f"release(web): promote {self.candidate['release_id']} mission-on")
        self.assertEqual(self.inspect(on)["phase"], "mission-on")

        self.set_web("mission-off", "mission-on")
        rollback_off = self.commit(
            f"rollback(web): {self.candidate['release_id']} frontend-mission-off"
        )
        self.assertEqual(self.inspect(rollback_off)["phase"], "rollback-off")
        self.set_web("prior", "mission-off")
        rollback_prior = self.commit(
            f"rollback(web): {self.candidate['release_id']} frontend-prior"
        )
        state = self.inspect(rollback_prior)
        self.assertEqual(state["phase"], "services")
        self.assertEqual(state["web_phase"], "prior")
        self.assertEqual(state["services_commit"], services)

        self.set_web("mission-off", "prior")
        resumed_off = self.commit(
            f"release(web): promote {self.candidate['release_id']} mission-off"
        )
        self.set_web("mission-on", "mission-off")
        resumed_on = self.commit(
            f"release(web): promote {self.candidate['release_id']} mission-on"
        )
        state = self.inspect(resumed_on)
        self.assertEqual(state["phase"], "mission-on")
        self.assertEqual(state["off_commit"], resumed_off)
        self.assertEqual(state["on_commit"], resumed_on)

    def test_next_release_can_start_from_prior_mission_on_digest_state(self):
        self.set_migration()
        self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.set_services()
        self.commit(
            f"release(services): promote {self.candidate['release_id']} additive-services"
        )
        self.set_web("mission-off", "base")
        self.commit(f"release(web): promote {self.candidate['release_id']} mission-off")
        self.set_web("mission-on", "mission-off")
        prior_on = self.commit(
            f"release(web): promote {self.candidate['release_id']} mission-on"
        )
        prior_on_digest = self.candidate["frontend"]["selected_on_digest"]

        self.set_next_release_candidate(prior_on, prior_on_digest)
        self.assertEqual(self.inspect(prior_on)["phase"], "base")
        self.set_migration()
        migration = self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.assertEqual(self.inspect(migration)["phase"], "migration")
        self.set_services()
        services = self.commit(
            f"release(services): promote {self.candidate['release_id']} additive-services"
        )
        self.assertEqual(self.inspect(services)["phase"], "services")
        self.set_web("mission-off", "base")
        self.commit(f"release(web): promote {self.candidate['release_id']} mission-off")
        self.set_web("mission-on", "mission-off")
        current = self.commit(
            f"release(web): promote {self.candidate['release_id']} mission-on"
        )
        self.assertEqual(self.inspect(current)["phase"], "mission-on")

    def test_next_release_with_same_migration_digest_gets_a_fresh_job_name(self):
        digest = self.candidate["shared_migration"]["image_digest"]
        first_release_hash = self.release_hash
        self.set_migration()
        first = self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {first_release_hash}"
        )
        first_source = (
            self.root / "apps/devpath-migration/base/kustomization.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            self.chain.migration_job_name(digest, first_release_hash), first_source
        )

        self.candidate["gitops"]["base_sha"] = first
        self.release_hash = "b" * 64
        self.assertEqual(self.inspect(first)["phase"], "base")
        self.set_migration()
        second_source = (
            self.root / "apps/devpath-migration/base/kustomization.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            self.chain.migration_job_name(digest, self.release_hash), second_source
        )
        self.assertNotEqual(first_source, second_source)
        second = self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.assertEqual(self.inspect(second)["phase"], "migration")

    def test_next_release_can_start_from_prior_rollback_prior_digest_state(self):
        self.set_migration()
        self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.set_services()
        self.commit(
            f"release(services): promote {self.candidate['release_id']} additive-services"
        )
        self.set_web("mission-off", "base")
        self.commit(f"release(web): promote {self.candidate['release_id']} mission-off")
        self.set_web("prior", "mission-off")
        prior = self.commit(
            f"rollback(web): {self.candidate['release_id']} frontend-prior"
        )
        prior_digest = self.candidate["frontend"]["rollback"]["prior_digest"]

        self.set_next_release_candidate(prior, prior_digest)
        self.assertEqual(self.inspect(prior)["phase"], "base")
        self.set_migration()
        migration = self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.assertEqual(self.inspect(migration)["phase"], "migration")
        self.set_services()
        services = self.commit(
            f"release(services): promote {self.candidate['release_id']} additive-services"
        )
        self.assertEqual(self.inspect(services)["phase"], "services")

    def test_unrelated_subject_or_path_after_migration_is_rejected(self):
        self.set_migration()
        migration = self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.assertEqual(self.inspect(migration)["phase"], "migration")
        extra = self.root / "apps/devpath-admin/base/service.yaml"
        extra.write_text(extra.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        drift = self.commit("unsealed drift")
        with self.assertRaises(ValueError):
            self.inspect(drift)

    def test_recognized_commit_from_non_app_actor_is_rejected(self):
        self.set_migration()
        git(self.root, "config", "user.name", "lookalike-release-bot")
        migration = self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        with self.assertRaisesRegex(ValueError, "release App"):
            self.inspect(migration)

    def test_partial_or_swapped_service_digest_commit_is_rejected(self):
        self.set_migration()
        self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.set_services()
        admin = self.root / self.services.SERVICE_PATHS["devpath-admin"]
        ai = self.root / self.services.SERVICE_PATHS["devpath-ai-svc"]
        admin_text = admin.read_text(encoding="utf-8")
        ai_text = ai.read_text(encoding="utf-8")
        admin_digest = self.candidate["services"]["devpath-admin"]["image_digest"]
        ai_digest = self.candidate["services"]["devpath-ai-svc"]["image_digest"]
        admin.write_text(admin_text.replace(admin_digest, ai_digest), encoding="utf-8")
        ai.write_text(ai_text.replace(ai_digest, admin_digest), encoding="utf-8")
        commit = self.commit(
            f"release(services): promote {self.candidate['release_id']} additive-services"
        )
        with self.assertRaises(ValueError):
            self.inspect(commit)

    def test_services_commit_accepts_only_the_exact_changed_subset_or_empty_noop(self):
        unchanged = self.services.SERVICE_NAMES[0]
        target = self.root / self.services.SERVICE_PATHS[unchanged]
        target.write_text(
            self.services.render_kustomization(
                target.read_text(encoding="utf-8"), self.candidate, unchanged
            ),
            encoding="utf-8",
            newline="\n",
        )
        git(self.root, "add", self.services.SERVICE_PATHS[unchanged])
        git(self.root, "commit", "--amend", "--no-edit")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.candidate["gitops"]["base_sha"] = self.base
        self.set_migration()
        self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.set_services()
        services = self.commit(
            f"release(services): promote {self.candidate['release_id']} additive-services"
        )
        self.assertEqual(self.inspect(services)["phase"], "services")

        # A new release with all nine already selected still gets an exact empty S node.
        next_candidate = copy.deepcopy(self.candidate)
        next_candidate["gitops"]["base_sha"] = services
        next_candidate["shared_migration"]["image_digest"] = "sha256:" + "f" * 64
        self.candidate = next_candidate
        self.release_hash = "b" * 64
        self.assertEqual(self.inspect(services)["phase"], "base")
        self.set_migration()
        self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.set_services()
        empty = self.commit(
            f"release(services): promote {self.candidate['release_id']} additive-services",
            allow_empty=True,
        )
        self.assertEqual(self.inspect(empty)["phase"], "services")

    def test_force_annotation_or_non_derived_job_name_is_rejected(self):
        self.set_migration()
        path = self.root / "apps/devpath-migration/base/kustomization.yaml"
        source = path.read_text(encoding="utf-8")
        path.write_text(
            source.replace(
                "      path: /metadata/name\n",
                "      path: /metadata/annotations/argocd.argoproj.io~1sync-options\n",
            ).replace(
                next(line for line in source.splitlines() if line.startswith("      value: ")),
                "      value: Force=true,Replace=true",
            ),
            encoding="utf-8",
            newline="\n",
        )
        commit = self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        with self.assertRaises(ValueError):
            self.inspect(commit)

    def test_malformed_or_legacy_migration_patch_modes_are_rejected(self):
        path = self.root / "apps/devpath-migration/base/kustomization.yaml"
        pristine = path.read_text(encoding="utf-8")
        for suffix in (
            "patches: []\n",
            " patches:\n",
            "patchesStrategicMerge:\n- unsafe.yaml\n",
            "patchesJson6902:\n- target: {}\n",
        ):
            with self.subTest(suffix=suffix):
                path.write_text(pristine + suffix, encoding="utf-8", newline="\n")
                malformed = self.commit("malformed migration base")
                self.candidate["gitops"]["base_sha"] = malformed
                with self.assertRaisesRegex(ValueError, "migration"):
                    self.inspect(malformed)
                git(self.root, "reset", "--hard", "HEAD^")
                self.candidate["gitops"]["base_sha"] = self.base

    def test_base_migration_job_must_be_inert_before_the_sealed_commit(self):
        job = self.root / self.chain.MIGRATION_JOB_PATH
        job.write_text(
            job.read_text(encoding="utf-8").replace("  suspend: true\n", "  suspend: false\n"),
            encoding="utf-8",
            newline="\n",
        )
        unsafe_base = self.commit("unsafe base migration job")
        self.candidate["gitops"]["base_sha"] = unsafe_base
        with self.assertRaisesRegex(ValueError, "not inert"):
            self.inspect(unsafe_base)

        git(self.root, "reset", "--hard", "HEAD^")
        job = self.root / self.chain.MIGRATION_JOB_PATH
        source = job.read_text(encoding="utf-8")
        job.write_text(
            source.replace("  suspend: true\n", "  misplaced: true\n").replace(
                "metadata:\n", "metadata:\n  suspend: true\n", 1
            ),
            encoding="utf-8",
            newline="\n",
        )
        misplaced = self.commit("misplaced migration suspension")
        self.candidate["gitops"]["base_sha"] = misplaced
        with self.assertRaisesRegex(ValueError, "not inert"):
            self.inspect(misplaced)

    def test_allowed_service_or_web_path_cannot_hide_unrelated_bytes(self):
        self.set_migration()
        self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.set_services()
        admin = self.root / self.services.SERVICE_PATHS["devpath-admin"]
        admin.write_text(
            admin.read_text(encoding="utf-8") + "namespace: unsealed\n",
            encoding="utf-8",
            newline="\n",
        )
        services = self.commit(
            f"release(services): promote {self.candidate['release_id']} additive-services"
        )
        with self.assertRaisesRegex(ValueError, "changed more"):
            self.inspect(services)

        git(self.root, "reset", "--hard", "HEAD^")
        self.set_services()
        self.commit(
            f"release(services): promote {self.candidate['release_id']} additive-services"
        )
        self.set_web("mission-off", "base")
        web = self.root / "apps/devpath-web/base/kustomization.yaml"
        web.write_text(
            web.read_text(encoding="utf-8") + "# hidden rollout mutation\n",
            encoding="utf-8",
            newline="\n",
        )
        off = self.commit(
            f"release(web): promote {self.candidate['release_id']} mission-off"
        )
        with self.assertRaisesRegex(ValueError, "changed more"):
            self.inspect(off)


if __name__ == "__main__":
    unittest.main()
