"""Byte-for-byte parity against the original Rust binary.

`tests/oracle.json` was recorded by running every case in `tests/cases.py`
through the real `mgdm/htmlq` binary (see `tools/capture_oracle.py`). These
tests replay the same corpus through the Python port and require the stdout,
stderr and exit status to match exactly -- so the coverage here is not "the
Python behaves sensibly" but "the Python behaves identically", including panic
text, clap's usage errors, and the exit codes 0 / 2 / 101.
"""

from __future__ import annotations

import json
import os

import pytest

from .cases import CASES
from .conftest import run_htmlq

ORACLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oracle.json")

with open(ORACLE_PATH, encoding="utf-8") as _handle:
    ORACLE = json.load(_handle)


@pytest.mark.parametrize(
    "case", CASES, ids=[name for name, _, _ in CASES]
)
def test_matches_rust_binary(case):
    name, argv, stdin = case
    expected = ORACLE[name]

    result = run_htmlq(argv, stdin=stdin)

    assert result.stdout.decode("utf-8") == expected["stdout"], "stdout differs"
    assert result.stderr.decode("utf-8") == expected["stderr"], "stderr differs"
    assert result.status == expected["status"], "exit status differs"


def test_every_case_has_a_recording():
    """Guards against a case being added without re-running the oracle."""
    missing = sorted({name for name, _, _ in CASES} - set(ORACLE))
    assert not missing, "no oracle recording for: %s" % ", ".join(missing)
