#!/usr/bin/env python3
"""Join this instance to the cluster as the node it replaces.

A spot reclaim used to need three manual deletions because the replacement joined under a new
name while the model volume stayed pinned to the old one. With the same node name and the same
node password the server takes the new machine for the same Node, and nothing has to be deleted
(measured 2026-10-06; documents docs/superpowers/specs/2026-10-07-gpu-node-auto-recovery-design.md).

cloud-init runs this once. Every step can run again with the same result. No value read from
Parameter Store is printed, passed on a command line or put in an environment variable.
"""
import os
import posixpath
import re
import subprocess
import sys
import time
from pathlib import Path

REGION = "ap-northeast-2"
PARAMETER_PREFIX = "/devpath/gpu-node/"
NODE_NAME = "devpath-gpu"
SERVER_URL = "https://172.31.48.82:6443"
NODE_LABEL = "devpath.ai/gpu=true"
NODE_TAINT = "devpath.ai/gpu=true:NoSchedule"
PASSWORD_FILE = "etc/rancher/node/password"
TOKEN_FILE = "etc/rancher/k3s/agent-token"
STORAGE_ROOT = "/var/lib/rancher/k3s/storage/"
INSTALL_PIPELINE = (
    "curl -sfL https://get.k3s.io | sh -s - agent"
    f" --node-name {NODE_NAME} --node-label {NODE_LABEL} --node-taint {NODE_TAINT}"
)
# One budget for the whole boot: the parameter reads and the install share it. cloud-init runs
# this once per instance and the group's EC2 health check cannot see a failed join, so a transient
# error must be retried here or the recovery ends (2026-10-16 review).
INSTALL_BUDGET_SECONDS = 1800
INSTALL_RETRY_SECONDS = 30
INSTALL_ATTEMPT_TIMEOUT_SECONDS = 600
INSTALL_OUTPUT_LINES = 20
TIMED_OUT = 124
HEARTBEAT_TIMER = "devpath-gpu-heartbeat.timer"
AGENT_SERVICE = "k3s-agent"


class BootstrapError(Exception):
    """A step failed and the operator has to read why. Never carries a parameter value."""


def run_command(args, env=None, timeout=None):
    try:
        return subprocess.run(args, env=env, capture_output=True, text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as expired:

        def text(value):
            if value is None:
                return ""
            return value.decode("utf-8", "replace") if isinstance(value, bytes) else value

        return subprocess.CompletedProcess(
            args, TIMED_OUT, text(expired.stdout), text(expired.stderr) + f"\ntimed out after {timeout}s"
        )


class Budget:
    """Retry a step on BootstrapError until the boot's shared budget is spent."""

    def __init__(self, sleep, clock):
        self.sleep = sleep
        self.clock = clock
        self.deadline = clock() + INSTALL_BUDGET_SECONDS

    def retry(self, attempt):
        attempts = 0
        while True:
            attempts += 1
            try:
                return attempt(), attempts
            except BootstrapError as error:
                if self.clock() >= self.deadline:
                    raise BootstrapError(f"{error}; gave up after {attempts} attempts in {INSTALL_BUDGET_SECONDS}s") from None
                print(f"{error}; retrying in {INSTALL_RETRY_SECONDS}s", file=sys.stderr)
                self.sleep(INSTALL_RETRY_SECONDS)


def redact(text, secrets):
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


def read_parameter(run, name, *, decrypt=False, required=True):
    args = [
        "aws", "ssm", "get-parameter", "--region", REGION, "--name", PARAMETER_PREFIX + name,
        "--query", "Parameter.Value", "--output", "text",
    ]
    if decrypt:
        args.append("--with-decryption")
    result = run(args)
    if result.returncode != 0:
        code = re.search(r"\((\w+)\)", result.stderr)
        reason = code.group(1) if code else f"exit {result.returncode}"
        if reason == "ParameterNotFound" and not required:
            return None
        raise BootstrapError(f"cannot read parameter {PARAMETER_PREFIX}{name} ({reason})")
    value = result.stdout.strip()
    if not value:
        raise BootstrapError(f"parameter {PARAMETER_PREFIX}{name} is empty")
    return value


def write_secret(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as handle:
        handle.write(value + "\n")
    os.chmod(path, 0o600)


def ensure_model_directory(root, value):
    # This directory is made world-writable, so a mistyped parameter must not reach "/" or "/etc".
    path = posixpath.normpath(value)
    if not path.startswith(STORAGE_ROOT):
        raise BootstrapError(f"parameter {PARAMETER_PREFIX}model-dir is not a directory under {STORAGE_ROOT}")
    target = root / path.lstrip("/")
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(target, 0o777)
    return path


def install_agent(run, version, token_file, budget, secrets):
    # INSTALL_K3S_SKIP_START: the agent unit is Type=notify with TimeoutStartSec=0, so the installer's
    # own `systemctl restart` never returns while the join fails. Install only; main() starts it.
    env = dict(
        os.environ,
        INSTALL_K3S_VERSION=version,
        K3S_URL=SERVER_URL,
        K3S_TOKEN_FILE=str(token_file),
        INSTALL_K3S_SKIP_START="true",
    )

    def attempt():
        # pipefail: with get.k3s.io unreachable, curl fails and sh, fed nothing, would exit 0.
        result = run(
            ["bash", "-o", "pipefail", "-c", INSTALL_PIPELINE], env=env, timeout=INSTALL_ATTEMPT_TIMEOUT_SECONDS
        )
        if result.returncode != 0:
            # cloud-init-output.log is where the operator looks first: say why, never what the secrets are.
            output = redact((result.stdout or "") + (result.stderr or ""), secrets)
            for line in output.splitlines()[-INSTALL_OUTPUT_LINES:]:
                print("  " + line[:300], file=sys.stderr)
            raise BootstrapError(f"k3s agent install failed (exit {result.returncode})")

    _, attempts = budget.retry(attempt)
    return attempts


def main(run=run_command, root=Path("/"), sleep=time.sleep, clock=time.monotonic):
    try:
        # The heartbeat comes on first: it reports "not ready" until ollama serves, and a node that
        # is repaired by hand after a failed boot is then seen as recovered (2026-10-16 review).
        for args in (["systemctl", "daemon-reload"], ["systemctl", "enable", "--now", HEARTBEAT_TIMER]):
            if run(args).returncode != 0:
                raise BootstrapError(f"cannot enable {HEARTBEAT_TIMER}")
        print("heartbeat timer enabled")
        budget = Budget(sleep, clock)
        version, _ = budget.retry(lambda: read_parameter(run, "k3s-version"))
        token, _ = budget.retry(lambda: read_parameter(run, "agent-token", decrypt=True))
        password, _ = budget.retry(lambda: read_parameter(run, "node-password", decrypt=True))
        model_dir, _ = budget.retry(lambda: read_parameter(run, "model-dir", required=False))
        write_secret(root / PASSWORD_FILE, password)
        write_secret(root / TOKEN_FILE, token)
        print("wrote the node password and the agent token")
        if model_dir is None:
            print("no model-dir parameter: the provisioner will create the volume directory")
        else:
            print(f"volume directory ready: {ensure_model_directory(root, model_dir)}")
        attempts = install_agent(run, version, root / TOKEN_FILE, budget, (token, password))
        print(f"k3s agent {version} installed as {NODE_NAME} (attempt {attempts})")
        if run(["systemctl", "start", "--no-block", AGENT_SERVICE]).returncode != 0:
            raise BootstrapError(f"cannot start {AGENT_SERVICE}")
        print(f"{AGENT_SERVICE} starting; the heartbeat reports once ollama-gpu serves both models")
    except BootstrapError as error:
        print(f"gpu-node bootstrap failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
