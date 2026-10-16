import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from urllib.error import URLError


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "infra" / "aws" / "gpu-node" / "heartbeat.py"
SPEC = importlib.util.spec_from_file_location("tested_gpu_node_heartbeat", SOURCE)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

POD_ID = "b67356d746769616a7234a1c50ba4531d3316a6a41da9a5d101a91d209f67f40"
INSPECT = json.dumps({"status": {"network": {"ip": "10.42.7.4"}, "metadata": {"namespace": "devpath"}}})
TAGS = json.dumps({"models": [{"name": "qwen2.5:7b"}, {"name": "qwen2.5:3b"}]}).encode()
PUBLISH = [
    "aws", "cloudwatch", "put-metric-data", "--region", "ap-northeast-2",
    "--namespace", "DevPath/GPU", "--metric-name", "OllamaReady",
    "--dimensions", "Node=devpath-gpu", "--value", "1",
]


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeNode:
    def __init__(self, *, pods=POD_ID + "\n", inspect=INSPECT, tags=TAGS, publish=None):
        self.pods = pods
        self.inspect = inspect
        self.tags = tags
        self.publish = publish or Result(0)
        self.commands = []
        self.fetched = []

    def run(self, args):
        self.commands.append(list(args))
        if args[:3] == ["k3s", "crictl", "pods"]:
            return self.pods if isinstance(self.pods, Result) else Result(0, self.pods)
        if args[:3] == ["k3s", "crictl", "inspectp"]:
            return self.inspect if isinstance(self.inspect, Result) else Result(0, self.inspect)
        if args[:3] == ["aws", "cloudwatch", "put-metric-data"]:
            return self.publish
        raise AssertionError(f"unexpected command: {args}")

    def fetch(self, url):
        self.fetched.append(url)
        if isinstance(self.tags, Exception):
            raise self.tags
        return self.tags

    @property
    def published(self):
        return [command for command in self.commands if command[:3] == ["aws", "cloudwatch", "put-metric-data"]]


class GpuNodeHeartbeatTest(unittest.TestCase):
    def beat(self, node):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = module.main(run=node.run, fetch=node.fetch)
        return code, stdout.getvalue() + stderr.getvalue()

    def test_writes_one_datapoint_while_ollama_serves_both_models(self):
        node = FakeNode()
        code, _ = self.beat(node)
        self.assertEqual(code, 0)
        self.assertEqual(node.published, [PUBLISH])
        self.assertEqual(node.fetched, ["http://10.42.7.4:11434/api/tags"])

    def test_looks_for_the_ready_ollama_gpu_pod_of_the_devpath_namespace(self):
        node = FakeNode()
        self.beat(node)
        self.assertEqual(
            node.commands[0],
            [
                "k3s", "crictl", "pods", "--label", "app=ollama-gpu",
                "--label", "io.kubernetes.pod.namespace=devpath", "--state", "Ready", "-q",
            ],
        )
        self.assertEqual(node.commands[1], ["k3s", "crictl", "inspectp", POD_ID])

    def test_other_models_next_to_the_two_do_not_matter(self):
        tags = json.dumps({"models": [{"name": n} for n in ("qwen2.5:14b", "qwen2.5:3b", "qwen2.5:7b")]}).encode()
        code, _ = self.beat(FakeNode(tags=tags))
        self.assertEqual(code, 0)

    def test_stays_silent_while_no_pod_is_ready(self):
        # The state right after a reclaim: the instance is up, the pod is not.
        for pods in ("", "\n", Result(1, "", "runtime is not ready")):
            with self.subTest(pods=pods):
                node = FakeNode(pods=pods)
                code, output = self.beat(node)
                self.assertEqual(code, 1)
                self.assertEqual(node.published, [])
                self.assertIn("not ready", output)

    def test_stays_silent_while_the_pod_has_no_address(self):
        for inspect in (
            "{}",
            json.dumps({"status": {"network": {"ip": ""}}}),
            json.dumps({"status": {"network": None}}),
            "not json",
            Result(1, "", "not found"),
        ):
            with self.subTest(inspect=inspect):
                node = FakeNode(inspect=inspect)
                code, _ = self.beat(node)
                self.assertEqual(code, 1)
                self.assertEqual(node.published, [])

    def test_stays_silent_when_ollama_does_not_answer(self):
        for failure in (URLError("connection refused"), TimeoutError("timed out"), ConnectionResetError()):
            with self.subTest(failure=failure):
                node = FakeNode(tags=failure)
                code, _ = self.beat(node)
                self.assertEqual(code, 1)
                self.assertEqual(node.published, [])

    def test_stays_silent_on_a_body_that_is_not_a_model_list(self):
        # A 200 that is not ollama's answer must neither count as ready nor crash the unit.
        for body in (
            b"<html>bad gateway</html>",
            b"",
            b"{}",
            b'{"models": null}',
            b'{"models": [{"model": "qwen2.5:3b"}]}',
            b'{"models": ["qwen2.5:3b", "qwen2.5:7b"]}',
            b"[1]",
            b"\xff\xfe",
        ):
            with self.subTest(body=body):
                node = FakeNode(tags=body)
                code, _ = self.beat(node)
                self.assertEqual(code, 1)
                self.assertEqual(node.published, [])

    def test_stays_silent_while_a_model_is_still_being_pulled(self):
        # postStart pulls in the background, so a Ready pod can still lack the 7b model.
        node = FakeNode(tags=json.dumps({"models": [{"name": "qwen2.5:3b"}]}).encode())
        code, output = self.beat(node)
        self.assertEqual(code, 1)
        self.assertEqual(node.published, [])
        self.assertIn("qwen2.5:7b", output)

    def test_a_similar_tag_is_not_the_required_model(self):
        tags = json.dumps({"models": [{"name": "qwen2.5:3b"}, {"name": "qwen2.5:7b-instruct"}]}).encode()
        node = FakeNode(tags=tags)
        code, _ = self.beat(node)
        self.assertEqual(code, 1)
        self.assertEqual(node.published, [])

    def test_a_datapoint_that_was_not_written_is_a_failure(self):
        # Silence means "not recovered" to the alarm; a ready node that cannot write must say so.
        node = FakeNode(publish=Result(254, "", "An error occurred (AccessDenied) when calling PutMetricData"))
        code, output = self.beat(node)
        self.assertEqual(code, 2)
        self.assertIn("AccessDenied", output)


if __name__ == "__main__":
    unittest.main()
