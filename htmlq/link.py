"""Port of `src/link.rs`."""

from __future__ import annotations

from .vendor.html5ever import local_name
from .vendor.url import Url, parse_ok


def rewrite_relative_url(node, base: Url) -> None:
    """Rewrite `node`'s `href` against `base`, in place.

    Note that the element's *namespace* is never checked -- an SVG `<a>` is
    rewritten just like an HTML one, because the Rust compares only `name.local`.
    """
    elem = node.as_element()
    if elem is None:
        return
    if not (
        local_name("a") == elem.name.local
        or local_name("link") == elem.name.local
        or local_name("area") == elem.name.local
    ):
        return
    attrs = elem.attributes.borrow_mut()

    if attrs.contains("href"):
        url = attrs.get_mut("href")
        if url is None:
            return
        if url.value.startswith("////"):
            # `trim_start_matches('/')` strips *every* leading slash, and the
            # href is left un-joined.
            url.value = url.value.lstrip("/")
            return
        # `base.join(url).ok().unwrap_or_else(|| base.to_owned())`
        new_url = base.join_ok(url.value)
        if new_url is None:
            new_url = base.to_owned()
        attrs.insert("href", str(new_url))


def detect_base(document):
    """Return the `<base href>` of `document` as a `Url`, or None."""
    node = document.select_first("base")
    if node is None:
        return None

    attrs = node.attributes.borrow()

    if attrs.contains("href"):
        href = attrs.get("href")
        if href is None:
            raise AssertionError(
                "should have retrieved href from node attributes"
            )
        # `match Url::parse(href) { Ok(url) => Some(url), _ => None }`
        return parse_ok(href)

    return None
