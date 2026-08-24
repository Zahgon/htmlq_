"""Port of the `url` crate (rust-url) -- the WHATWG URL Standard.

`Cargo.toml` depends on `url = "2.2.2"`, which is Servo's rust-url: a direct
implementation of the WHATWG URL Standard's *basic URL parser* state machine.
Python's `urllib.parse` is **not** a substitute -- it implements RFC 3986, which
differs from WHATWG on exactly the inputs htmlq's `--base` handling runs into
(backslashes, tab/newline stripping, default-port elision, IPv4 shorthand
normalisation, and `Url::parse("https://example.org")` gaining a `/` path).
So the state machine is ported here rather than approximated.

Only the surface htmlq actually uses is exposed, mirroring the Rust names:

    Url.parse(s)   -> Url                       (raises ParseError, cf. `Result`)
    url.join(rel)  -> Url                       (raises ParseError)
    str(url)       -> str                       (cf. `Display`/`to_string`)
    url == other                                (cf. `PartialEq`, on serialisation)

`Url::parse(..).ok()` maps to `parse_ok(..)`, returning None instead of raising.
"""

from __future__ import annotations

import re
import string

__all__ = ["Url", "ParseError", "parse_ok"]


class ParseError(ValueError):
    """Mirrors `url::ParseError`; `.ok()` on the Rust side becomes catching this."""


# -- WHATWG tables ----------------------------------------------------------

# "A special scheme is an ASCII string that is listed in the first column of
#  this table. The default port for a special scheme is listed in the second."
SPECIAL_SCHEMES = {
    "ftp": 21,
    "file": None,
    "http": 80,
    "https": 443,
    "ws": 80,
    "wss": 443,
}

ASCII_ALPHA = set(string.ascii_letters)
ASCII_ALPHANUMERIC = set(string.ascii_letters + string.digits)
ASCII_DIGITS = set(string.digits)
ASCII_HEX_DIGITS = set(string.hexdigits)

# "A forbidden host code point is U+0000 NULL, U+0009 TAB, U+000A LF, U+000D CR,
#  U+0020 SPACE, U+0023 (#), U+002F (/), U+003A (:), U+003C (<), U+003E (>),
#  U+003F (?), U+0040 (@), U+005B ([), U+005C (\), U+005D (]), U+005E (^), or
#  U+007C (|)."
FORBIDDEN_HOST = set("\x00\t\n\r #/:<>?@[\\]^|")
# "A forbidden domain code point is a forbidden host code point, a C0 control,
#  U+0025 (%), or U+007F DELETE."
FORBIDDEN_DOMAIN = FORBIDDEN_HOST | {chr(c) for c in range(0x20)} | {"%", "\x7f"}


class _CodePointSet:
    """A percent-encode set. Membership is `ch in set`, as the spec phrases it.

    Stored as an explicit set of "safe" ASCII plus a rule for everything else,
    so that the non-ASCII tail (which every one of these sets includes) does not
    have to be materialised as a million-element set.
    """

    __slots__ = ("_extra",)

    def __init__(self, extra=""):
        # The C0-control percent-encode set is "C0 controls and all code points
        # greater than U+007E (~)"; every other set layers more ASCII on top.
        self._extra = frozenset(extra)

    def __contains__(self, ch: str) -> bool:
        return ch <= "\x1f" or ch > "\x7e" or ch in self._extra

    def union(self, extra: str) -> "_CodePointSet":
        return _CodePointSet("".join(self._extra) + extra)


C0_CONTROL_SET = _CodePointSet()
FRAGMENT_SET = C0_CONTROL_SET.union(' "<>`')
QUERY_SET = C0_CONTROL_SET.union(' "#<>')
SPECIAL_QUERY_SET = QUERY_SET.union("'")
PATH_SET = QUERY_SET.union("?`{}")
USERINFO_SET = PATH_SET.union("/:;=@[\\]^|")

# Interior tabs and newlines are removed outright before parsing.
TAB_OR_NEWLINE_RE = re.compile("[\t\n\r]")


def _is_c0_control_or_space(ch: str) -> bool:
    return ch <= "\x20"


def _utf8_percent_encode(value: str, encode_set: _CodePointSet) -> str:
    out = []
    for ch in value:
        if ch in encode_set:
            out.append("".join("%%%02X" % b for b in ch.encode("utf-8")))
        else:
            out.append(ch)
    return "".join(out)


def _percent_decode(value: str) -> bytes:
    out = bytearray()
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
        if (
            ch == "%"
            and i + 2 < n
            and value[i + 1] in ASCII_HEX_DIGITS
            and value[i + 2] in ASCII_HEX_DIGITS
        ):
            out.append(int(value[i + 1 : i + 3], 16))
            i += 3
        else:
            out.extend(ch.encode("utf-8"))
            i += 1
    return bytes(out)


def _starts_with_windows_drive_letter(s: str) -> bool:
    """"A string starts with a Windows drive letter if ..." (file: URLs only)."""
    if len(s) < 2:
        return False
    if not (s[0] in ASCII_ALPHA and s[1] in ":|"):
        return False
    return len(s) == 2 or s[2] in "/\\?#"


def _is_normalized_windows_drive_letter(s: str) -> bool:
    return len(s) == 2 and s[0] in ASCII_ALPHA and s[1] == ":"


def _is_windows_drive_letter(s: str) -> bool:
    return len(s) == 2 and s[0] in ASCII_ALPHA and s[1] in ":|"


# -- Host parsing -----------------------------------------------------------


class _Host:
    """Tagged host value: ('domain'|'ipv4'|'ipv6'|'opaque'|'empty', value)."""

    __slots__ = ("kind", "value")

    def __init__(self, kind, value):
        self.kind = kind
        self.value = value

    def __eq__(self, other):
        return isinstance(other, _Host) and (self.kind, self.value) == (
            other.kind,
            other.value,
        )

    def __hash__(self):
        return hash((self.kind, self.value))

    def serialize(self) -> str:
        if self.kind == "ipv6":
            return "[" + _serialize_ipv6(self.value) + "]"
        if self.kind == "ipv4":
            return _serialize_ipv4(self.value)
        return self.value


def _domain_to_ascii(domain: str, be_strict: bool) -> str:
    """UTS #46 ToASCII, as rust-url delegates to the `idna` crate."""
    if domain.isascii():
        # The overwhelmingly common path: ToASCII on an all-ASCII domain is a
        # lowercase plus a validity check, with no Punycode involved.
        lowered = domain.lower()
        if lowered == "":
            raise ParseError("domain to ASCII produced the empty string")
        for label in lowered.split("."):
            if label.startswith("xn--"):
                # A Punycode label still has to round-trip through IDNA.
                return _idna_to_ascii(lowered, be_strict)
        return lowered
    return _idna_to_ascii(domain, be_strict)


def _idna_to_ascii(domain: str, be_strict: bool) -> str:
    try:
        import idna as _idna
    except ImportError as exc:  # pragma: no cover - declared as a dependency
        raise ParseError("IDNA processing unavailable") from exc
    try:
        trailing_dot = domain.endswith(".")
        stripped = domain[:-1] if trailing_dot else domain
        encoded = _idna.encode(
            stripped, uts46=True, std3_rules=be_strict, transitional=False
        ).decode("ascii")
        return encoded + ("." if trailing_dot else "")
    except Exception as exc:
        raise ParseError("domain to ASCII failed") from exc


def _parse_host(inp: str, is_not_special: bool) -> _Host:
    if inp.startswith("["):
        if not inp.endswith("]"):
            raise ParseError("unclosed IPv6 address")
        return _Host("ipv6", _parse_ipv6(inp[1:-1]))
    if inp == "":
        return _Host("empty", "")
    if is_not_special:
        return _Host("opaque", _parse_opaque_host(inp))
    decoded = _percent_decode(inp).decode("utf-8", "replace")
    ascii_domain = _domain_to_ascii(decoded, False)
    for ch in ascii_domain:
        if ch in FORBIDDEN_DOMAIN:
            raise ParseError("forbidden domain code point")
    if _ends_in_number(ascii_domain):
        return _Host("ipv4", _parse_ipv4(ascii_domain))
    return _Host("domain", ascii_domain)


def _parse_opaque_host(inp: str) -> str:
    for ch in inp:
        if ch in FORBIDDEN_HOST:
            raise ParseError("forbidden host code point")
    return _utf8_percent_encode(inp, C0_CONTROL_SET)


def _ends_in_number(inp: str) -> bool:
    parts = inp.split(".")
    if parts and parts[-1] == "":
        if len(parts) == 1:
            return False
        parts.pop()
    last = parts[-1]
    if last != "" and all(c in ASCII_DIGITS for c in last):
        return True
    return _parse_ipv4_number(last)[0] is not None


def _parse_ipv4_number(inp: str):
    """Returns (number, validation_error); number is None on failure."""
    if inp == "":
        return None, True
    validation_error = False
    r = 10
    if len(inp) >= 2 and inp[0] == "0" and inp[1] in "xX":
        validation_error = True
        inp = inp[2:]
        r = 16
    elif len(inp) >= 2 and inp[0] == "0":
        validation_error = True
        inp = inp[1:]
        r = 8
    if inp == "":
        return 0, True
    digits = {10: ASCII_DIGITS, 16: ASCII_HEX_DIGITS, 8: set("01234567")}[r]
    for ch in inp:
        if ch not in digits:
            return None, True
    return int(inp, r), validation_error


def _parse_ipv4(inp: str) -> int:
    parts = inp.split(".")
    if parts and parts[-1] == "":
        if len(parts) > 1:
            parts.pop()
    if len(parts) > 4:
        raise ParseError("invalid IPv4 address")
    numbers = []
    for part in parts:
        result, _ = _parse_ipv4_number(part)
        if result is None:
            raise ParseError("invalid IPv4 address")
        numbers.append(result)
    for n in numbers[:-1]:
        if n > 255:
            raise ParseError("invalid IPv4 address")
    if numbers and numbers[-1] >= 256 ** (5 - len(numbers)):
        raise ParseError("invalid IPv4 address")
    ipv4 = numbers[-1] if numbers else 0
    numbers = numbers[:-1]
    for counter, n in enumerate(numbers):
        ipv4 += n * 256 ** (3 - counter)
    return ipv4


def _serialize_ipv4(address: int) -> str:
    out = []
    n = address
    for _ in range(4):
        out.insert(0, str(n % 256))
        n //= 256
    return ".".join(out)


def _parse_ipv6(inp: str):
    address = [0] * 8
    piece_index = 0
    compress = None
    pointer = 0
    length = len(inp)

    if pointer < length and inp[pointer] == ":":
        if not inp.startswith("::"):
            raise ParseError("invalid IPv6 address")
        pointer += 2
        piece_index += 1
        compress = piece_index
    while pointer < length:
        if piece_index == 8:
            raise ParseError("invalid IPv6 address")
        if inp[pointer] == ":":
            if compress is not None:
                raise ParseError("invalid IPv6 address")
            pointer += 1
            piece_index += 1
            compress = piece_index
            continue
        value = 0
        digit_length = 0
        while digit_length < 4 and pointer < length and inp[pointer] in ASCII_HEX_DIGITS:
            value = value * 0x10 + int(inp[pointer], 16)
            pointer += 1
            digit_length += 1
        if pointer < length and inp[pointer] == ".":
            if digit_length == 0:
                raise ParseError("invalid IPv6 address")
            pointer -= digit_length
            if piece_index > 6:
                raise ParseError("invalid IPv6 address")
            numbers_seen = 0
            while pointer < length:
                ipv4_piece = None
                if numbers_seen > 0:
                    if inp[pointer] == "." and numbers_seen < 4:
                        pointer += 1
                    else:
                        raise ParseError("invalid IPv6 address")
                if pointer >= length or inp[pointer] not in ASCII_DIGITS:
                    raise ParseError("invalid IPv6 address")
                while pointer < length and inp[pointer] in ASCII_DIGITS:
                    number = int(inp[pointer])
                    if ipv4_piece is None:
                        ipv4_piece = number
                    elif ipv4_piece == 0:
                        raise ParseError("invalid IPv6 address")
                    else:
                        ipv4_piece = ipv4_piece * 10 + number
                    if ipv4_piece > 255:
                        raise ParseError("invalid IPv6 address")
                    pointer += 1
                address[piece_index] = address[piece_index] * 0x100 + ipv4_piece
                numbers_seen += 1
                if numbers_seen == 2 or numbers_seen == 4:
                    piece_index += 1
            if numbers_seen != 4:
                raise ParseError("invalid IPv6 address")
            break
        elif pointer < length and inp[pointer] == ":":
            pointer += 1
            if pointer >= length:
                raise ParseError("invalid IPv6 address")
        elif pointer < length:
            raise ParseError("invalid IPv6 address")
        address[piece_index] = value
        piece_index += 1
    if compress is not None:
        swaps = piece_index - compress
        piece_index = 7
        while piece_index != 0 and swaps > 0:
            address[piece_index], address[compress + swaps - 1] = (
                address[compress + swaps - 1],
                address[piece_index],
            )
            piece_index -= 1
            swaps -= 1
    elif piece_index != 8:
        raise ParseError("invalid IPv6 address")
    return tuple(address)


def _serialize_ipv6(address) -> str:
    # The longest run of zero pieces wins the "::" compression; ties go first.
    best_start, best_len, cur_start, cur_len = None, 0, None, 0
    for i, piece in enumerate(address):
        if piece == 0:
            if cur_start is None:
                cur_start = i
            cur_len += 1
            if cur_len > best_len:
                best_start, best_len = cur_start, cur_len
        else:
            cur_start, cur_len = None, 0
    compress = best_start if best_len > 1 else None
    out = []
    ignore0 = False
    for index in range(8):
        if ignore0 and address[index] == 0:
            continue
        elif ignore0:
            ignore0 = False
        if compress == index:
            out.append("::" if index == 0 else ":")
            ignore0 = True
            continue
        out.append(format(address[index], "x"))
        if index != 7:
            out.append(":")
    return "".join(out)


# -- Url --------------------------------------------------------------------


class Url:
    """Mirrors `url::Url`, for the operations htmlq performs."""

    __slots__ = (
        "scheme",
        "username",
        "password",
        "host",
        "port",
        "path",
        "query",
        "fragment",
        "has_opaque_path",
    )

    def __init__(self):
        self.scheme = ""
        self.username = ""
        self.password = ""
        self.host = None
        self.port = None
        self.path = []  # list[str] of segments; a plain str when opaque
        self.query = None
        self.fragment = None
        self.has_opaque_path = False

    # -- constructors --

    @staticmethod
    def parse(inp: str) -> "Url":
        """Mirrors `Url::parse`; raises ParseError where Rust returns `Err`."""
        return _basic_url_parse(inp, None)

    def join(self, inp: str) -> "Url":
        """Mirrors `Url::join`: parse `inp` with `self` as the base."""
        return _basic_url_parse(inp, self)

    def join_ok(self, inp: str):
        """`self.join(inp).ok()` -- the joined URL, or None if it does not parse."""
        try:
            return self.join(inp)
        except ParseError:
            return None

    def to_owned(self) -> "Url":
        """Mirrors `ToOwned::to_owned`; `Url` is value-like, so this clones."""
        clone = Url()
        clone.scheme = self.scheme
        clone.username = self.username
        clone.password = self.password
        clone.host = self.host
        clone.port = self.port
        clone.path = self.path if self.has_opaque_path else list(self.path)
        clone.query = self.query
        clone.fragment = self.fragment
        clone.has_opaque_path = self.has_opaque_path
        return clone

    # -- predicates --

    @property
    def is_special(self) -> bool:
        return self.scheme in SPECIAL_SCHEMES

    @property
    def includes_credentials(self) -> bool:
        return self.username != "" or self.password != ""

    def cannot_be_a_base(self) -> bool:
        """Mirrors `Url::cannot_be_a_base`."""
        return self.has_opaque_path

    # -- serialisation --

    def __str__(self) -> str:
        out = [self.scheme, ":"]
        if self.host is not None:
            out.append("//")
            if self.includes_credentials:
                out.append(self.username)
                if self.password != "":
                    out.append(":")
                    out.append(self.password)
                out.append("@")
            out.append(self.host.serialize())
            if self.port is not None:
                out.append(":")
                out.append(str(self.port))
        elif (
            not self.has_opaque_path and len(self.path) > 1 and self.path[0] == ""
        ):
            out.append("/.")
        if self.has_opaque_path:
            out.append(self.path)
        else:
            for segment in self.path:
                out.append("/")
                out.append(segment)
        if self.query is not None:
            out.append("?")
            out.append(self.query)
        if self.fragment is not None:
            out.append("#")
            out.append(self.fragment)
        return "".join(out)

    def as_str(self) -> str:
        return str(self)

    def __repr__(self) -> str:
        return "Url(%r)" % str(self)

    # rust-url derives PartialEq/Eq/Hash from the serialisation.
    def __eq__(self, other) -> bool:
        if not isinstance(other, Url):
            return NotImplemented
        return str(self) == str(other)

    def __hash__(self) -> int:
        return hash(str(self))


def parse_ok(inp: str):
    """`Url::parse(inp).ok()` -- the URL, or None if it does not parse."""
    try:
        return Url.parse(inp)
    except ParseError:
        return None


# -- the basic URL parser ---------------------------------------------------

_SCHEME_START = "scheme start"
_SCHEME = "scheme"
_NO_SCHEME = "no scheme"
_SPECIAL_RELATIVE_OR_AUTHORITY = "special relative or authority"
_PATH_OR_AUTHORITY = "path or authority"
_RELATIVE = "relative"
_RELATIVE_SLASH = "relative slash"
_SPECIAL_AUTHORITY_SLASHES = "special authority slashes"
_SPECIAL_AUTHORITY_IGNORE_SLASHES = "special authority ignore slashes"
_AUTHORITY = "authority"
_HOST = "host"
_PORT = "port"
_FILE = "file"
_FILE_SLASH = "file slash"
_FILE_HOST = "file host"
_PATH_START = "path start"
_PATH = "path"
_OPAQUE_PATH = "opaque path"
_QUERY = "query"
_FRAGMENT = "fragment"


def _shorten_path(url: Url) -> None:
    if url.has_opaque_path or not url.path:
        return
    if (
        url.scheme == "file"
        and len(url.path) == 1
        and _is_normalized_windows_drive_letter(url.path[0])
    ):
        return
    url.path.pop()


def _is_single_dot(segment: str) -> bool:
    return segment == "." or segment.lower() == "%2e"


def _is_double_dot(segment: str) -> bool:
    return segment.lower() in ("..", ".%2e", "%2e.", "%2e%2e")


def _basic_url_parse(inp: str, base, state_override=None) -> Url:  # noqa: C901
    url = Url()

    # "Remove any leading and trailing C0 control or space", then "remove all
    # ASCII tab or newline from input".
    if state_override is None:
        start, end = 0, len(inp)
        while start < end and _is_c0_control_or_space(inp[start]):
            start += 1
        while end > start and _is_c0_control_or_space(inp[end - 1]):
            end -= 1
        inp = inp[start:end]
    inp = TAB_OR_NEWLINE_RE.sub("", inp)

    state = state_override or _SCHEME_START
    buffer = []
    at_sign_seen = False
    inside_brackets = False
    password_token_seen = False
    pointer = 0
    length = len(inp)

    while pointer <= length:
        c = inp[pointer] if pointer < length else None
        remaining = inp[pointer + 1 :]

        if state == _SCHEME_START:
            if c is not None and c in ASCII_ALPHA:
                buffer.append(c.lower())
                state = _SCHEME
            elif state_override is None:
                state = _NO_SCHEME
                continue  # reprocess the same code point
            else:
                raise ParseError("invalid scheme")

        elif state == _SCHEME:
            if c is not None and (c in ASCII_ALPHANUMERIC or c in "+-."):
                buffer.append(c.lower())
            elif c == ":":
                url.scheme = "".join(buffer)
                buffer = []
                if url.scheme == "file":
                    state = _FILE
                elif url.is_special and base is not None and base.scheme == url.scheme:
                    state = _SPECIAL_RELATIVE_OR_AUTHORITY
                elif url.is_special:
                    state = _SPECIAL_AUTHORITY_SLASHES
                elif remaining.startswith("/"):
                    state = _PATH_OR_AUTHORITY
                    pointer += 1
                else:
                    url.path = ""
                    url.has_opaque_path = True
                    state = _OPAQUE_PATH
            elif state_override is None:
                buffer = []
                state = _NO_SCHEME
                pointer = -1  # restart from the beginning of the input
            else:
                raise ParseError("invalid scheme")

        elif state == _NO_SCHEME:
            if base is None or (base.has_opaque_path and c != "#"):
                raise ParseError("relative URL without a base")
            elif base.has_opaque_path and c == "#":
                url.scheme = base.scheme
                url.path = base.path
                url.has_opaque_path = True
                url.query = base.query
                url.fragment = ""
                state = _FRAGMENT
            elif base.scheme != "file":
                state = _RELATIVE
                continue
            else:
                state = _FILE
                continue

        elif state == _SPECIAL_RELATIVE_OR_AUTHORITY:
            if c == "/" and remaining.startswith("/"):
                state = _SPECIAL_AUTHORITY_IGNORE_SLASHES
                pointer += 1
            else:
                state = _RELATIVE
                continue

        elif state == _PATH_OR_AUTHORITY:
            if c == "/":
                state = _AUTHORITY
            else:
                state = _PATH
                continue

        elif state == _RELATIVE:
            url.scheme = base.scheme
            if c == "/" or (url.is_special and c == "\\"):
                state = _RELATIVE_SLASH
            else:
                url.username = base.username
                url.password = base.password
                url.host = base.host
                url.port = base.port
                url.path = base.path if base.has_opaque_path else list(base.path)
                url.has_opaque_path = base.has_opaque_path
                url.query = base.query
                if c == "?":
                    url.query = ""
                    state = _QUERY
                elif c == "#":
                    url.fragment = ""
                    state = _FRAGMENT
                elif c is not None:
                    url.query = None
                    _shorten_path(url)
                    state = _PATH
                    continue

        elif state == _RELATIVE_SLASH:
            if url.is_special and c in ("/", "\\"):
                state = _SPECIAL_AUTHORITY_IGNORE_SLASHES
            elif c == "/":
                state = _AUTHORITY
            else:
                url.username = base.username
                url.password = base.password
                url.host = base.host
                url.port = base.port
                state = _PATH
                continue

        elif state == _SPECIAL_AUTHORITY_SLASHES:
            if c == "/" and remaining.startswith("/"):
                state = _SPECIAL_AUTHORITY_IGNORE_SLASHES
                pointer += 1
            else:
                state = _SPECIAL_AUTHORITY_IGNORE_SLASHES
                continue

        elif state == _SPECIAL_AUTHORITY_IGNORE_SLASHES:
            if c not in ("/", "\\"):
                state = _AUTHORITY
                continue

        elif state == _AUTHORITY:
            if c == "@":
                if at_sign_seen:
                    buffer.insert(0, "%40")
                at_sign_seen = True
                for ch in "".join(buffer):
                    if ch == ":" and not password_token_seen:
                        password_token_seen = True
                        continue
                    encoded = _utf8_percent_encode(ch, USERINFO_SET)
                    if password_token_seen:
                        url.password += encoded
                    else:
                        url.username += encoded
                buffer = []
            elif c is None or c in "/?#" or (url.is_special and c == "\\"):
                if at_sign_seen and not buffer:
                    raise ParseError("empty host")
                pointer -= len(buffer) + 1
                buffer = []
                state = _HOST
            else:
                buffer.append(c)

        elif state == _HOST:
            if state_override is not None and url.scheme == "file":
                state = _FILE_HOST
                continue
            elif c == ":" and not inside_brackets:
                if not buffer:
                    raise ParseError("empty host")
                url.host = _parse_host("".join(buffer), not url.is_special)
                buffer = []
                state = _PORT
            elif c is None or c in "/?#" or (url.is_special and c == "\\"):
                pointer -= 1
                if url.is_special and not buffer:
                    raise ParseError("empty host")
                url.host = _parse_host("".join(buffer), not url.is_special)
                buffer = []
                state = _PATH_START
            else:
                if c == "[":
                    inside_brackets = True
                elif c == "]":
                    inside_brackets = False
                buffer.append(c)

        elif state == _PORT:
            if c is not None and c in ASCII_DIGITS:
                buffer.append(c)
            elif (
                c is None
                or c in "/?#"
                or (url.is_special and c == "\\")
                or state_override
            ):
                if buffer:
                    port = int("".join(buffer))
                    if port > 2**16 - 1:
                        raise ParseError("invalid port")
                    url.port = None if port == SPECIAL_SCHEMES.get(url.scheme) else port
                    buffer = []
                if state_override:
                    return url
                state = _PATH_START
                continue
            else:
                raise ParseError("invalid port")

        elif state == _FILE:
            url.scheme = "file"
            url.host = _Host("empty", "")
            if c in ("/", "\\"):
                state = _FILE_SLASH
            elif base is not None and base.scheme == "file":
                url.host = base.host
                url.path = base.path if base.has_opaque_path else list(base.path)
                url.has_opaque_path = base.has_opaque_path
                url.query = base.query
                if c == "?":
                    url.query = ""
                    state = _QUERY
                elif c == "#":
                    url.fragment = ""
                    state = _FRAGMENT
                elif c is not None:
                    url.query = None
                    if not _starts_with_windows_drive_letter(inp[pointer:]):
                        _shorten_path(url)
                    else:
                        url.path = []
                    state = _PATH
                    continue
            else:
                state = _PATH
                continue

        elif state == _FILE_SLASH:
            if c in ("/", "\\"):
                state = _FILE_HOST
            else:
                if base is not None and base.scheme == "file":
                    url.host = base.host
                    if (
                        not _starts_with_windows_drive_letter(inp[pointer:])
                        and base.path
                        and _is_normalized_windows_drive_letter(base.path[0])
                    ):
                        url.path.append(base.path[0])
                state = _PATH
                continue

        elif state == _FILE_HOST:
            if c is None or c in "/\\?#":
                pointer -= 1
                buffered = "".join(buffer)
                if state_override is None and _is_windows_drive_letter(buffered):
                    state = _PATH
                elif buffered == "":
                    url.host = _Host("empty", "")
                    if state_override:
                        return url
                    state = _PATH_START
                else:
                    host = _parse_host(buffered, not url.is_special)
                    if host.kind == "domain" and host.value == "localhost":
                        host = _Host("empty", "")
                    url.host = host
                    if state_override:
                        return url
                    buffer = []
                    state = _PATH_START
            else:
                buffer.append(c)

        elif state == _PATH_START:
            if url.is_special:
                state = _PATH
                if c not in ("/", "\\"):
                    continue
            elif state_override is None and c == "?":
                url.query = ""
                state = _QUERY
            elif state_override is None and c == "#":
                url.fragment = ""
                state = _FRAGMENT
            elif c is not None:
                state = _PATH
                if c != "/":
                    continue
            elif state_override is not None and url.host is None:
                url.path.append("")

        elif state == _PATH:
            if (
                c is None
                or c == "/"
                or (url.is_special and c == "\\")
                or (state_override is None and c in "?#")
            ):
                buffered = "".join(buffer)
                slash_ends_segment = c == "/" or (url.is_special and c == "\\")
                if _is_double_dot(buffered):
                    _shorten_path(url)
                    if not slash_ends_segment:
                        url.path.append("")
                elif _is_single_dot(buffered) and not slash_ends_segment:
                    url.path.append("")
                elif not _is_single_dot(buffered):
                    if (
                        url.scheme == "file"
                        and not url.path
                        and _is_windows_drive_letter(buffered)
                    ):
                        buffered = buffered[0] + ":"
                    url.path.append(buffered)
                buffer = []
                if c == "?":
                    url.query = ""
                    state = _QUERY
                elif c == "#":
                    url.fragment = ""
                    state = _FRAGMENT
            else:
                buffer.append(_utf8_percent_encode(c, PATH_SET))

        elif state == _OPAQUE_PATH:
            if c == "?":
                url.query = ""
                state = _QUERY
            elif c == "#":
                url.fragment = ""
                state = _FRAGMENT
            elif c is not None:
                url.path += _utf8_percent_encode(c, C0_CONTROL_SET)

        elif state == _QUERY:
            if c is None or (state_override is None and c == "#"):
                encode_set = SPECIAL_QUERY_SET if url.is_special else QUERY_SET
                url.query += _utf8_percent_encode("".join(buffer), encode_set)
                buffer = []
                if c == "#":
                    url.fragment = ""
                    state = _FRAGMENT
            else:
                buffer.append(c)

        elif state == _FRAGMENT:
            if c is not None:
                url.fragment += _utf8_percent_encode(c, FRAGMENT_SET)

        pointer += 1

    return url
