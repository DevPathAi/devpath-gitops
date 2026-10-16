import datetime
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
    "HEARTBEAT_NODE": "devpath-gpu",
}
NOW = datetime.datetime(2026, 10, 7, 3, 0, tzinfo=datetime.timezone.utc)


class FakeCloudWatch:
    def __init__(self, *sums, failure=None):
        self.sums = sums
        self.failure = failure
        self.query = None

    def get_metric_statistics(self, **kwargs):
        self.query = kwargs
        if self.failure:
            raise self.failure
        return {"Datapoints": [{"Sum": value, "Unit": "None"} for value in self.sums]}


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


class UnusedEc2:
    def get_paginator(self, operation):
        raise AssertionError("EC2 is only asked once the heartbeat is silent")


class FakeSns:
    def __init__(self):
        self.published = []

    def publish(self, **kwargs):
        self.published.append(kwargs)


def page(*instance_ids):
    return {"Reservations": [{"Instances": [{"InstanceId": instance_id}]} for instance_id in instance_ids]}


class GpuNodeAbsenceWatchTest(unittest.TestCase):
    def run_watch(self, cloudwatch, ec2, environ=ENVIRON):
        sns = FakeSns()
        result = module.handler({}, None, ec2=ec2, sns=sns, cloudwatch=cloudwatch, environ=environ, now=NOW)
        return result, sns

    def test_stays_silent_while_the_heartbeat_arrives(self):
        result, sns = self.run_watch(FakeCloudWatch(15.0), UnusedEc2())
        self.assertEqual(sns.published, [])
        self.assertEqual(result, {"heartbeats": 15.0, "notified": False})

    def test_reminds_the_operators_when_no_instance_exists(self):
        # 2026-09-08: the GPU spot node was reclaimed and nobody knew for 23 days.
        result, sns = self.run_watch(FakeCloudWatch(), FakeEc2(page()))
        self.assertEqual(result, {"heartbeats": 0, "gpu_instances": [], "notified": True})
        self.assertEqual(len(sns.published), 1)
        published = sns.published[0]
        self.assertEqual(published["TopicArn"], ENVIRON["TOPIC_ARN"])
        self.assertEqual(published["Subject"], "[DevPath/AWS] GPU node is not serving: no instance")
        self.assertIn("role=k3s-agent-gpu", published["Message"])
        self.assertIn("devpath-gpu-node", published["Message"])
        self.assertIn("runbook-k3s-bootstrap.md", published["Message"])

    def test_says_so_when_an_instance_is_up_but_ollama_is_not(self):
        # The case the instance check could not see: auto recovery launched a node that never
        # came to serve. An instance exists, so "no instance" would send the operator the wrong way.
        result, sns = self.run_watch(FakeCloudWatch(), FakeEc2(page("i-0aaaaaaaaaaaaaaaa")))
        self.assertEqual(result, {"heartbeats": 0, "gpu_instances": ["i-0aaaaaaaaaaaaaaaa"], "notified": True})
        published = sns.published[0]
        self.assertEqual(
            published["Subject"], "[DevPath/AWS] GPU node is not serving: instance up, ollama not ready"
        )
        self.assertIn("i-0aaaaaaaaaaaaaaaa", published["Message"])
        self.assertIn("cloud-init-output.log", published["Message"])
        self.assertIn("FailedMount", published["Message"])

    def test_asks_for_the_last_fifteen_minutes_of_this_node(self):
        cloudwatch = FakeCloudWatch(1.0)
        self.run_watch(cloudwatch, UnusedEc2())
        self.assertEqual(
            cloudwatch.query,
            {
                "Namespace": "DevPath/GPU",
                "MetricName": "OllamaReady",
                "Dimensions": [{"Name": "Node", "Value": "devpath-gpu"}],
                "StartTime": NOW - datetime.timedelta(minutes=15),
                "EndTime": NOW,
                "Period": 900,
                "Statistics": ["Sum"],
            },
        )

    def test_adds_up_split_datapoints(self):
        # A 15-minute window rarely lines up with a period boundary and comes back in two pieces.
        result, sns = self.run_watch(FakeCloudWatch(0.0, 4.0), UnusedEc2())
        self.assertEqual(sns.published, [])
        self.assertEqual(result["heartbeats"], 4.0)

    def test_datapoints_that_sum_to_zero_are_silence(self):
        result, sns = self.run_watch(FakeCloudWatch(0.0, 0.0), FakeEc2(page()))
        self.assertTrue(result["notified"])
        self.assertEqual(len(sns.published), 1)

    def test_a_failed_metric_query_is_an_error_not_silence(self):
        sns = FakeSns()
        with self.assertRaisesRegex(RuntimeError, "throttled"):
            module.handler(
                {}, None, ec2=UnusedEc2(), sns=sns,
                cloudwatch=FakeCloudWatch(failure=RuntimeError("throttled")), environ=ENVIRON, now=NOW,
            )
        self.assertEqual(sns.published, [])

    def test_subjects_fit_what_sns_accepts_for_email(self):
        for subject in (module.SUBJECT_NO_INSTANCE, module.SUBJECT_NOT_READY):
            self.assertTrue(subject.isascii())
            self.assertLess(len(subject), 100)
            self.assertNotIn("\n", subject)

    def test_looks_for_pending_or_running_instances_that_carry_the_role_tag(self):
        ec2 = FakeEc2(page())
        self.run_watch(FakeCloudWatch(), ec2)
        self.assertEqual(
            ec2.filters,
            [
                {"Name": "tag:role", "Values": ["k3s-agent-gpu"]},
                {"Name": "instance-state-name", "Values": ["pending", "running"]},
            ],
        )

    def test_finds_an_instance_on_a_later_page(self):
        result, _ = self.run_watch(FakeCloudWatch(), FakeEc2(page(), page("i-0bbbbbbbbbbbbbbbb")))
        self.assertEqual(result["gpu_instances"], ["i-0bbbbbbbbbbbbbbbb"])

    def test_node_and_role_default_to_the_deployed_names(self):
        cloudwatch, ec2 = FakeCloudWatch(), FakeEc2(page())
        self.run_watch(cloudwatch, ec2, environ={"TOPIC_ARN": ENVIRON["TOPIC_ARN"]})
        self.assertEqual(cloudwatch.query["Dimensions"], [{"Name": "Node", "Value": "devpath-gpu"}])
        self.assertEqual(ec2.filters[0], {"Name": "tag:role", "Values": ["k3s-agent-gpu"]})


if __name__ == "__main__":
    unittest.main()
