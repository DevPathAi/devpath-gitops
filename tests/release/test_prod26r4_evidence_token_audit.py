import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-auth-smoke.yml"


class Prod26R4EvidenceTokenAuditContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_existing_auth_jobs_remain_main_only(self) -> None:
        self.assertIn(
            "  gitops-release-app:\n    if: github.ref == 'refs/heads/main'",
            self.workflow,
        )
        self.assertIn(
            "  full-write-authority:\n"
            "    if: github.ref == 'refs/heads/main' && inputs.full",
            self.workflow,
        )

    def test_audit_job_is_branch_only_and_read_only(self) -> None:
        self.assertIn(
            "  audit-evidence-token:\n"
            "    if: github.ref == "
            "'refs/heads/chore/prod26r4-evidence-token-audit'",
            self.workflow,
        )
        self.assertIn("GH_TOKEN: ${{ secrets.RELEASE_EVIDENCE_TOKEN }}", self.workflow)
        self.assertIn('test "$GITHUB_RUN_ATTEMPT" = "1"', self.workflow)
        self.assertNotIn("actions/runs/$inner_run_id", self.workflow)

    def test_audit_rejects_human_and_prohibited_identities(self) -> None:
        self.assertIn('test "$token_login" != "VelkaressiaBlutkrone"', self.workflow)
        self.assertIn('test "$token_login" != "Qahnaarin"', self.workflow)
        self.assertIn("gh api user --jq '.login'", self.workflow)
        self.assertIn('printf \'token login: %s\\n\' "$token_login"', self.workflow)

    def test_capability_probe_cannot_create_a_run(self) -> None:
        self.assertIn(
            '"ref": "refs/heads/prod26r4-capability-probe-does-not-exist"',
            self.workflow,
        )
        self.assertIn(
            'repos/DevPathAi/devpath-shared/actions/workflows/335839429/dispatches',
            self.workflow,
        )
        self.assertIn('test "$probe_status" = "422"', self.workflow)
        self.assertIn('test "$probe_exit" -ne 0', self.workflow)


if __name__ == "__main__":
    unittest.main()
