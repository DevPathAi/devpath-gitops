import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-auth-smoke.yml"

MAIN_SHA = "c1d5e8cf197c7dbcb0d5f224011b82b73412e17a"
FENCE_BASE_SHA = "69e7bd15570f5ba0f271c83b5bd46955cb249c8e"
TARGET_SHA = "fcf97cf686df8e8bad56597d4679f9a96fd597fc"
TARGET_TREE = "a1c43f95c1332611e0f32066b51aa25090e98b5a"
HELPER_BRANCH = "chore/r2-writer-fence-removal-publish-20260921"
TARGET_BRANCH = "fix/r2-writer-fence-removal-main-20260921"
ENVIRONMENT = "mission-spine-production-off"
CONTRACT_TEST = "test_r2_unfence_main_publish.py"
PLATFORM_PATH = "apps/devpath-platform-svc/base/kustomization.yaml"
SANDBOX_PATH = "apps/devpath-sandbox-svc/base/kustomization.yaml"
MIGRATION_PATH = "apps/devpath-migration/base/kustomization.yaml"
SUBJECT = "release: remove the abandoned ms-20260920-community-flat-pages-r2 writer fence from main"
PINNED_ACTION = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}")
LINE_CONTINUATION = re.compile(r"\\\n[ \t]*")

STEP_CONTEXT = "Prove the exact one-shot helper context"
STEP_CONTRACT_TEST = "Run the publisher contract test on the exact helper bytes"
STEP_LIVE_ENVIRONMENT = "Authenticate the live protected environment and this approval"
STEP_TARGET_CHECKOUT = "Checkout the exact tested fence-removal target"
STEP_TARGET_TEST = "Test the exact fence-removal target"
STEP_MINT = "Mint the production-scoped release App token"
STEP_PUSH = "Fast-forward protected main to the exact tested fence-removal target"
STEP_REAUTH = "Re-authenticate the published main"


class R2UnfenceMainPublishTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.document = yaml.safe_load(cls.text)
        cls.job = cls.document["jobs"]["publish"]
        cls.steps = cls.job["steps"]
        cls.names = [step.get("name") for step in cls.steps]

    def _step(self, name: str) -> dict:
        matches = [step for step in self.steps if step.get("name") == name]
        self.assertEqual(1, len(matches), name)
        return matches[0]

    def _run(self, name: str) -> str:
        return self._step(name)["run"]

    def test_trigger_is_the_single_boolean_dispatch(self) -> None:
        # PyYAML reads the bare key `on` as the boolean True.
        trigger = self.document.get("on", self.document.get(True))
        self.assertEqual(["workflow_dispatch"], list(trigger))
        self.assertEqual(["full"], list(trigger["workflow_dispatch"]["inputs"]))
        full = trigger["workflow_dispatch"]["inputs"]["full"]
        self.assertEqual("boolean", full["type"])
        self.assertIs(True, full["required"])
        self.assertIs(False, full["default"])

    def test_job_is_the_only_job_and_is_fenced(self) -> None:
        self.assertEqual(["publish"], list(self.document["jobs"]))
        self.assertEqual({"contents": "read"}, self.document["permissions"])
        self.assertEqual(
            {"actions": "read", "contents": "read", "deployments": "write"},
            self.job["permissions"],
        )
        self.assertEqual(ENVIRONMENT, self.job["environment"])
        self.assertEqual(
            {"group": "r2-writer-fence-removal-main-publish", "cancel-in-progress": False},
            self.document["concurrency"],
        )
        self.assertEqual(
            f"github.ref == 'refs/heads/{HELPER_BRANCH}' && inputs.full",
            " ".join(self.job["if"].split()),
        )

    def test_exact_coordinates_are_pinned(self) -> None:
        env = self.job["env"]
        self.assertEqual(MAIN_SHA, env["MAIN_SHA"])
        self.assertEqual(MAIN_SHA, env["HELPER_BASE_SHA"])
        self.assertEqual(FENCE_BASE_SHA, env["FENCE_BASE_SHA"])
        self.assertEqual(TARGET_SHA, env["TARGET_SHA"])
        self.assertEqual(TARGET_TREE, env["TARGET_TREE"])
        self.assertEqual(HELPER_BRANCH, env["HELPER_BRANCH"])
        self.assertEqual(TARGET_BRANCH, env["TARGET_BRANCH"])
        self.assertEqual(ENVIRONMENT, env["PROTECTED_ENVIRONMENT"])
        self.assertEqual("VelkaressiaBlutkrone", env["APPROVER_LOGIN"])
        self.assertEqual(3, len({MAIN_SHA, FENCE_BASE_SHA, TARGET_SHA}))

    def test_no_step_shadows_a_pinned_coordinate(self) -> None:
        # A step-level `env` wins over the job-level `env`: one line on the push step would
        # retarget the fast-forward after every gate had passed on the job-level value.
        pins = set(self.job["env"])
        self.assertTrue({"MAIN_SHA", "TARGET_SHA", "TARGET_TREE", "FENCE_BASE_SHA"} <= pins)
        for step in self.steps:
            shadowed = pins & set(step.get("env", {}))
            self.assertEqual(set(), shadowed, step.get("name"))
        self.assertNotIn("env", {key for key in self.document if key not in ("jobs",)})

    def test_every_run_block_starts_in_strict_mode(self) -> None:
        # Without an explicit `shell:` the runner uses `bash -e {0}` - no pipefail, no nounset.
        runs = [step for step in self.steps if "run" in step]
        self.assertTrue(runs)
        for step in runs:
            self.assertNotIn("shell", step, step.get("name"))
            self.assertEqual(
                "set -euo pipefail", step["run"].splitlines()[0].strip(), step.get("name")
            )
            for weakening in ("set +e", "set +u", "set +o pipefail", "|| true"):
                self.assertNotIn(weakening, step["run"], step.get("name"))

    def test_every_action_is_pinned_to_a_full_sha(self) -> None:
        used = [step["uses"] for step in self.steps if "uses" in step]
        self.assertTrue(used)
        for reference in used:
            self.assertIsNotNone(PINNED_ACTION.fullmatch(reference), reference)

    def test_helper_context_is_bot_dispatched_attempt_one(self) -> None:
        run = self._run(STEP_CONTEXT)
        for fragment in (
            'test "$GITHUB_ACTOR" = "github-actions[bot]"',
            'test "$GITHUB_TRIGGERING_ACTOR" = "github-actions[bot]"',
            'test "$GITHUB_EVENT_NAME" = "workflow_dispatch"',
            'test "$GITHUB_RUN_ATTEMPT" = "1"',
            'test "$GITHUB_REF" = "refs/heads/$HELPER_BRANCH"',
            'test "$INPUT_FULL" = "true"',
            'test "$MAIN_SHA" = "$HELPER_BASE_SHA"',
            'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
            'test "$(git rev-parse HEAD^)" = "$HELPER_BASE_SHA"',
            'test "${#helper_paths[@]}" -eq 2',
            'test "${helper_paths[0]}" = ".github/workflows/mission-spine-auth-smoke.yml"',
            f'test "${{helper_paths[1]}}" = "tests/release/{CONTRACT_TEST}"',
            'test "$current_main" = "$MAIN_SHA"',
        ):
            self.assertIn(fragment, run)
        self.assertNotIn("VelkaressiaBlutkrone", run)

    def test_target_changes_exactly_the_two_writer_kustomizations(self) -> None:
        # The grandparent (the pre-fence base) must be present to compare blobs against.
        self.assertEqual(3, self._step(STEP_TARGET_CHECKOUT)["with"]["fetch-depth"])
        run = self._run(STEP_TARGET_TEST)
        for fragment in (
            'test "$(git rev-parse HEAD)" = "$TARGET_SHA"',
            'test "$(git rev-list --parents -n 1 HEAD)" = "$TARGET_SHA $MAIN_SHA"',
            'test "$(git rev-list --parents -n 1 HEAD^)" = "$MAIN_SHA $FENCE_BASE_SHA"',
            "test \"$(git rev-parse 'HEAD^{tree}')\" = \"$TARGET_TREE\"",
            SUBJECT,
            "test \"$(git show -s --format=%an HEAD)\" = 'devpath-gitops-release[bot]'",
            "test \"$(git show -s --format=%cn HEAD)\" = 'devpath-gitops-release[bot]'",
            'target_listing="$(git diff-tree --no-commit-id --name-status -r HEAD)"',
            'test "${#target_rows[@]}" -eq 2',
            f"test \"${{target_rows[0]}}\" = $'M\\t{PLATFORM_PATH}'",
            f"test \"${{target_rows[1]}}\" = $'M\\t{SANDBOX_PATH}'",
            'git diff --check "$MAIN_SHA" "$TARGET_SHA"',
            "python -m unittest discover -s tests/release -p 'test_*.py'",
        ):
            self.assertIn(fragment, run)
        self.assertEqual(
            2, run.count("244265210+devpath-gitops-release[bot]@users.noreply.github.com")
        )

    def test_writer_blobs_return_to_the_pre_fence_base_and_cannot_compare_empty(self) -> None:
        run = self._run(STEP_TARGET_TEST)
        for fragment in (
            f"for writer_path in {PLATFORM_PATH} {SANDBOX_PATH}; do",
            'target_blob="$(git rev-parse "HEAD:$writer_path")"',
            'base_blob="$(git rev-parse "$FENCE_BASE_SHA:$writer_path")"',
            'test -n "$target_blob"',
            'test -n "$base_blob"',
            'test "$target_blob" = "$base_blob"',
            "if grep -q '^replicas' \"$writer_path\"; then",
            f'migration_target="$(git rev-parse "HEAD:{MIGRATION_PATH}")"',
            f'migration_main="$(git rev-parse "$MAIN_SHA:{MIGRATION_PATH}")"',
            'test -n "$migration_target"',
            'test "$migration_target" = "$migration_main"',
        ):
            self.assertIn(fragment, run)
        # A blob comparison written as `test "$(a)" = "$(b)"` passes when both commands fail.
        self.assertIsNone(re.search(r'test "\$\(git rev-parse "[^"]*:[^"]*"\)" = "\$\(', run))
        # `! grep` does not trip `set -e`; the refusal must be an explicit branch.
        self.assertNotIn("! grep", run)

    def test_live_environment_and_approval_are_authenticated(self) -> None:
        run = self._run(STEP_LIVE_ENVIRONMENT)
        for fragment in (
            "\"repos/$GITHUB_REPOSITORY/environments/$PROTECTED_ENVIRONMENT\"",
            "'.can_admins_bypass' <<<\"$environment_json\")\" = \"false\"",
            "| .prevent_self_review' <<<\"$environment_json\")\" = \"true\"",
            "= \"User:$APPROVER_LOGIN\"",
            "deployment-branch-policies?per_page=100",
            "'.total_count' <<<\"$policies_json\")\" = \"1\"",
            "= \"branch:main\"",
            "\"repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID/approvals\"",
            "'length' <<<\"$approvals_json\")\" = \"1\"",
            "'.[0].state' <<<\"$approvals_json\")\" = \"approved\"",
            "'.[0].user.login' <<<\"$approvals_json\")\" = \"$APPROVER_LOGIN\"",
        ):
            self.assertIn(fragment, run)

    def test_every_gate_precedes_the_app_token(self) -> None:
        mint = self.names.index(STEP_MINT)
        for gate in (STEP_CONTEXT, STEP_CONTRACT_TEST, STEP_LIVE_ENVIRONMENT, STEP_TARGET_TEST):
            self.assertLess(self.names.index(gate), mint, gate)
        self.assertLess(mint, self.names.index(STEP_PUSH))
        self.assertIn(
            f"python -m unittest discover -s tests/release -p '{CONTRACT_TEST}'",
            self._run(STEP_CONTRACT_TEST),
        )
        for step in self.steps[:mint]:
            self.assertNotIn("app_token", str(step), step.get("name"))

    def test_only_the_release_app_fast_forwards_main_exactly_once(self) -> None:
        mint = self._step(STEP_MINT)
        self.assertEqual("read", mint["with"]["permission-administration"])
        self.assertEqual("write", mint["with"]["permission-contents"])
        self.assertEqual("DevPathAi", mint["with"]["owner"])
        # Fold shell line continuations first: `git ... \` + `push ...` is ONE command.
        shell = "\n".join(
            LINE_CONTINUATION.sub(" ", step["run"]) for step in self.steps if "run" in step
        )
        pushes = [line.strip() for line in shell.splitlines() if re.search(r"\bpush\b", line)]
        self.assertEqual(
            ['git -C gitops-main push origin "$TARGET_SHA:refs/heads/main"'], pushes
        )
        run = self._run(STEP_PUSH)
        push_at = run.index("git -C gitops-main push origin")
        self.assertLess(run.index("verify_gitops_write_authority.py"), push_at)
        self.assertLess(
            run.index('test "$(git -C gitops-main rev-parse origin/main)" = "$MAIN_SHA"'),
            push_at,
        )
        self.assertLess(
            push_at,
            run.index('test "$(git -C gitops-main rev-parse origin/main)" = "$TARGET_SHA"'),
        )
        self.assertEqual(2, self.text.count("verify_gitops_write_authority.py"))
        for forbidden in ("--force", "+$TARGET_SHA", "+refs", "--mirror", "--delete"):
            self.assertNotIn(forbidden, self.text)

    def test_token_bearing_steps_have_no_other_write_path(self) -> None:
        # The App token can write any ref through the API, not only through `git push`.
        mint = self.names.index(STEP_MINT)
        for step in self.steps[mint:]:
            run = LINE_CONTINUATION.sub(" ", step.get("run", ""))
            for pattern in (r"\bgh\b", r"\bcurl\b", r"\bwget\b", r"git/refs", r"update-ref"):
                self.assertIsNone(re.search(pattern, run), (step.get("name"), pattern))
        # Pinned coordinates live in the job `env`; no step may rewrite them mid-job.
        for forbidden in ("GITHUB_ENV", "GITHUB_PATH", "BASH_ENV"):
            self.assertNotIn(forbidden, self.text)

    def test_listings_cannot_fail_silently(self) -> None:
        # `mapfile < <(cmd)` hides cmd's exit status from `set -e`; an assignment does not.
        self.assertNotIn("< <(", self.text)
        self.assertIn(
            'helper_listing="$(git diff-tree --no-commit-id --name-only -r HEAD)"',
            self._run(STEP_CONTEXT),
        )
        self.assertIn('mapfile -t target_rows <<<"$target_listing"', self._run(STEP_TARGET_TEST))

    def test_the_promotion_chain_is_deliberately_absent(self) -> None:
        # The r2 chain is abandoned on purpose; its verifiers would refuse this unregistered subject.
        for forbidden in (
            "verify_promotion_chain",
            "verify_migration_result",
            "SEALED_SHA",
            "sealed-release",
            "RELEASE_EVIDENCE_TOKEN",
            "RELEASE_ID",
        ):
            self.assertNotIn(forbidden, self.text)

    def test_published_main_is_re_authenticated(self) -> None:
        run = self._run(STEP_REAUTH)
        self.assertIn('test "$(git -C gitops-main rev-parse origin/main)" = "$TARGET_SHA"', run)
        self.assertIn(
            "test \"$(git -C gitops-main rev-parse 'origin/main^{tree}')\" = \"$TARGET_TREE\"",
            run,
        )
        self.assertEqual(STEP_REAUTH, self.names[-1])


if __name__ == "__main__":
    unittest.main()
