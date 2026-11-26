"""Keep the DevPath database stopped while DevPath is parked.

RDS starts a stopped DB instance by itself after seven consecutive days (RDS-EVENT-0154) and has
no setting to keep it stopped longer. This function runs every hour; while its rule is enabled it
stops the instance again whenever it finds it available with the k3s server not running.

The schedule, not the RDS event, is the trigger: RDS delivers its events to EventBridge on a best
effort basis, and one lost event would leave the database running unnoticed.

The k3s server is the guard. Once DevPath is brought back the server runs, and the database is left
alone even if the rule was not disabled first. Disable the rule before starting the database
(devpath-gitops/docs/runbook-k3s-bootstrap.md 'keep the database stopped').
"""
import os

RULE = "devpath-rds-keep-stopped"
SERVER_IN_USE = ("pending", "running")
SUBJECT = "[DevPath/AWS] RDS {db} started by itself and was stopped again"
MESSAGE = """\
[DevPath/AWS] RDS {db} started by itself and was stopped again
DevPath is parked: the k3s server {server} is {server_state}. RDS starts a stopped database by
itself after seven days (RDS-EVENT-0154); this check found {db} available and stopped it again.
Expect the same mail about every seven days while DevPath stays parked.

To bring DevPath back, disable the EventBridge rule {rule} first, then start the database.
Runbook: devpath-gitops/docs/runbook-k3s-bootstrap.md 'keep the database stopped'."""


def _client(service):
    import boto3  # provided by the Lambda runtime; the tests inject their own clients

    return boto3.client(service)


def _error_code(error):
    return getattr(error, "response", {}).get("Error", {}).get("Code")


def database_status(rds, db):
    return rds.describe_db_instances(DBInstanceIdentifier=db)["DBInstances"][0]["DBInstanceStatus"]


def server_state(ec2, instance_id):
    try:
        reservations = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"]
    except Exception as error:
        if _error_code(error) == "InvalidInstanceID.NotFound":
            return "absent"
        raise
    instances = [instance for reservation in reservations for instance in reservation["Instances"]]
    return instances[0]["State"]["Name"] if instances else "absent"


def handler(event, context, *, rds=None, ec2=None, sns=None, environ=os.environ):
    db = environ["DB_IDENTIFIER"]
    server = environ["K3S_INSTANCE_ID"]
    rds = rds or _client("rds")
    status = database_status(rds, db)
    if status != "available":
        return {"db_status": status, "stopped": False}
    state = server_state(ec2 or _client("ec2"), server)
    if state in SERVER_IN_USE:
        return {"db_status": status, "server_state": state, "stopped": False}
    try:
        rds.stop_db_instance(DBInstanceIdentifier=db)
    except Exception as error:
        if _error_code(error) != "InvalidDBInstanceState":
            raise
        return {"db_status": status, "server_state": state, "stopped": False, "retry": "next run"}
    (sns or _client("sns")).publish(
        TopicArn=environ["TOPIC_ARN"],
        Subject=SUBJECT.format(db=db),
        Message=MESSAGE.format(db=db, server=server, server_state=state, rule=RULE),
    )
    return {"db_status": status, "server_state": state, "stopped": True}
