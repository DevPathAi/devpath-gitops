#!/usr/bin/env python3
"""Tell CloudWatch that this node's ollama serves both models.

Auto recovery makes "is there an instance" the wrong question: an instance can run while the join
or the model pull failed. One datapoint a minute says the service itself is up; the alarm and the
reminder Lambda read silence as "not recovered"
(documents docs/superpowers/specs/2026-10-07-gpu-node-auto-recovery-design.md, 3.4).
"""
import http.client
import json
import subprocess
import sys
import urllib.request

REGION = "ap-northeast-2"
NODE_NAME = "devpath-gpu"
METRIC_NAMESPACE = "DevPath/GPU"
METRIC_NAME = "OllamaReady"
POD_NAMESPACE = "devpath"
POD_LABEL = "app=ollama-gpu"
# The two models the ollama-gpu postStart hook pulls (apps/devpath-ollama-gpu/base/deployment.yaml).
REQUIRED_MODELS = frozenset({"qwen2.5:3b", "qwen2.5:7b"})
TAGS_TIMEOUT_SECONDS = 5


class NotReady(Exception):
    """ollama is not serving both models. Expected while a node boots."""


def run_command(args):
    return subprocess.run(args, capture_output=True, text=True, check=False)


def fetch_url(url):
    with urllib.request.urlopen(url, timeout=TAGS_TIMEOUT_SECONDS) as response:
        return response.read()


def ready_pod_address(run):
    listed = run([
        "k3s", "crictl", "pods", "--label", POD_LABEL,
        "--label", f"io.kubernetes.pod.namespace={POD_NAMESPACE}", "--state", "Ready", "-q",
    ])
    pod_ids = listed.stdout.split() if listed.returncode == 0 else []
    if not pod_ids:
        raise NotReady("no Ready ollama-gpu pod on this node")
    inspected = run(["k3s", "crictl", "inspectp", pod_ids[0]])
    try:
        address = json.loads(inspected.stdout)["status"]["network"]["ip"]
    except (ValueError, KeyError, TypeError):
        address = None
    if inspected.returncode != 0 or not isinstance(address, str) or not address:
        raise NotReady("the ollama-gpu pod has no address")
    return address


def served_models(fetch, address):
    try:
        models = json.loads(fetch(f"http://{address}:11434/api/tags"))["models"]
        return {model["name"] for model in models}
    except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError) as error:
        raise NotReady(f"ollama did not list its models ({error.__class__.__name__})") from None


def main(run=run_command, fetch=fetch_url):
    try:
        missing = REQUIRED_MODELS - served_models(fetch, ready_pod_address(run))
        if missing:
            raise NotReady("missing models: " + ", ".join(sorted(missing)))
    except NotReady as reason:
        print(f"not ready: {reason}")
        return 1
    published = run([
        "aws", "cloudwatch", "put-metric-data", "--region", REGION,
        "--namespace", METRIC_NAMESPACE, "--metric-name", METRIC_NAME,
        "--dimensions", f"Node={NODE_NAME}", "--value", "1",
    ])
    if published.returncode != 0:
        print(f"ready, but the datapoint was not written: {published.stderr.strip()[:300]}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
