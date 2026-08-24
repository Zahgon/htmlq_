"""Port of `src/main.rs`."""

from __future__ import annotations

import errno
import sys

from . import link, pretty_print as pretty_print_module
from .cli import parse as parse_config, _Exit
from .vendor import kuchikiki
from .vendor.kuchikiki import rust_trim, text_nodes
from .vendor.selectors import SelectorParseError
from .vendor.url import parse_ok


# `.expect(..)` panics, which aborts with status 101 and a fixed message shape.
# The `src/main.rs:LINE:COL` locations are the *Rust* ones on purpose: they are
# part of the byte-for-byte stderr this port reproduces, and they still name the
# statement that failed, since every line here has a counterpart there.
_PANIC_OPEN_INPUT = "src/main.rs:93:37"
_PANIC_SELECTOR = "src/main.rs:115:10"
_PANIC_CREATE_OUTPUT = "src/main.rs:99:39"

# `std::io::ErrorKind`, as `Debug`-formatted inside a panic message.
_IO_ERROR_KINDS = {
    errno.ENOENT: ("NotFound", "No such file or directory"),
    errno.EACCES: ("PermissionDenied", "Permission denied"),
    errno.EEXIST: ("AlreadyExists", "File exists"),
    errno.EISDIR: ("IsADirectory", "Is a directory"),
    errno.ENOTDIR: ("NotADirectory", "Not a directory"),
}


class Panic(Exception):
    """A Rust `panic!`: message printed to stderr, process exits 101."""

    def __init__(self, location: str, message: str):
        super().__init__(message)
        self.location = location
        self.message = message

    def report(self) -> int:
        # Written through the binary buffer: Rust's panic handler emits bare
        # LF, and a text-mode write would become CRLF on Windows.
        message = (
            "\nthread 'main' panicked at %s:\n%s\n"
            "note: run with `RUST_BACKTRACE=1` environment variable "
            "to display a backtrace\n" % (self.location, self.message)
        )
        buffer = getattr(sys.stderr, "buffer", None)
        if buffer is None:
            sys.stderr.write(message)
        else:
            buffer.write(message.encode("utf-8"))
        sys.stderr.flush()
        return 101


def _io_error_debug(error: OSError) -> str:
    """`Debug` for `std::io::Error`, e.g. `Os { code: 2, kind: NotFound, .. }`."""
    kind, message = _IO_ERROR_KINDS.get(
        error.errno, ("Uncategorized", error.strerror or "")
    )
    return 'Os { code: %d, kind: %s, message: "%s" }' % (
        error.errno,
        kind,
        message,
    )


def _writeln(output, text: str) -> None:
    """`writeln!(output, "{}", text).ok()` -- errors are swallowed."""
    try:
        output.write(text.encode("utf-8"))
        output.write(b"\n")
    except OSError:
        pass


def select_attributes(node, attributes, output) -> None:
    as_element = node.as_element()
    if as_element is not None:
        for attr in attributes:
            elem_atts = as_element.attributes.try_borrow()
            if elem_atts is not None:
                val = elem_atts.get(attr)
                if val is not None:
                    _writeln(output, val)


def serialize_text(node, ignore_whitespace: bool) -> str:
    result = []
    for text_node in text_nodes(node.inclusive_descendants()):
        if ignore_whitespace and rust_trim(text_node.borrow()) == "":
            continue

        result.append(text_node.borrow())

        if ignore_whitespace:
            result.append("\n")

    return "".join(result)


def run(argv, stdin, stdout, stderr) -> int:
    config = parse_config(argv)

    if config.input_path == "-":
        input_stream = stdin
        close_input = False
    else:
        try:
            input_stream = open(config.input_path, "rb")
        except OSError as error:
            raise Panic(
                _PANIC_OPEN_INPUT,
                "should have opened input file: %s" % _io_error_debug(error),
            )
        close_input = True

    if config.output_path == "-":
        output = stdout
        close_output = False
    else:
        try:
            output = open(config.output_path, "wb")
        except OSError as error:
            raise Panic(
                _PANIC_CREATE_OUTPUT,
                "should have created output file: %s" % _io_error_debug(error),
            )
        close_output = True

    try:
        document = kuchikiki.parse_html().from_utf8().read_from(input_stream)

        # `match (&config.base, &config.detect_base)`
        if config.base is not None and config.detect_base:
            base = link.detect_base(document)
            if base is None:
                base = parse_ok(config.base)
        elif config.base is not None:
            base = parse_ok(config.base)
        elif config.detect_base:
            base = link.detect_base(document)
        else:
            base = None

        remove_node_selector = ",".join(config.remove_nodes)

        try:
            selected = document.select(config.selector)
        except SelectorParseError:
            raise Panic(_PANIC_SELECTOR, "Failed to parse CSS selector: ()")

        # The Rust is a lazy `.inspect(..).map(..).for_each(..)` chain, so each
        # matched node is detached-from, rewritten and written out before the
        # selector iterator takes its next step. That ordering is observable:
        # removing a node mid-iteration prunes it from the remaining traversal.
        #
        # It is written as an explicit `while` rather than a `for` because the
        # handles have to be released at the points Rust drops them. A `for`
        # loop keeps the previous item bound while it asks for the next one,
        # which would keep a detached subtree alive one step longer than kuchiki
        # does and let the traversal continue where the Rust's stops -- see the
        # reference-lifetime note in vendor/kuchikiki.py.
        while True:
            try:
                matched_noderef = next(selected)
            except StopIteration:
                break

            # .inspect(..) -- `remove` is dropped when the closure returns.
            remove = matched_noderef.as_node().select_first(remove_node_selector)
            if remove is not None:
                remove.as_node().detach()
            remove = None

            # .map(..)
            if base is not None:
                link.rewrite_relative_url(matched_noderef.as_node(), base)

            # .for_each(..)
            node = matched_noderef.as_node()

            if config.attributes:
                select_attributes(node, config.attributes, output)
            elif config.text_only:
                _writeln(output, serialize_text(node, config.ignore_whitespace))
            elif config.pretty_print:
                _writeln(output, pretty_print_module.pretty_print(node))
            else:
                _writeln(output, node.to_string())

            # End of the `for_each` closure: both handles go out of scope here.
            node = None
            matched_noderef = None
    finally:
        try:
            output.flush()
        except (OSError, ValueError):
            pass
        if close_output:
            output.close()
        if close_input:
            input_stream.close()

    return 0


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    try:
        return run(argv, sys.stdin.buffer, sys.stdout.buffer, sys.stderr)
    except _Exit as exit_request:
        return exit_request.code
    except Panic as panic:
        return panic.report()
    except BrokenPipeError:
        # `writeln!(..).ok()` discards write errors, so a closed pipe is not an
        # error here either.
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
