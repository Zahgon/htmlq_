"""The `clap` derive layer of `src/main.rs`, reproduced argument for argument.

`#[derive(Parser)]` on `struct Config` generates a parser whose help text,
error text and exit codes are part of htmlq's observable behaviour, so this
module reimplements clap 4's surface rather than delegating to `argparse`,
whose messages and usage line differ in every particular.

The behaviours reproduced here are:

* `--long value`, `--long=value`, `-s value`, `-svalue`, and clustered short
  flags (`-tp`), with `--` ending option parsing.
* `-h` / `--help` and `-V` / `--version` print to stdout and exit 0.
* Usage errors print clap's wording to stderr and exit **2**, including the
  `tip: to pass '-x' as a value, use '-- -x'` line that clap adds when an
  unexpected argument looks like a flag.
"""

from __future__ import annotations

import sys


def _write(stream, text: str) -> None:
    """Write LF-terminated text verbatim.

    The Rust binary emits a bare LF on every platform. Python's text streams
    translate line endings on Windows, so help, version and error output all go
    through the underlying binary buffer instead.
    """
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(text)
    else:
        buffer.write(text.encode("utf-8"))
    stream.flush()


# `#[command(version, author, about)]` pulls these from Cargo.toml.
BIN_NAME = "htmlq"
VERSION = "0.5.0"
ABOUT = "Like jq, but for HTML."


class Config:
    """Mirrors `struct Config`."""

    __slots__ = (
        "selector",
        "input_path",
        "output_path",
        "base",
        "detect_base",
        "text_only",
        "ignore_whitespace",
        "pretty_print",
        "remove_nodes",
        "attributes",
    )

    def __init__(self):
        self.selector = "html"
        self.input_path = "-"
        self.output_path = "-"
        self.base = None
        self.detect_base = False
        self.text_only = False
        self.ignore_whitespace = False
        self.pretty_print = False
        self.remove_nodes = []
        self.attributes = []


class _Opt:
    __slots__ = ("field", "short", "long", "value_name", "help", "kind")

    def __init__(self, field, short, long, value_name, help_text, kind):
        self.field = field
        self.short = short
        self.long = long
        self.value_name = value_name
        self.help = help_text
        self.kind = kind  # "value" | "flag" | "append"

    def spec(self) -> str:
        parts = []
        if self.short:
            parts.append("-%s" % self.short)
        parts.append("--%s" % self.long)
        rendered = ", ".join(parts)
        if self.value_name:
            rendered += " <%s>" % self.value_name
        return rendered

    def invocation(self) -> str:
        """How clap names the argument in a 'value is required' error."""
        if self.value_name:
            return "--%s <%s>" % (self.long, self.value_name)
        return "--%s" % self.long


# Declaration order matches the field order of `struct Config`, which is the
# order clap lists them in `--help`.
_OPTIONS = [
    _Opt("input_path", "f", "filename", "INPUT_PATH",
         "Where to read HTML input from [default: -]", "value"),
    _Opt("output_path", "o", "output", "OUTPUT_PATH",
         "Where to write the filtered HTML to [default: -]", "value"),
    _Opt("base", "b", "base", "BASE",
         "What URL to prepend to links without an origin, i.e. starting with a slash (/)",
         "value"),
    _Opt("detect_base", "B", "detect-base", None,
         "Look for the `<base>` tag in input for the base", "flag"),
    _Opt("text_only", "t", "text", None,
         "Output only the contained text of the filtered nodes, not the entire HTML",
         "flag"),
    _Opt("ignore_whitespace", "i", "ignore-whitespace", None,
         "Skip over text nodes whose text that is solely whitespace", "flag"),
    _Opt("pretty_print", "p", "pretty", None,
         "If to reformat the HTML to be more nicely user-readable", "flag"),
    _Opt("remove_nodes", "r", "remove-nodes", "REMOVE_NODES",
         "Do not output the nodes matching any of these selectors", "append"),
    _Opt("attributes", "a", "attributes", "ATTRIBUTES",
         "Output only the contents of the given attributes", "append"),
    _Opt(None, "h", "help", None, "Print help", "help"),
    _Opt(None, "V", "version", None, "Print version", "version"),
]

_BY_LONG = {opt.long: opt for opt in _OPTIONS}
_BY_SHORT = {opt.short: opt for opt in _OPTIONS if opt.short}

_POSITIONAL_HELP = "What CSS selector to filter with [default: html]"
_USAGE = "Usage: %s [OPTIONS] [SELECTOR]" % BIN_NAME


class _Exit(Exception):
    """Carries clap's chosen exit code out of the parser."""

    def __init__(self, code: int):
        super().__init__(code)
        self.code = code


def render_help() -> str:
    lines = [ABOUT, "", _USAGE, "", "Arguments:"]

    positional = "[SELECTOR]"
    lines.append("  %s  %s" % (positional, _POSITIONAL_HELP))
    lines.append("")
    lines.append("Options:")

    width = max(len(opt.spec()) for opt in _OPTIONS)
    for opt in _OPTIONS:
        lines.append("  %-*s  %s" % (width, opt.spec(), opt.help))
    return "\n".join(lines) + "\n"


def _error(message: str, with_usage: bool, tip: str = None) -> "_Exit":
    out = ["error: %s\n" % message, "\n"]
    if tip is not None:
        out.append("  tip: %s\n" % tip)
        out.append("\n")
    if with_usage:
        out.append(_USAGE + "\n")
        out.append("\n")
    out.append("For more information, try '--help'.\n")
    _write(sys.stderr, "".join(out))
    return _Exit(2)


def _unexpected(argument: str) -> "_Exit":
    tip = None
    if argument.startswith("-"):
        tip = "to pass '%s' as a value, use '-- %s'" % (argument, argument)
    return _error("unexpected argument '%s' found" % argument, True, tip)


def _missing_value(opt: _Opt) -> "_Exit":
    return _error(
        "a value is required for '%s' but none was supplied" % opt.invocation(),
        False,
    )


def _repeated(opt: _Opt) -> "_Exit":
    """clap rejects a second occurrence of any non-`Vec` argument."""
    return _error(
        "the argument '%s' cannot be used multiple times" % opt.invocation(),
        True,
    )


def _assign(config: Config, opt: _Opt, value, seen: set) -> None:
    if opt.kind == "append":
        # `Vec<String>` fields use `ArgAction::Append`, so repeats accumulate.
        getattr(config, opt.field).append(value)
        return
    # `bool` and `String`/`Option<String>` fields use SetTrue/Set, and clap
    # errors on a second occurrence rather than letting the last one win.
    if opt.long in seen:
        raise _repeated(opt)
    seen.add(opt.long)
    setattr(config, opt.field, value)


def parse(argv) -> Config:
    """Mirrors `Config::parse()`; raises `_Exit` where clap would exit."""
    config = Config()
    positionals = []
    seen = set()
    index = 0
    options_ended = False

    while index < len(argv):
        argument = argv[index]
        index += 1

        if options_ended:
            positionals.append(argument)
            continue

        if argument == "--":
            options_ended = True
            continue

        if argument.startswith("--"):
            body = argument[2:]
            if "=" in body:
                name, _, inline_value = body.partition("=")
            else:
                name, inline_value = body, None
            opt = _BY_LONG.get(name)
            if opt is None:
                raise _unexpected(argument)
            if opt.kind == "help":
                _write(sys.stdout, render_help())
                raise _Exit(0)
            if opt.kind == "version":
                _write(sys.stdout, "%s %s\n" % (BIN_NAME, VERSION))
                raise _Exit(0)
            if opt.kind == "flag":
                if inline_value is not None:
                    raise _unexpected(argument)
                _assign(config, opt, True, seen)
                continue
            if inline_value is None:
                if index >= len(argv):
                    raise _missing_value(opt)
                inline_value = argv[index]
                index += 1
            _assign(config, opt, inline_value, seen)
            continue

        if argument.startswith("-") and argument != "-":
            cluster = argument[1:]
            position = 0
            while position < len(cluster):
                letter = cluster[position]
                position += 1
                opt = _BY_SHORT.get(letter)
                if opt is None:
                    raise _unexpected(argument)
                if opt.kind == "help":
                    _write(sys.stdout, render_help())
                    raise _Exit(0)
                if opt.kind == "version":
                    _write(sys.stdout, "%s %s\n" % (BIN_NAME, VERSION))
                    raise _Exit(0)
                if opt.kind == "flag":
                    _assign(config, opt, True, seen)
                    continue
                # A value-taking short flag consumes the rest of the cluster,
                # or the next argument when the cluster ends here.
                if position < len(cluster):
                    value = cluster[position:]
                else:
                    if index >= len(argv):
                        raise _missing_value(opt)
                    value = argv[index]
                    index += 1
                _assign(config, opt, value, seen)
                break
            continue

        positionals.append(argument)

    if len(positionals) > 1:
        raise _unexpected(positionals[1])
    if positionals:
        config.selector = positionals[0]

    return config
