"""Shared helpers: run the CLI the way `assert_cmd::Command` runs the binary."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


class CommandResult:
    """The outcome of one CLI run: raw bytes, plus the exit status."""

    __slots__ = ("status", "stdout", "stderr")

    def __init__(self, status: int, stdout: bytes, stderr: bytes):
        self.status = status
        self.stdout = stdout
        self.stderr = stderr

    # The three assertions `tests/cli.rs` chains onto `Command::cargo_bin`.
    def assert_success(self) -> "CommandResult":
        assert self.status == 0, (
            "expected success, got status %d\nstderr:\n%s"
            % (self.status, self.stderr.decode("utf-8", "replace"))
        )
        return self

    def assert_stdout_diff(self, expected: str) -> "CommandResult":
        assert self.stdout.decode("utf-8") == expected
        return self


def run_htmlq(args, stdin: str = "") -> CommandResult:
    """Invoke `python -m htmlq` -- the console script's own entry point.

    A subprocess rather than an in-process call, so exit status, stream
    buffering and byte-level output are all exercised as the user sees them.
    """
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        REPO_ROOT if not existing else REPO_ROOT + os.pathsep + existing
    )
    environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, "-m", "htmlq"] + list(args),
        input=stdin.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


@pytest.fixture(scope="session")
def htmlq():
    return run_htmlq
