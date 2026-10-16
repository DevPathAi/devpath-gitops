import importlib.util
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "infra" / "aws" / "gpu-node"


def load(name):
    spec = importlib.util.spec_from_file_location(f"tested_gpu_node_{name}", SOURCES / f"{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


module = load("render_user_data")
bootstrap = load("bootstrap")
heartbeat = load("heartbeat")

WRITTEN = {
    "/usr/local/lib/devpath-gpu/bootstrap.py": "bootstrap.py",
    "/usr/local/lib/devpath-gpu/heartbeat.py": "heartbeat.py",
    "/etc/systemd/system/devpath-gpu-heartbeat.service": "devpath-gpu-heartbeat.service",
    "/etc/systemd/system/devpath-gpu-heartbeat.timer": "devpath-gpu-heartbeat.timer",
}


class GpuNodeUserDataTest(unittest.TestCase):
    def test_cloud_init_writes_every_source_file_byte_for_byte(self):
        rendered = module.render()
        self.assertTrue(rendered.startswith("#cloud-config\n"))
        document = yaml.safe_load(rendered)
        self.assertEqual([entry["path"] for entry in document["write_files"]], list(WRITTEN))
        for entry in document["write_files"]:
            source = (SOURCES / WRITTEN[entry["path"]]).read_bytes().decode("utf-8")
            self.assertEqual(entry["content"], source, entry["path"])
            self.assertEqual(entry["permissions"], "0644")
            self.assertEqual(entry["owner"], "root:root")

    def test_cloud_init_runs_the_bootstrap_once(self):
        document = yaml.safe_load(module.render())
        self.assertEqual(document["runcmd"], [["/usr/bin/python3", "/usr/local/lib/devpath-gpu/bootstrap.py"]])

    def test_fits_the_ec2_user_data_limit(self):
        self.assertLessEqual(len(module.render().encode("utf-8")), 16384)

    def test_the_same_sources_render_the_same_bytes(self):
        self.assertEqual(module.render(), module.render())
        self.assertNotIn("\r", module.render())

    def test_carries_nothing_that_looks_like_a_secret(self):
        # Everything sensitive is read from Parameter Store at boot. user-data is readable through
        # the console and DescribeInstanceAttribute, so a token or a password must never be here.
        rendered = module.render()
        self.assertIsNone(re.search(r"K10[0-9a-f]{16,}", rendered))
        self.assertIsNone(re.search(r"\b[0-9a-f]{32}\b", rendered))

    def test_refuses_a_source_that_yaml_could_bend(self):
        for name, damage in (
            ("crlf", lambda text: text.replace("\n", "\r\n")),
            ("tab", lambda text: text.replace("    ", "\t", 1)),
            ("no final newline", lambda text: text.rstrip("\n")),
            ("two final newlines", lambda text: text + "\n"),
            ("indented first line", lambda text: "  " + text),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                copy = Path(directory)
                for source in WRITTEN.values():
                    shutil.copy(SOURCES / source, copy / source)
                target = copy / "heartbeat.py"
                target.write_bytes(damage(target.read_bytes().decode("utf-8")).encode("utf-8"))
                with self.assertRaisesRegex(ValueError, "heartbeat.py"):
                    module.render(copy)

    def test_the_units_run_the_heartbeat_that_was_written(self):
        service = (SOURCES / "devpath-gpu-heartbeat.service").read_text(encoding="utf-8")
        timer = (SOURCES / "devpath-gpu-heartbeat.timer").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/usr/bin/python3 /usr/local/lib/devpath-gpu/heartbeat.py\n", service)
        # Exit 1 is "not ready yet", which is normal while a node boots; only exit 2 is a failure.
        self.assertIn("SuccessExitStatus=1\n", service)
        self.assertIn("OnUnitActiveSec=60\n", timer)
        self.assertIn("WantedBy=timers.target\n", timer)
        self.assertEqual(bootstrap.HEARTBEAT_TIMER, "devpath-gpu-heartbeat.timer")

    def test_the_two_scripts_agree_on_who_and_where_they_are(self):
        self.assertEqual(bootstrap.NODE_NAME, heartbeat.NODE_NAME)
        self.assertEqual(bootstrap.REGION, heartbeat.REGION)


if __name__ == "__main__":
    unittest.main()
