"""Keep telling the operators while the GPU node's ollama is not serving.

The EC2 state-change rule reports a reclaim once. If that mail is missed nothing speaks again:
the GPU node was reclaimed on 2026-09-08 and stayed gone, unnoticed, for 23 days. This function
runs on a schedule and publishes to the same SNS topic until the node is back.

"Back" is the node's own heartbeat (infra/aws/gpu-node/heartbeat.py), not the existence of an
instance: with auto recovery an instance can be up while the join or the model pull failed.
"""
import datetime
import os

DEFAULT_ROLE_TAG = "k3s-agent-gpu"
DEFAULT_NODE = "devpath-gpu"
METRIC_NAMESPACE = "DevPath/GPU"
METRIC_NAME = "OllamaReady"
WINDOW = datetime.timedelta(minutes=15)
SUBJECT_NO_INSTANCE = "[DevPath/AWS] GPU node is not serving: no instance"
SUBJECT_NOT_READY = "[DevPath/AWS] GPU node is not serving: instance up, ollama not ready"
MESSAGE_NO_INSTANCE = """\
[DevPath/AWS] GPU node is not serving: no instance
No heartbeat from node {node} for 15 minutes, and no pending or running EC2 instance carries
tag role={role}.

Auto recovery (Auto Scaling group devpath-gpu-node) has not produced an instance. The usual cause
is no spot capacity; the group's activity history says why.
Until the node is back, learning-path generation is down and the Claude fallback of review,
community-seed and retention has nowhere to go.
Runbook: devpath-gitops/docs/runbook-k3s-bootstrap.md 'auto recovery'.

This reminder repeats on a schedule until the heartbeat returns."""
MESSAGE_NOT_READY = """\
[DevPath/AWS] GPU node is not serving: instance up, ollama not ready
No heartbeat from node {node} for 15 minutes, although an instance is up: {instances}.

The instance booted but ollama is not serving both models. Look, in this order, at the node's
/var/log/cloud-init-output.log (the bootstrap), at whether the node joined, and at the ollama-gpu
pod's events (FailedMount means the model-dir parameter no longer matches the volume).
Until it serves, learning-path generation is down and the Claude fallback of review,
community-seed and retention has nowhere to go.
Runbook: devpath-gitops/docs/runbook-k3s-bootstrap.md 'auto recovery'.

This reminder repeats on a schedule until the heartbeat returns."""


def _client(service):
    import boto3  # provided by the Lambda runtime; the tests inject their own clients

    return boto3.client(service)


def heartbeats(cloudwatch, node, now):
    statistics = cloudwatch.get_metric_statistics(
        Namespace=METRIC_NAMESPACE,
        MetricName=METRIC_NAME,
        Dimensions=[{"Name": "Node", "Value": node}],
        StartTime=now - WINDOW,
        EndTime=now,
        Period=int(WINDOW.total_seconds()),
        Statistics=["Sum"],
    )
    return sum(point["Sum"] for point in statistics["Datapoints"])


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


def handler(event, context, *, ec2=None, sns=None, cloudwatch=None, environ=os.environ, now=None):
    node = environ.get("HEARTBEAT_NODE", DEFAULT_NODE)
    role = environ.get("GPU_ROLE_TAG", DEFAULT_ROLE_TAG)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    count = heartbeats(cloudwatch or _client("cloudwatch"), node, now)
    if count > 0:
        return {"heartbeats": count, "notified": False}
    present = gpu_instances(ec2 or _client("ec2"), role)
    subject, message = (
        (SUBJECT_NOT_READY, MESSAGE_NOT_READY) if present else (SUBJECT_NO_INSTANCE, MESSAGE_NO_INSTANCE)
    )
    (sns or _client("sns")).publish(
        TopicArn=environ["TOPIC_ARN"],
        Subject=subject,
        Message=message.format(node=node, role=role, instances=", ".join(present)),
    )
    return {"heartbeats": 0, "gpu_instances": present, "notified": True}
