"""Port of the parts of the `html5ever` crate that htmlq touches.

`src/pretty_print.rs` imports `QualName`, `AttrRef`, `HtmlSerializer`,
`Serialize`, `SerializeOpts`, `Serializer` and `TraversalScope` and *subclasses*
the serializer -- it wraps `HtmlSerializer`, delegating every callback while
reaching into `inner.writer` to inject newlines and indentation. That means the
serializer's exact byte output and its exact callback protocol are observable
behaviour, not an implementation detail, so both are reproduced here rather than
delegated to a Python HTML writer.

`src/link.rs` imports `local_name!`, which is just an interned `LocalName`; in
Python a plain `str` serves, so `local_name("a")` is spelled `"a"`.
"""

from __future__ import annotations

import io

__all__ = [
    "ns",
    "QualName",
    "ExpandedName",
    "TraversalScope",
    "SerializeOpts",
    "HtmlSerializer",
    "Writer",
    "serialize",
    "local_name",
]


class ns:
    """The `ns!(..)` macro's namespace URLs."""

    NONE = ""
    HTML = "http://www.w3.org/1999/xhtml"
    XML = "http://www.w3.org/XML/1998/namespace"
    XMLNS = "http://www.w3.org/2000/xmlns/"
    XLINK = "http://www.w3.org/1999/xlink"
    SVG = "http://www.w3.org/2000/svg"
    MATHML = "http://www.w3.org/1998/Math/MathML"


def local_name(name: str) -> str:
    """`local_name!("a")` -- an interned local name, which here is just a str."""
    return name


class QualName:
    """Mirrors `html5ever::QualName { prefix, ns, local }`."""

    __slots__ = ("prefix", "ns", "local")

    def __init__(self, prefix, namespace: str, local: str):
        self.prefix = prefix
        self.ns = namespace
        self.local = local

    def expanded(self) -> "ExpandedName":
        return ExpandedName(self.ns, self.local)

    def __eq__(self, other):
        return (
            isinstance(other, QualName)
            and self.prefix == other.prefix
            and self.ns == other.ns
            and self.local == other.local
        )

    def __hash__(self):
        return hash((self.prefix, self.ns, self.local))

    def __repr__(self):
        return "QualName(prefix=%r, ns=%r, local=%r)" % (self.prefix, self.ns, self.local)


class ExpandedName:
    """Mirrors `html5ever::ExpandedName { ns, local }` -- an attribute map key.

    `impl<'a> From<&'a str> for ExpandedName` yields the *no-namespace* name, so
    `attributes.get("href")` looks up `("", "href")`. `ExpandedName.of("href")`
    is that conversion.
    """

    __slots__ = ("ns", "local")

    def __init__(self, namespace: str, local: str):
        self.ns = namespace
        self.local = local

    @staticmethod
    def of(name) -> "ExpandedName":
        """The `Into<ExpandedName>` conversion used by `Attributes` lookups."""
        if isinstance(name, ExpandedName):
            return name
        if isinstance(name, QualName):
            return name.expanded()
        return ExpandedName(ns.NONE, name)

    def __eq__(self, other):
        return (
            isinstance(other, ExpandedName)
            and self.ns == other.ns
            and self.local == other.local
        )

    def __hash__(self):
        return hash((self.ns, self.local))

    def __repr__(self):
        return "ExpandedName(ns=%r, local=%r)" % (self.ns, self.local)


class TraversalScope:
    """Mirrors `html5ever::serialize::TraversalScope`.

    `ChildrenOnly` carries an `Option<QualName>`; `children_only(name)` builds
    that variant, and the module-level `CHILDREN_ONLY_NONE` is `ChildrenOnly(None)`.
    """

    __slots__ = ("kind", "name")

    INCLUDE_NODE = "IncludeNode"
    CHILDREN_ONLY = "ChildrenOnly"

    def __init__(self, kind, name=None):
        self.kind = kind
        self.name = name

    @staticmethod
    def include_node() -> "TraversalScope":
        return _INCLUDE_NODE

    @staticmethod
    def children_only(name=None) -> "TraversalScope":
        return TraversalScope(TraversalScope.CHILDREN_ONLY, name)

    def is_include_node(self) -> bool:
        return self.kind == TraversalScope.INCLUDE_NODE

    def __eq__(self, other):
        return (
            isinstance(other, TraversalScope)
            and self.kind == other.kind
            and self.name == other.name
        )

    def __hash__(self):
        return hash((self.kind, self.name))

    def __repr__(self):
        return "TraversalScope(%s)" % self.kind


_INCLUDE_NODE = TraversalScope(TraversalScope.INCLUDE_NODE)
TraversalScope.IncludeNode = _INCLUDE_NODE


class SerializeOpts:
    """Mirrors `html5ever::serialize::SerializeOpts` and its `Default`."""

    __slots__ = ("scripting_enabled", "traversal_scope", "create_missing_parent")

    def __init__(
        self,
        scripting_enabled: bool = True,
        traversal_scope: TraversalScope = None,
        create_missing_parent: bool = False,
    ):
        self.scripting_enabled = scripting_enabled
        # `Default` is `ChildrenOnly(None)`.
        self.traversal_scope = (
            traversal_scope
            if traversal_scope is not None
            else TraversalScope.children_only(None)
        )
        self.create_missing_parent = create_missing_parent


class Writer:
    """A `std::io::Write` byte sink, so the ported code can call `write_all`."""

    __slots__ = ("_buf",)

    def __init__(self, buf=None):
        self._buf = buf if buf is not None else io.BytesIO()

    def write_all(self, data: bytes) -> None:
        self._buf.write(data)

    def getvalue(self) -> bytes:
        return self._buf.getvalue()


class _ElemInfo:
    __slots__ = ("html_name", "ignore_children")

    def __init__(self, html_name, ignore_children):
        self.html_name = html_name
        self.ignore_children = ignore_children


# "void elements": their children are dropped and no end tag is written. This is
# why `<link href=".." rel="stylesheet"/>` round-trips as `<link href=".." rel="stylesheet">`.
_VOID_ELEMENTS = frozenset(
    [
        "area",
        "base",
        "basefont",
        "bgsound",
        "br",
        "col",
        "embed",
        "frame",
        "hr",
        "img",
        "input",
        "keygen",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    ]
)

# Elements whose text children are emitted raw, without character escaping.
_RAW_TEXT_ELEMENTS = frozenset(
    ["style", "script", "xmp", "iframe", "noembed", "noframes", "plaintext"]
)


class Serializer:
    """Mirrors the `Serializer` trait; `PrettyPrint` in pretty_print.py implements it."""

    def start_elem(self, name: QualName, attrs):
        raise NotImplementedError

    def end_elem(self, name: QualName):
        raise NotImplementedError

    def write_text(self, text: str):
        raise NotImplementedError

    def write_comment(self, text: str):
        raise NotImplementedError

    def write_doctype(self, name: str):
        raise NotImplementedError

    def write_processing_instruction(self, target: str, data: str):
        raise NotImplementedError


def _tagname(name: QualName) -> str:
    """`fn tagname` -- the local name, whatever the namespace."""
    return name.local


class HtmlSerializer(Serializer):
    """Mirrors `html5ever::serialize::HtmlSerializer`.

    `writer` is a public field in Rust and `pretty_print.rs` writes to it
    directly, so it stays public here.
    """

    __slots__ = ("writer", "opts", "stack")

    def __init__(self, writer: Writer, opts: SerializeOpts):
        scope = opts.traversal_scope
        if scope.kind == TraversalScope.CHILDREN_ONLY and scope.name is not None:
            html_name = scope.name.local
        else:
            html_name = None
        self.writer = writer
        self.opts = opts
        self.stack = [_ElemInfo(html_name=html_name, ignore_children=False)]

    def _parent(self) -> _ElemInfo:
        if not self.stack:
            raise AssertionError("no parent ElemInfo")
        return self.stack[-1]

    def _write_escaped(self, text: str, attr_mode: bool) -> None:
        out = []
        for c in text:
            if c == "&":
                out.append("&amp;")
            elif c == " ":  # U+00A0 NO-BREAK SPACE
                out.append("&nbsp;")
            elif c == '"' and attr_mode:
                out.append("&quot;")
            elif c == "<" and not attr_mode:
                out.append("&lt;")
            elif c == ">" and not attr_mode:
                out.append("&gt;")
            else:
                out.append(c)
        self.writer.write_all("".join(out).encode("utf-8"))

    def start_elem(self, name: QualName, attrs) -> None:
        html_name = name.local if name.ns == ns.HTML else None

        if self._parent().ignore_children:
            self.stack.append(_ElemInfo(html_name=html_name, ignore_children=True))
            return

        self.writer.write_all(b"<")
        self.writer.write_all(_tagname(name).encode("utf-8"))
        for attr_name, value in attrs:
            self.writer.write_all(b" ")

            if attr_name.ns == ns.NONE:
                pass
            elif attr_name.ns == ns.XML:
                self.writer.write_all(b"xml:")
            elif attr_name.ns == ns.XMLNS:
                if attr_name.local != "xmlns":
                    self.writer.write_all(b"xmlns:")
            elif attr_name.ns == ns.XLINK:
                self.writer.write_all(b"xlink:")
            else:
                self.writer.write_all(b"unknown_namespace:")

            self.writer.write_all(attr_name.local.encode("utf-8"))
            self.writer.write_all(b'="')
            self._write_escaped(value, True)
            self.writer.write_all(b'"')
        self.writer.write_all(b">")

        ignore_children = name.ns == ns.HTML and name.local in _VOID_ELEMENTS

        self.stack.append(
            _ElemInfo(html_name=html_name, ignore_children=ignore_children)
        )

    def end_elem(self, name: QualName) -> None:
        if not self.stack:
            raise AssertionError("no ElemInfo")
        info = self.stack.pop()
        if info.ignore_children:
            return

        self.writer.write_all(b"</")
        self.writer.write_all(_tagname(name).encode("utf-8"))
        self.writer.write_all(b">")

    def write_text(self, text: str) -> None:
        parent_name = self._parent().html_name
        if parent_name in _RAW_TEXT_ELEMENTS:
            escape = False
        elif parent_name == "noscript":
            escape = not self.opts.scripting_enabled
        else:
            escape = True

        if escape:
            self._write_escaped(text, False)
        else:
            self.writer.write_all(text.encode("utf-8"))

    def write_comment(self, text: str) -> None:
        self.writer.write_all(b"<!--")
        self.writer.write_all(text.encode("utf-8"))
        self.writer.write_all(b"-->")

    def write_doctype(self, name: str) -> None:
        self.writer.write_all(b"<!DOCTYPE ")
        self.writer.write_all(name.encode("utf-8"))
        self.writer.write_all(b">")

    def write_processing_instruction(self, target: str, data: str) -> None:
        self.writer.write_all(b"<?")
        self.writer.write_all(target.encode("utf-8"))
        self.writer.write_all(b" ")
        self.writer.write_all(data.encode("utf-8"))
        self.writer.write_all(b">")


def serialize(writer: Writer, node, opts: SerializeOpts) -> None:
    """Mirrors the free function `html5ever::serialize::serialize`."""
    ser = HtmlSerializer(writer, opts)
    node.serialize(ser, opts.traversal_scope)
