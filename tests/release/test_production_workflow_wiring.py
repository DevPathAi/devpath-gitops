from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
APP_ACTION = "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"


class ProductionWorkflowWiringTest(unittest.TestCase):
    def test_live_candidate_journeys_run_on_the_exact_candidate_web_and_restore_prior(self):
        text = (WORKFLOWS / "mission-spine-validate.yml").read_text(
            encoding="utf-8"
        )
        start = text.index("  candidate-journeys:")
        end = text.index("  seal-and-staging:", start)
        section = text[start:end]

        configure = section.index("Configure candidate journey staging cluster")
        prior = section.index("Verify candidate journey live prior CAS")
        stage = section.index("Stage exact candidate web for live journeys")
        journey = section.index("npm run test:release")
        restore = section.index("Fail-safe restore staging prior after live journeys")
        cleanup = section.index("Remove the candidate journey staging kubeconfig")
        self.assertEqual(
            [configure, prior, stage, journey, restore, cleanup],
            sorted([configure, prior, stage, journey, restore, cleanup]),
        )
        self.assertLess(
            section.index("Authenticate exact candidate artifact and B-to-C data tree"),
            section.index("secrets.STAGING_KUBECONFIG_B64"),
        )

        configure_block = section[configure:prior]
        self.assertIn(
            "RELEASE_EVIDENCE_TOKEN: ${{ secrets.RELEASE_EVIDENCE_TOKEN }}",
            configure_block,
        )

        stage_block = section[stage:journey]
        ordered = (
            "--phase mission-off --expected-current prior",
            "--environment staging --phase mission-off",
            "--phase mission-on --expected-current mission-off",
            "--environment staging --phase mission-on",
        )
        positions = [stage_block.index(value) for value in ordered]
        self.assertEqual(positions, sorted(positions))

        restore_block = section[restore:cleanup]
        self.assertIn(
            "if: always() && steps.journey_kube.outcome == 'success' && steps.journey_prior_cas.outcome == 'success'",
            restore_block,
        )
        self.assertIn("--phase prior --expected-current candidate", restore_block)
        self.assertIn("--environment staging --phase prior", restore_block)
        self.assertEqual(section.count("stage_web_release.py"), 3)
        self.assertEqual(section.count("wait_web_rollout.py"), 4)
        self.assertEqual(section.count("--candidate-only"), 7)
        self.assertNotIn("--candidate-only", text[end:])
        self.assertEqual(section.count("manage_production_kubeconfig.py create"), 1)
        self.assertEqual(section.count("manage_production_kubeconfig.py cleanup"), 1)

    def test_staging_seal_context_check_receives_the_workflow_token(self):
        text = (WORKFLOWS / "mission-spine-validate.yml").read_text(
            encoding="utf-8"
        )
        start = text.index("      - name: Configure approved staging cluster")
        end = text.index("\n      - name:", start + 1)
        section = text[start:end]
        self.assertIn("GH_TOKEN: ${{ github.token }}", section)
        self.assertIn("--mode sealed", section)

    def test_staging_seal_pins_the_exact_synthetic_host_before_cluster_checks(self):
        text = (WORKFLOWS / "mission-spine-validate.yml").read_text(
            encoding="utf-8"
        )
        start = text.index("  seal-and-staging:")
        section = text[start:]
        alias = "13.124.153.105 staging-app.13-124-153-105.nip.io"
        self.assertIn("Pin exact staging synthetic hostname", section)
        self.assertIn(alias, section)
        self.assertIn("sudo tee -a /etc/hosts", section)
        self.assertLess(section.index(alias), section.index("Verify live prior CAS"))

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
                ("rebaseline_staging", "Authenticate this attempt-one protected staging approval"),
            ],
            "mission-spine-landing-last.yml": [
                ("landing-last", "Authenticate this attempt-one protected Landing approval")
            ],
            "mission-spine-rollback.yml": [
                ("reverse-rollback", "Authenticate this attempt-one protected rollback approval"),
                ("rebaseline_staging", "Authenticate this attempt-one protected staging approval"),
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
                            "secrets.STAGING_KUBECONFIG_B64",
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
        self.assertEqual(promote.count("if: always()"), 6)
        self.assertEqual(rollback.count("if: always()"), 4)
        self.assertEqual(promote.count("manage_production_kubeconfig.py create"), 3)
        self.assertEqual(promote.count("manage_production_kubeconfig.py cleanup"), 3)
        self.assertEqual(rollback.count("manage_production_kubeconfig.py create"), 2)
        self.assertEqual(rollback.count("manage_production_kubeconfig.py cleanup"), 2)
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

    def test_every_remote_kubernetes_job_uses_ephemeral_runner_32_ingress(self):
        action = (
            "aws-actions/configure-aws-credentials@"
            "e6de054238d6b7531b4efff3b6587d9aade6a06c"
        )
        role = "arn:aws:iam::963773969059:role/devpath-github-actions-k3s-api"
        cases = {
            "mission-spine-validate.yml": ("candidate-journeys", "seal-and-staging"),
            "mission-spine-promote.yml": (
                "production_off",
                "rebaseline_staging",
                "production_on",
            ),
            "mission-spine-rollback.yml": ("reverse-rollback", "rebaseline_staging"),
        }
        for filename, jobs in cases.items():
            text = (WORKFLOWS / filename).read_text(encoding="utf-8")
            self.assertEqual(text.count(action), len(jobs))
            for job in jobs:
                start = text.index(f"  {job}:")
                later = [
                    match.start()
                    for match in re.finditer(r"^  [a-z][a-z0-9_-]+:\s*$", text, re.M)
                    if match.start() > start
                ]
                section = text[start : min(later) if later else len(text)]
                with self.subTest(filename=filename, job=job):
                    self.assertIn("id-token: write", section)
                    self.assertIn(action, section)
                    self.assertIn(role, section)
                    self.assertEqual(
                        section.count("manage_kubernetes_api_ingress.py open"), 1
                    )
                    self.assertEqual(
                        section.count("manage_kubernetes_api_ingress.py close"), 1
                    )
                    opened = section.index("manage_kubernetes_api_ingress.py open")
                    kube = section.index("manage_production_kubeconfig.py create")
                    kube_cleanup = section.index("manage_production_kubeconfig.py cleanup")
                    closed = section.index("manage_kubernetes_api_ingress.py close")
                    self.assertEqual(
                        [opened, kube, kube_cleanup, closed],
                        sorted([opened, kube, kube_cleanup, closed]),
                    )
                    close_step = section.rfind("      - name:", 0, closed)
                    self.assertIn("if: always()", section[close_step:closed])
                    self.assertNotIn("AWS_ACCESS_KEY_ID", section)
                    self.assertNotIn("AWS_SECRET_ACCESS_KEY", section)

    def test_every_production_web_runtime_binds_the_exact_gitops_commit(self):
        expected_counts = {
            "mission-spine-promote.yml": 3,
            "mission-spine-rollback.yml": 2,
        }
        for filename, expected_count in expected_counts.items():
            text = (WORKFLOWS / filename).read_text(encoding="utf-8")
            starts = [
                match.start()
                for match in re.finditer(
                    r"python control/scripts/release/wait_web_rollout\.py", text
                )
            ]
            blocks = []
            for index, start in enumerate(starts):
                end = text.find("\n      - name:", start)
                block = text[start : len(text) if end < 0 else end]
                if "--environment production" in block:
                    blocks.append((index, block))
            self.assertEqual(expected_count, len(blocks), filename)
            for index, block in blocks:
                with self.subTest(filename=filename, index=index):
                    self.assertIn("--commit", block)
                    self.assertIn(
                        '--gitops-root "$GITHUB_WORKSPACE/gitops-main"', block
                    )
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

    def test_successful_on_rebaselines_staging_under_the_staging_lease(self):
        text = (WORKFLOWS / "mission-spine-promote.yml").read_text(encoding="utf-8")
        start = text.index("  rebaseline_staging:")
        section = text[start:]
        self.assertIn("needs: [resolve, production_on]", section)
        production_on = text[text.index("  production_on:") :]
        self.assertIn(
            "on_commit: ${{ steps.commit.outputs.on_commit }}", production_on
        )
        self.assertIn("environment: mission-spine-staging", section)
        self.assertIn("group: mission-spine-staging", section)
        self.assertIn("queue: max", section)
        self.assertIn("cancel-in-progress: false", section)
        self.assertIn("STAGING_KUBECONFIG_B64", section)
        self.assertIn("ref: ${{ github.sha }}", section)
        self.assertIn("path: gitops-main", section)
        self.assertEqual(
            section.count("verify_post_promotion_staging_context.py"), 2
        )
        self.assertIn(
            '--expected-commit "${{ needs.production_on.outputs.on_commit }}"',
            section,
        )
        self.assertIn("--expected-phase mission-on", section)
        self.assertLess(
            section.index("verify_post_promotion_staging_context.py"),
            section.index("secrets.STAGING_KUBECONFIG_B64"),
        )
        self.assertIn(
            "--phase mission-on --expected-current prior", section
        )
        self.assertIn("--environment staging --phase mission-on", section)
        self.assertIn("--scope staging", section)
        self.assertIn("manage_production_kubeconfig.py cleanup", section)
        self.assertIn("failure() && steps.kube.outcome == 'success'", section)
        self.assertEqual(section.count("stage_web_release.py"), 2)

    def test_successful_rollback_rebaselines_staging_to_the_exact_prior(self):
        text = (WORKFLOWS / "mission-spine-rollback.yml").read_text(encoding="utf-8")
        reverse = text[text.index("  reverse-rollback:") : text.index("  rebaseline_staging:")]
        section = text[text.index("  rebaseline_staging:") :]
        self.assertIn(
            "prior_commit: ${{ steps.rollback_prior.outputs.prior_commit }}", reverse
        )
        self.assertIn("needs: [reverse-rollback]", section)
        self.assertIn("environment: mission-spine-staging", section)
        self.assertIn("group: mission-spine-staging", section)
        self.assertIn("queue: max", section)
        self.assertIn("cancel-in-progress: false", section)
        self.assertIn("STAGING_KUBECONFIG_B64", section)
        self.assertEqual(
            section.count("verify_post_promotion_staging_context.py"), 2
        )
        self.assertIn("--expected-phase prior", section)
        self.assertIn(
            "--expected-commit \"${{ needs['reverse-rollback'].outputs.prior_commit }}\"",
            section,
        )
        self.assertLess(
            section.index("verify_post_promotion_staging_context.py"),
            section.index("secrets.STAGING_KUBECONFIG_B64"),
        )
        self.assertIn("--phase prior --expected-current candidate", section)
        self.assertIn("--environment staging --phase prior", section)
        self.assertIn("--scope staging", section)
        self.assertIn("manage_production_kubeconfig.py cleanup", section)

    def test_staging_rebaseline_fail_safes_are_exact_and_self_contained(self):
        cases = (
            (
                "mission-spine-promote.yml",
                "Fail-safe restore staging prior after an incomplete rebaseline",
                "if: failure() && steps.kube.outcome == 'success' && steps.prior_cas.outcome == 'success'",
            ),
            (
                "mission-spine-rollback.yml",
                "Fail-safe retain staging prior after an incomplete rebaseline",
                "if: failure() && steps.kube.outcome == 'success'",
            ),
        )
        for filename, step_name, guard in cases:
            text = (WORKFLOWS / filename).read_text(encoding="utf-8")
            start = text.index(f"      - name: {step_name}")
            end = text.find("\n      - name:", start + 1)
            block = text[start : len(text) if end < 0 else end]
            with self.subTest(filename=filename):
                self.assertIn(guard, block)
                self.assertEqual(block.count("stage_web_release.py"), 1)
                self.assertEqual(block.count("wait_web_rollout.py"), 1)
                self.assertIn("--phase prior --expected-current candidate", block)
                self.assertIn("--environment staging --phase prior", block)
                self.assertIn('test -n "${MISSION_SYNTHETIC_PROBE_TOKEN:-}"', block)


if __name__ == "__main__":
    unittest.main()
