import contextlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "infra" / "aws" / "gpu-node" / "bootstrap.py"
SPEC = importlib.util.spec_from_file_location("tested_gpu_node_bootstrap", SOURCE)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

TOKEN = "K10" + "ab" * 32 + "::q1w2e3.0123456789abcdef"
PASSWORD = "0f" * 16
MODEL_DIR = "/var/lib/rancher/k3s/storage/pvc-1234_devpath_ollama-gpu-models"
POSIX = os.name != "nt"


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeHost:
    """Answers the commands the bootstrap runs and records them in order."""

    def __init__(self, root, *, parameters=None, install_results=(0,)):
        self.root = root
        self.parameters = {
            "k3s-version": "v1.36.2+k3s1\n",
            "agent-token": TOKEN + "\n",
            "node-password": PASSWORD + "\n",
            "model-dir": MODEL_DIR + "\n",
        }
        self.parameters.update(parameters or {})
        self.install_results = list(install_results)
        self.calls = []
        self.timeouts = []
        self.seen_at_install = None
        self.slept = []
        self.now = 0.0

    def run(self, args, env=None, timeout=None):
        self.calls.append((list(args), dict(env) if env is not None else None))
        self.timeouts.append(timeout)
        if args[:3] == ["aws", "ssm", "get-parameter"]:
            name = args[args.index("--name") + 1].removeprefix(module.PARAMETER_PREFIX)
            value = self.parameters.get(name)
            if isinstance(value, list):  # one answer per read, the last one repeats
                value = value.pop(0) if len(value) > 1 else value[0]
            if value is None:
                return Result(254, "", "An error occurred (ParameterNotFound) when calling the GetParameter operation: ")
            return value if isinstance(value, Result) else Result(0, value)
        if args[:1] == ["bash"]:
            self.seen_at_install = {
                "password": (self.root / module.PASSWORD_FILE).read_text(encoding="ascii"),
                "token": (self.root / module.TOKEN_FILE).read_text(encoding="ascii"),
                "model_dir": (self.root / MODEL_DIR.lstrip("/")).is_dir(),
            }
            outcome = self.install_results.pop(0) if len(self.install_results) > 1 else self.install_results[0]
            return outcome if isinstance(outcome, Result) else Result(outcome)
        return Result(0)

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now

    def installs(self):
        return [(args, env) for args, env in self.calls if args[:1] == ["bash"]]


class GpuNodeBootstrapTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def bootstrap(self, host):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = module.main(run=host.run, root=self.root, sleep=host.sleep, clock=host.clock)
        return code, stdout.getvalue() + stderr.getvalue()

    def test_writes_the_password_and_the_token_before_the_agent_starts(self):
        # 2026-10-06: a node that starts under an existing name without the saved password is
        # rejected ("Node password rejected, duplicate hostname ..."), and a volume directory that
        # does not exist leaves the pod in FailedMount. All three have to be there first.
        host = FakeHost(self.root)
        code, _ = self.bootstrap(host)
        self.assertEqual(code, 0)
        self.assertEqual(
            host.seen_at_install,
            {"password": PASSWORD + "\n", "token": TOKEN + "\n", "model_dir": True},
        )

    @unittest.skipUnless(POSIX, "POSIX file modes")
    def test_secret_files_are_readable_by_root_only(self):
        host = FakeHost(self.root)
        self.bootstrap(host)
        for name in (module.PASSWORD_FILE, module.TOKEN_FILE):
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), 0o600, name)
        # local-path creates its volume directories 0777.
        self.assertEqual(stat.S_IMODE((self.root / MODEL_DIR.lstrip("/")).stat().st_mode), 0o777)

    def test_joins_under_the_fixed_name_with_the_gpu_label_and_taint(self):
        host = FakeHost(self.root)
        self.bootstrap(host)
        args, env = host.installs()[0]
        # pipefail: with get.k3s.io unreachable, curl fails and sh, fed nothing, would exit 0.
        self.assertEqual(args[:4], ["bash", "-o", "pipefail", "-c"])
        self.assertIn("curl -sfL https://get.k3s.io | sh -s - agent", args[4])
        self.assertIn("--node-name devpath-gpu", args[4])
        self.assertIn("--node-label devpath.ai/gpu=true", args[4])
        self.assertIn("--node-taint devpath.ai/gpu=true:NoSchedule", args[4])
        self.assertEqual(env["INSTALL_K3S_VERSION"], "v1.36.2+k3s1")
        self.assertEqual(env["K3S_URL"], "https://172.31.48.82:6443")
        self.assertEqual(env["K3S_TOKEN_FILE"], str(self.root / module.TOKEN_FILE))

    def test_no_parameter_value_reaches_a_command_line_or_the_environment(self):
        host = FakeHost(self.root)
        self.bootstrap(host)
        for args, env in host.calls:
            for text in [" ".join(args), *(env or {}).values()]:
                self.assertNotIn(TOKEN, text)
                self.assertNotIn(PASSWORD, text)

    def test_prints_no_parameter_value(self):
        host = FakeHost(self.root)
        code, output = self.bootstrap(host)
        self.assertEqual(code, 0)
        self.assertNotIn(TOKEN, output)
        self.assertNotIn(PASSWORD, output)

    def test_reads_the_secrets_decrypted_and_the_rest_plain(self):
        host = FakeHost(self.root)
        self.bootstrap(host)
        decrypted = {
            args[args.index("--name") + 1]: "--with-decryption" in args
            for args, _ in host.calls
            if args[:3] == ["aws", "ssm", "get-parameter"]
        }
        self.assertEqual(
            decrypted,
            {
                "/devpath/gpu-node/k3s-version": False,
                "/devpath/gpu-node/agent-token": True,
                "/devpath/gpu-node/node-password": True,
                "/devpath/gpu-node/model-dir": False,
            },
        )

    def test_continues_without_a_model_directory_when_the_parameter_does_not_exist(self):
        # At the cutover the volume does not exist yet; the provisioner creates its directory.
        host = FakeHost(self.root, parameters={"model-dir": None})
        code, _ = self.bootstrap(host)
        self.assertEqual(code, 0)
        self.assertFalse(host.seen_at_install["model_dir"])
        self.assertFalse((self.root / "var").exists())

    def test_refuses_a_model_directory_outside_the_volume_storage_root(self):
        # The directory is created 0777. A mistyped parameter must not do that to "/" or "/etc".
        for value in (
            "/",
            "/etc",
            "/var/lib/rancher/k3s/storage",
            "/var/lib/rancher/k3s/storage/",
            "/var/lib/rancher/k3s/storage/../../../../etc",
            "storage/pvc-1234_devpath_ollama-gpu-models",
        ):
            with self.subTest(value=value):
                host = FakeHost(self.root, parameters={"model-dir": value})
                code, output = self.bootstrap(host)
                self.assertEqual(code, 1)
                self.assertIn("model-dir", output)
                self.assertEqual(host.installs(), [])

    def test_trims_whitespace_around_parameter_values(self):
        host = FakeHost(
            self.root,
            parameters={
                "k3s-version": " v1.36.2+k3s1\n",
                "agent-token": "  " + TOKEN + " \n\n",
                "node-password": PASSWORD + "\r\n",
                "model-dir": MODEL_DIR + " \n",
            },
        )
        code, _ = self.bootstrap(host)
        self.assertEqual(code, 0)
        self.assertEqual((self.root / module.TOKEN_FILE).read_bytes(), (TOKEN + "\n").encode())
        self.assertEqual((self.root / module.PASSWORD_FILE).read_bytes(), (PASSWORD + "\n").encode())
        self.assertEqual(host.installs()[0][1]["INSTALL_K3S_VERSION"], "v1.36.2+k3s1")
        self.assertTrue((self.root / MODEL_DIR.lstrip("/")).is_dir())

    def test_retries_a_failed_install_until_it_succeeds(self):
        host = FakeHost(self.root, install_results=(1, 1, 0))
        code, _ = self.bootstrap(host)
        self.assertEqual(code, 0)
        self.assertEqual(len(host.installs()), 3)
        self.assertEqual(host.slept, [module.INSTALL_RETRY_SECONDS] * 2)

    def test_gives_up_when_the_install_budget_runs_out(self):
        host = FakeHost(self.root, install_results=(1,))
        code, output = self.bootstrap(host)
        self.assertEqual(code, 1)
        self.assertIn("k3s agent install failed", output)
        self.assertEqual(sum(host.slept), module.INSTALL_BUDGET_SECONDS)
        self.assertNotIn(TOKEN, output)
        self.assertNotIn(PASSWORD, output)
        # 2026-10-16 final review I1: the heartbeat timer is already on, so a node fixed by hand
        # later is seen as recovered; the agent itself was never started.
        self.assertIn(["systemctl", "enable", "--now", "devpath-gpu-heartbeat.timer"], [a for a, _ in host.calls])
        self.assertNotIn(["systemctl", "start", "--no-block", "k3s-agent"], [a for a, _ in host.calls])

    def test_retries_a_transient_parameter_read_failure(self):
        # 2026-10-16 final review I1: cloud-init runs this once per instance and the group's EC2
        # health check cannot see a failed join, so one throttled read must not end the recovery.
        throttled = Result(254, "", "An error occurred (ThrottlingException) when calling the GetParameter operation: Rate exceeded")
        host = FakeHost(self.root, parameters={"agent-token": [throttled, throttled, TOKEN + "\n"]})
        code, output = self.bootstrap(host)
        self.assertEqual(code, 0)
        self.assertEqual(host.slept, [module.INSTALL_RETRY_SECONDS] * 2)
        self.assertEqual((self.root / module.TOKEN_FILE).read_bytes(), (TOKEN + "\n").encode())
        self.assertNotIn(TOKEN, output)

    def test_installs_without_starting_and_then_starts_the_agent_without_waiting(self):
        # 2026-10-16 final review I2: the agent unit is Type=notify with TimeoutStartSec=0, so the
        # installer's own `systemctl restart` never returns while the join fails (rejected password,
        # revoked token, server unreachable). Install only; start without blocking; let the heartbeat
        # judge the join, and keep the agent's errors in its journal.
        host = FakeHost(self.root)
        code, _ = self.bootstrap(host)
        self.assertEqual(code, 0)
        args, env = host.installs()[0]
        self.assertEqual(env["INSTALL_K3S_SKIP_START"], "true")
        calls = [a for a, _ in host.calls]
        self.assertGreater(
            calls.index(["systemctl", "start", "--no-block", "k3s-agent"]), calls.index(args)
        )

    def test_bounds_each_install_attempt(self):
        # 2026-10-16 final review I2: a download that hangs must count as a failed attempt.
        host = FakeHost(self.root)
        self.bootstrap(host)
        install_index = [i for i, (a, _) in enumerate(host.calls) if a[:1] == ["bash"]][0]
        self.assertEqual(host.timeouts[install_index], module.INSTALL_ATTEMPT_TIMEOUT_SECONDS)

    def test_shows_why_an_install_attempt_failed_without_the_secrets(self):
        # 2026-10-16 final review I2: the operator's first look is cloud-init-output.log, which used
        # to end in "install failed N times" with no reason (a mistyped k3s-version is a 404).
        failed = Result(22, "[INFO]  Using v9.9.9 as release\n", "curl: (22) The requested URL returned error: 404\n" + TOKEN)
        host = FakeHost(self.root, install_results=(failed, 0))
        code, output = self.bootstrap(host)
        self.assertEqual(code, 0)
        self.assertIn("404", output)
        self.assertNotIn(TOKEN, output)

    def test_a_hung_command_comes_back_as_a_failure(self):
        result = module.run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.5)
        self.assertEqual(result.returncode, 124)
        self.assertIn("timed out", result.stderr)

    def test_stops_before_the_install_when_a_required_parameter_cannot_be_read(self):
        denied = Result(
            254, "", "An error occurred (AccessDeniedException) when calling the GetParameter operation: no"
        )
        host = FakeHost(self.root, parameters={"node-password": denied})
        code, output = self.bootstrap(host)
        self.assertEqual(code, 1)
        self.assertIn("/devpath/gpu-node/node-password", output)
        self.assertIn("AccessDeniedException", output)
        self.assertEqual(host.installs(), [])

    def test_a_missing_required_parameter_is_an_error_not_a_skip(self):
        host = FakeHost(self.root, parameters={"agent-token": None})
        code, output = self.bootstrap(host)
        self.assertEqual(code, 1)
        self.assertIn("/devpath/gpu-node/agent-token", output)
        self.assertEqual(host.installs(), [])

    def test_enables_the_heartbeat_timer_first_and_starts_the_agent_last(self):
        # 2026-10-16 final review I1: the timer used to come on only after every step succeeded, so
        # a node repaired by hand kept reporting nothing. The heartbeat is harmless before the join.
        host = FakeHost(self.root)
        self.bootstrap(host)
        order = [args for args, _ in host.calls if args[0] in ("bash", "systemctl", "aws")]
        self.assertEqual(
            order[:2],
            [["systemctl", "daemon-reload"], ["systemctl", "enable", "--now", "devpath-gpu-heartbeat.timer"]],
        )
        self.assertEqual(order[-2][0], "bash")
        self.assertEqual(order[-1], ["systemctl", "start", "--no-block", "k3s-agent"])

    def test_running_twice_leaves_the_same_files(self):
        self.bootstrap(FakeHost(self.root))
        code, _ = self.bootstrap(FakeHost(self.root))
        self.assertEqual(code, 0)
        self.assertEqual((self.root / module.PASSWORD_FILE).read_bytes(), (PASSWORD + "\n").encode())
        self.assertEqual((self.root / module.TOKEN_FILE).read_bytes(), (TOKEN + "\n").encode())


if __name__ == "__main__":
    unittest.main()
