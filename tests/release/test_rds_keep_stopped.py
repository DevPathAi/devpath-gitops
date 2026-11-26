import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
HANDLER = ROOT / "infra" / "aws" / "rds-keep-stopped" / "handler.py"
SPEC = importlib.util.spec_from_file_location("tested_rds_keep_stopped", HANDLER)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

ENVIRON = {
    "DB_IDENTIFIER": "devpath-pg",
    "K3S_INSTANCE_ID": "i-09e252854566cc123",
    "TOPIC_ARN": "arn:aws:sns:ap-northeast-2:000000000000:devpath-spot-interruption",
}


class AwsError(Exception):
    """Shaped like botocore's ClientError: the code sits in response['Error']['Code']."""

    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code, "Message": code}}


class FakeRds:
    def __init__(self, status, stop_failure=None):
        self.status = status
        self.stop_failure = stop_failure
        self.described = []
        self.stopped = []

    def describe_db_instances(self, **kwargs):
        self.described.append(kwargs["DBInstanceIdentifier"])
        return {"DBInstances": [{"DBInstanceIdentifier": kwargs["DBInstanceIdentifier"], "DBInstanceStatus": self.status}]}

    def stop_db_instance(self, **kwargs):
        if self.stop_failure:
            raise self.stop_failure
        self.stopped.append(kwargs["DBInstanceIdentifier"])
        return {"DBInstance": {"DBInstanceStatus": "stopping"}}


class FakeEc2:
    def __init__(self, state=None, failure=None):
        self.state = state
        self.failure = failure
        self.asked = []

    def describe_instances(self, **kwargs):
        self.asked.append(kwargs["InstanceIds"])
        if self.failure:
            raise self.failure
        if self.state is None:
            return {"Reservations": []}
        return {"Reservations": [{"Instances": [{"InstanceId": kwargs["InstanceIds"][0], "State": {"Name": self.state}}]}]}


class UnusedEc2:
    def describe_instances(self, **kwargs):
        raise AssertionError("the server is only asked about once the database is available")


class FakeSns:
    def __init__(self):
        self.published = []

    def publish(self, **kwargs):
        self.published.append(kwargs)


class RdsKeepStoppedTest(unittest.TestCase):
    def run_check(self, rds, ec2, environ=ENVIRON):
        sns = FakeSns()
        result = module.handler({}, None, rds=rds, ec2=ec2, sns=sns, environ=environ)
        return result, sns

    def test_leaves_a_stopped_database_alone(self):
        rds = FakeRds("stopped")
        result, sns = self.run_check(rds, UnusedEc2())
        self.assertEqual(rds.described, ["devpath-pg"])
        self.assertEqual(rds.stopped, [])
        self.assertEqual(sns.published, [])
        self.assertEqual(result, {"db_status": "stopped", "stopped": False})

    def test_waits_for_the_next_run_while_rds_is_still_starting_it(self):
        # RDS refuses to stop an instance that is not available yet; the next hourly run stops it.
        for status in ("starting", "backing-up", "stopping", "modifying"):
            with self.subTest(status=status):
                rds = FakeRds(status)
                result, sns = self.run_check(rds, UnusedEc2())
                self.assertEqual(rds.stopped, [])
                self.assertEqual(sns.published, [])
                self.assertEqual(result, {"db_status": status, "stopped": False})

    def test_stops_the_database_again_while_devpath_is_parked(self):
        # RDS starts a stopped instance by itself after seven days (RDS-EVENT-0154).
        rds = FakeRds("available")
        ec2 = FakeEc2("stopped")
        result, sns = self.run_check(rds, ec2)
        self.assertEqual(ec2.asked, [["i-09e252854566cc123"]])
        self.assertEqual(rds.stopped, ["devpath-pg"])
        self.assertEqual(result, {"db_status": "available", "server_state": "stopped", "stopped": True})
        self.assertEqual(len(sns.published), 1)
        mail = sns.published[0]
        self.assertEqual(mail["TopicArn"], ENVIRON["TOPIC_ARN"])
        self.assertEqual(mail["Subject"], "[DevPath/AWS] RDS devpath-pg started by itself and was stopped again")
        self.assertIn("i-09e252854566cc123 is stopped", mail["Message"])
        self.assertIn("disable the EventBridge rule devpath-rds-keep-stopped first", mail["Message"])
        self.assertIn("'keep the database stopped'", mail["Message"])
        self.assertLessEqual(len(mail["Subject"]), 100)  # SNS email subjects stop at 100 characters

    def test_never_stops_the_database_while_the_k3s_server_runs(self):
        # DevPath is back in use even though the rule was not disabled first.
        for state in ("pending", "running"):
            with self.subTest(state=state):
                rds = FakeRds("available")
                result, sns = self.run_check(rds, FakeEc2(state))
                self.assertEqual(rds.stopped, [])
                self.assertEqual(sns.published, [])
                self.assertEqual(result, {"db_status": "available", "server_state": state, "stopped": False})

    def test_stops_the_database_when_the_k3s_server_is_gone(self):
        for ec2, state in (
            (FakeEc2(failure=AwsError("InvalidInstanceID.NotFound")), "absent"),
            (FakeEc2(None), "absent"),
            (FakeEc2("terminated"), "terminated"),
        ):
            with self.subTest(state=state):
                rds = FakeRds("available")
                result, sns = self.run_check(rds, ec2)
                self.assertEqual(rds.stopped, ["devpath-pg"])
                self.assertEqual(result["server_state"], state)
                self.assertEqual(len(sns.published), 1)

    def test_does_not_stop_when_the_server_state_cannot_be_read(self):
        rds = FakeRds("available")
        with self.assertRaises(AwsError):
            self.run_check(rds, FakeEc2(failure=AwsError("UnauthorizedOperation")))
        self.assertEqual(rds.stopped, [])

    def test_leaves_a_refused_stop_to_the_next_run_without_mail(self):
        rds = FakeRds("available", stop_failure=AwsError("InvalidDBInstanceState"))
        result, sns = self.run_check(rds, FakeEc2("stopped"))
        self.assertEqual(sns.published, [])
        self.assertEqual(
            result, {"db_status": "available", "server_state": "stopped", "stopped": False, "retry": "next run"}
        )

    def test_surfaces_any_other_stop_failure(self):
        rds = FakeRds("available", stop_failure=AwsError("AccessDenied"))
        sns = FakeSns()
        with self.assertRaises(AwsError):
            module.handler({}, None, rds=rds, ec2=FakeEc2("stopped"), sns=sns, environ=ENVIRON)
        self.assertEqual(sns.published, [])


if __name__ == "__main__":
    unittest.main()
