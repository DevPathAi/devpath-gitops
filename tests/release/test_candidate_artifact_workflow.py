import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-candidate.yml"

CHECKOUT_PIN = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
SETUP_PYTHON_PIN = "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1"
UPLOAD_PIN = "actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f"


class CandidateArtifactWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_dispatch_accepts_only_release_id_and_has_read_only_permissions(self):
        text = self.text
        inputs = text.split("inputs:", 1)[1].split("permissions:", 1)[0]
        self.assertEqual(inputs.count("release_id:"), 1)
        for forbidden in ("digest:", "head_sha:", "candidate_spec_sha256:", "artifact"):
            self.assertNotIn(forbidden, inputs)

        self.assertGreaterEqual(text.count("contents: read"), 2)
        self.assertNotRegex(text, r"\b(?:contents|actions|deployments|id-token): write\b")
        self.assertNotIn("environment:", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("GH_TOKEN", text)

    def test_source_is_exact_candidate_only_commit_and_full_file_is_validated(self):
        text = self.text
        for contract in (
            'test "$GITHUB_EVENT_NAME" = "workflow_dispatch"',
            'test "$GITHUB_REPOSITORY" = "DevPathAi/devpath-gitops"',
            'test "$GITHUB_REF_TYPE" = "branch"',
            'test "$GITHUB_REF_NAME" = "release/candidate-$RELEASE_ID"',
            'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
            "--candidate-id \"$RELEASE_ID\"",
            "--emit-github-output \"$GITHUB_OUTPUT\"",
            "base_sha=",
            'git fetch --no-tags origin main:refs/remotes/origin/main',
            'test "$(git rev-parse origin/main)" = "$base_sha"',
            'test "$(git rev-parse HEAD^)" = "$base_sha"',
            'git diff --name-only "$base_sha"...HEAD',
            'git diff --name-status "$base_sha"...HEAD',
            'release-manifests/candidates/$RELEASE_ID.candidate-spec.json',
            'test ! -e "release-manifests/releases/$RELEASE_ID.json"',
            'git status --porcelain=v1 --untracked-files=all',
        ):
            self.assertIn(contract, text)

        self.assertIn(CHECKOUT_PIN, text)
        self.assertIn(SETUP_PYTHON_PIN, text)
        self.assertEqual(
            re.findall(r"^\s*uses:\s*(\S+)", text, flags=re.MULTILINE),
            [CHECKOUT_PIN, SETUP_PYTHON_PIN, UPLOAD_PIN],
        )
        self.assertIn("fetch-depth: 0", text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotRegex(text, r"(?m)^\s*git fetch (?:origin main|--all)\s*$")
        self.assertIn("verify_candidate_web_base.py", text)
        self.assertLess(
            text.index("verify_candidate_web_base.py"), text.index("artifact_dir=")
        )

    def test_upload_is_exact_one_file_byte_copy_with_run_scoped_name(self):
        text = self.text
        self.assertEqual(text.count(UPLOAD_PIN), 1)
        self.assertIn(
            "name: ${{ inputs.release_id }}-candidate-spec-run-${{ github.run_id }}-attempt-${{ github.run_attempt }}",
            text,
        )
        self.assertIn('artifact_path=$artifact_dir/candidate-spec.json', text)
        self.assertIn("path: ${{ steps.candidate.outputs.artifact_path }}", text)
        self.assertIn('cp -- "$candidate_path" "$artifact_dir/candidate-spec.json"', text)
        self.assertIn('cmp -s "$candidate_path" "$artifact_dir/candidate-spec.json"', text)
        self.assertIn("sha256sum", text)
        self.assertRegex(text, r"find \"\$artifact_dir\"[^\n]+\| wc -l")
        self.assertIn("if-no-files-found: error", text)
        retention = re.search(r"retention-days:\s*([0-9]+)", text)
        self.assertIsNotNone(retention)
        self.assertGreaterEqual(int(retention.group(1)), 1)
        self.assertLessEqual(int(retention.group(1)), 30)

    def test_workflow_has_no_mutation_deploy_or_sealing_path(self):
        lowered = self.text.lower()
        for forbidden in (
            "git commit",
            "git push",
            "contents: write",
            "kubectl",
            "wrangler",
            "cloudflare",
            "seal_release_manifest.py",
            "release_evidence_token",
            "pull-requests: write",
            "id-token: write",
        ):
            self.assertNotIn(forbidden, lowered)

        for block in self.text.split("run: |")[1:]:
            script = block.split("\n      - ", 1)[0]
            self.assertNotIn(
                "${{ inputs.release_id }}",
                script,
                "untrusted release_id must reach shell only through the environment",
            )


if __name__ == "__main__":
    unittest.main()
