"""Keep telling the operators while the GPU spot node is gone.

The EC2 state-change rule reports a reclaim once. If that mail is missed nothing speaks again:
the GPU node was reclaimed on 2026-09-08 and stayed gone, unnoticed, for 23 days. This function
runs on a schedule and publishes to the same SNS topic for as long as no GPU instance is pending
or running.

It sees EC2 only. An instance that runs but never joined the cluster keeps it silent.
"""
import os

DEFAULT_ROLE_TAG = "k3s-agent-gpu"
SUBJECT = "[DevPath/AWS] GPU spot node is still missing"
MESSAGE = """\
[DevPath/AWS] GPU spot node is still missing
No pending or running EC2 instance carries tag role={role}.

Until it is rebuilt, learning-path generation is down and the Claude fallback of review,
community-seed and retention has nowhere to go.
Recovery: devpath-gitops/docs/runbook-k3s-bootstrap.md 'spot reclaim recovery'.
A new node alone leaves the pod Pending: delete the old node, the old pod, then the PVC.
Tag the new instance role={role}, or this reminder keeps coming.

This reminder repeats on a schedule until such an instance is pending or running."""


def _client(service):
    import boto3  # provided by the Lambda runtime; the tests inject their own clients

    return boto3.client(service)


def gpu_instances(ec2, role):
    pages = ec2.get_paginator("describe_instances").paginate(
        Filters=[
            {"Name": "tag:role", "Values": [role]},
            {"Name": "instance-state-name", "Values": ["pending", "running"]},
        ]
    )
    return [
        instance["InstanceId"]
        for page in pages
        for reservation in page["Reservations"]
        for instance in reservation["Instances"]
    ]


def handler(event, context, *, ec2=None, sns=None, environ=os.environ):
    role = environ.get("GPU_ROLE_TAG", DEFAULT_ROLE_TAG)
    present = gpu_instances(ec2 or _client("ec2"), role)
    if present:
        return {"gpu_instances": present, "notified": False}
    (sns or _client("sns")).publish(
        TopicArn=environ["TOPIC_ARN"],
        Subject=SUBJECT,
        Message=MESSAGE.format(role=role),
    )
    return {"gpu_instances": [], "notified": True}
