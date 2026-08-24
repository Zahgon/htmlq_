"""htmlq -- like jq, but for HTML.

A Python migration of https://github.com/mgdm/htmlq (MIT, Michael Maclean),
preserving the CLI, output bytes, exit codes and error messages of the Rust
original. `htmlq.main.main` is the entry point; `htmlq.link` and
`htmlq.pretty_print` mirror the modules of the same name in `src/`, and the
crates htmlq depends on are ported under `htmlq.vendor`.
"""

__version__ = "0.5.0"
