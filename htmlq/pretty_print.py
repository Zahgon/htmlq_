"""Port of `src/pretty_print.rs`."""

from __future__ import annotations

from .vendor.html5ever import (
    HtmlSerializer,
    QualName,
    SerializeOpts,
    Serializer,
    TraversalScope,
    Writer,
)
from .vendor.kuchikiki import rust_trim

# `lazy_static! { static ref INLINE_ELEMENTS: HashSet<&'static str> }`
INLINE_ELEMENTS = frozenset(
    [
        "a", "abbr", "acronym", "audio", "b", "bdi", "bdo", "big", "button", "canvas", "cite",
        "code", "data", "datalist", "del", "dfn", "em", "embed", "i", "iframe", "img", "input",
        "ins", "kbd", "label", "map", "mark", "meter", "noscript", "object", "output", "picture",
        "progress", "q", "ruby", "s", "samp", "script", "select", "slot", "small", "span",
        "strong", "sub", "sup", "svg", "template", "textarea", "time", "u", "tt", "var", "video",
        "wbr",
    ]
)


def is_inline(name: str) -> bool:
    return name in INLINE_ELEMENTS


class PrettyPrint(Serializer):
    """Mirrors `struct PrettyPrint<W: Write>` and its `Serializer` impl.

    It wraps an `HtmlSerializer` and writes newlines and indentation straight
    into `inner.writer` around the delegated calls, so the indentation is
    interleaved with -- not layered on top of -- the normal serialisation.
    """

    __slots__ = ("indent", "previous_was_block", "inner")

    def __init__(self, indent: int, previous_was_block: bool, inner: HtmlSerializer):
        self.indent = indent
        self.previous_was_block = previous_was_block
        self.inner = inner

    def start_elem(self, name: QualName, attrs) -> None:
        inline = is_inline(name.local)
        if not inline or self.previous_was_block:
            self.inner.writer.write_all(b"\n")
            self.inner.writer.write_all(b" " * self.indent)

        self.indent += 2
        self.inner.start_elem(name, attrs)

    def end_elem(self, name: QualName) -> None:
        self.indent -= 2

        if is_inline(name.local):
            self.previous_was_block = False
        else:
            # Written even for void elements, whose end tag `inner.end_elem`
            # then suppresses -- which is why `<link>` is followed by a bare
            # newline-and-indent.
            self.inner.writer.write_all(b"\n")
            self.inner.writer.write_all(b" " * self.indent)
            self.previous_was_block = True

        self.inner.end_elem(name)

    def write_text(self, text: str) -> None:
        if rust_trim(text) == "":
            return
        if self.previous_was_block:
            self.inner.writer.write_all(b"\n")
            self.inner.writer.write_all(b" " * self.indent)

        self.previous_was_block = False
        self.inner.write_text(text)

    def write_comment(self, text: str) -> None:
        self.inner.write_comment(text)

    def write_doctype(self, name: str) -> None:
        self.inner.write_doctype(name)

    def write_processing_instruction(self, target: str, data: str) -> None:
        self.inner.write_processing_instruction(target, data)


def pretty_print(node) -> str:
    content = Writer()
    pp = PrettyPrint(
        indent=0,
        previous_was_block=False,
        inner=HtmlSerializer(
            content,
            SerializeOpts(traversal_scope=TraversalScope.IncludeNode),
        ),
    )
    node.serialize(pp, TraversalScope.IncludeNode)
    return content.getvalue().decode("utf-8")
