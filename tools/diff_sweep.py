"""Differential sweep: cross every document against every invocation.

`tests/cases.py` is a curated corpus -- good for regression, but only as good as
what was thought of. This runs a combinatorial product of documents x selectors
x flag sets through both the Rust binary and this port and reports every
divergence, which is how the corpus gets its next entries.

    python tools/diff_sweep.py            # full sweep, prints mismatches
    python tools/diff_sweep.py --limit 50 # stop after 50 mismatches

Mismatches are printed as ready-to-paste `tests/cases.py` tuples.
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import subprocess
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = os.environ.get("HTMLQ_ORACLE_IMAGE", "htmlq-rust-test")
BINARY = os.environ.get("HTMLQ_ORACLE_BINARY", "/src/target/debug/htmlq")

DOCUMENTS = {
    "simple": "<p>hello <b>world</b></p>",
    "nested": (
        "<!DOCTYPE html><html><head><title>T</title>"
        '<base href="https://example.org/dir/"><link rel="stylesheet" href="/s.css">'
        "</head><body><div id=\"main\" class=\"a b\"><h1>Head</h1>"
        '<p class="intro">Intro <a href="/one">one</a> and <a href="two">two</a>.</p>'
        "<ul><li>1</li><li>2</li><li>3</li></ul>"
        "</div><!--comment--><footer>End</footer></body></html>"
    ),
    "whitespace": "<div>\n  <p>  a  </p>\n\n  <p>b</p>\n</div>",
    "entities": "<p>&amp;&lt;&gt;&quot;&nbsp;&copy;&#x1F600;</p>",
    "raw_text": "<div><script>a < b && c</script><style>p > i {}</style></div>",
    "svg": (
        '<div><svg xmlns:xlink="http://www.w3.org/1999/xlink" width="10">'
        '<a xlink:href="/vector"><rect type="text"/></a></svg>'
        '<a href="/html">h</a></div>'
    ),
    "table_misnest": "<table><tr><td>cell</td></tr>stray<tr><td>2</td></tr></table>",
    "template": "<div><template><p>t</p></template><p>after</p></div>",
    "forms": (
        '<form><input type="text" name="a" value="V" disabled>'
        '<input type="checkbox" checked><select><option selected>o</option></select>'
        "<button>go</button></form>"
    ),
    "links": (
        '<div><a href="/root">r</a><a href="rel">e</a><a href="//proto/x">p</a>'
        '<a href="////four">f</a><a href="https://abs.example/z">a</a>'
        '<a href="?q=1">q</a><a href="#frag">g</a><a href="">empty</a>'
        '<a>none</a><area href="/area"><link href="/link"></div>'
    ),
    "unclosed": "<div><p>one<p>two<span>three</div>",
    "duplicate_attrs": '<p id="x" id="y" class="c">dup</p>',
    "uppercase_tags": '<DIV CLASS="Hi"><P ID="Big">Text</P></DIV>',
    "deep": "<a1><b2><c3><d4><e5>deep</e5></d4></c3></b2></a1>",
    "empty_elements": "<div><p></p><span> </span><i><!--c--></i></div>",
    "unicode": "<p lang=\"ja\">日本語 éà 中文</p>",
    "nbsp_runs": "<p>a  b c</p>",
}

SELECTORS = [
    "html", "*", "p", "div", "a", "li", "input", "svg", "template", "script",
    ".a\\.b", "[rel='next' s]", "[rel=next i]", "[data-Q=1]", "[empty='']",
    '[class~="b"]', '[lang|="ja"]', "[href$='x']", '[id*="a"]',
    "div p", "div > p", "h1 + p", "h1 ~ p", "ul li:first-child",
    "li:last-child", "li:nth-child(2)", "li:nth-child(2n+1)", "li:nth-last-child(1)",
    "p:first-of-type", "p:last-of-type", "p:only-of-type", "p:empty",
    ":root", ":scope", "p:not(.intro)", ":link", ":any-link", "input:checked",
    "input:disabled", "*|a", "|p", "[*|href]", "a, p", "div, span, i",
    # forms that should fail to parse, so the panic path is swept too
    "((", "p::before", "p:nonsense", "", "p >", "p:not(:not(a))", "p:is(.a)",
    "p:has(a)", "[", "[]", "p,", ",p", "> p", "a..b", "#", ".",
    # escapes, flags and quoting
    r".a\.b", "[rel='next' s]", "[rel=next i]", "[data-Q=1]", "[empty='']",
    "p[class~='A']", "p[class~='a' i]", "option:checked", "optgroup > option",
    "span span span", "div > * > span", ":not(div)", "*:first-child",
    "li:nth-child(-n+2)", "li:nth-of-type(odd)", "li:nth-last-of-type(2)",
    "svg|a", "unknown|p",
]

FLAG_SETS = [
    [],
    ["-t"],
    ["-t", "-i"],
    ["-p"],
    ["-a", "href"],
    ["-a", "href", "-a", "class"],
    ["-r", "a"],
    ["-r", "p", "-r", "span"],
    ["-b", "https://mgdm.net"],
    ["-b", "https://mgdm.net/deep/path"],
    ["-B"],
    ["-B", "-b", "https://fallback.example"],
    ["-t", "-p"],
    ["-b", "https://mgdm.net", "-t"],
    ["-b", "not a url"],
    ["-b", "https://mgdm.net:443/x"],
    ["-b", "http://mgdm.net:8080"],
    ["-b", "file:///tmp/"],
    ["-b", "mailto:a@b.c"],
    ["-b", "//protocol-relative.example"],
    ["-b", "https://éxample.test/"],
    ["-a", "id"],
    ["-a", "xlink:href"],
    ["-r", "*"],
    ["-r", "html"],
    ["-r", "((" ],
    ["-p", "-i"],
    ["-t", "-i", "-p"],
]


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def combinations():
    for doc_name, doc in DOCUMENTS.items():
        for selector in SELECTORS:
            for flags in FLAG_SETS:
                yield ("%s|%s|%s" % (doc_name, selector, " ".join(flags)),
                       [selector] + flags, doc)


def run_rust(cases):
    lines = ["set -u"]
    for name, argv, stdin in cases:
        stdin_b64 = base64.b64encode(stdin.encode("utf-8")).decode("ascii")
        args = " ".join(shell_quote(a) for a in argv)
        lines.append("printf '%%s' %s | base64 -d > /tmp/in.bin" % shell_quote(stdin_b64))
        lines.append(
            "%s %s < /tmp/in.bin > /tmp/out.bin 2> /tmp/err.bin; status=$?"
            % (BINARY, args)
        )
        lines.append(
            "printf '%%s\\t%%s\\t%%s\\t%%s\\n' %s \"$status\" "
            '"$(base64 -w0 < /tmp/out.bin)" "$(base64 -w0 < /tmp/err.bin)"'
            % shell_quote(name)
        )
    completed = subprocess.run(
        ["docker", "run", "--rm", "-i", "--network", "none", "--entrypoint", "sh",
         IMAGE, "-s"],
        input="\n".join(lines).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr.decode("utf-8", "replace"))
        raise SystemExit(completed.returncode)

    results = {}
    for line in completed.stdout.decode("utf-8").splitlines():
        if not line.strip():
            continue
        name, status, out_b64, err_b64 = line.split("\t")
        results[name] = (
            int(status),
            base64.b64decode(out_b64),
            base64.b64decode(err_b64),
        )
    return results


class _CaptureStream:
    """A stand-in for sys.stdout/stderr exposing the `.buffer` the CLI writes to."""

    def __init__(self):
        self.buffer = io.BytesIO()

    def write(self, text):
        self.buffer.write(text.encode("utf-8"))

    def flush(self):
        pass


def run_python(cases):
    """Run the port in-process.

    `tests/test_parity.py` spawns a real subprocess per case, which is the
    honest way to assert CLI behaviour; a sweep of ten thousand combinations
    cannot afford ten thousand interpreter starts, so this drives the same
    entry point directly with the standard streams swapped out.
    """
    from htmlq.main import main as htmlq_main

    results = {}
    saved = (sys.stdin, sys.stdout, sys.stderr)
    try:
        for name, argv, stdin in cases:
            stdin_stream = _CaptureStream()
            stdin_stream.buffer = io.BytesIO(stdin.encode("utf-8"))
            out_stream = _CaptureStream()
            err_stream = _CaptureStream()
            sys.stdin, sys.stdout, sys.stderr = stdin_stream, out_stream, err_stream
            try:
                status = htmlq_main(list(argv))
            except SystemExit as exit_request:
                status = exit_request.code or 0
            except Exception:
                sys.stdout, sys.stderr = saved[1], saved[2]
                traceback.print_exc()
                status = -1
            finally:
                sys.stdin, sys.stdout, sys.stderr = saved
            results[name] = (
                status,
                out_stream.buffer.getvalue(),
                err_stream.buffer.getvalue(),
            )
    finally:
        sys.stdin, sys.stdout, sys.stderr = saved
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=25,
                        help="stop reporting after this many mismatches")
    options = parser.parse_args()

    cases = list(combinations())
    print("sweeping %d combinations..." % len(cases))

    rust = run_rust(cases)
    python = run_python(cases)

    mismatches = 0
    for name, argv, stdin in cases:
        if rust[name] == python[name]:
            continue
        mismatches += 1
        if mismatches <= options.limit:
            r_status, r_out, r_err = rust[name]
            p_status, p_out, p_err = python[name]
            print("\n=== MISMATCH %s" % name)
            print("    argv : %r" % (argv,))
            print("    stdin: %r" % stdin)
            if r_out != p_out:
                print("    rust  stdout: %r" % r_out)
                print("    py    stdout: %r" % p_out)
            if r_err != p_err:
                print("    rust  stderr: %r" % r_err)
                print("    py    stderr: %r" % p_err)
            if r_status != p_status:
                print("    status: rust=%d py=%d" % (r_status, p_status))

    print("\n%d/%d combinations diverge" % (mismatches, len(cases)))
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
