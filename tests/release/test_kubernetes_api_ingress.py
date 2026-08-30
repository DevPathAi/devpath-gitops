import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "manage_kubernetes_api_ingress.py"
SPEC = importlib.util.spec_from_file_location("manage_kubernetes_api_ingress", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)


def completed(stdout: str = "", *, returncode: int = 0, stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeAws:
    def __init__(self, responses):
        self.responses = list(responses)
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        if not self.responses:
            raise AssertionError(f"unexpected AWS command: {command}")
        return self.responses.pop(0)


class KubernetesApiIngressTest(unittest.TestCase):
    def valid_rule(self, cidr="8.8.8.8/32"):
        return {
            "SecurityGroupRuleId": "sgr-" + "a" * 17,
            "GroupId": module.SECURITY_GROUP_ID,
            "IsEgress": False,
            "IpProtocol": "tcp",
            "FromPort": 6443,
            "ToPort": 6443,
            "CidrIpv4": cidr,
            "Description": "mission-spine-gha:123:1:candidate-journeys",
            "Tags": [
                {"Key": "ManagedBy", "Value": module.MANAGED_BY},
                {"Key": "GitHubRun", "Value": "123-1"},
                {"Key": "GitHubJob", "Value": "candidate-journeys"},
                {"Key": "ExpiresAtEpoch", "Value": "4600"},
            ],
        }

    def test_open_authorizes_only_the_current_global_runner_ipv4_and_exports_rule_id(self):
        rule = self.valid_rule()
        aws = FakeAws([completed(json.dumps({"Return": True, "SecurityGroupRules": [rule]}))])
        with tempfile.TemporaryDirectory() as directory:
            github_env = Path(directory) / "github-env"
            github_env.touch()
            rule_id = module.open_ingress(
                github_env,
                "123",
                "1",
                "candidate-journeys",
                ip_fetcher=lambda: b"8.8.8.8\n",
                command_runner=aws,
                now=lambda: 1000,
            )
            self.assertEqual(rule_id, "sgr-" + "a" * 17)
            self.assertEqual(
                github_env.read_text(encoding="utf-8"),
                f"KUBERNETES_API_INGRESS_RULE_ID={rule_id}\n",
            )
        command = aws.commands[0]
        self.assertEqual(command[:3], ["aws", "ec2", "authorize-security-group-ingress"])
        self.assertIn(module.SECURITY_GROUP_ID, command)
        permissions = json.loads(command[command.index("--ip-permissions") + 1])
        self.assertEqual(
            permissions,
            [{
                "IpProtocol": "tcp",
                "FromPort": 6443,
                "ToPort": 6443,
                "IpRanges": [{
                    "CidrIp": "8.8.8.8/32",
                    "Description": "mission-spine-gha:123:1:candidate-journeys",
                }],
            }],
        )

    def test_open_rejects_non_global_or_malformed_addresses_before_aws(self):
        for value in (b"127.0.0.1\n", b"10.0.0.1\n", b"2001:db8::1\n", b"not-ip\n"):
            aws = FakeAws([])
            with tempfile.TemporaryDirectory() as directory:
                github_env = Path(directory) / "github-env"
                github_env.touch()
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "runner IPv4"):
                    module.open_ingress(
                        github_env,
                        "123",
                        "1",
                        "candidate-journeys",
                        ip_fetcher=lambda value=value: value,
                        command_runner=aws,
                    )
            self.assertEqual(aws.commands, [])

    def test_open_revokes_a_created_rule_if_export_fails(self):
        rule = self.valid_rule()
        aws = FakeAws([
            completed(json.dumps({"Return": True, "SecurityGroupRules": [rule]})),
            completed(json.dumps({"Return": True, "UnknownIpPermissions": []})),
        ])
        with tempfile.TemporaryDirectory() as directory:
            github_env = Path(directory) / "missing-github-env"
            with self.assertRaisesRegex(ValueError, "GITHUB_ENV"):
                module.open_ingress(
                    github_env,
                    "123",
                    "1",
                    "candidate-journeys",
                    ip_fetcher=lambda: b"8.8.8.8\n",
                    command_runner=aws,
                    now=lambda: 1000,
                )
        self.assertIn("revoke-security-group-ingress", aws.commands[1])
        self.assertIn("sgr-" + "a" * 17, aws.commands[1])

    def test_close_describes_managed_exact_rule_before_revocation(self):
        rule = self.valid_rule()
        aws = FakeAws([
            completed(json.dumps({"SecurityGroupRules": [rule]})),
            completed(json.dumps({"Return": True, "UnknownIpPermissions": []})),
        ])
        self.assertTrue(module.close_ingress("sgr-" + "a" * 17, command_runner=aws))
        self.assertIn("describe-security-group-rules", aws.commands[0])
        self.assertIn("revoke-security-group-ingress", aws.commands[1])
        self.assertFalse(module.close_ingress("", command_runner=FakeAws([])))
        with self.assertRaisesRegex(ValueError, "rule ID"):
            module.close_ingress("sgr-wrong", command_runner=FakeAws([]))


if __name__ == "__main__":
    unittest.main()
