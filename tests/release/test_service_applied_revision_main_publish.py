import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-auth-smoke.yml"

MAIN_SHA = "8b036dce5c410191c99b6334b288cccd104a0b34"
TARGET_SHA = "8b01c02dd1d1e1a3d400a2536e7b7f23227bd264"
TARGET_TREE = "b38a6ebf15c9949431754eaa7facd94be4d415cd"
HELPER_BRANCH = "chore/service-applied-revision-publish-20260924"
TARGET_BRANCH = "fix/service-applied-revision-main-20260924"
ENVIRONMENT = "mission-spine-production-off"
CONTRACT_TEST = "test_service_applied_revision_main_publish.py"
TARGET_ROWS = (
    ("M", "scripts/release/build_production_canary.py"),
    ("M", "scripts/release/promote_service_digests.py"),
    ("M", "scripts/release/verify_promotion_chain.py"),
    ("M", "scripts/release/verify_promotion_evidence.py"),
    ("M", "scripts/release/wait_release_rollouts.py"),
    ("M", "tests/release/test_kubernetes_release_runtime.py"),
    ("M", "tests/release/test_production_canary.py"),
    ("M", "tests/release/test_promotion_chain.py"),
    ("M", "tests/release/test_promotion_evidence.py"),
    ("M", "tests/release/test_service_promotion.py"),
)
SUBJECT = "fix(release): bind service applied revision to the app base"
PINNED_ACTION = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}")
LINE_CONTINUATION = re.compile(r"\\\n[ \t]*")

STEP_CONTEXT = "Prove the exact one-shot helper context"
STEP_CONTRACT_TEST = "Run the publisher contract test on the exact helper bytes"
STEP_LIVE_ENVIRONMENT = "Authenticate the live protected environment and this approval"
STEP_TARGET_CHECKOUT = "Checkout the exact tested service-applied-revision target"
STEP_TARGET_TEST = "Test the exact service-applied-revision target"
STEP_MINT = "Mint the production-scoped release App token"
STEP_PUSH = "Fast-forward protected main to the exact tested service-applied-revision target"
STEP_REAUTH = "Re-authenticate the published main"

# 2026-09-23 independent review, F1/F2: the literals below are the reviewed workflow bytes.
STEP_NAMES = [
    'Checkout the exact helper branch',
    'Prove the exact one-shot helper context',
    None,
    'Run the publisher contract test on the exact helper bytes',
    'Authenticate the live protected environment and this approval',
    'Checkout the exact tested service-applied-revision target',
    None,
    'Test the exact service-applied-revision target',
    'Mint the production-scoped release App token',
    'Checkout current protected main with release App authority',
    'Authenticate authority and the target graph',
    'Fast-forward protected main to the exact tested service-applied-revision target',
    'Re-authenticate the published main',
]
PINNED_ACTIONS = [
    'actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803',
    'actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1',
    'actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1',
    'azure/setup-kubectl@829323503d1be3d00ca8346e5391ca0b07a9ab0d',
]
APP_TOKEN_REFERENCES = 7
SHELL_ESCAPE = re.compile(
    r"(eval|source|\.|export|read|printf -v|declare|typeset|local|unset|trap|exec|alias|function)(\s|$)"
    r"|(export\s+)?(MAIN_SHA|TARGET_SHA|TARGET_TREE|HELPER_BASE_SHA|HELPER_BRANCH|TARGET_BRANCH"
    r"|PROTECTED_ENVIRONMENT|APPROVER_LOGIN|GITHUB_[A-Z_]+)="
)
GATE_BODIES = {
    'Prove the exact one-shot helper context': [
        'set -euo pipefail',
        'test "$GITHUB_ACTOR" = "github-actions[bot]"',
        'test "$GITHUB_TRIGGERING_ACTOR" = "github-actions[bot]"',
        'test "$GITHUB_EVENT_NAME" = "workflow_dispatch"',
        'test "$GITHUB_RUN_ATTEMPT" = "1"',
        'test "$GITHUB_REF" = "refs/heads/$HELPER_BRANCH"',
        'test "$GITHUB_REF_NAME" = "$HELPER_BRANCH"',
        'test "$INPUT_FULL" = "true"',
        'test "$MAIN_SHA" = "$HELPER_BASE_SHA"',
        'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
        'test "$(git rev-parse HEAD^)" = "$HELPER_BASE_SHA"',
        'helper_listing="$(git diff-tree --no-commit-id --name-only -r HEAD)"',
        'mapfile -t helper_paths <<<"$helper_listing"',
        'test "${#helper_paths[@]}" -eq 2',
        'test "${helper_paths[0]}" = ".github/workflows/mission-spine-auth-smoke.yml"',
        'test "${helper_paths[1]}" = "tests/release/test_service_applied_revision_main_publish.py"',
        'current_main="$(',
        'gh api -H "X-GitHub-Api-Version: $GITHUB_API_VERSION" "repos/$GITHUB_REPOSITORY/branches/main" --jq \'.commit.sha\'',
        ')"',
        'test "$current_main" = "$MAIN_SHA"',
    ],
    'Run the publisher contract test on the exact helper bytes': [
        'set -euo pipefail',
        'python -m pip install --disable-pip-version-check PyYAML==6.0.2',
        "python -m unittest discover -s tests/release -p 'test_service_applied_revision_main_publish.py'",
    ],
    'Authenticate the live protected environment and this approval': [
        'set -euo pipefail',
        'environment_json="$(',
        'gh api -H "X-GitHub-Api-Version: $GITHUB_API_VERSION" "repos/$GITHUB_REPOSITORY/environments/$PROTECTED_ENVIRONMENT"',
        ')"',
        'test "$(jq -r \'.name\' <<<"$environment_json")" = "$PROTECTED_ENVIRONMENT"',
        'test "$(jq -r \'.can_admins_bypass\' <<<"$environment_json")" = "false"',
        'test "$(jq -r \'.deployment_branch_policy.protected_branches\' <<<"$environment_json")" = "false"',
        'test "$(jq -r \'.deployment_branch_policy.custom_branch_policies\' <<<"$environment_json")" = "true"',
        'test "$(jq -r \'[.protection_rules[] | select(.type == "required_reviewers")] | length\' <<<"$environment_json")" = "1"',
        'test "$(jq -r \'.protection_rules[] | select(.type == "required_reviewers") | .prevent_self_review\' <<<"$environment_json")" = "true"',
        'test "$(jq -r \'[.protection_rules[] | select(.type == "required_reviewers") | .reviewers[] | "\\(.type):\\(.reviewer.login)"] | join(",")\' <<<"$environment_json")" = "User:$APPROVER_LOGIN"',
        'policies_json="$(',
        'gh api -H "X-GitHub-Api-Version: $GITHUB_API_VERSION" "repos/$GITHUB_REPOSITORY/environments/$PROTECTED_ENVIRONMENT/deployment-branch-policies?per_page=100"',
        ')"',
        'test "$(jq -r \'.total_count\' <<<"$policies_json")" = "1"',
        'test "$(jq -r \'[.branch_policies[] | "\\(.type):\\(.name)"] | join(",")\' <<<"$policies_json")" = "branch:main"',
        'approvals_json="$(',
        'gh api -H "X-GitHub-Api-Version: $GITHUB_API_VERSION" "repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID/approvals"',
        ')"',
        'test "$(jq -r \'length\' <<<"$approvals_json")" = "1"',
        'test "$(jq -r \'.[0].state\' <<<"$approvals_json")" = "approved"',
        'test "$(jq -r \'.[0].user.login\' <<<"$approvals_json")" = "$APPROVER_LOGIN"',
        'test "$(jq -r \'[.[0].environments[].name] | join(",")\' <<<"$approvals_json")" = "$PROTECTED_ENVIRONMENT"',
    ],
    'Test the exact service-applied-revision target': [
        'set -euo pipefail',
        'test "$(git rev-parse HEAD)" = "$TARGET_SHA"',
        'test "$(git rev-list --parents -n 1 HEAD)" = "$TARGET_SHA $MAIN_SHA"',
        'test "$(git rev-parse \'HEAD^{tree}\')" = "$TARGET_TREE"',
        'test "$(git show -s --format=%s HEAD)" = \'fix(release): bind service applied revision to the app base\'',
        'test "$(git show -s --format=%an HEAD)" = \'devpath-gitops-release[bot]\'',
        'test "$(git show -s --format=%cn HEAD)" = \'devpath-gitops-release[bot]\'',
        'test "$(git show -s --format=%ae HEAD)" = \'244265210+devpath-gitops-release[bot]@users.noreply.github.com\'',
        'test "$(git show -s --format=%ce HEAD)" = \'244265210+devpath-gitops-release[bot]@users.noreply.github.com\'',
        'target_listing="$(git diff-tree --no-commit-id --name-status -r HEAD)"',
        'test -n "$target_listing"',
        'expected_listing="$(printf \'%s\\n\' $\'M\\tscripts/release/build_production_canary.py\' $\'M\\tscripts/release/promote_service_digests.py\' $\'M\\tscripts/release/verify_promotion_chain.py\' $\'M\\tscripts/release/verify_promotion_evidence.py\' $\'M\\tscripts/release/wait_release_rollouts.py\' $\'M\\ttests/release/test_kubernetes_release_runtime.py\' $\'M\\ttests/release/test_production_canary.py\' $\'M\\ttests/release/test_promotion_chain.py\' $\'M\\ttests/release/test_promotion_evidence.py\' $\'M\\ttests/release/test_service_promotion.py\')"',
        'test -n "$expected_listing"',
        'test "$target_listing" = "$expected_listing"',
        'mapfile -t target_rows <<<"$target_listing"',
        'test "${#target_rows[@]}" -eq 10',
        'git diff --check "$MAIN_SHA" "$TARGET_SHA"',
        'python -m pip install --disable-pip-version-check jsonschema==4.25.1 PyYAML==6.0.2',
        "python -m unittest discover -s tests/release -p 'test_*.py'",
    ],
}
TOKEN_BEARING_STEPS = r"""
- name: Mint the production-scoped release App token
  id: app_token
  uses: actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1 # v3.2.0
  with:
    app-id: ${{ secrets.GITOPS_RELEASE_APP_ID }}
    private-key: ${{ secrets.GITOPS_RELEASE_APP_PRIVATE_KEY }}
    owner: DevPathAi
    permission-administration: read
    permission-contents: write

- name: Checkout current protected main with release App authority
  uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
  with:
    repository: DevPathAi/devpath-gitops
    ref: main
    token: ${{ steps.app_token.outputs.token }}
    fetch-depth: 0
    persist-credentials: true
    path: gitops-main

- name: Authenticate authority and the target graph
  env:
    GH_TOKEN: ${{ steps.app_token.outputs.token }}
    APP_ID: ${{ secrets.GITOPS_RELEASE_APP_ID }}
    APP_SLUG: ${{ steps.app_token.outputs.app-slug }}
    INSTALLATION_ID: ${{ steps.app_token.outputs.installation-id }}
  run: |
    set -euo pipefail
    python target/scripts/release/verify_gitops_write_authority.py \
      --expected-app-id "$APP_ID" --app-slug "$APP_SLUG" \
      --installation-id "$INSTALLATION_ID"
    test "$(git -C target rev-parse HEAD)" = "$TARGET_SHA"
    test "$(git -C gitops-main rev-parse HEAD)" = "$MAIN_SHA"
    git -C gitops-main fetch --no-tags origin \
      "$TARGET_BRANCH:refs/remotes/origin/$TARGET_BRANCH"
    test "$(git -C gitops-main rev-parse "refs/remotes/origin/$TARGET_BRANCH")" = "$TARGET_SHA"
    test "$(git -C gitops-main rev-parse "$TARGET_SHA^")" = "$MAIN_SHA"
    test "$(git -C gitops-main rev-parse "$TARGET_SHA^{tree}")" = "$TARGET_TREE"

- name: Fast-forward protected main to the exact tested service-applied-revision target
  env:
    GH_TOKEN: ${{ steps.app_token.outputs.token }}
    APP_ID: ${{ secrets.GITOPS_RELEASE_APP_ID }}
    APP_SLUG: ${{ steps.app_token.outputs.app-slug }}
    INSTALLATION_ID: ${{ steps.app_token.outputs.installation-id }}
  run: |
    set -euo pipefail
    python target/scripts/release/verify_gitops_write_authority.py \
      --expected-app-id "$APP_ID" --app-slug "$APP_SLUG" \
      --installation-id "$INSTALLATION_ID"
    git -C gitops-main fetch --no-tags origin main:refs/remotes/origin/main
    test "$(git -C gitops-main rev-parse origin/main)" = "$MAIN_SHA"
    git -C gitops-main push origin "$TARGET_SHA:refs/heads/main"
    git -C gitops-main fetch --no-tags origin main:refs/remotes/origin/main
    test "$(git -C gitops-main rev-parse origin/main)" = "$TARGET_SHA"

- name: Re-authenticate the published main
  run: |
    set -euo pipefail
    git -C gitops-main fetch --no-tags origin main:refs/remotes/origin/main
    test "$(git -C gitops-main rev-parse origin/main)" = "$TARGET_SHA"
    test "$(git -C gitops-main rev-parse 'origin/main^{tree}')" = "$TARGET_TREE"
"""


class ServiceAppliedRevisionMainPublishTest(unittest.TestCase):
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
            {"group": "service-applied-revision-main-publish", "cancel-in-progress": False},
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
        self.assertEqual(2, len({MAIN_SHA, TARGET_SHA}))
        self.assertNotIn("FENCE_BASE_SHA", env)

    def test_no_step_shadows_a_pinned_coordinate(self) -> None:
        # A step-level `env` wins over the job-level `env`: one line on the push step would
        # retarget the fast-forward after every gate had passed on the job-level value.
        pins = set(self.job["env"])
        self.assertTrue({"MAIN_SHA", "TARGET_SHA", "TARGET_TREE", "HELPER_BASE_SHA"} <= pins)
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
            for weakening in (
                "set +e", "set +u", "set +o pipefail", "|| true", "|| :", "||:", "true ||", ":||"
            ):
                self.assertNotIn(weakening, step["run"], step.get("name"))
            # F2: no builtin that rewrites variables or sources code, no pin reassigned mid-step.
            for line in step["run"].splitlines():
                self.assertIsNone(SHELL_ESCAPE.match(line.strip()), (step.get("name"), line))

    def test_every_action_is_pinned_to_a_full_sha(self) -> None:
        used = [step["uses"] for step in self.steps if "uses" in step]
        self.assertTrue(used)
        for reference in used:
            self.assertIsNotNone(PINNED_ACTION.fullmatch(reference), reference)
        # F1: the action set is exact - no other action may receive the App token.
        self.assertEqual(PINNED_ACTIONS, sorted(set(used)))
        self.assertEqual(STEP_NAMES, self.names)

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

    def test_target_changes_exactly_the_twelve_pinned_paths(self) -> None:
        # Only the parent is needed: the target is the single child of the completed release.
        self.assertEqual(2, self._step(STEP_TARGET_CHECKOUT)["with"]["fetch-depth"])
        run = self._run(STEP_TARGET_TEST)
        for fragment in (
            'test "$(git rev-parse HEAD)" = "$TARGET_SHA"',
            'test "$(git rev-list --parents -n 1 HEAD)" = "$TARGET_SHA $MAIN_SHA"',
            "test \"$(git rev-parse 'HEAD^{tree}')\" = \"$TARGET_TREE\"",
            SUBJECT,
            "test \"$(git show -s --format=%an HEAD)\" = 'devpath-gitops-release[bot]'",
            "test \"$(git show -s --format=%cn HEAD)\" = 'devpath-gitops-release[bot]'",
            'git diff --check "$MAIN_SHA" "$TARGET_SHA"',
            "python -m unittest discover -s tests/release -p 'test_*.py'",
        ):
            self.assertIn(fragment, run)
        self.assertEqual(
            2, run.count("244265210+devpath-gitops-release[bot]@users.noreply.github.com")
        )

    def test_target_listing_is_compared_whole_and_cannot_compare_empty(self) -> None:
        run = LINE_CONTINUATION.sub(" ", self._run(STEP_TARGET_TEST))
        rows = " ".join(f"$'{status}\\t{path}'" for status, path in TARGET_ROWS)
        self.assertEqual(10, len(TARGET_ROWS))
        self.assertEqual(sorted(path for _, path in TARGET_ROWS), [path for _, path in TARGET_ROWS])
        expected = [
            'target_listing="$(git diff-tree --no-commit-id --name-status -r HEAD)"',
            'test -n "$target_listing"',
            f"expected_listing=\"$(printf '%s\\n' {rows})\"",
            'test -n "$expected_listing"',
            'test "$target_listing" = "$expected_listing"',
            'mapfile -t target_rows <<<"$target_listing"',
            'test "${#target_rows[@]}" -eq 10',
        ]
        # Folding a continuation leaves two spaces; the rows hold a literal backslash-t, never a tab.
        lines = [" ".join(line.split()) for line in run.splitlines()]
        start = lines.index(expected[0])
        # The seven statements are consecutive: nothing may reassign a listing between them.
        self.assertEqual(expected, lines[start : start + len(expected)])
        for name in ("target_listing=", "expected_listing="):
            self.assertEqual(1, sum(line.startswith(name) for line in lines), name)
        # The full unit suite runs only after the listing gate.
        self.assertLess(start + len(expected) - 1, lines.index("git diff --check \"$MAIN_SHA\" \"$TARGET_SHA\""))

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
        # The publisher never runs the chain or migration verifiers. The target itself edits
        # verify_promotion_chain.py, so that path may appear only in the expected target listing rows.
        guarded = "\n".join(
            line for line in self.text.splitlines()
            if not line.lstrip().startswith("$'M\\t")
            and not line.lstrip().startswith("$'A\\t")
        )
        self.assertNotIn("verify_promotion_chain.py --", self.text)
        for forbidden in (
            "verify_promotion_chain",
            "verify_migration_result",
            "SEALED_SHA",
            "sealed-release",
            "RELEASE_EVIDENCE_TOKEN",
            "RELEASE_ID",
        ):
            self.assertNotIn(forbidden, guarded)

    def test_published_main_is_re_authenticated(self) -> None:
        run = self._run(STEP_REAUTH)
        self.assertIn('test "$(git -C gitops-main rev-parse origin/main)" = "$TARGET_SHA"', run)
        self.assertIn(
            "test \"$(git -C gitops-main rev-parse 'origin/main^{tree}')\" = \"$TARGET_TREE\"",
            run,
        )
        self.assertEqual(STEP_REAUTH, self.names[-1])

    def test_gate_step_bodies_are_exact(self) -> None:
        # F2: a substring survives ` || :` and `true || `; each gate body is compared line for line
        # (continuations folded, whitespace normalized) against the reviewed bytes.
        for name, expected in GATE_BODIES.items():
            run = LINE_CONTINUATION.sub(" ", self._run(name))
            self.assertEqual(expected, [" ".join(line.split()) for line in run.splitlines()], name)

    def test_token_bearing_steps_are_the_reviewed_documents(self) -> None:
        # F1: from the mint onward nothing may differ from the reviewed step documents - not a
        # reassignment before the push, not a spliced `pu""sh`, not an API call, not an extra step.
        mint = self.names.index(STEP_MINT)
        self.assertEqual(yaml.safe_load(TOKEN_BEARING_STEPS), self.steps[mint:])
        self.assertEqual("ubuntu-24.04", self.job["runs-on"])
        self.assertEqual(25, self.job["timeout-minutes"])
        self.assertEqual(APP_TOKEN_REFERENCES, self.text.count("steps.app_token"))


if __name__ == "__main__":
    unittest.main()
