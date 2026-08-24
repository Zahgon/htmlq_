"""`--output` writes to a file rather than stdout.

The parity corpus compares stdout, stderr and exit status, so it cannot check
what lands in a *file*. The expected values below were read off the original
Rust binary: writing to a path leaves stdout empty and puts the same bytes in
the file that would otherwise have been printed, newline included.
"""

from __future__ import annotations

from .conftest import run_htmlq


def test_output_goes_to_the_file_not_stdout(tmp_path):
    destination = tmp_path / "out.html"

    result = run_htmlq(["-o", str(destination), "p"], stdin="<p>x</p>")

    assert result.status == 0
    assert result.stdout == b""
    assert result.stderr == b""
    assert destination.read_bytes() == b"<p>x</p>\n"


def test_output_file_is_truncated_and_holds_every_match(tmp_path):
    destination = tmp_path / "out.html"
    destination.write_bytes(b"stale content that must not survive")

    result = run_htmlq(["-o", str(destination), "li"], stdin="<ul><li>1</li><li>2</li></ul>")

    assert result.status == 0
    assert result.stdout == b""
    assert destination.read_bytes() == b"<li>1</li>\n<li>2</li>\n"


def test_output_file_written_for_text_mode(tmp_path):
    destination = tmp_path / "out.txt"

    result = run_htmlq(["-o", str(destination), "-t", "p"], stdin="<p>hello <b>world</b></p>")

    assert result.status == 0
    assert result.stdout == b""
    assert destination.read_bytes() == b"hello world\n"
