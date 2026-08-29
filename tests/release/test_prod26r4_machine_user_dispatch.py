import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mission-spine-auth-smoke.yml"


class Prod26R4MachineUserDispatchContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_manual_modes_default_to_off(self) -> None:
        self.assertIn("prod26r4_machine_user_mode:", self.workflow)
        self.assertIn('description: "prod26r4 machine-user credential action"', self.workflow)
        self.assertIn("type: choice", self.workflow)
        self.assertIn("default: off", self.workflow)
        self.assertIn("- audit", self.workflow)
        self.assertIn("- dispatch", self.workflow)
        self.assertIn("prod26r4_dispatch_confirmation:", self.workflow)
        self.assertIn("default: \"\"", self.workflow)

    def test_audit_is_branch_only_and_cannot_create_a_run(self) -> None:
        self.assertIn(
            "  audit-prod26r4-machine-user:\n"
            "    if: github.ref == "
            "'refs/heads/chore/prod26r4-evidence-token-audit' && "
            "inputs.prod26r4_machine_user_mode == 'audit'",
            self.workflow,
        )
        self.assertIn(
            "GH_TOKEN: ${{ secrets.PROD26R4_INITIATOR_TOKEN }}",
            self.workflow,
        )
        self.assertIn(
            '"ref": "refs/heads/prod26r4-capability-probe-does-not-exist"',
            self.workflow,
        )
        self.assertIn('test "$probe_status" = "422"', self.workflow)

    def test_machine_user_identity_is_fail_closed(self) -> None:
        self.assertIn("gh api user --jq '.login'", self.workflow)
        self.assertIn("gh api user --jq '.id'", self.workflow)
        self.assertIn("gh api user --jq '.type'", self.workflow)
        self.assertIn('test "$token_type" = "User"', self.workflow)
        self.assertIn('token_login_folded="${token_login,,}"', self.workflow)
        self.assertIn('test "$token_login_folded" != "velkaressiablutkrone"', self.workflow)
        self.assertIn('test "$token_login_folded" != "qahnaarin"', self.workflow)
        self.assertIn(
            '[[ "$token_login" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,37}'
            '[A-Za-z0-9])?$ ]]',
            self.workflow,
        )

    def test_dispatch_requires_exact_confirmation_and_coordinates(self) -> None:
        self.assertIn(
            "  dispatch-prod26r4-machine-user:\n"
            "    if: github.ref == "
            "'refs/heads/chore/prod26r4-evidence-token-audit' && "
            "inputs.prod26r4_machine_user_mode == 'dispatch'",
            self.workflow,
        )
        self.assertIn(
            "CONFIRMATION: ${{ inputs.prod26r4_dispatch_confirmation }}",
            self.workflow,
        )
        self.assertIn(
            'test "$CONFIRMATION" = "dispatch-ms-20260828-prod26r4"',
            self.workflow,
        )
        for value in (
            "ms-20260828-prod26r4",
            "4f245d61cc4924c9db0f3f3cbb90434ad0fe7d93",
            "e866a50f5ca535ebc1ed83343c651af064e424de",
            "eca2a73378eff8a9ce310f7ef997b51b7910984f",
            "0b012f527ad30fde9a9cfdf9280377d84ef9a38da78987962ad86b38661a0575",
        ):
            self.assertIn(value, self.workflow)

    def test_dispatch_validates_or_cancels_the_exact_inner_run(self) -> None:
        self.assertIn(
            "repos/DevPathAi/devpath-shared/actions/workflows/335839429/dispatches",
            self.workflow,
        )
        self.assertIn("inner_run_id", self.workflow)
        self.assertIn('test "$inner_actor" = "$token_login"', self.workflow)
        self.assertIn('test "$inner_triggering_actor" = "$token_login"', self.workflow)
        self.assertIn('test "$inner_event" = "workflow_dispatch"', self.workflow)
        self.assertIn('test "$inner_attempt" = "1"', self.workflow)
        self.assertIn(
            'test "$inner_sha" = "4f245d61cc4924c9db0f3f3cbb90434ad0fe7d93"',
            self.workflow,
        )
        self.assertIn(
            'actions/runs/$inner_run_id/cancel',
            self.workflow,
        )
        self.assertLess(
            self.workflow.index("trap cancel_inner ERR"),
            self.workflow.index(
                '[[ "$inner_run_url" =~ '
                '^https://github\\.com/DevPathAi/devpath-shared/actions/runs/'
            ),
        )


if __name__ == "__main__":
    unittest.main()
