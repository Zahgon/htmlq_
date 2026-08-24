Task:
Migrate the Rust repository `mgdm_htmlq` to Python. The migrated program must
behave exactly as the original does, for every input it accepts.

Input:

1. `scraped_repos/Rust/mgdm_htmlq/` — the repository to migrate, at base commit
   `bfcb1d1d`. A Cargo binary crate, edition 2024, MIT licensed: 3 source files
   totalling 407 lines, 1 integration test file totalling 38 lines, a manifest
   and lockfile, a Nix flake, and a release workflow. It is `htmlq`, a filter in
   the shape of `jq`: it reads HTML from standard input or a file, selects nodes
   with a CSS selector, and writes them back out, with options to emit only the
   text of the selected nodes, only named attributes of them, or a
   pretty-printed re-serialisation, to strip matching nodes before output, and
   to rewrite relative links against a base URL.

2. `docker/htmlq-rust-test.Dockerfile` — a container definition that builds the
   Rust crate and runs its tests. It is kept outside the repository so the
   source stays pristine.

Output:

`migrated_repo_python/mgdm_htmlq/` — the migrated repository, laid out the way
the sibling migrations in this workspace are laid out, containing:

- the ported source as an installable Python package, one module per Rust source
  file;
- the ported tests;
- a `Dockerfile` that builds the port and runs its tests;
- a `Makefile` exposing install, test and run;
- a `README.md` recording how the source maps onto the port, how each declared
  Rust dependency is accounted for, and every place the two implementations
  necessarily differ, with the reason for each;
- `golden.patch` — a single Git diff that turns a pristine checkout of the
  source at the base commit into the migrated tree.

Requirements:

- Every module, type, function, field and public API in the source has an
  equivalent in the port.
- Observable behaviour is preserved: the command-line surface and its defaults,
  what the program writes to standard output, what it writes to standard error,
  the order in which it writes them, what it writes to a destination file when
  asked to, and the status it exits with.
- Behaviour that the source obtains from a dependency rather than from its own
  code is part of observable behaviour and is preserved. This includes how HTML
  is parsed and written back out, which CSS selectors are accepted and what they
  match, how URLs are parsed and resolved, and what the argument parser prints.
- Behaviour that follows from the source language's memory model rather than
  from its statements is part of observable behaviour and is preserved.
- Error and failure behaviour is part of observable behaviour and is preserved
  as found, not corrected. Where the source is wrong, the port is wrong the same
  way.
- Every test in the source is migrated, keeping its name, its inputs, its
  assertions and its position relative to the code it covers. Tests may be
  added; none may be dropped, merged or weakened.
- Both repositories build, and their tests run and pass.

Scoring:

The two implementations are executed against the same inputs and their results
compared. There is one case per test in the source suite, one case per option
and option combination covering standard output, standard error and exit status,
one case per source module, one case per class of input the program accepts —
documents, selectors, base URLs — and one case each for the source build, the
migrated build, the migrated test suite and the patch. Score = cases passed /
total.

Docker is available. The migrated package must install and test with no network
access.
