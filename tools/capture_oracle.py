"""Record the original Rust binary's behaviour for every case in tests/cases.py.

Run this against a built `htmlq-rust-test` image to (re)generate
`tests/oracle.json`, the fixture `tests/test_parity.py` asserts against:

    docker build -f ../../docker/htmlq-rust-test.Dockerfile \
                 -t htmlq-rust-test ../../scraped_repos/Rust/mgdm_htmlq
    python tools/capture_oracle.py

The whole corpus runs inside one container: a generated shell script feeds each
case's stdin in through base64 (so bytes survive untouched) and prints the
exit status plus base64 stdout/stderr, one record per line. Doing it in a single
`docker run` keeps a hundred-odd cases to a couple of seconds instead of a
hundred container starts.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.cases import CASES  # noqa: E402

IMAGE = os.environ.get("HTMLQ_ORACLE_IMAGE", "htmlq-rust-test")
BINARY = os.environ.get("HTMLQ_ORACLE_BINARY", "/src/target/debug/htmlq")
OUTPUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests",
    "oracle.json",
)


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def build_script() -> str:
    lines = ["set -u"]
    for name, argv, stdin in CASES:
        stdin_b64 = base64.b64encode(stdin.encode("utf-8")).decode("ascii")
        args = " ".join(shell_quote(arg) for arg in argv)
        lines.append(
            "printf '%s' {stdin} | base64 -d > /tmp/in.bin".format(
                stdin=shell_quote(stdin_b64)
            )
        )
        lines.append(
            "{binary} {args} < /tmp/in.bin > /tmp/out.bin 2> /tmp/err.bin; "
            "status=$?".format(binary=BINARY, args=args)
        )
        lines.append(
            "printf '%s\\t%s\\t%s\\t%s\\n' {name} \"$status\" "
            '"$(base64 -w0 < /tmp/out.bin)" "$(base64 -w0 < /tmp/err.bin)"'.format(
                name=shell_quote(name)
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    script = build_script()
    completed = subprocess.run(
        ["docker", "run", "--rm", "-i", "--network", "none", "--entrypoint", "sh", IMAGE, "-s"],
        input=script.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr.decode("utf-8", "replace"))
        return completed.returncode

    records = {}
    for line in completed.stdout.decode("utf-8").splitlines():
        if not line.strip():
            continue
        name, status, out_b64, err_b64 = line.split("\t")
        records[name] = {
            "status": int(status),
            "stdout": base64.b64decode(out_b64).decode("utf-8", "surrogateescape"),
            "stderr": base64.b64decode(err_b64).decode("utf-8", "surrogateescape"),
        }

    missing = [name for name, _, _ in CASES if name not in records]
    if missing:
        sys.stderr.write("no record captured for: %s\n" % ", ".join(missing))
        return 1

    with open(OUTPUT, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(records, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    print("recorded %d cases -> %s" % (len(records), OUTPUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
