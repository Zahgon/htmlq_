"""Port of the `kuchikiki` crate -- an html5ever DOM with CSS selector matching.

htmlq uses six things from it: `parse_html().from_utf8().read_from(..)`,
`NodeRef` and its `as_element()` / `attributes` accessors, the lazy
`inclusive_descendants()` / `text_nodes()` iterators, `select()` /
`select_first()`, `detach()`, and `Serialize`/`Display`.

Three behaviours here are load-bearing and easy to get wrong.

**Lazy traversal.** `select()` walks the tree as it is asked for items, and
`main.rs` detaches nodes from inside `.inspect()` while that walk is in
progress. `_Traverse` therefore reproduces kuchiki's cursor exactly: a single
`(next, next_back)` pair, re-derived from live links at each step, stopping when
`next == next_back` or when a link it needs is absent.

**Reference lifetimes.** kuchiki's links are asymmetric -- `first_child` and
`next_sibling` are strong `Rc`s, while `parent`, `previous_sibling` and
`last_child` are `Weak`s. So detaching a node during iteration does not merely
unlink it: once the last `Rc` to it goes away the node is *freed*, and its
children's `parent()` starts returning `None`, which truncates the traversal.
That is observable -- `htmlq 'p, i, u' -r p` stops emitting after the first
element inside the removed `<p>`. The same asymmetry is reproduced here with
`weakref.ref` for the back-edges, which makes CPython's refcounting free nodes
at exactly the points Rust drops them.

**Attribute order** is insertion order (kuchiki stores an `IndexMap`), and
re-inserting an existing key keeps its original position. That is what makes
`<link href=".." rel="stylesheet">` come back with `href` still first.

The parser itself is html5lib -- the Python implementation of the same WHATWG
tree-construction algorithm html5ever implements. Its output is adapted to this
DOM in `_convert`, which also restores two html5ever behaviours html5lib does
not share: merging adjacent text nodes, and moving `<template>` children into a
separate contents fragment.
"""

from __future__ import annotations

import weakref

from .html5ever import (
    ExpandedName,
    QualName,
    SerializeOpts,
    TraversalScope,
    Writer,
    ns,
    serialize as _serialize,
)
from .selectors import Selectors, SelectorParseError

__all__ = [
    "NodeRef",
    "NodeDataRef",
    "ElementData",
    "Attributes",
    "Attribute",
    "parse_html",
    "text_nodes",
    "rust_trim",
    "SelectorParseError",
]


# `char::is_whitespace` follows the Unicode White_Space property. Python's
# `str.isspace()` additionally treats U+001C..U+001F as space, so `str.strip()`
# is not a drop-in for Rust's `str::trim()` -- this set is.
_RUST_WHITESPACE = (
    "\t\n\x0b\x0c\r\x20"      # TAB, LF, VT, FF, CR, SPACE
    "\x85\xa0"                 # NEL, NO-BREAK SPACE
    "\u1680"                   # OGHAM SPACE MARK
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029"             # LINE / PARAGRAPH SEPARATOR
    "\u202f\u205f\u3000"       # NARROW NBSP, MMSP, IDEOGRAPHIC
)


def rust_trim(value: str) -> str:
    """`str::trim()` -- strips Unicode White_Space, not Python's wider set."""
    return value.strip(_RUST_WHITESPACE)


def _weak(node):
    """`Rc::downgrade` -- a weak handle, or None for a missing link."""
    return None if node is None else weakref.ref(node)


def _upgrade(reference):
    """`Weak::upgrade` -- the node, or None if it has been freed."""
    return None if reference is None else reference()


# -- attributes -------------------------------------------------------------


class Attribute:
    """Mirrors `kuchikiki::Attribute { prefix, value }`."""

    __slots__ = ("prefix", "value")

    def __init__(self, prefix, value: str):
        self.prefix = prefix
        self.value = value

    def __repr__(self):
        return "Attribute(prefix=%r, value=%r)" % (self.prefix, self.value)


class Attributes:
    """Mirrors `kuchikiki::Attributes`, an `IndexMap<ExpandedName, Attribute>`.

    Python dicts preserve insertion order and keep an existing key's position on
    reassignment, which is precisely `IndexMap::insert`'s behaviour.
    """

    __slots__ = ("map",)

    def __init__(self):
        self.map = {}

    # `RefCell` accessors. Python has no borrow checker, so these hand back the
    # same object; keeping the names makes the ported call sites line up with
    # the Rust, and `try_borrow` never fails, exactly as it never fails there.
    def borrow(self) -> "Attributes":
        return self

    def borrow_mut(self) -> "Attributes":
        return self

    def try_borrow(self) -> "Attributes":
        return self

    def get(self, name):
        """`Attributes::get` -- the value, or None."""
        attr = self.map.get(ExpandedName.of(name))
        return attr.value if attr is not None else None

    def get_mut(self, name):
        """`Attributes::get_mut` -- the `Attribute`, whose `.value` is mutable."""
        return self.map.get(ExpandedName.of(name))

    def contains(self, name) -> bool:
        return ExpandedName.of(name) in self.map

    def insert(self, name, value: str):
        """`Attributes::insert` -- returns the previous value, or None."""
        key = ExpandedName.of(name)
        existing = self.map.get(key)
        if existing is not None:
            old = existing.value
            existing.value = value  # keeps the key's position, as IndexMap does
            return old
        self.map[key] = Attribute(None, value)
        return None

    def remove(self, name):
        attr = self.map.pop(ExpandedName.of(name), None)
        return attr.value if attr is not None else None

    def items(self):
        return self.map.items()

    def __len__(self):
        return len(self.map)

    def __repr__(self):
        return "Attributes(%r)" % (
            {(k.ns, k.local): v.value for k, v in self.map.items()},
        )


class ElementData:
    """Mirrors `kuchikiki::ElementData { name, attributes, template_contents }`."""

    __slots__ = ("name", "attributes", "template_contents")

    def __init__(self, name: QualName, attributes: Attributes, template_contents=None):
        self.name = name
        self.attributes = attributes
        self.template_contents = template_contents

    def __repr__(self):
        return "ElementData(%r)" % (self.name,)


class Doctype:
    """Mirrors `kuchikiki::Doctype`."""

    __slots__ = ("name", "public_id", "system_id")

    def __init__(self, name: str, public_id: str, system_id: str):
        self.name = name
        self.public_id = public_id
        self.system_id = system_id


class DocumentData:
    """Mirrors `kuchikiki::DocumentData` (quirks mode is not used by htmlq)."""

    __slots__ = ("quirks_mode",)

    def __init__(self, quirks_mode="no-quirks"):
        self.quirks_mode = quirks_mode


# -- nodes ------------------------------------------------------------------

ELEMENT = "Element"
TEXT = "Text"
COMMENT = "Comment"
PROCESSING_INSTRUCTION = "ProcessingInstruction"
DOCTYPE = "Doctype"
DOCUMENT = "Document"
DOCUMENT_FRAGMENT = "DocumentFragment"


class NodeRef:
    """Mirrors `kuchikiki::NodeRef`, an `Rc<Node>`.

    Forward links (`first_child`, `next_sibling`) own their targets; back links
    (`parent`, `previous_sibling`, `last_child`) are weak and are exposed as
    properties that upgrade on read, returning None once the target is gone.
    Identity comparison (`is`) stands in for `Rc::ptr_eq`.
    """

    __slots__ = (
        "kind",
        "data",
        "_parent",
        "first_child",
        "_last_child",
        "_previous_sibling",
        "next_sibling",
        "__weakref__",
    )

    def __init__(self, kind: str, data):
        self.kind = kind
        self.data = data
        self._parent = None
        self.first_child = None
        self._last_child = None
        self._previous_sibling = None
        self.next_sibling = None

    # -- weak back-links --

    @property
    def parent(self):
        return _upgrade(self._parent)

    @property
    def previous_sibling(self):
        return _upgrade(self._previous_sibling)

    @property
    def last_child(self):
        return _upgrade(self._last_child)

    # -- constructors (kuchiki's `NodeRef::new_*`) --

    @staticmethod
    def new_element(name: QualName, attributes: Attributes, template_contents=None):
        return NodeRef(ELEMENT, ElementData(name, attributes, template_contents))

    @staticmethod
    def new_text(value: str):
        return NodeRef(TEXT, [value])  # boxed, so the cell can be mutated

    @staticmethod
    def new_comment(value: str):
        return NodeRef(COMMENT, [value])

    @staticmethod
    def new_processing_instruction(target: str, data: str):
        return NodeRef(PROCESSING_INSTRUCTION, [target, data])

    @staticmethod
    def new_doctype(name: str, public_id: str, system_id: str):
        return NodeRef(DOCTYPE, Doctype(name, public_id, system_id))

    @staticmethod
    def new_document():
        return NodeRef(DOCUMENT, DocumentData())

    @staticmethod
    def new_document_fragment():
        return NodeRef(DOCUMENT_FRAGMENT, None)

    # -- data accessors --

    def as_element(self):
        """`NodeRef::as_element` -- the `ElementData`, or None."""
        return self.data if self.kind == ELEMENT else None

    def as_text(self):
        """`NodeRef::as_text` -- the text cell, or None."""
        return self.data if self.kind == TEXT else None

    def as_comment(self):
        return self.data if self.kind == COMMENT else None

    def as_doctype(self):
        return self.data if self.kind == DOCTYPE else None

    def as_document(self):
        return self.data if self.kind == DOCUMENT else None

    def borrow(self) -> str:
        """`text_node.borrow()` -- the text or comment cell's string."""
        return self.data[0]

    # -- tree mutation --

    def detach(self) -> None:
        """`NodeRef::detach` -- unlink from parent and siblings.

        The local `next_sibling_strong` mirrors the Rust binding of the same
        name: it is the only remaining owner of the following siblings until
        they are re-homed, and if they are not, they die when it goes out of
        scope -- which is exactly what the Rust does.
        """
        parent_weak = self._parent
        self._parent = None
        previous_sibling_weak = self._previous_sibling
        self._previous_sibling = None
        next_sibling_strong = self.next_sibling
        self.next_sibling = None

        previous_sibling_opt = _upgrade(previous_sibling_weak)

        if next_sibling_strong is not None:
            next_sibling_strong._previous_sibling = previous_sibling_weak
        else:
            parent_strong = _upgrade(parent_weak)
            if parent_strong is not None:
                parent_strong._last_child = previous_sibling_weak

        if previous_sibling_opt is not None:
            previous_sibling_opt.next_sibling = next_sibling_strong
        else:
            parent_strong = _upgrade(parent_weak)
            if parent_strong is not None:
                parent_strong.first_child = next_sibling_strong

    def append(self, new_child: "NodeRef") -> None:
        """`NodeRef::append` -- detach `new_child` and add it as the last child."""
        new_child.detach()
        new_child._parent = _weak(self)
        last_child = self.last_child
        if last_child is not None:
            new_child._previous_sibling = _weak(last_child)
            last_child.next_sibling = new_child
        else:
            self.first_child = new_child
        self._last_child = _weak(new_child)

    # -- traversal --

    def children(self):
        """`NodeRef::children` -- a `Siblings` iterator over the child nodes."""
        first_child = self.first_child
        last_child = self.last_child
        if first_child is None or last_child is None:
            return _Siblings(None)
        return _Siblings((first_child, last_child))

    def traverse_inclusive(self):
        """`NodeRef::traverse_inclusive` -- Start/End edges, self included."""
        return _Traverse(((_START, self), (_END, self)))

    def traverse(self):
        """`NodeRef::traverse` -- Start/End edges over the descendants."""
        first_child = self.first_child
        last_child = self.last_child
        if first_child is None or last_child is None:
            return _Traverse(None)
        return _Traverse(((_START, first_child), (_END, last_child)))

    def inclusive_descendants(self):
        """`NodeRef::inclusive_descendants` -- lazy preorder, self first."""
        return _Descendants(self.traverse_inclusive())

    def descendants(self):
        """`NodeRef::descendants` -- lazy preorder over the descendants."""
        return _Descendants(self.traverse())

    # -- selectors --

    def select(self, selectors: str):
        """`NodeRef::select` -- lazy; raises SelectorParseError like `Err(())`."""
        compiled = Selectors.compile(selectors)
        return _Select(_Elements(self.inclusive_descendants()), compiled)

    def select_first(self, selectors: str):
        """`NodeRef::select_first`.

        Rust returns `Err(())` both when the selector fails to parse and when
        nothing matches, and every call site treats the two identically, so this
        returns None in both cases.
        """
        try:
            elements = self.select(selectors)
        except SelectorParseError:
            return None
        try:
            return next(elements)
        except StopIteration:
            return None

    # -- serialisation --

    def serialize(self, serializer, traversal_scope: TraversalScope) -> None:
        """Mirrors `impl Serialize for NodeRef`."""
        if self.kind == ELEMENT:
            element = self.data
            if traversal_scope.is_include_node():
                # kuchiki materialises `(QualName, &str)` pairs from the map,
                # carrying each attribute's own prefix onto the emitted name.
                attrs = [
                    (QualName(attr.prefix, name.ns, name.local), attr.value)
                    for name, attr in element.attributes.map.items()
                ]
                serializer.start_elem(element.name, attrs)

            for child in self.children():
                child.serialize(serializer, TraversalScope.IncludeNode)

            if traversal_scope.is_include_node():
                serializer.end_elem(element.name)
            return

        if self.kind in (DOCUMENT, DOCUMENT_FRAGMENT):
            for child in self.children():
                child.serialize(serializer, TraversalScope.IncludeNode)
            return

        if not traversal_scope.is_include_node():
            return

        if self.kind == DOCTYPE:
            serializer.write_doctype(self.data.name)
        elif self.kind == TEXT:
            serializer.write_text(self.data[0])
        elif self.kind == COMMENT:
            serializer.write_comment(self.data[0])
        elif self.kind == PROCESSING_INSTRUCTION:
            serializer.write_processing_instruction(self.data[0], self.data[1])

    def serialize_to(self, writer: Writer) -> None:
        """`NodeRef::serialize` -- write this node and its subtree."""
        _serialize(
            writer,
            self,
            SerializeOpts(traversal_scope=TraversalScope.IncludeNode),
        )

    def to_string(self) -> str:
        """`impl Display for NodeRef` / `.to_string()`."""
        writer = Writer()
        self.serialize_to(writer)
        return writer.getvalue().decode("utf-8")

    def __str__(self) -> str:
        return self.to_string()

    def __repr__(self) -> str:
        if self.kind == ELEMENT:
            return "NodeRef(<%s>)" % self.data.name.local
        return "NodeRef(%s)" % self.kind


# -- iterators --------------------------------------------------------------
#
# These are explicit iterator classes rather than generator functions on
# purpose. A generator frame keeps its locals alive across a `yield`, which
# would hold a node past the point Rust drops it and so keep a detached subtree
# reachable; an iterator whose `__next__` frame is torn down on return releases
# each node at the same moment the Rust does.

_START = "Start"
_END = "End"


def _edge_eq(left, right) -> bool:
    """`NodeEdge` equality: same edge kind and the same node by pointer."""
    return left[0] == right[0] and left[1] is right[1]


class _Traverse:
    """Mirrors `kuchikiki::iter::Traverse`.

    The state is the Rust's `Option<State<NodeEdge>>`: None once the walk is
    over, otherwise the `(next, next_back)` pair. The successor is computed only
    when `next != next_back`, and a missing `parent()` -- including one whose
    node has been freed -- ends the walk.
    """

    __slots__ = ("state",)

    def __init__(self, state):
        self.state = state

    def __iter__(self):
        return self

    def __next__(self):
        state = self.state
        if state is None:
            raise StopIteration
        self.state = None
        next_edge, next_back = state

        if not _edge_eq(next_edge, next_back):
            kind, node = next_edge
            if kind == _START:
                child = node.first_child
                if child is not None:
                    self.state = ((_START, child), next_back)
                else:
                    self.state = ((_END, node), next_back)
            else:
                sibling = node.next_sibling
                if sibling is not None:
                    self.state = ((_START, sibling), next_back)
                else:
                    parent = node.parent
                    if parent is not None:
                        self.state = ((_END, parent), next_back)
                    else:
                        self.state = None
        return next_edge


class _Descendants:
    """Mirrors `Descendants` -- the `Start` edges of a traversal."""

    __slots__ = ("traverse",)

    def __init__(self, traverse: _Traverse):
        self.traverse = traverse

    def __iter__(self):
        return self

    def __next__(self):
        edge = None
        while True:
            # Release the rejected edge *before* stepping again: Rust drops the
            # loop binding at the end of each iteration, and holding it across
            # the call would keep a freed-in-Rust node alive here.
            edge = None
            edge = self.traverse.__next__()
            if edge[0] == _START:
                return edge[1]


class _Elements:
    """Mirrors `NodeIterator::elements` -- keeps only element nodes."""

    __slots__ = ("inner",)

    def __init__(self, inner):
        self.inner = inner

    def __iter__(self):
        return self

    def __next__(self):
        node = None
        while True:
            node = None  # drop the rejected node before advancing
            node = self.inner.__next__()
            if node.kind == ELEMENT:
                return NodeDataRef(node)


class _TextNodes:
    """Mirrors `NodeIterator::text_nodes` -- keeps only text nodes."""

    __slots__ = ("inner",)

    def __init__(self, inner):
        self.inner = inner

    def __iter__(self):
        return self

    def __next__(self):
        node = None
        while True:
            node = None  # drop the rejected node before advancing
            node = self.inner.__next__()
            if node.kind == TEXT:
                return node


def text_nodes(nodes):
    """`nodes.text_nodes()`."""
    return _TextNodes(nodes)


class _Select:
    """Mirrors `Select` -- the elements of `inner` that match `selectors`."""

    __slots__ = ("inner", "selectors")

    def __init__(self, inner, selectors: Selectors):
        self.inner = inner
        self.selectors = selectors

    def __iter__(self):
        return self

    def __next__(self):
        element = None
        while True:
            # `for element in self.iter.by_ref()` drops each non-matching
            # element before pulling the next one; with `-r *` the dropped
            # element can be the last owner of a detached subtree, and keeping
            # it alive one step longer changes where the traversal ends.
            element = None
            element = self.inner.__next__()
            if self.selectors.matches(element.node):
                return element


class _Siblings:
    """Mirrors `Siblings`.

    kuchiki stops when `next == next_back` *or* when `next_sibling()` is absent,
    and reads `next_sibling` before deciding -- so a node detached while this
    iterator is live ends the walk rather than jumping to its old successor.
    """

    __slots__ = ("state",)

    def __init__(self, state):
        self.state = state

    def __iter__(self):
        return self

    def __next__(self):
        state = self.state
        if state is None:
            raise StopIteration
        self.state = None
        node, next_back = state
        sibling = node.next_sibling
        if sibling is not None and node is not next_back:
            self.state = (sibling, next_back)
        return node


class NodeDataRef:
    """Mirrors `NodeDataRef<ElementData>` as yielded by `select`.

    `Deref` to the `ElementData` is spelled as forwarded `name`/`attributes`
    properties, and `as_node()` is the same method it is in Rust.
    """

    __slots__ = ("node",)

    def __init__(self, node: NodeRef):
        self.node = node

    def as_node(self) -> NodeRef:
        return self.node

    @property
    def name(self) -> QualName:
        return self.node.data.name

    @property
    def attributes(self) -> Attributes:
        return self.node.data.attributes

    def __repr__(self):
        return "NodeDataRef(%r)" % (self.node,)


# -- parsing ----------------------------------------------------------------


class _Utf8Parser:
    """The object returned by `parse_html().from_utf8()`."""

    __slots__ = ()

    def read_from(self, reader) -> NodeRef:
        """`.read_from(&mut input)` -- parse a byte stream as a document.

        `from_utf8()` selects html5ever's lossy UTF-8 decoder, so invalid
        sequences become U+FFFD rather than an error, and
        `TokenizerOpts::discard_bom` defaults to true, so a leading BOM is
        dropped.
        """
        data = reader.read()
        if isinstance(data, bytes):
            text = data.decode("utf-8", "replace")
        else:
            text = data
        if text.startswith("\ufeff"):
            text = text[1:]
        return parse_document(text)


class _ParseHtml:
    """The object returned by `kuchikiki::parse_html()`."""

    __slots__ = ()

    def from_utf8(self) -> _Utf8Parser:
        return _Utf8Parser()

    def one(self, text: str) -> NodeRef:
        """`.one(..)` -- parse a string that is already decoded."""
        return parse_document(text)


def parse_html() -> _ParseHtml:
    """Mirrors `kuchikiki::parse_html()`."""
    return _ParseHtml()


def parse_document(text: str) -> NodeRef:
    """Run html5lib's tree construction and adapt the result to this DOM."""
    import html5lib

    minidom_document = html5lib.parse(
        text, treebuilder="dom", namespaceHTMLElements=True
    )
    document = NodeRef.new_document()
    _append_children(document, minidom_document)
    return document


# minidom node type constants, spelled out so the adapter does not depend on a
# live node to read them from.
_MD_ELEMENT = 1
_MD_TEXT = 3
_MD_CDATA = 4
_MD_PROCESSING_INSTRUCTION = 7
_MD_COMMENT = 8
_MD_DOCUMENT_TYPE = 10


def _append_children(parent: NodeRef, minidom_parent) -> None:
    """Convert and append children, merging runs of adjacent text nodes.

    html5ever's `TreeSink::append` folds `AppendText` into a preceding text
    sibling, so a document never contains two adjacent text nodes. html5lib
    creates one text node per character-token run instead, so the runs are
    merged here -- otherwise `--text --ignore-whitespace` would emit a stray
    newline per fragment and `--pretty` would re-test each fragment for
    whitespace independently.
    """
    pending_text = []

    def flush() -> None:
        if pending_text:
            parent.append(NodeRef.new_text("".join(pending_text)))
            del pending_text[:]

    for child in minidom_parent.childNodes:
        node_type = child.nodeType
        if node_type in (_MD_TEXT, _MD_CDATA):
            pending_text.append(child.data)
            continue
        flush()
        converted = _convert(child)
        if converted is not None:
            parent.append(converted)
    flush()


def _convert(minidom_node):
    node_type = minidom_node.nodeType

    if node_type == _MD_ELEMENT:
        namespace = minidom_node.namespaceURI or ns.NONE
        name = QualName(minidom_node.prefix, namespace, minidom_node.localName)

        attributes = Attributes()
        for attr in minidom_node.attributes.values():
            attr_ns = attr.namespaceURI or ns.NONE
            key = ExpandedName(attr_ns, attr.localName)
            attributes.map[key] = Attribute(attr.prefix, attr.value)

        # kuchiki keeps `<template>` children in a separate contents fragment,
        # so a serialised `<template>` is empty. html5lib appends them as
        # ordinary children, so they are moved across here.
        is_template = namespace == ns.HTML and minidom_node.localName == "template"
        template_contents = NodeRef.new_document_fragment() if is_template else None

        element = NodeRef.new_element(name, attributes, template_contents)
        _append_children(template_contents or element, minidom_node)
        return element

    if node_type == _MD_COMMENT:
        return NodeRef.new_comment(minidom_node.data)

    if node_type == _MD_DOCUMENT_TYPE:
        return NodeRef.new_doctype(
            minidom_node.name or "",
            minidom_node.publicId or "",
            minidom_node.systemId or "",
        )

    if node_type == _MD_PROCESSING_INSTRUCTION:
        return NodeRef.new_processing_instruction(
            minidom_node.target, minidom_node.data
        )

    return None
