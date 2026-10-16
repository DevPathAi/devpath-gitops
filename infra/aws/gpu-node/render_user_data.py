#!/usr/bin/env python3
"""Render the launch template's user-data from the files next to this one.

cloud-init writes the two scripts and the two systemd units, then runs the bootstrap once. The
output holds no secret: everything sensitive is read from Parameter Store at boot.

    py render_user_data.py            # the user-data bytes
    py render_user_data.py --sha256   # their hash, to compare with the launch template
"""
import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = (
    ("bootstrap.py", "/usr/local/lib/devpath-gpu/bootstrap.py"),
    ("heartbeat.py", "/usr/local/lib/devpath-gpu/heartbeat.py"),
    ("devpath-gpu-heartbeat.service", "/etc/systemd/system/devpath-gpu-heartbeat.service"),
    ("devpath-gpu-heartbeat.timer", "/etc/systemd/system/devpath-gpu-heartbeat.timer"),
)
RUN = ("/usr/bin/python3", "/usr/local/lib/devpath-gpu/bootstrap.py")
USER_DATA_LIMIT = 16384
INDENT = " " * 6


def render(source_dir=HERE):
    lines = ["#cloud-config", "write_files:"]
    for name, target in FILES:
        text = (Path(source_dir) / name).read_bytes().decode("utf-8")
        # A YAML block scalar keeps these bytes only if the indentation is unambiguous.
        if "\r" in text or "\t" in text or not text.endswith("\n") or text.endswith("\n\n") or text[:1].isspace():
            raise ValueError(
                f"{name}: needs LF line ends, no tabs, an unindented first line and exactly one final newline"
            )
        lines += [f"  - path: {target}", "    owner: root:root", "    permissions: '0644'", "    content: |"]
        lines += [INDENT + line if line else "" for line in text[:-1].split("\n")]
    lines += ["runcmd:", "  - [" + ", ".join(RUN) + "]"]
    rendered = "\n".join(lines) + "\n"
    if len(rendered.encode("utf-8")) > USER_DATA_LIMIT:
        raise ValueError("user-data is over the 16 KB EC2 limit")
    return rendered


def main(argv):
    rendered = render().encode("utf-8")
    if argv[1:] == ["--sha256"]:
        print(hashlib.sha256(rendered).hexdigest())
    else:
        sys.stdout.buffer.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
