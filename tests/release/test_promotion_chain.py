import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "release"
FIXTURE = ROOT / "tests" / "release" / "fixtures" / "valid-candidate-spec.json"
WEB_APPLIED_REVISION_FIX_PATHS = (
    ".github/workflows/mission-spine-promote.yml",
    ".github/workflows/mission-spine-rollback.yml",
    "scripts/release/verify_promotion_chain.py",
    "scripts/release/wait_web_rollout.py",
    "tests/release/test_production_workflow_wiring.py",
    "tests/release/test_promotion_chain.py",
    "tests/release/test_release_hardening.py",
)
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
        [
            "git",
            "-c",
            "maintenance.auto=false",
            "-c",
            "gc.auto=0",
            *args,
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class GitTestHelperTest(unittest.TestCase):
    @mock.patch("subprocess.run")
    def test_transient_repositories_disable_automatic_git_maintenance(self, run):
        run.return_value.stdout = ""

        git(Path("temporary-repository"), "status")

        self.assertEqual(
            [
                "git",
                "-c",
                "maintenance.auto=false",
                "-c",
                "gc.auto=0",
                "status",
            ],
            run.call_args.args[0],
        )


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
        copied_contract_paths = {
            *self.chain.SHARED_MIGRATION_APPROVAL_FIX_PATHS,
            *self.chain.MIGRATION_RUNTIME_FIX_PATHS,
            *self.chain.MIGRATION_RUNTIME_ADMISSION_FIX_PATHS,
            *self.chain.MIGRATION_PREFLIGHT_IDENTITY_FIX_PATHS,
            *self.chain.SERVICE_STATUS_IMAGE_FIX_PATHS,
            *self.chain.SERVICE_SOURCE_STATUS_FIX_PATHS,
            *self.chain.CANARY_RUNTIME_FORM_FIX_PATHS,
            *WEB_APPLIED_REVISION_FIX_PATHS,
        }
        for relative in copied_contract_paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        self.candidate = copy.deepcopy(self.fixture)
        self.candidate_hash = "c" * 64
        self.release_hash = "a" * 64
        web_path = self.root / "apps/devpath-web/base/kustomization.yaml"
        prior = self.candidate["frontend"]["rollback"]["prior_identity"]
        web_source = (
            "apiVersion: kustomize.config.k8s.io/v1beta1\n"
            "kind: Kustomization\n"
            "resources:\n"
            "- deployment.yaml\n"
            "- service.yaml\n"
            "- ingress.yaml\n"
            "configMapGenerator:\n"
            "- name: devpath-web-release-identity\n"
            "  literals:\n"
            f"  - MISSION_RELEASE_READY={'true' if prior['ready'] else 'false'}\n"
            f"  - MISSION_RELEASE_ID={prior['release_id']}\n"
            f"  - MISSION_CANDIDATE_SPEC_SHA256={prior['candidate_spec_sha256']}\n"
            f"  - MISSION_IMAGE_DIGEST={prior['image_digest']}\n"
            "images:\n"
            "- name: ghcr.io/devpathai/devpath-web\n"
            "  newName: ghcr.io/devpathai/devpath-web\n"
            f"  newTag: {self.candidate['gitops']['base_web_tag']}\n"
        )
        web_path.write_text(web_source, encoding="utf-8", newline="\n")
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "devpath-gitops-release[bot]")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "add", ".github", "apps", "scripts", "tests")
        git(self.root, "commit", "-m", "base")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.candidate["gitops"]["base_sha"] = self.base

    def tearDown(self):
        self.temp.cleanup()

    def commit(self, subject: str, *, allow_empty: bool = False) -> str:
        git(self.root, "add", "apps")
        arguments = ["commit"]
        if allow_empty:
            arguments.append("--allow-empty")
        git(self.root, *arguments, "-m", subject)
        return git(self.root, "rev-parse", "HEAD")

    def commit_shared_migration_approval_fix(self, suffix: str = "") -> str:
        paths = self.chain.SHARED_MIGRATION_APPROVAL_FIX_PATHS
        for relative in paths:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"approval-contract-fix{suffix}\n", encoding="utf-8")
        git(self.root, "add", *paths)
        git(self.root, "commit", "-m", self.chain.SHARED_MIGRATION_APPROVAL_FIX_SUBJECT)
        return git(self.root, "rev-parse", "HEAD")

    def commit_migration_runtime_fix(self, *, suffix: str = "") -> str:
        paths = self.chain.MIGRATION_RUNTIME_FIX_PATHS
        source_sha = self.candidate["shared_migration"]["source_sha"]
        target = self.candidate["shared_migration"]["flyway_target"]
        required = self.candidate["shared_migration"]["required_migration"]
        job_path = self.root / self.chain.MIGRATION_JOB_PATH
        job = job_path.read_text(encoding="utf-8")
        job = self.chain.render_migration_runtime_job(
            job,
            source_sha=source_sha,
            flyway_target=target,
            required_migration=required,
        )
        job_path.write_text(job, encoding="utf-8", newline="\n")
        preflight_path = self.root / self.chain.MIGRATION_PREFLIGHT_PATH
        preflight = self.chain.render_migration_preflight(
            preflight_path.read_text(encoding="utf-8"),
            source_sha=source_sha,
            flyway_target=target,
        )
        preflight_path.write_text(preflight, encoding="utf-8", newline="\n")
        for relative in paths:
            if relative in {self.chain.MIGRATION_JOB_PATH, self.chain.MIGRATION_PREFLIGHT_PATH}:
                continue
            path = self.root / relative
            path.write_text(
                path.read_text(encoding="utf-8") + f"\n# migration-runtime-fix{suffix}\n",
                encoding="utf-8",
                newline="\n",
            )
        git(self.root, "add", *paths)
        git(self.root, "commit", "-m", self.chain.MIGRATION_RUNTIME_FIX_SUBJECT)
        return git(self.root, "rev-parse", "HEAD")

    def commit_migration_runtime_admission_fix(self, *, suffix: str = "") -> str:
        paths = self.chain.MIGRATION_RUNTIME_ADMISSION_FIX_PATHS
        for relative in paths:
            path = self.root / relative
            path.write_text(
                path.read_text(encoding="utf-8")
                + f"\n# migration-runtime-admission-fix{suffix}\n",
                encoding="utf-8",
                newline="\n",
            )
        git(self.root, "add", *paths)
        git(
            self.root,
            "commit",
            "-m",
            self.chain.MIGRATION_RUNTIME_ADMISSION_FIX_SUBJECT,
        )
        return git(self.root, "rev-parse", "HEAD")

    def commit_migration_preflight_identity_fix(self, *, suffix: str = "") -> str:
        paths = self.chain.MIGRATION_PREFLIGHT_IDENTITY_FIX_PATHS
        for relative in paths:
            path = self.root / relative
            path.write_text(
                path.read_text(encoding="utf-8")
                + f"\n# migration-preflight-identity-fix{suffix}\n",
                encoding="utf-8",
                newline="\n",
            )
        git(self.root, "add", *paths)
        git(
            self.root,
            "commit",
            "-m",
            self.chain.MIGRATION_PREFLIGHT_IDENTITY_FIX_SUBJECT,
        )
        return git(self.root, "rev-parse", "HEAD")

    def commit_service_status_image_fix(self, *, suffix: str = "") -> str:
        paths = self.chain.SERVICE_STATUS_IMAGE_FIX_PATHS
        for relative in paths:
            path = self.root / relative
            path.write_text(
                path.read_text(encoding="utf-8")
                + f"\n# service-status-image-fix{suffix}\n",
                encoding="utf-8",
                newline="\n",
            )
        git(self.root, "add", *paths)
        git(self.root, "commit", "-m", self.chain.SERVICE_STATUS_IMAGE_FIX_SUBJECT)
        return git(self.root, "rev-parse", "HEAD")

    def commit_service_source_status_fix(self, *, suffix: str = "") -> str:
        paths = self.chain.SERVICE_SOURCE_STATUS_FIX_PATHS
        for relative in paths:
            path = self.root / relative
            path.write_text(
                path.read_text(encoding="utf-8")
                + f"\n# service-source-status-fix{suffix}\n",
                encoding="utf-8",
                newline="\n",
            )
        git(self.root, "add", *paths)
        git(self.root, "commit", "-m", self.chain.SERVICE_SOURCE_STATUS_FIX_SUBJECT)
        return git(self.root, "rev-parse", "HEAD")

    def commit_canary_runtime_form_fix(self, *, suffix: str = "") -> str:
        paths = self.chain.CANARY_RUNTIME_FORM_FIX_PATHS
        for relative in paths:
            path = self.root / relative
            path.write_text(
                path.read_text(encoding="utf-8")
                + f"\n# canary-runtime-form-fix{suffix}\n",
                encoding="utf-8",
                newline="\n",
            )
        git(self.root, "add", *paths)
        git(self.root, "commit", "-m", self.chain.CANARY_RUNTIME_FORM_FIX_SUBJECT)
        return git(self.root, "rev-parse", "HEAD")

    def commit_post_on_resume_fix(self, *, suffix: str = "") -> str:
        paths = self.chain.POST_ON_RESUME_FIX_PATHS
        for relative in paths:
            path = self.root / relative
            path.write_text(
                path.read_text(encoding="utf-8")
                + f"\n# post-on-resume-fix{suffix}\n",
                encoding="utf-8",
                newline="\n",
            )
        git(self.root, "add", *paths)
        git(self.root, "commit", "-m", self.chain.POST_ON_RESUME_FIX_SUBJECT)
        return git(self.root, "rev-parse", "HEAD")

    def commit_web_applied_revision_fix(self, *, suffix: str = "") -> str:
        self.assertEqual(
            WEB_APPLIED_REVISION_FIX_PATHS,
            self.chain.WEB_APPLIED_REVISION_FIX_PATHS,
        )
        for relative in self.chain.WEB_APPLIED_REVISION_FIX_PATHS:
            path = self.root / relative
            path.write_text(
                path.read_text(encoding="utf-8")
                + f"\n# web-applied-revision-fix{suffix}\n",
                encoding="utf-8",
                newline="\n",
            )
        git(self.root, "add", *self.chain.WEB_APPLIED_REVISION_FIX_PATHS)
        git(self.root, "commit", "-m", self.chain.WEB_APPLIED_REVISION_FIX_SUBJECT)
        return git(self.root, "rev-parse", "HEAD")

    def commit_staging_context_auth_fix(self, *, suffix: str = "") -> str:
        for relative in self.chain.STAGING_CONTEXT_AUTH_FIX_PATHS:
            path = self.root / relative
            path.write_text(
                path.read_text(encoding="utf-8")
                + f"\n# staging-context-auth-fix{suffix}\n",
                encoding="utf-8",
                newline="\n",
            )
        git(self.root, "add", *self.chain.STAGING_CONTEXT_AUTH_FIX_PATHS)
        git(self.root, "commit", "-m", self.chain.STAGING_CONTEXT_AUTH_FIX_SUBJECT)
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

    def current_web_identity(self):
        source = (
            self.root / "apps/devpath-web/base/kustomization.yaml"
        ).read_text(encoding="utf-8")
        _, values = self.web._parse_identity(source)
        return {
            "ready": values[0] == "true",
            "release_id": values[1],
            "candidate_spec_sha256": values[2],
            "image_digest": values[3],
        }

    def set_next_release_candidate(
        self, base: str, base_web_digest: str, prior_identity: dict
    ):
        next_release_id = "ms-20990102-contract-fixture"
        candidate = json.loads(
            json.dumps(self.fixture).replace(
                self.fixture["release_id"], next_release_id
            )
        )
        candidate["gitops"]["base_sha"] = base
        candidate["gitops"]["base_web_digest"] = base_web_digest
        candidate["frontend"]["rollback"]["prior_digest"] = base_web_digest
        candidate["frontend"]["rollback"]["prior_identity"] = prior_identity
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
        prior_identity = self.current_web_identity()

        self.set_next_release_candidate(prior_on, prior_on_digest, prior_identity)
        self.assertEqual(
            self.candidate["frontend"]["rollback"]["prior_identity"],
            {
                "ready": True,
                "release_id": "ms-20990101-contract-fixture",
                "candidate_spec_sha256": "c" * 64,
                "image_digest": prior_on_digest,
            },
        )
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

        prior_identity = self.current_web_identity()
        base_web_digest = self.candidate["gitops"]["base_web_digest"]
        self.set_next_release_candidate(first, base_web_digest, prior_identity)
        self.candidate["shared_migration"]["image_digest"] = digest
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
        prior_identity = self.current_web_identity()

        self.set_next_release_candidate(prior, prior_digest, prior_identity)
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

    def test_single_exact_shared_migration_approval_fix_is_phase_transparent(self):
        self.set_migration()
        migration = self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        control = self.commit_shared_migration_approval_fix()

        state = self.inspect(control)
        self.assertEqual("migration", state["phase"])
        self.assertEqual(migration, state["migration_commit"])
        self.assertEqual(control, state["current_commit"])

        self.set_services()
        services = self.commit(
            f"release(services): promote {self.candidate['release_id']} additive-services"
        )
        self.assertEqual("services", self.inspect(services)["phase"])

    def test_shared_migration_approval_fix_requires_migration_and_cannot_repeat(self):
        before_migration = self.commit_shared_migration_approval_fix()
        with self.assertRaisesRegex(ValueError, "directly follow migration"):
            self.inspect(before_migration)

        git(self.root, "reset", "--hard", self.base)
        self.set_migration()
        self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.commit_shared_migration_approval_fix()
        repeated = self.commit_shared_migration_approval_fix("-repeated")
        with self.assertRaisesRegex(ValueError, "directly follow migration"):
            self.inspect(repeated)

    def test_exact_migration_runtime_fix_is_phase_transparent_and_cannot_repeat(self):
        self.set_migration()
        migration = self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        approval = self.commit_shared_migration_approval_fix()
        runtime = self.commit_migration_runtime_fix()

        state = self.inspect(runtime)
        self.assertEqual("migration", state["phase"])
        self.assertEqual(migration, state["migration_commit"])
        self.assertEqual(approval, state["shared_migration_approval_fix_commit"])
        self.assertEqual(runtime, state["migration_runtime_fix_commit"])
        self.assertEqual(runtime, state["current_commit"])

        repeated = self.commit_migration_runtime_fix(suffix="-repeated")
        with self.assertRaisesRegex(ValueError, "directly follow approval fix"):
            self.inspect(repeated)

    def test_migration_runtime_fix_requires_the_approval_fix_and_exact_paths(self):
        self.set_migration()
        self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        runtime = self.commit_migration_runtime_fix()
        with self.assertRaisesRegex(ValueError, "directly follow approval fix"):
            self.inspect(runtime)

        git(self.root, "reset", "--hard", "HEAD^")
        self.commit_shared_migration_approval_fix()
        omitted = self.chain.MIGRATION_RUNTIME_FIX_PATHS[-1]
        path = self.root / omitted
        path.write_text(
            path.read_text(encoding="utf-8") + "\n# omitted-path-restored\n",
            encoding="utf-8",
            newline="\n",
        )
        runtime = self.commit_migration_runtime_fix()
        git(self.root, "checkout", "HEAD^", "--", omitted)
        git(self.root, "add", omitted)
        git(self.root, "commit", "--amend", "--no-edit")
        runtime = git(self.root, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "path set is not exact"):
            self.inspect(runtime)

    def test_exact_runtime_admission_fix_is_phase_transparent_and_cannot_repeat(self):
        self.set_migration()
        migration = self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        approval = self.commit_shared_migration_approval_fix()
        runtime = self.commit_migration_runtime_fix()
        admission = self.commit_migration_runtime_admission_fix()

        state = self.inspect(admission)
        self.assertEqual("migration", state["phase"])
        self.assertEqual(migration, state["migration_commit"])
        self.assertEqual(approval, state["shared_migration_approval_fix_commit"])
        self.assertEqual(runtime, state["migration_runtime_fix_commit"])
        self.assertEqual(admission, state["migration_runtime_admission_fix_commit"])
        self.assertEqual(admission, state["current_commit"])

        repeated = self.commit_migration_runtime_admission_fix(suffix="-repeated")
        with self.assertRaisesRegex(ValueError, "directly follow runtime fix"):
            self.inspect(repeated)

    def test_runtime_admission_fix_requires_runtime_fix_and_exact_paths(self):
        self.set_migration()
        self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.commit_shared_migration_approval_fix()
        admission = self.commit_migration_runtime_admission_fix()
        with self.assertRaisesRegex(ValueError, "directly follow runtime fix"):
            self.inspect(admission)

        git(self.root, "reset", "--hard", "HEAD^")
        self.commit_migration_runtime_fix()
        omitted = self.chain.MIGRATION_RUNTIME_ADMISSION_FIX_PATHS[-1]
        self.commit_migration_runtime_admission_fix()
        git(self.root, "checkout", "HEAD^", "--", omitted)
        git(self.root, "add", omitted)
        git(self.root, "commit", "--amend", "--no-edit")
        admission = git(self.root, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "path set is not exact"):
            self.inspect(admission)

    def test_exact_preflight_identity_fix_is_phase_transparent_and_cannot_repeat(self):
        self.set_migration()
        migration = self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        approval = self.commit_shared_migration_approval_fix()
        runtime = self.commit_migration_runtime_fix()
        admission = self.commit_migration_runtime_admission_fix()
        identity = self.commit_migration_preflight_identity_fix()

        state = self.inspect(identity)
        self.assertEqual("migration", state["phase"])
        self.assertEqual(migration, state["migration_commit"])
        self.assertEqual(approval, state["shared_migration_approval_fix_commit"])
        self.assertEqual(runtime, state["migration_runtime_fix_commit"])
        self.assertEqual(admission, state["migration_runtime_admission_fix_commit"])
        self.assertEqual(identity, state["migration_preflight_identity_fix_commit"])
        self.assertEqual(identity, state["current_commit"])

        repeated = self.commit_migration_preflight_identity_fix(suffix="-repeated")
        with self.assertRaisesRegex(ValueError, "directly follow admission fix"):
            self.inspect(repeated)

    def test_preflight_identity_fix_requires_admission_fix_and_exact_paths(self):
        self.set_migration()
        self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.commit_shared_migration_approval_fix()
        self.commit_migration_runtime_fix()
        identity = self.commit_migration_preflight_identity_fix()
        with self.assertRaisesRegex(ValueError, "directly follow admission fix"):
            self.inspect(identity)

        git(self.root, "reset", "--hard", "HEAD^")
        self.commit_migration_runtime_admission_fix()
        omitted = self.chain.MIGRATION_PREFLIGHT_IDENTITY_FIX_PATHS[-1]
        self.commit_migration_preflight_identity_fix()
        git(self.root, "checkout", "HEAD^", "--", omitted)
        git(self.root, "add", omitted)
        git(self.root, "commit", "--amend", "--no-edit")
        identity = git(self.root, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "path set is not exact"):
            self.inspect(identity)

    def test_exact_service_status_image_fix_is_phase_transparent_and_cannot_repeat(self):
        self.set_migration()
        migration = self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        approval = self.commit_shared_migration_approval_fix()
        runtime = self.commit_migration_runtime_fix()
        admission = self.commit_migration_runtime_admission_fix()
        identity = self.commit_migration_preflight_identity_fix()
        self.set_services()
        services = self.commit(
            f"release(services): promote {self.candidate['release_id']} additive-services"
        )
        status_image = self.commit_service_status_image_fix()

        state = self.inspect(status_image)
        self.assertEqual("services", state["phase"])
        self.assertEqual(migration, state["migration_commit"])
        self.assertEqual(approval, state["shared_migration_approval_fix_commit"])
        self.assertEqual(runtime, state["migration_runtime_fix_commit"])
        self.assertEqual(admission, state["migration_runtime_admission_fix_commit"])
        self.assertEqual(identity, state["migration_preflight_identity_fix_commit"])
        self.assertEqual(services, state["services_commit"])
        self.assertEqual(status_image, state["service_status_image_fix_commit"])
        self.assertEqual(status_image, state["current_commit"])

        repeated = self.commit_service_status_image_fix(suffix="-repeated")
        with self.assertRaisesRegex(ValueError, "directly follow services"):
            self.inspect(repeated)

    def test_service_status_image_fix_requires_identity_fix_and_exact_paths(self):
        self.set_migration()
        self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.commit_shared_migration_approval_fix()
        self.commit_migration_runtime_fix()
        self.commit_migration_runtime_admission_fix()
        status_image = self.commit_service_status_image_fix()
        with self.assertRaisesRegex(ValueError, "directly follow services"):
            self.inspect(status_image)

        git(self.root, "reset", "--hard", "HEAD^")
        self.commit_migration_preflight_identity_fix()
        self.set_services()
        self.commit(
            f"release(services): promote {self.candidate['release_id']} additive-services"
        )
        omitted = self.chain.SERVICE_STATUS_IMAGE_FIX_PATHS[-1]
        self.commit_service_status_image_fix()
        git(self.root, "checkout", "HEAD^", "--", omitted)
        git(self.root, "add", omitted)
        git(self.root, "commit", "--amend", "--no-edit")
        status_image = git(self.root, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "path set is not exact"):
            self.inspect(status_image)

    def test_exact_service_source_status_fix_is_phase_transparent_and_cannot_repeat(self):
        self.set_migration()
        self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.commit_shared_migration_approval_fix()
        self.commit_migration_runtime_fix()
        self.commit_migration_runtime_admission_fix()
        self.commit_migration_preflight_identity_fix()
        self.set_services()
        services = self.commit(
            f"release(services): promote {self.candidate['release_id']} additive-services"
        )
        status_image = self.commit_service_status_image_fix()
        source_status = self.commit_service_source_status_fix()

        state = self.inspect(source_status)
        self.assertEqual("services", state["phase"])
        self.assertEqual(services, state["services_commit"])
        self.assertEqual(status_image, state["service_status_image_fix_commit"])
        self.assertEqual(source_status, state["service_source_status_fix_commit"])
        self.assertEqual(source_status, state["current_commit"])

        repeated = self.commit_service_source_status_fix(suffix="-repeated")
        with self.assertRaisesRegex(ValueError, "directly follow status image fix"):
            self.inspect(repeated)

    def test_service_source_status_fix_requires_status_fix_and_exact_paths(self):
        self.set_migration()
        self.commit(
            f"deploy(devpath-migration): {self.candidate['release_id']} sealed {self.release_hash}"
        )
        self.commit_shared_migration_approval_fix()
        self.commit_migration_runtime_fix()
        self.commit_migration_runtime_admission_fix()
        self.commit_migration_preflight_identity_fix()
        self.set_services()
        self.commit(
            f"release(services): promote {self.candidate['release_id']} additive-services"
        )
        source_status = self.commit_service_source_status_fix()
        with self.assertRaisesRegex(ValueError, "directly follow status image fix"):
            self.inspect(source_status)

        git(self.root, "reset", "--hard", "HEAD^")
        self.commit_service_status_image_fix()
        omitted = self.chain.SERVICE_SOURCE_STATUS_FIX_PATHS[-1]
        self.commit_service_source_status_fix()
        git(self.root, "checkout", "HEAD^", "--", omitted)
        git(self.root, "add", omitted)
        git(self.root, "commit", "--amend", "--no-edit")
        source_status = git(self.root, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "path set is not exact"):
            self.inspect(source_status)

    def test_exact_canary_runtime_form_fix_is_on_transparent_and_cannot_repeat(self):
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
        on = self.commit(
            f"release(web): promote {self.candidate['release_id']} mission-on"
        )
        runtime_form = self.commit_canary_runtime_form_fix()

        state = self.inspect(runtime_form)
        self.assertEqual("mission-on", state["phase"])
        self.assertNotEqual(on, runtime_form)
        self.assertEqual(runtime_form, state["on_commit"])
        self.assertEqual(runtime_form, state["canary_runtime_form_fix_commit"])
        self.assertEqual(runtime_form, state["current_commit"])

        repeated = self.commit_canary_runtime_form_fix(suffix="-repeated")
        with self.assertRaisesRegex(ValueError, "directly follow mission-ON"):
            self.inspect(repeated)

    def test_canary_runtime_form_fix_requires_on_and_exact_paths(self):
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
        runtime_form = self.commit_canary_runtime_form_fix()
        with self.assertRaisesRegex(ValueError, "directly follow mission-ON"):
            self.inspect(runtime_form)

        git(self.root, "reset", "--hard", "HEAD^")
        self.set_web("mission-on", "mission-off")
        self.commit(f"release(web): promote {self.candidate['release_id']} mission-on")
        omitted = self.chain.CANARY_RUNTIME_FORM_FIX_PATHS[-1]
        self.commit_canary_runtime_form_fix()
        git(self.root, "checkout", "HEAD^", "--", omitted)
        git(self.root, "add", omitted)
        git(self.root, "commit", "--amend", "--no-edit")
        runtime_form = git(self.root, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "path set is not exact"):
            self.inspect(runtime_form)

    def test_post_on_resume_fix_advances_exact_on_identity_and_cannot_repeat(self):
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
        self.commit(f"release(web): promote {self.candidate['release_id']} mission-on")
        runtime_form = self.commit_canary_runtime_form_fix()
        resume = self.commit_post_on_resume_fix()

        state = self.inspect(resume)
        self.assertEqual("mission-on", state["phase"])
        self.assertEqual(runtime_form, state["canary_runtime_form_fix_commit"])
        self.assertEqual(resume, state["post_on_resume_fix_commit"])
        self.assertEqual(resume, state["on_commit"])
        self.assertEqual(resume, state["current_commit"])

        omitted = self.chain.POST_ON_RESUME_FIX_PATHS[-1]
        git(self.root, "checkout", "HEAD^", "--", omitted)
        git(self.root, "add", omitted)
        git(self.root, "commit", "--amend", "--no-edit")
        malformed = git(self.root, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "path set is not exact"):
            self.inspect(malformed)

        git(self.root, "reset", "--hard", runtime_form)
        self.commit_post_on_resume_fix()
        repeated = self.commit_post_on_resume_fix(suffix="-repeated")
        with self.assertRaisesRegex(ValueError, "directly follow canary runtime form fix"):
            self.inspect(repeated)

    def test_web_applied_revision_fix_advances_on_identity_and_cannot_repeat(self):
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
        self.commit(f"release(web): promote {self.candidate['release_id']} mission-on")
        self.commit_canary_runtime_form_fix()
        resume = self.commit_post_on_resume_fix()
        applied = self.commit_web_applied_revision_fix()

        state = self.inspect(applied)
        self.assertEqual("mission-on", state["phase"])
        self.assertEqual(resume, state["post_on_resume_fix_commit"])
        self.assertEqual(applied, state["web_applied_revision_fix_commit"])
        self.assertEqual(applied, state["on_commit"])
        self.assertEqual(applied, state["current_commit"])

        repeated = self.commit_web_applied_revision_fix(suffix="-repeated")
        with self.assertRaisesRegex(ValueError, "directly follow post-ON resume fix"):
            self.inspect(repeated)

    def test_staging_context_auth_fix_advances_on_identity_and_cannot_repeat(self):
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
        self.commit(f"release(web): promote {self.candidate['release_id']} mission-on")
        self.commit_canary_runtime_form_fix()
        self.commit_post_on_resume_fix()
        applied = self.commit_web_applied_revision_fix()
        auth = self.commit_staging_context_auth_fix()

        state = self.inspect(auth)
        self.assertEqual("mission-on", state["phase"])
        self.assertEqual(applied, state["web_applied_revision_fix_commit"])
        self.assertEqual(auth, state["staging_context_auth_fix_commit"])
        self.assertEqual(auth, state["on_commit"])
        self.assertEqual(auth, state["current_commit"])

        repeated = self.commit_staging_context_auth_fix(suffix="-repeated")
        with self.assertRaisesRegex(ValueError, "directly follow web applied revision fix"):
            self.inspect(repeated)

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
