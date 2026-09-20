import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-auth-smoke.yml"

MAIN_SHA = "4f3ed64b2a148394eb0b8b3f5311e327f0edd759"
TARGET_SHA = "69e7bd15570f5ba0f271c83b5bd46955cb249c8e"
TARGET_TREE = "7799cc07f3083a002d0e2064db5437e2cde46f84"
HELPER_BRANCH = "chore/s2a-mobile-free-contract-publish-20260920"
TARGET_BRANCH = "fix/s2a-mobile-free-contract-main-20260920"
ENVIRONMENT = "mission-spine-production-off"
PINNED_ACTION = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}")
LINE_CONTINUATION = re.compile(r"\\\n[ \t]*")

STEP_CONTRACT_TEST = "Run the publisher contract test on the exact helper bytes"
STEP_LIVE_ENVIRONMENT = "Authenticate the live protected environment and this approval"
STEP_TARGET_TEST = "Test the exact S2a target"
STEP_MINT = "Mint the production-scoped release App token"
STEP_PUSH = "Fast-forward protected main to the exact tested S2a target"


class S2aMainPublishTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.document = yaml.safe_load(cls.text)
        cls.job = cls.document["jobs"]["publish"]
        cls.steps = cls.job["steps"]
        cls.names = [step.get("name") for step in cls.steps]

    def _run(self, name: str) -> str:
        matches = [step for step in self.steps if step.get("name") == name]
        self.assertEqual(1, len(matches), name)
        return matches[0]["run"]

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
            {"group": "s2a-mobile-free-contract-main-publish", "cancel-in-progress": False},
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
        self.assertEqual(TARGET_SHA, env["TARGET_SHA"])
        self.assertEqual(TARGET_TREE, env["TARGET_TREE"])
        self.assertEqual(HELPER_BRANCH, env["HELPER_BRANCH"])
        self.assertEqual(TARGET_BRANCH, env["TARGET_BRANCH"])
        self.assertEqual(ENVIRONMENT, env["PROTECTED_ENVIRONMENT"])
        self.assertEqual("VelkaressiaBlutkrone", env["APPROVER_LOGIN"])

    def test_every_action_is_pinned_to_a_full_sha(self) -> None:
        used = [step["uses"] for step in self.steps if "uses" in step]
        self.assertTrue(used)
        for reference in used:
            self.assertIsNotNone(PINNED_ACTION.fullmatch(reference), reference)

    def test_helper_context_is_bot_dispatched_attempt_one(self) -> None:
        run = self._run("Prove the exact one-shot helper context")
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
            'test "${helper_paths[1]}" = "tests/release/test_s2a_main_publish.py"',
            'test "$current_main" = "$MAIN_SHA"',
        ):
            self.assertIn(fragment, run)
        self.assertNotIn("VelkaressiaBlutkrone", run)

    def test_target_is_pinned_by_tree_not_by_a_path_list(self) -> None:
        run = self._run(STEP_TARGET_TEST)
        for fragment in (
            'test "$(git rev-parse HEAD)" = "$TARGET_SHA"',
            'test "$(git rev-list --parents -n 1 HEAD)" = "$TARGET_SHA $MAIN_SHA"',
            "test \"$(git rev-parse 'HEAD^{tree}')\" = \"$TARGET_TREE\"",
            "release: promote the mobile-free release contract and the pinned ET13 catalog to main",
            "test \"$(git show -s --format=%an HEAD)\" = 'devpath-gitops-release[bot]'",
            "test \"$(git show -s --format=%cn HEAD)\" = 'devpath-gitops-release[bot]'",
            "release-manifests/*|scripts/release/*|tests/release/*) ;;",
            '*) echo "unexpected target path: $target_path" >&2; exit 1 ;;',
            'git diff --check "$MAIN_SHA" "$TARGET_SHA"',
            "python -m unittest discover -s tests/release -p 'test_*.py'",
        ):
            self.assertIn(fragment, run)
        self.assertEqual(
            2, run.count("244265210+devpath-gitops-release[bot]@users.noreply.github.com")
        )
        self.assertNotIn("--name-status", self.text)

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
        for gate in (
            "Prove the exact one-shot helper context",
            STEP_CONTRACT_TEST,
            STEP_LIVE_ENVIRONMENT,
            STEP_TARGET_TEST,
        ):
            self.assertLess(self.names.index(gate), mint, gate)
        self.assertLess(mint, self.names.index(STEP_PUSH))
        self.assertIn(
            "python -m unittest discover -s tests/release -p 'test_s2a_main_publish.py'",
            self._run(STEP_CONTRACT_TEST),
        )
        for step in self.steps[:mint]:
            self.assertNotIn("app_token", str(step), step.get("name"))

    def test_only_the_release_app_fast_forwards_main_exactly_once(self) -> None:
        mint = self.steps[self.names.index(STEP_MINT)]
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
            self._run("Prove the exact one-shot helper context"),
        )
        self.assertIn(
            'target_listing="$(git diff-tree --no-commit-id --name-only -r HEAD)"',
            self._run(STEP_TARGET_TEST),
        )

    def test_the_promotion_chain_is_deliberately_absent(self) -> None:
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
        run = self._run("Re-authenticate the published main")
        self.assertIn('test "$(git -C gitops-main rev-parse origin/main)" = "$TARGET_SHA"', run)
        self.assertIn(
            "test \"$(git -C gitops-main rev-parse 'origin/main^{tree}')\" = \"$TARGET_TREE\"",
            run,
        )
        self.assertEqual("Re-authenticate the published main", self.names[-1])


if __name__ == "__main__":
    unittest.main()
