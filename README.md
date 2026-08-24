# htmlq

Like [`jq`](https://stedolan.github.io/jq/), but for HTML. Uses [CSS selectors](https://developer.mozilla.org/en-US/docs/Learn/CSS/Introduction_to_CSS/Selectors) to extract bits of content from HTML files.

This is a **Python migration of [mgdm/htmlq](https://github.com/mgdm/htmlq)** (Rust, MIT).
It is a behavioural port, not a reimplementation: the CLI, the output bytes, the
exit codes and the panic messages are reproduced exactly. See
[Migration notes](#migration-notes) for what that means in practice and where
the edges are.

## Installation

```sh
pip install .
```

That puts an `htmlq` command on your path. `python -m htmlq` is equivalent.

## Usage

```console
$ htmlq -h
Like jq, but for HTML.

Usage: htmlq [OPTIONS] [SELECTOR]

Arguments:
  [SELECTOR]  What CSS selector to filter with [default: html]

Options:
  -f, --filename <INPUT_PATH>        Where to read HTML input from [default: -]
  -o, --output <OUTPUT_PATH>         Where to write the filtered HTML to [default: -]
  -b, --base <BASE>                  What URL to prepend to links without an origin, i.e. starting with a slash (/)
  -B, --detect-base                  Look for the `<base>` tag in input for the base
  -t, --text                         Output only the contained text of the filtered nodes, not the entire HTML
  -i, --ignore-whitespace            Skip over text nodes whose text that is solely whitespace
  -p, --pretty                       If to reformat the HTML to be more nicely user-readable
  -r, --remove-nodes <REMOVE_NODES>  Do not output the nodes matching any of these selectors
  -a, --attributes <ATTRIBUTES>      Output only the contents of the given attributes
  -h, --help                         Print help
  -V, --version                      Print version
```

## Examples

### Using with cURL to find part of a page by ID

```console
$ curl --silent https://www.rust-lang.org/ | htmlq '#get-help'
<div class="four columns mt3 mt0-l" id="get-help">
        <h4>Get help!</h4>
        <ul>
          <li><a href="https://doc.rust-lang.org">Documentation</a></li>
          <li><a href="https://users.rust-lang.org">Ask a Question on the Users Forum</a></li>
          <li><a href="http://ping.rust-lang.org">Check Website Status</a></li>
        </ul>
      </div>
```

### Find all the links in a page

```console
$ curl --silent https://www.rust-lang.org/ | htmlq --attributes href a
/
/tools/install
/learn
https://blog.rust-lang.org/
[...]
```

### Get the text content of a post

```console
$ curl --silent https://nixos.org/nixos/about.html | htmlq --text .main
```

### Remove a node before output

```console
$ curl --silent https://nixos.org/ | htmlq '.whynix' --remove-nodes svg
```

### Pretty print HTML

(This is a bit of a work in progress)

```console
$ curl --silent https://mgdm.net | htmlq --pretty '#posts'
```

### Syntax highlighting with [`bat`](https://github.com/sharkdp/bat)

```console
$ curl --silent example.com | htmlq 'body' | bat --language html
```

## Migration notes

### Layout

The package mirrors the crate module for module. `src/link.rs` and
`src/pretty_print.rs` become `htmlq/link.py` and `htmlq/pretty_print.py`;
`src/main.rs` splits into `htmlq/main.py` (the program) and `htmlq/cli.py` (the
`#[derive(Parser)]` layer, which is large enough to deserve its own file).

```
src/main.rs         ->  htmlq/main.py + htmlq/cli.py
src/link.rs         ->  htmlq/link.py
src/pretty_print.rs ->  htmlq/pretty_print.py
tests/cli.rs        ->  tests/test_cli.py
src/link.rs  #[cfg(test)]  ->  tests/test_link.py
```

### Dependencies

htmlq leans on its crates heavily enough that their behaviour *is* htmlq's
behaviour, so most of them are ported rather than swapped for a Python
equivalent that would be "close enough".

| Rust crate | Python | Why |
|---|---|---|
| `html5ever` (parsing) | `html5lib` | The same WHATWG tree-construction algorithm, already implemented in Python. |
| `html5ever` (serializing) | `htmlq/vendor/html5ever.py` | `pretty_print.rs` *subclasses* `HtmlSerializer` and writes into its buffer between callbacks, so the exact byte output and callback protocol are observable. |
| `kuchikiki` | `htmlq/vendor/kuchikiki.py` | The DOM, its lazy iterators and its `Rc`/`Weak` link structure are all observable — see below. |
| `selectors` 0.22 | `htmlq/vendor/selectors.py` | Which selectors *parse* decides which inputs panic, and kuchikiki's `SelectorImpl` makes `:checked`, `:disabled` &c. never match. |
| `url` 2.2 (rust-url) | `htmlq/vendor/url.py` | WHATWG URL, not RFC 3986 — `urllib.parse` gives different answers for `--base`. |
| `clap` 4 | `htmlq/cli.py` | Help text, error wording and exit code 2 are part of the CLI contract. |
| `lazy_static` | a module-level `frozenset` | Python evaluates module bodies once already. |

`html5lib` and `idna` are the only installed dependencies.

### Behaviours that took deliberate work

These are the places where a naive port would compile, pass a smoke test, and
still be wrong.

- **Detaching frees nodes, and that truncates the traversal.** kuchikiki holds
  `first_child`/`next_sibling` as strong `Rc`s and `parent`/`previous_sibling`/
  `last_child` as `Weak`s. `--remove-nodes` detaches a node while the selector
  iterator is walking it, so once the last `Rc` goes the node is dropped and its
  children's `parent()` returns `None`, ending the walk. `htmlq 'p, i, u' -r p`
  really does stop after the first element inside the removed `<p>`. The port
  reproduces this with `weakref.ref` back-edges plus explicit iterator classes
  (a generator frame would hold a node one step too long).
- **`--remove-nodes` removes only the first match** per selected node, because
  the Rust calls `select_first`.
- **Attribute order** is source order, and rewriting `href` keeps its original
  position — kuchikiki stores an `IndexMap`.
- **Adjacent text nodes are merged.** html5ever folds character runs into one
  text node; html5lib does not, and the difference shows up in
  `--text --ignore-whitespace` (a newline per fragment) and `--pretty`.
- **`<template>` children live in a separate contents fragment**, so a
  serialised `<template>` is empty.
- **Attribute-value matching is case-insensitive for 45 HTML attributes**
  (`rel`, `type`, `lang`, …) but only against HTML elements — `[type="TEXT"]`
  matches `<input type="text">` and not `<rect type="text">` inside an `<svg>`.
- **`str::trim()` is not `str.strip()`**: Rust trims Unicode `White_Space`,
  Python's `strip()` also eats U+001C–U+001F. `rust_trim` implements the former.
- **Output is bare LF.** Everything goes through the binary buffers so Windows
  does not turn it into CRLF.
- **`.expect()` panics exit 101**, with Rust's message shape on stderr; clap
  usage errors exit 2. Both are reproduced, including the `src/main.rs:LINE:COL`
  locations, which are kept verbatim so the stderr stays byte-identical.

### Known differences

One, and it is inherited from the parser: html5lib has no *scripting enabled*
flag, so it parses `<noscript>` in `<head>` as markup, where html5ever with its
default `scripting_enabled: true` treats the contents as raw text. Documents
using `<noscript>` in `<head>` can therefore produce a different tree. The
*serialiser* side of the same flag is implemented, so `<noscript>` text is
written unescaped exactly as the Rust writes it.

## Testing

```sh
pip install -e ".[test]"
python -m pytest
```

Three suites:

- `tests/test_cli.py` and `tests/test_link.py` are the migrated Rust tests — the
  same inputs, assertions and expected outputs as `tests/cli.rs` and the
  `#[cfg(test)]` block in `src/link.rs`.
- `tests/test_parity.py` replays `tests/cases.py` against `tests/oracle.json`, a
  recording of what the **real Rust binary** printed for each case, and requires
  stdout, stderr and exit status to match byte for byte.

`tools/capture_oracle.py` regenerates the recording from the Rust binary, and
`tools/diff_sweep.py` cross-multiplies documents, selectors and flag sets
against both implementations to hunt for divergences the curated corpus misses.
Both need the `htmlq-rust-test` image:

```sh
docker build -f docker/htmlq-rust-test.Dockerfile -t htmlq-rust-test scraped_repos/Rust/mgdm_htmlq
```

## License

MIT, Copyright (c) 2019 Michael Maclean. See [LICENSE.md](LICENSE.md); the
original project's license is carried over unchanged.
