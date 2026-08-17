from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
APP_ACTION = "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"


class ProductionWorkflowWiringTest(unittest.TestCase):
    def test_all_production_workflows_serialize_without_unapproved_cancellation(self):
        for filename in (
            "mission-spine-promote.yml",
            "mission-spine-landing-last.yml",
            "mission-spine-rollback.yml",
        ):
            workflow = (WORKFLOWS / filename).read_text(encoding="utf-8")
            self.assertIn("group: mission-spine-production", workflow)
            self.assertIn("cancel-in-progress: false", workflow)
            self.assertNotIn("cancel-in-progress: true", workflow)

        promote = (WORKFLOWS / "mission-spine-promote.yml").read_text(encoding="utf-8")
        self.assertIn("git -C gitops-main commit --allow-empty", promote)
        for name in (
            "devpath-admin",
            "devpath-ai-svc",
            "devpath-community-svc",
            "devpath-gateway",
            "devpath-lcs-svc",
            "devpath-learning-svc",
            "devpath-notification-svc",
            "devpath-platform-svc",
            "devpath-sandbox-svc",
        ):
            self.assertIn(f"apps/{name}/base/kustomization.yaml", promote)

    def test_promotion_orders_migration_nine_services_off_on_and_runtime_canary(self):
        text = (WORKFLOWS / "mission-spine-promote.yml").read_text(encoding="utf-8")
        ordered = [
            "verify_migration_result.py",
            "--phase migration",
            "promote_service_digests.py",
            "--phase services",
            "--target mission-off",
            "--phase mission-off --canary-seconds 0",
            "--target mission-on",
            "--phase mission-on --canary-seconds 900",
            "Re-observe all nine exact service runtimes after the web canary",
            "build_production_canary.py",
        ]
        positions = [text.index(value) for value in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(text.count("wait_release_rollouts.py"), 5)
        self.assertIn("--service-runtime", text)
        self.assertIn(
            "${{ inputs.release_id }}-production-canary-run-${{ github.run_id }}-attempt-${{ github.run_attempt }}",
            text,
        )

    def test_every_production_side_effect_job_authenticates_live_approval_first(self):
        cases = {
            "mission-spine-promote.yml": [
                ("production_off", "Authenticate this attempt-one protected OFF approval"),
                ("production_on", "Authenticate this attempt-one protected ON approval"),
            ],
            "mission-spine-landing-last.yml": [
                ("landing-last", "Authenticate this attempt-one protected Landing approval")
            ],
            "mission-spine-rollback.yml": [
                ("reverse-rollback", "Authenticate this attempt-one protected rollback approval")
            ],
        }
        for filename, jobs in cases.items():
            text = (WORKFLOWS / filename).read_text(encoding="utf-8")
            for job, approval in jobs:
                with self.subTest(filename=filename, job=job):
                    start = text.index(f"  {job}:")
                    later_jobs = [
                        match.start()
                        for match in re.finditer(r"^  [a-z][a-z0-9_-]+:\s*$", text, re.M)
                        if match.start() > start
                    ]
                    section = text[start : min(later_jobs) if later_jobs else len(text)]
                    approval_index = section.index(approval)
                    self.assertIn("verify_current_protected_approval.py", section)
                    sensitive = [
                        value
                        for value in (
                            "secrets.RELEASE_EVIDENCE_TOKEN",
                            "secrets.GITOPS_RELEASE_APP_PRIVATE_KEY",
                            "secrets.PRODUCTION_KUBECONFIG_B64",
                            "secrets.CLOUDFLARE_API_TOKEN",
                        )
                        if value in section
                    ]
                    for value in sensitive:
                        self.assertLess(approval_index, section.index(value))

    def test_only_environment_scoped_app_checkouts_can_write_gitops_main(self):
        for filename in ("mission-spine-promote.yml", "mission-spine-rollback.yml"):
            text = (WORKFLOWS / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                self.assertIn(APP_ACTION, text)
                self.assertIn("verify_gitops_write_authority.py", text)
                self.assertNotIn("repositories: devpath-gitops", text)
                self.assertIn("permission-administration: read", text)
                self.assertIn("permission-contents: write", text)
                self.assertNotIn("contents: write\n", text.split("jobs:", 1)[0])
                self.assertNotIn("--force", text)
                self.assertNotIn("reset --hard", text)
                for push in re.finditer(r"git -C gitops-main push origin HEAD:main", text):
                    authority = text.rfind("verify_gitops_write_authority.py", 0, push.start())
                    self.assertGreater(authority, 0)

    def test_kubeconfig_cleanup_and_run_scoped_result_artifacts_are_exact(self):
        promote = (WORKFLOWS / "mission-spine-promote.yml").read_text(encoding="utf-8")
        rollback = (WORKFLOWS / "mission-spine-rollback.yml").read_text(encoding="utf-8")
        landing = (WORKFLOWS / "mission-spine-landing-last.yml").read_text(encoding="utf-8")
        self.assertEqual(promote.count("name: Remove the exact production kubeconfig"), 2)
        self.assertEqual(rollback.count("name: Remove the exact production kubeconfig"), 1)
        self.assertEqual(promote.count("if: always()"), 2)
        self.assertEqual(rollback.count("if: always()"), 1)
        self.assertEqual(promote.count("manage_production_kubeconfig.py create"), 2)
        self.assertEqual(promote.count("manage_production_kubeconfig.py cleanup"), 2)
        self.assertEqual(rollback.count("manage_production_kubeconfig.py create"), 1)
        self.assertEqual(rollback.count("manage_production_kubeconfig.py cleanup"), 1)
        self.assertNotIn('> "$RUNNER_TEMP/production-kubeconfig"', promote + rollback)
        self.assertIn("-reverse-rollback-run-${{ github.run_id }}-attempt-${{ github.run_attempt }}", rollback)
        self.assertIn("-landing-last-run-${{ github.run_id }}-attempt-${{ github.run_attempt }}", landing)
        for key in (
            "CANARY_RUN_ID",
            "CANARY_RUN_ATTEMPT",
            "ON_COMMIT",
            "SERVICES_COMMIT",
            "MIGRATION_COMMIT",
        ):
            self.assertIn(key, landing)
        self.assertIn("--github-output \"$GITHUB_OUTPUT\"", landing)
        self.assertNotIn("runs-on: ubuntu-latest", landing)
        self.assertIn("deploy_mode", landing)
        self.assertIn("reuse)", landing)
        self.assertLess(
            landing.index("Verify exact current deployment CAS, public marker, and smoke"),
            landing.index("Re-authenticate unchanged main, release, and canary after Landing"),
        )
        self.assertLess(
            landing.index("Re-authenticate unchanged main, release, and canary after Landing"),
            landing.index("Record sanitized Landing-last evidence"),
        )
        self.assertIn("production_deployments_enabled", (ROOT / "scripts/release/cloudflare_pages.py").read_text(encoding="utf-8"))
        for text in (promote, rollback, landing):
            self.assertIn("overwrite: false", text)

    def test_every_production_web_runtime_binds_the_exact_gitops_commit(self):
        for filename in ("mission-spine-promote.yml", "mission-spine-rollback.yml"):
            text = (WORKFLOWS / filename).read_text(encoding="utf-8")
            starts = [
                match.start()
                for match in re.finditer(
                    r"python scripts/release/wait_web_rollout\.py", text
                )
            ]
            for index, start in enumerate(starts):
                end = text.find("\n      - name:", start)
                block = text[start : len(text) if end < 0 else end]
                with self.subTest(filename=filename, index=index):
                    self.assertIn("--environment production", block)
                    self.assertIn("--commit", block)
        rollback = (WORKFLOWS / "mission-spine-rollback.yml").read_text(encoding="utf-8")
        self.assertLess(
            rollback.index("Require exact mission-OFF runtime before prior mutation"),
            rollback.index("Create or reuse exact frontend-prior rollback"),
        )

    def test_every_gitops_main_refresh_uses_an_explicit_remote_tracking_refspec(self):
        for filename in ("mission-spine-promote.yml", "mission-spine-rollback.yml"):
            text = (WORKFLOWS / filename).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"git -C gitops-main fetch --no-tags origin main\s*$")
            for line in text.splitlines():
                if "git -C gitops-main fetch --no-tags origin main" in line:
                    self.assertTrue(
                        line.endswith("main:refs/remotes/origin/main"), line
                    )


if __name__ == "__main__":
    unittest.main()
