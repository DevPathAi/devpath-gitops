import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
HANDLER = ROOT / "infra" / "aws" / "gpu-node-absence-watch" / "handler.py"
SPEC = importlib.util.spec_from_file_location("tested_gpu_node_absence_watch", HANDLER)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

ENVIRON = {
    "TOPIC_ARN": "arn:aws:sns:ap-northeast-2:000000000000:devpath-spot-interruption",
    "GPU_ROLE_TAG": "k3s-agent-gpu",
}


class FakeEc2:
    def __init__(self, *pages):
        self.pages = pages
        self.filters = None

    def get_paginator(self, operation):
        assert operation == "describe_instances"
        return self

    def paginate(self, **kwargs):
        self.filters = kwargs["Filters"]
        return iter(self.pages)


class FakeSns:
    def __init__(self):
        self.published = []

    def publish(self, **kwargs):
        self.published.append(kwargs)


def page(*instance_ids):
    return {"Reservations": [{"Instances": [{"InstanceId": instance_id}]} for instance_id in instance_ids]}


class GpuNodeAbsenceWatchTest(unittest.TestCase):
    def run_watch(self, ec2, environ=ENVIRON):
        sns = FakeSns()
        result = module.handler({}, None, ec2=ec2, sns=sns, environ=environ)
        return result, sns

    def test_stays_silent_while_a_gpu_instance_is_pending_or_running(self):
        result, sns = self.run_watch(FakeEc2(page("i-0aaaaaaaaaaaaaaaa")))
        self.assertEqual(sns.published, [])
        self.assertEqual(result, {"gpu_instances": ["i-0aaaaaaaaaaaaaaaa"], "notified": False})

    def test_reminds_the_operators_while_no_gpu_instance_exists(self):
        # 2026-09-08: the GPU spot node was reclaimed and nobody knew for 23 days. The state-change
        # rule reports a reclaim once; this is the layer that keeps speaking until it is rebuilt.
        result, sns = self.run_watch(FakeEc2(page()))
        self.assertEqual(result, {"gpu_instances": [], "notified": True})
        self.assertEqual(len(sns.published), 1)
        published = sns.published[0]
        self.assertEqual(published["TopicArn"], ENVIRON["TOPIC_ARN"])
        self.assertIn("role=k3s-agent-gpu", published["Message"])
        self.assertIn("runbook-k3s-bootstrap.md", published["Message"])
        # A new node alone leaves the pod Pending; the mail has to say what else to delete.
        self.assertIn("PVC", published["Message"])

    def test_subject_fits_what_sns_accepts_for_email(self):
        _, sns = self.run_watch(FakeEc2(page()))
        subject = sns.published[0]["Subject"]
        self.assertTrue(subject.isascii())
        self.assertLess(len(subject), 100)
        self.assertNotIn("\n", subject)

    def test_asks_only_for_pending_or_running_instances_that_carry_the_role_tag(self):
        ec2 = FakeEc2(page())
        self.run_watch(ec2)
        self.assertEqual(
            ec2.filters,
            [
                {"Name": "tag:role", "Values": ["k3s-agent-gpu"]},
                {"Name": "instance-state-name", "Values": ["pending", "running"]},
            ],
        )

    def test_finds_a_gpu_instance_on_a_later_page(self):
        result, sns = self.run_watch(FakeEc2(page(), page("i-0bbbbbbbbbbbbbbbb")))
        self.assertEqual(sns.published, [])
        self.assertEqual(result["gpu_instances"], ["i-0bbbbbbbbbbbbbbbb"])

    def test_role_tag_defaults_to_the_one_the_state_change_mail_names(self):
        ec2 = FakeEc2(page())
        self.run_watch(ec2, environ={"TOPIC_ARN": ENVIRON["TOPIC_ARN"]})
        self.assertEqual(ec2.filters[0], {"Name": "tag:role", "Values": ["k3s-agent-gpu"]})


if __name__ == "__main__":
    unittest.main()
