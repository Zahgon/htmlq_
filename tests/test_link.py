"""Migration of the `#[cfg(test)] mod tests` block in `src/link.rs`.

The Rust block holds two macros -- `rewrite_tests!` and `detect_base_tests!` --
plus the `make_doc` / `serialize_doc` helpers they share. Both macros become
parametrized tests here, carrying the same inputs and the same expected values.

The Rust tests live inside the module under test; in Python they live in
`tests/` instead, which is the idiomatic split and changes nothing about what is
asserted.
"""

from __future__ import annotations

import io

import pytest

from htmlq.link import detect_base, rewrite_relative_url
from htmlq.vendor import kuchikiki
from htmlq.vendor.html5ever import Writer
from htmlq.vendor.url import Url


def make_doc(html: str):
    """`kuchikiki::parse_html().from_utf8().read_from(&mut html.as_bytes())`."""
    return kuchikiki.parse_html().from_utf8().read_from(io.BytesIO(html.encode("utf-8")))


def serialize_doc(doc) -> str:
    """`doc.serialize(&mut content)` then `str::from_utf8`."""
    content = Writer()
    doc.serialize_to(content)
    return content.getvalue().decode("utf-8")


# rewrite_tests! { .. }
REWRITE_TESTS = [
    (
        "rewrite_a_href",
        (
            '<html><head></head><body><a href="/foo/bar">Hello</a></body></html>',
            '<html><head></head><body><a href="https://mgdm.net/foo/bar">Hello</a>'
            "</body></html>",
        ),
    ),
    (
        "rewrite_link_href",
        (
            '<html><head><link  href="/style.css" rel="stylesheet"/></head>'
            "<body>Hello</body></html>",
            '<html><head><link href="https://mgdm.net/style.css" rel="stylesheet">'
            "</head><body>Hello</body></html>",
        ),
    ),
    (
        "rewrite_map_area_href",
        (
            '<html><head></head><body><map name="primary">'
            '<area coords="75,75,75" href="left.html" shape="circle"></map>'
            "</body></html>",
            '<html><head></head><body><map name="primary">'
            '<area coords="75,75,75" href="https://mgdm.net/left.html" shape="circle">'
            "</map></body></html>",
        ),
    ),
    (
        "do_not_rewrite_absolute_url",
        (
            '<html><head></head><body><a href="https://example.org/foo/bar">Hello</a>'
            "</body></html>",
            '<html><head></head><body><a href="https://example.org/foo/bar">Hello</a>'
            "</body></html>",
        ),
    ),
]


@pytest.mark.parametrize(
    "value", [case for _, case in REWRITE_TESTS],
    ids=[name for name, _ in REWRITE_TESTS],
)
def test_rewrite(value):
    input_html, expected = value
    base = Url.parse("https://mgdm.net")
    doc = make_doc(input_html)
    for css_match in doc.select("a, area, link"):
        node = css_match.as_node()
        rewrite_relative_url(node, base)

    result = serialize_doc(doc)
    assert expected == result


# detect_base_tests! { .. }
DETECT_BASE_TESTS = [
    (
        "base_ok",
        (
            '<html><head><base href="https://example.org"></head><body>'
            '<a href="https://example.org/foo/bar">Hello</a></body></html>',
            Url.parse("https://example.org"),
        ),
    ),
    (
        "base_not_found",
        (
            '<html><head></head><body><a href="https://example.org/foo/bar">Hello</a>'
            "</body></html>",
            None,
        ),
    ),
]


@pytest.mark.parametrize(
    "value", [case for _, case in DETECT_BASE_TESTS],
    ids=[name for name, _ in DETECT_BASE_TESTS],
)
def test_detect_base(value):
    input_html, expected = value
    doc = make_doc(input_html)
    result = detect_base(doc)
    assert expected == result
