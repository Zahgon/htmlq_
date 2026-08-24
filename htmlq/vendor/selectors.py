"""Port of Servo's `selectors` 0.22 as kuchikiki 0.8 configures it.

kuchikiki supplies the `SelectorImpl`, and its choices are behaviour htmlq can
observe, so they are reproduced rather than replaced with a general-purpose CSS
engine:

* The only non-tree-structural pseudo-classes that *parse* are `:any-link`,
  `:link`, `:visited`, `:active`, `:focus`, `:hover`, `:enabled`, `:disabled`,
  `:checked` and `:indeterminate`. Anything else -- including every pseudo-
  *element* -- is a parse error.
* Of those, only `:any-link` / `:link` can ever match; kuchikiki's
  `match_non_ts_pseudo_class` returns false for the other eight, so `:checked`
  and `:disabled` never select anything however the document is marked up.
* selectors 0.22 predates `:is()`, `:where()` and `:has()`, and its `:not()`
  takes exactly *one* simple selector -- `:not(.a.b)` and `:not(a, b)` are
  errors, not matches.
* Matching runs in `QuirksMode::NoQuirks` with no scope element, so id, class
  and attribute-value comparisons are case-sensitive unless the `i` flag is
  given, and `:scope` degenerates to `:root`.

A parse failure raises `SelectorParseError`, which is the `Err(())` that
`NodeRef::select` returns and `main.rs` turns into a panic.
"""

from __future__ import annotations

import re

from .html5ever import ns

__all__ = ["Selectors", "SelectorParseError"]


class SelectorParseError(Exception):
    """Mirrors the `Err(())` from `Selectors::compile`."""


# `selectors::attr::SELECTOR_WHITESPACE` -- ASCII only, and deliberately *not*
# the Unicode set `has_class` uses.
SELECTOR_WHITESPACE = " \t\n\r\x0c"

# kuchikiki's `has_class` splits on `str::split_whitespace`, which is Unicode.
_UNICODE_WHITESPACE_RE = re.compile(
    "[\t\n\x0b\x0c\r\x20\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000]+"
)


def _split_unicode_whitespace(value: str):
    """`str::split_whitespace()` -- no empty pieces at either end."""
    return [part for part in _UNICODE_WHITESPACE_RE.split(value) if part]


# -- tokenizer --------------------------------------------------------------

_HEX = "0123456789abcdefABCDEF"


class _Input:
    """A cursor over the selector text, in the shape cssparser is used in."""

    __slots__ = ("text", "pos")

    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def at_end(self) -> bool:
        return self.pos >= len(self.text)

    def peek(self):
        return self.text[self.pos] if self.pos < len(self.text) else None

    def peek_at(self, offset: int):
        index = self.pos + offset
        return self.text[index] if index < len(self.text) else None

    def advance(self, count: int = 1) -> None:
        self.pos += count

    def skip_whitespace(self) -> bool:
        """Consume run of whitespace; returns whether any was consumed."""
        start = self.pos
        while not self.at_end() and self.text[self.pos] in SELECTOR_WHITESPACE:
            self.pos += 1
        return self.pos != start

    def expect(self, ch: str) -> None:
        if self.peek() != ch:
            raise SelectorParseError("expected %r" % ch)
        self.advance()


def _is_ident_start(ch) -> bool:
    return ch is not None and (ch.isalpha() and ch.isascii() or ch == "_" or ch >= "\x80")


def _is_ident_char(ch) -> bool:
    return ch is not None and (
        (ch.isalnum() and ch.isascii()) or ch in "_-" or ch >= "\x80"
    )


def _consume_escape(inp: _Input) -> str:
    """A CSS escape: `\\` plus up to six hex digits, or `\\` plus one char."""
    inp.advance()  # the backslash
    ch = inp.peek()
    if ch is None:
        # cssparser maps EOF after a backslash to U+FFFD.
        return "�"
    if ch in _HEX:
        digits = []
        while len(digits) < 6 and inp.peek() is not None and inp.peek() in _HEX:
            digits.append(inp.peek())
            inp.advance()
        if inp.peek() is not None and inp.peek() in SELECTOR_WHITESPACE:
            inp.advance()
        value = int("".join(digits), 16)
        if value == 0 or value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
            return "�"
        return chr(value)
    if ch == "\n":
        raise SelectorParseError("newline in escape")
    inp.advance()
    return ch


def _consume_ident(inp: _Input) -> str:
    """Parse an <ident-token>; raises if one does not start here."""
    out = []
    ch = inp.peek()
    if ch == "-":
        out.append("-")
        inp.advance()
        ch = inp.peek()
        if ch == "-":
            out.append("-")
            inp.advance()
            ch = inp.peek()
        elif ch == "\\":
            out.append(_consume_escape(inp))
            ch = inp.peek()
        elif not _is_ident_start(ch):
            raise SelectorParseError("expected an identifier")
        else:
            out.append(ch)
            inp.advance()
            ch = inp.peek()
    elif ch == "\\":
        out.append(_consume_escape(inp))
        ch = inp.peek()
    elif _is_ident_start(ch):
        out.append(ch)
        inp.advance()
        ch = inp.peek()
    else:
        raise SelectorParseError("expected an identifier")

    while True:
        ch = inp.peek()
        if ch == "\\":
            out.append(_consume_escape(inp))
        elif _is_ident_char(ch):
            out.append(ch)
            inp.advance()
        else:
            break
    return "".join(out)


def _consume_string(inp: _Input) -> str:
    quote = inp.peek()
    inp.advance()
    out = []
    while True:
        ch = inp.peek()
        if ch is None:
            raise SelectorParseError("unterminated string")
        if ch == quote:
            inp.advance()
            return "".join(out)
        if ch == "\n":
            raise SelectorParseError("newline in string")
        if ch == "\\":
            nxt = inp.peek_at(1)
            if nxt == "\n":
                inp.advance(2)
                continue
            out.append(_consume_escape(inp))
            continue
        out.append(ch)
        inp.advance()


# -- components -------------------------------------------------------------


class _Component:
    """One `selectors::parser::Component`."""

    __slots__ = ("kind", "a", "b", "c", "d")

    def __init__(self, kind, a=None, b=None, c=None, d=None):
        self.kind = kind
        self.a = a
        self.b = b
        self.c = c
        self.d = d

    def __repr__(self):
        return "_Component(%s)" % self.kind


# Combinators, matching `selectors::parser::Combinator`.
_DESCENDANT = "Descendant"
_CHILD = "Child"
_NEXT_SIBLING = "NextSibling"
_LATER_SIBLING = "LaterSibling"

# The pseudo-classes kuchikiki's `parse_non_ts_pseudo_class` accepts. Only the
# first two can match; `match_non_ts_pseudo_class` returns false for the rest.
_LINK_PSEUDO_CLASSES = frozenset(["any-link", "link"])
_NEVER_MATCHING_PSEUDO_CLASSES = frozenset(
    [
        "visited",
        "active",
        "focus",
        "hover",
        "enabled",
        "disabled",
        "checked",
        "indeterminate",
    ]
)

_SIMPLE_TREE_PSEUDO_CLASSES = {
    "first-child": "FirstChild",
    "last-child": "LastChild",
    "only-child": "OnlyChild",
    "root": "Root",
    "empty": "Empty",
    "scope": "Scope",
    "first-of-type": "FirstOfType",
    "last-of-type": "LastOfType",
    "only-of-type": "OnlyOfType",
}

_NTH_PSEUDO_CLASSES = {
    "nth-child": "NthChild",
    "nth-of-type": "NthOfType",
    "nth-last-child": "NthLastChild",
    "nth-last-of-type": "NthLastOfType",
}

# `selectors::attr`'s generated `ascii_case_insensitive_html_attributes.rs`.
# For an attribute selector with no namespace whose name is in this list and no
# explicit `i`/`s` flag, the *value* comparison becomes ASCII-case-insensitive,
# but only against HTML elements in an HTML document -- so `[type="TEXT"]` picks
# up `<input type="text">` and skips `<rect type="text">` inside an `<svg>`.
_ASCII_CASE_INSENSITIVE_HTML_ATTRIBUTES = frozenset(
    [
        "accept", "accept-charset", "align", "alink", "axis", "bgcolor",
        "charset", "checked", "clear", "codetype", "color", "compact",
        "declare", "defer", "dir", "direction", "disabled", "enctype", "face",
        "frame", "hreflang", "http-equiv", "lang", "language", "link", "media",
        "method", "multiple", "nohref", "noresize", "noshade", "nowrap",
        "readonly", "rel", "rev", "rules", "scope", "scrolling", "selected",
        "shape", "target", "text", "type", "valign", "valuetype", "vlink",
    ]
)

_ATTR_OPERATORS = {
    "=": "Equal",
    "~=": "Includes",
    "|=": "DashMatch",
    "^=": "Prefix",
    "$=": "Suffix",
    "*=": "Substring",
}


_NTH_RE = re.compile(
    r"^(?P<a>[+-]?\d*)[nN](?:\s*(?P<sign>[+-])\s*(?P<b>\d+))?$"
)


def _parse_nth(argument: str):
    """cssparser's `parse_nth` -- returns the (a, b) of `an+b`."""
    text = argument.strip(SELECTOR_WHITESPACE)
    lowered = text.lower()
    if lowered == "odd":
        return 2, 1
    if lowered == "even":
        return 2, 0
    if re.fullmatch(r"[+-]?\d+", text):
        return 0, int(text)
    match = _NTH_RE.match(text)
    if match is None:
        raise SelectorParseError("invalid an+b")
    a_text = match.group("a")
    if a_text in ("", "+"):
        a = 1
    elif a_text == "-":
        a = -1
    else:
        a = int(a_text)
    b = 0
    if match.group("b") is not None:
        b = int(match.group("b"))
        if match.group("sign") == "-":
            b = -b
    return a, b


# -- parsing ----------------------------------------------------------------


def _parse_type_selector(inp: _Input, sequence: list) -> bool:
    """Parse an optional namespace prefix plus type/universal selector.

    Returns whether anything was consumed. kuchikiki's parser declares neither a
    default namespace nor any prefix mapping, so `p|div` is an error while
    `*|div` and a bare `div` match any namespace.
    """
    start = inp.pos

    prefix = None  # None: absent, "*": any, "": explicit no-namespace
    ch = inp.peek()
    if ch == "|":
        prefix = ""
        inp.advance()
    elif ch == "*" and inp.peek_at(1) == "|":
        prefix = "*"
        inp.advance(2)
    elif ch == "*":
        # A bare universal selector: no namespace part at all.
        inp.advance()
        sequence.append(_Component("ExplicitUniversalType"))
        return True
    elif _is_ident_start(ch) or ch == "\\" or ch == "-":
        mark = inp.pos
        try:
            name = _consume_ident(inp)
        except SelectorParseError:
            inp.pos = mark
            return False
        if inp.peek() == "|" and inp.peek_at(1) != "=":
            # An explicit prefix, and kuchikiki maps no prefix to a namespace.
            raise SelectorParseError("unknown namespace prefix %r" % name)
        # Not a prefix after all: it was the local name.
        sequence.append(_Component("LocalName", name, name.lower()))
        return True
    else:
        inp.pos = start
        return False

    if prefix == "":
        sequence.append(_Component("ExplicitNoNamespace"))

    ch = inp.peek()
    if ch == "*":
        inp.advance()
        if prefix == "":
            return True
        sequence.append(_Component("ExplicitUniversalType"))
        return True
    name = _consume_ident(inp)
    sequence.append(_Component("LocalName", name, name.lower()))
    return True


def _parse_attribute_selector(inp: _Input) -> _Component:
    inp.expect("[")
    inp.skip_whitespace()

    if inp.peek() == "*" and inp.peek_at(1) == "|":
        inp.advance(2)
        any_namespace = True
    elif inp.peek() == "|":
        inp.advance()
        any_namespace = False
    else:
        any_namespace = False
        mark = inp.pos
        name = _consume_ident(inp)
        if inp.peek() == "|" and inp.peek_at(1) != "=":
            inp.pos = mark
            raise SelectorParseError("unknown namespace prefix in attribute")
        local_name = name
        return _finish_attribute_selector(inp, local_name, False)

    local_name = _consume_ident(inp)
    return _finish_attribute_selector(inp, local_name, any_namespace)


def _finish_attribute_selector(inp: _Input, local_name: str, any_namespace: bool):
    inp.skip_whitespace()

    ch = inp.peek()
    if ch == "]":
        inp.advance()
        return _Component(
            "AttributeExists", local_name, local_name.lower(), any_namespace
        )

    operator = None
    for token in ("~=", "|=", "^=", "$=", "*="):
        if inp.text.startswith(token, inp.pos):
            operator = _ATTR_OPERATORS[token]
            inp.advance(2)
            break
    if operator is None:
        if ch == "=":
            operator = "Equal"
            inp.advance()
        else:
            raise SelectorParseError("invalid attribute operator")

    inp.skip_whitespace()
    ch = inp.peek()
    if ch in ('"', "'"):
        value = _consume_string(inp)
    else:
        value = _consume_ident(inp)
    inp.skip_whitespace()

    # `parse_attribute_flags`: `i` forces ASCII-case-insensitive matching and
    # `s` forces case-sensitive matching; anything else is a parse error. With
    # no flag at all the mode depends on the attribute name -- see the table
    # above -- which is why `[rel="NEXT"]` still matches `rel="next"`.
    if inp.peek() not in (None, "]"):
        flag = _consume_ident(inp)
        lowered_flag = flag.lower()
        if lowered_flag == "i":
            case_sensitivity = "AsciiCaseInsensitive"
        elif lowered_flag == "s":
            case_sensitivity = "ExplicitCaseSensitive"
        else:
            raise SelectorParseError("invalid attribute selector flag")
        inp.skip_whitespace()
    elif (
        not any_namespace
        and local_name.lower() in _ASCII_CASE_INSENSITIVE_HTML_ATTRIBUTES
    ):
        case_sensitivity = "AsciiCaseInsensitiveIfInHtmlElementInHtmlDocument"
    else:
        case_sensitivity = "CaseSensitive"
    inp.expect("]")

    # `never_matches`, computed at parse time in 0.22.
    if operator == "Includes":
        never_matches = value == "" or any(c in SELECTOR_WHITESPACE for c in value)
    elif operator in ("Prefix", "Suffix", "Substring"):
        never_matches = value == ""
    else:
        never_matches = False

    return _Component(
        "AttributeOperation",
        local_name,
        local_name.lower(),
        (operator, value, case_sensitivity, never_matches),
        any_namespace,
    )


def _parse_pseudo(inp: _Input, inside_negation: bool) -> _Component:
    inp.advance()  # the ':'
    if inp.peek() == ":":
        # kuchikiki's `parse_pseudo_element` is the default `Err` impl.
        raise SelectorParseError("pseudo-elements are not supported")

    name = _consume_ident(inp)
    lowered = name.lower()

    if inp.peek() == "(":
        inp.advance()
        if lowered in _NTH_PSEUDO_CLASSES:
            argument = _read_until_close_paren(inp)
            a, b = _parse_nth(argument)
            return _Component(_NTH_PSEUDO_CLASSES[lowered], a, b)
        if lowered == "not":
            if inside_negation:
                raise SelectorParseError(":not() cannot be nested")
            component = _parse_negation(inp)
            inp.skip_whitespace()
            inp.expect(")")
            return component
        # `parse_non_ts_functional_pseudo_class` is the default `Err` impl.
        raise SelectorParseError("unsupported functional pseudo-class %r" % name)

    if lowered in _SIMPLE_TREE_PSEUDO_CLASSES:
        return _Component(_SIMPLE_TREE_PSEUDO_CLASSES[lowered])
    if lowered in _LINK_PSEUDO_CLASSES:
        return _Component("Link")
    if lowered in _NEVER_MATCHING_PSEUDO_CLASSES:
        return _Component("NeverMatches")
    raise SelectorParseError("unsupported pseudo-class %r" % name)


def _read_until_close_paren(inp: _Input) -> str:
    start = inp.pos
    depth = 0
    while True:
        ch = inp.peek()
        if ch is None:
            raise SelectorParseError("unterminated function")
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                argument = inp.text[start : inp.pos]
                inp.advance()
                return argument
            depth -= 1
        elif ch in ('"', "'"):
            _consume_string(inp)
            continue
        elif ch == "\\":
            _consume_escape(inp)
            continue
        inp.advance()


def _parse_negation(inp: _Input) -> _Component:
    """`parse_negation` -- exactly one simple selector, no combinators, no list."""
    inp.skip_whitespace()
    sequence = []
    if not _parse_type_selector(inp, sequence):
        component = _parse_one_simple_selector(inp, inside_negation=True)
        if component is None:
            raise SelectorParseError("empty :not()")
        sequence.append(component)
    return _Component("Negation", sequence)


def _parse_one_simple_selector(inp: _Input, inside_negation: bool):
    """One `#id`, `.class`, `[attr]` or `:pseudo`; None if none starts here."""
    ch = inp.peek()
    if ch == "#":
        inp.advance()
        return _Component("ID", _consume_ident(inp))
    if ch == ".":
        inp.advance()
        return _Component("Class", _consume_ident(inp))
    if ch == "[":
        return _parse_attribute_selector(inp)
    if ch == ":":
        return _parse_pseudo(inp, inside_negation)
    return None


def _parse_compound_selector(inp: _Input) -> list:
    sequence = []
    _parse_type_selector(inp, sequence)
    while True:
        component = _parse_one_simple_selector(inp, inside_negation=False)
        if component is None:
            break
        sequence.append(component)
    return sequence


class _ComplexSelector:
    """A compound-selector chain plus the combinator joining each pair."""

    __slots__ = ("compounds", "combinators")

    def __init__(self, compounds, combinators):
        self.compounds = compounds
        # combinators[i] joins compounds[i] to compounds[i + 1]
        self.combinators = combinators


def _parse_complex_selector(inp: _Input) -> _ComplexSelector:
    compounds = []
    combinators = []

    inp.skip_whitespace()
    compound = _parse_compound_selector(inp)
    if not compound:
        raise SelectorParseError("empty selector")
    compounds.append(compound)

    while True:
        saw_whitespace = inp.skip_whitespace()
        ch = inp.peek()
        if ch == ">":
            inp.advance()
            combinator = _CHILD
        elif ch == "+":
            inp.advance()
            combinator = _NEXT_SIBLING
        elif ch == "~":
            inp.advance()
            combinator = _LATER_SIBLING
        elif ch is None or ch == ",":
            break
        elif saw_whitespace:
            combinator = _DESCENDANT
        else:
            raise SelectorParseError("unexpected %r in selector" % ch)

        inp.skip_whitespace()
        compound = _parse_compound_selector(inp)
        if not compound:
            raise SelectorParseError("dangling combinator")
        combinators.append(combinator)
        compounds.append(compound)

    return _ComplexSelector(compounds, combinators)


def _parse_selector_list(text: str):
    inp = _Input(text)
    selectors = [_parse_complex_selector(inp)]
    while True:
        inp.skip_whitespace()
        if inp.at_end():
            return selectors
        if inp.peek() != ",":
            raise SelectorParseError("unexpected %r after selector" % inp.peek())
        inp.advance()
        selectors.append(_parse_complex_selector(inp))


# -- element helpers --------------------------------------------------------
#
# These duck-type kuchikiki's `NodeRef`, so this module stays free of a circular
# import back into the DOM.


def _is_element(node) -> bool:
    return node is not None and node.kind == "Element"


def _is_html_element_in_html_document(node) -> bool:
    """kuchikiki's impl: true when the element is in the HTML namespace."""
    return node.data.name.ns == ns.HTML


def _select_name(node, name: str, lower_name: str) -> str:
    return lower_name if _is_html_element_in_html_document(node) else name


def _previous_element_sibling(node):
    sibling = node.previous_sibling
    while sibling is not None and not _is_element(sibling):
        sibling = sibling.previous_sibling
    return sibling


def _next_element_sibling(node):
    sibling = node.next_sibling
    while sibling is not None and not _is_element(sibling):
        sibling = sibling.next_sibling
    return sibling


def _is_root(node) -> bool:
    """kuchikiki's `is_root`: the parent is the Document node."""
    parent = node.parent
    return parent is not None and parent.kind == "Document"


def _is_empty(node) -> bool:
    """kuchikiki's `is_empty`: no element children and no non-empty text."""
    child = node.first_child
    while child is not None:
        if child.kind == "Element":
            return False
        if child.kind == "Text" and child.data[0] != "":
            return False
        child = child.next_sibling
    return True


def _is_link(node) -> bool:
    """kuchikiki's `is_link`: an HTML a/area/link carrying an href."""
    name = node.data.name
    return (
        name.ns == ns.HTML
        and name.local in ("a", "area", "link")
        and node.data.attributes.contains("href")
    )


def _get_attribute(node, local_name: str, any_namespace: bool):
    attributes = node.data.attributes
    if not any_namespace:
        return attributes.get(local_name)
    for key, attr in attributes.map.items():
        if key.local == local_name:
            return attr.value
    return None


def _case_eq(left: str, right: str, case_insensitive: bool) -> bool:
    if case_insensitive:
        return left.lower() == right.lower()
    return left == right


def _eval_attr_operator(element_value: str, operator, value, case_insensitive) -> bool:
    """`AttrSelectorOperator::eval_str`."""
    if operator == "Equal":
        return _case_eq(element_value, value, case_insensitive)
    if operator == "Prefix":
        return len(element_value) >= len(value) and _case_eq(
            element_value[: len(value)], value, case_insensitive
        )
    if operator == "Suffix":
        return len(element_value) >= len(value) and _case_eq(
            element_value[len(element_value) - len(value) :], value, case_insensitive
        )
    if operator == "Substring":
        if case_insensitive:
            return value.lower() in element_value.lower()
        return value in element_value
    if operator == "Includes":
        parts = [p for p in re.split("[%s]" % re.escape(SELECTOR_WHITESPACE), element_value)]
        return any(_case_eq(part, value, case_insensitive) for part in parts)
    if operator == "DashMatch":
        if _case_eq(element_value, value, case_insensitive):
            return True
        return len(element_value) > len(value) and element_value[
            len(value)
        ] == "-" and _case_eq(element_value[: len(value)], value, case_insensitive)
    raise AssertionError("unknown attribute operator %r" % operator)


def _nth_matches(a: int, b: int, index: int) -> bool:
    """Whether `index` is `an + b` for some non-negative integer n."""
    if a == 0:
        return index == b
    n, remainder = divmod(index - b, a)
    return remainder == 0 and n >= 0


def _index_among_siblings(node, from_end: bool, same_type: bool) -> int:
    name = node.data.name if same_type else None
    index = 1
    sibling = _previous_element_sibling(node) if not from_end else _next_element_sibling(node)
    step = _previous_element_sibling if not from_end else _next_element_sibling
    while sibling is not None:
        if not same_type or (
            sibling.data.name.local == name.local and sibling.data.name.ns == name.ns
        ):
            index += 1
        sibling = step(sibling)
    return index


def _only_of_type(node) -> bool:
    name = node.data.name
    for step in (_previous_element_sibling, _next_element_sibling):
        sibling = step(node)
        while sibling is not None:
            if sibling.data.name.local == name.local and sibling.data.name.ns == name.ns:
                return False
            sibling = step(sibling)
    return True


# -- matching ---------------------------------------------------------------


def _to_unconditional(case_sensitivity: str, node) -> bool:
    """`ParsedCaseSensitivity::to_unconditional` -- True means case-insensitive."""
    if case_sensitivity == "AsciiCaseInsensitive":
        return True
    if case_sensitivity == "AsciiCaseInsensitiveIfInHtmlElementInHtmlDocument":
        return _is_html_element_in_html_document(node)
    return False


def _matches_component(component: _Component, node) -> bool:
    kind = component.kind

    if kind == "LocalName":
        return node.data.name.local == _select_name(node, component.a, component.b)
    if kind == "ExplicitUniversalType":
        return True
    if kind == "ExplicitNoNamespace":
        return node.data.name.ns == ns.NONE
    if kind == "ID":
        value = node.data.attributes.get("id")
        return value is not None and value == component.a
    if kind == "Class":
        value = node.data.attributes.get("class")
        if value is None:
            return False
        return component.a in _split_unicode_whitespace(value)
    if kind == "AttributeExists":
        return _get_attribute(node, _select_name(node, component.a, component.b), component.c) is not None
    if kind == "AttributeOperation":
        operator, value, case_sensitivity, never_matches = component.c
        if never_matches:
            return False
        element_value = _get_attribute(
            node, _select_name(node, component.a, component.b), component.d
        )
        if element_value is None:
            return False
        case_insensitive = _to_unconditional(case_sensitivity, node)
        return _eval_attr_operator(element_value, operator, value, case_insensitive)
    if kind == "FirstChild":
        return _previous_element_sibling(node) is None
    if kind == "LastChild":
        return _next_element_sibling(node) is None
    if kind == "OnlyChild":
        return (
            _previous_element_sibling(node) is None
            and _next_element_sibling(node) is None
        )
    if kind == "Root":
        return _is_root(node)
    if kind == "Scope":
        # kuchikiki passes no scope element, so `:scope` falls back to the root.
        return _is_root(node)
    if kind == "Empty":
        return _is_empty(node)
    if kind == "FirstOfType":
        return _index_among_siblings(node, False, True) == 1
    if kind == "LastOfType":
        return _index_among_siblings(node, True, True) == 1
    if kind == "OnlyOfType":
        return _only_of_type(node)
    if kind == "NthChild":
        return _nth_matches(component.a, component.b, _index_among_siblings(node, False, False))
    if kind == "NthLastChild":
        return _nth_matches(component.a, component.b, _index_among_siblings(node, True, False))
    if kind == "NthOfType":
        return _nth_matches(component.a, component.b, _index_among_siblings(node, False, True))
    if kind == "NthLastOfType":
        return _nth_matches(component.a, component.b, _index_among_siblings(node, True, True))
    if kind == "Negation":
        return not all(_matches_component(inner, node) for inner in component.a)
    if kind == "Link":
        return _is_link(node)
    if kind == "NeverMatches":
        # `match_non_ts_pseudo_class` returns false for :visited, :active,
        # :focus, :hover, :enabled, :disabled, :checked and :indeterminate.
        return False
    raise AssertionError("unknown component %r" % kind)


def _matches_compound(compound, node) -> bool:
    return all(_matches_component(component, node) for component in compound)


def _matches_from(selector: _ComplexSelector, index: int, node) -> bool:
    """Match compounds[0..=index] against `node`, walking leftwards."""
    if not _matches_compound(selector.compounds[index], node):
        return False
    if index == 0:
        return True

    combinator = selector.combinators[index - 1]
    if combinator == _CHILD:
        parent = node.parent
        if not _is_element(parent):
            return False
        return _matches_from(selector, index - 1, parent)
    if combinator == _DESCENDANT:
        ancestor = node.parent
        while _is_element(ancestor):
            if _matches_from(selector, index - 1, ancestor):
                return True
            ancestor = ancestor.parent
        return False
    if combinator == _NEXT_SIBLING:
        sibling = _previous_element_sibling(node)
        if sibling is None:
            return False
        return _matches_from(selector, index - 1, sibling)
    if combinator == _LATER_SIBLING:
        sibling = _previous_element_sibling(node)
        while sibling is not None:
            if _matches_from(selector, index - 1, sibling):
                return True
            sibling = _previous_element_sibling(sibling)
        return False
    raise AssertionError("unknown combinator %r" % combinator)


class Selectors:
    """Mirrors `kuchikiki::Selectors` -- a parsed selector list."""

    __slots__ = ("selectors", "source")

    def __init__(self, selectors, source: str):
        self.selectors = selectors
        self.source = source

    @staticmethod
    def compile(text: str) -> "Selectors":
        """`Selectors::compile` -- raises SelectorParseError for `Err(())`."""
        return Selectors(_parse_selector_list(text), text)

    def matches(self, node) -> bool:
        """True if any selector in the list matches the element `node`."""
        return any(
            _matches_from(selector, len(selector.compounds) - 1, node)
            for selector in self.selectors
        )

    def __repr__(self):
        return "Selectors(%r)" % self.source
