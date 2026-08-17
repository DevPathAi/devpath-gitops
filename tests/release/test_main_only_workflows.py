from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


class MainOnlyWorkflowTest(unittest.TestCase):
    @staticmethod
    def _job_section(text, job):
        start = text.index(f"  {job}:")
        boundaries = [
            match.start()
            for match in re.finditer(r"^  [a-z][a-z0-9_-]+:\s*$", text, re.M)
            if match.start() > start
        ]
        return text[start : min(boundaries) if boundaries else len(text)]

    def _assert_data_is_never_executable(self, text):
        for match in re.finditer(r"ref: release/candidate-\$\{\{ inputs\.release_id \}\}", text):
            end = text.find("\n      - ", match.start() + 1)
            checkout = text[match.start() : len(text) if end < 0 else end]
            self.assertRegex(checkout, r"path: (?:candidate-data|sealed-release)\s*$")
        forbidden = (
            r"(?m)^\s*(?:-\s+run:\s*)?(?:python3?|node|npm|npx)\s+(?:[\"']?\$GITHUB_WORKSPACE/)?(?:sealed-release|candidate-data)/",
            r"(?m)^\s*(?:-\s+run:\s*)?(?:bash|sh|source|\.)\b[^\n]*(?:sealed-release|candidate-data)",
            r"(?m)^\s*(?:[\"']?\$GITHUB_WORKSPACE/)?(?:sealed-release|candidate-data)/[^\s]+",
            r"uses:\s+\./(?:sealed-release|candidate-data)/",
            r"working-directory:\s+(?:\$GITHUB_WORKSPACE/)?(?:sealed-release|candidate-data)(?:/|\s*$)",
            r"(?m)^\s*(?:-\s+run:\s*)?(?:cp|mv|rsync)\b[^\n]*(?:sealed-release|candidate-data)",
        )
        for pattern in forbidden:
            self.assertNotRegex(text, pattern)

    def test_all_protected_release_jobs_execute_main_owned_code(self):
        cases = {
            "mission-spine-validate.yml": ("candidate-journeys", "seal-and-staging"),
            "mission-spine-promote.yml": ("production_off", "production_on"),
            "mission-spine-landing-last.yml": ("landing-last",),
            "mission-spine-rollback.yml": ("reverse-rollback",),
        }
        for filename, jobs in cases.items():
            text = (WORKFLOWS / filename).read_text(encoding="utf-8")
            self.assertNotIn('test "$GITHUB_REF" = "refs/heads/release/candidate-', text)
            self.assertNotIn('test "$GITHUB_REF_NAME" != "main"', text)
            for job in jobs:
                section = self._job_section(text, job)
                with self.subTest(filename=filename, job=job):
                    self.assertIn('PYTHONDONTWRITEBYTECODE: "1"', text)
                    self.assertIn("verify_current_protected_approval.py", section)
                    self.assertIn("verify_main_release_context.py", section)
                    self.assertIn("--workflow-path .github/workflows/", section)
                    self.assertIn("--data-root", section)
                    self.assertIn("ref: ${{ github.sha }}", section)
                    self.assertIn("path: control", section)
                    self.assertLess(
                        section.index("--mode control"),
                        section.index("verify_current_protected_approval.py"),
                    )
                    self._assert_data_is_never_executable(section)
                    approval = section.index("verify_current_protected_approval.py")
                    if "secrets." in section:
                        self.assertLess(approval, section.index("secrets."))

    def test_candidate_branch_is_checked_out_only_as_data(self):
        for filename in (
            "mission-spine-validate.yml",
            "mission-spine-promote.yml",
            "mission-spine-landing-last.yml",
            "mission-spine-rollback.yml",
        ):
            text = (WORKFLOWS / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                self.assertIn("ref: release/candidate-${{ inputs.release_id }}", text)
                self.assertRegex(text, r"path: (?:candidate-data|sealed-release)")
                self.assertNotIn("working-directory: sealed-release", text)
                self.assertNotIn("working-directory: candidate-data", text)
                self.assertNotRegex(text, r"python\s+(?:sealed-release|candidate-data)/")
                self._assert_data_is_never_executable(text)

    def test_candidate_script_action_source_copy_and_secret_order_mutations_fail(self):
        text = (WORKFLOWS / "mission-spine-validate.yml").read_text(encoding="utf-8")
        mutations = (
            text + "\n      - run: source \"$GITHUB_WORKSPACE/candidate-data/evil.sh\"\n",
            text + "\n      - run: sh \"$GITHUB_WORKSPACE/candidate-data/evil.sh\"\n",
            text + "\n      - uses: ./candidate-data/.github/actions/evil\n",
            text + "\n        working-directory: $GITHUB_WORKSPACE/candidate-data\n",
            text + "\n      - run: cp candidate-data/evil.py control/scripts/release/evil.py\n",
            text.replace("path: candidate-data", "path: attacker-data", 1),
        )
        for mutated in mutations:
            with self.subTest(mutation=mutated[-100:]), self.assertRaises(AssertionError):
                self._assert_data_is_never_executable(mutated)

        for job in ("candidate-journeys", "seal-and-staging"):
            section = self._job_section(text, job)
            changed = section.replace(
                "    steps:\n", "    steps:\n      - run: echo '${{ secrets.STAGING_KUBECONFIG_B64 }}'\n", 1
            )
            with self.subTest(job=job):
                self.assertGreater(
                    changed.index("verify_current_protected_approval.py"),
                    changed.index("secrets."),
                )

    def test_promote_off_and_on_require_fresh_main_dispatches(self):
        text = (WORKFLOWS / "mission-spine-promote.yml").read_text(encoding="utf-8")
        self.assertIn("initial_phase:", text)
        resolve = text[text.index("  resolve:") : text.index("  production_off:")]
        self.assertIn("migration|services|mission-off|mission-on", resolve)
        self.assertIn("dispatch requires an exact resumable", resolve)
        off = text[text.index("  production_off:") : text.index("  production_on:")]
        self.assertIn("needs.resolve.outputs.initial_phase == 'migration'", off)
        self.assertIn("needs.resolve.outputs.initial_phase == 'services'", off)
        on = text[text.index("  production_on:") :]
        self.assertIn("needs: resolve", on)
        self.assertNotIn("needs: [resolve, production_off]", on)
        self.assertIn("needs.resolve.outputs.initial_phase == 'mission-off'", on)


if __name__ == "__main__":
    unittest.main()
