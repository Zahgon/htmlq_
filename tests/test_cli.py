"""Migration of `tests/cli.rs`.

The Rust file declares its cases through a `cmd_success_tests!` macro that
expands each `(stdin, args, expected)` tuple into a `#[test]` running the binary
and asserting `.success()` plus an exact stdout diff. `pytest.mark.parametrize`
plays the macro's role here, with the same three tuples, the same argument
vectors and the same expected stdout, byte for byte.
"""

from __future__ import annotations

import pytest

from .conftest import run_htmlq

# cmd_success_tests! { .. } -- (name, (stdin, args, expected))
CMD_SUCCESS_TESTS = [
    (
        "find_by_class",
        (
            '<html><head></head><body><div class="hi">'
            '<a href="/foo/bar">Hello</a></div></body></html>',
            [".hi"],
            '<div class="hi"><a href="/foo/bar">Hello</a></div>\n',
        ),
    ),
    (
        "find_by_id",
        (
            '<html><head></head><body><div id="my-id">'
            '<a href="/foo/bar">Hello</a></div></body></html>',
            ["#my-id"],
            '<div id="my-id"><a href="/foo/bar">Hello</a></div>\n',
        ),
    ),
    (
        "remove_links",
        (
            '<html><head></head><body><div id="my-id">'
            '<a href="/foo/bar">Hello</a></div></body></html>',
            ["#my-id", "--remove-nodes", "a"],
            '<div id="my-id"></div>\n',
        ),
    ),
]


@pytest.mark.parametrize(
    "value", [case for _, case in CMD_SUCCESS_TESTS],
    ids=[name for name, _ in CMD_SUCCESS_TESTS],
)
def test_cmd_success(value):
    stdin, args, expected = value
    run_htmlq(args, stdin=stdin).assert_success().assert_stdout_diff(expected)
