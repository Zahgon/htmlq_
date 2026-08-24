# truth.md — mgdm/htmlq, Rust to Python (golden trajectory)

The ordered moves a competent run makes, from opening the task to handing over
the migrated repository. Each step says **what to do and why**; **no step states
what it evaluates to**. Derived from a completed migration with every produced
value stripped out: no serialised tag, no escaped character, no help text, no
usage error, no panic string, no exit code, no joined URL, no emitted node
sequence and no test count appears anywhere in this file.

**Method is kept; the goldens are stripped.** What is below is complete as
method — the inventory to take before touching anything, the dependency-boundary
decision that determines whether the port preserves behaviour or merely
resembles it, the memory-model reading that a transliteration silently gets
wrong, the order the operations run in, the full set of cases both repositories
must satisfy, and the cross-implementation check that catches the error a
same-language suite cannot see. What is deliberately **not** printed is any
observed value: what a void element round-trips to, what a non-breaking space
serialises as, what a rejected selector prints and exits with, what a base URL
joins to, what the argument parser says about a repeated flag, where a truncated
traversal stops. Those are the answer key. A run that has this file still has to
execute the original to learn them, which is the point — the capture is Step 4
and the comparison is Step 11.

**No worked port of any module appears here either**, for the same reason. One
correct rendering of the link module or the serialiser shows a reader the
pattern for the rest.

---

## What is being asked

Migrate a Rust repository to Python so that the migrated program behaves exactly
as the original does — same standard-output bytes, same standard-error bytes,
same exit status, for every input — and so that both repositories build, test,
and can be shown to agree.

The source is `scraped_repos/Rust/mgdm_htmlq` at base commit
`bfcb1d1d11a80fdd92c0dace1e7e559fbdb225cb` — a Cargo binary crate, edition 2024,
MIT licensed, three source files and one integration test file, plus a manifest,
a lockfile, a Nix flake and a release workflow. It is `htmlq`: a filter that runs
CSS selectors over HTML read from standard input or a file, with options to emit
only text, only named attributes, or a pretty-printed re-serialisation, to strip
nodes before output, and to rewrite relative links against a base URL.

The deliverable is `migrated_repo_python/mgdm_htmlq/`, laid out the way the
sibling migrations in this workspace are laid out.

---

## Delta-lever

**The specification is the dependency behaviour and the ownership model, not the
source text.**

The crate itself is small. Almost nothing a user observes is decided in it:

1. **The dependencies are the program.** Which selectors parse, how an element
   round-trips, which characters are escaped, what a base URL joins to, what the
   argument parser prints — every one of these is decided inside a library that
   is not in this repository, at the version this commit's lockfile pins. The
   three source files are a thin arrangement over those decisions. A port that
   substitutes the nearest library for each of them keeps the arrangement and
   loses the behaviour, and it will look entirely reasonable doing so.
2. **The ownership model is observable.** The DOM's links are asymmetric: some
   directions own their targets and the others do not. The program mutates the
   tree *while* an iterator is walking it, so nodes are not merely unlinked —
   they are released, and what the walk can still reach changes as a
   consequence. The set of nodes the program emits depends on this. A port built
   on ordinary references produces more output than the original, on inputs the
   feature exists to serve.

Compounding both: the source's own suite is small and exercises none of it. A
migrated suite that mirrors the source suite one for one will be green on a port
that diverges on most inputs.

So the lever is entirely in **capturing behaviour from the original by
execution** and in **running the two implementations against each other across a
wide input space**. Everything else here — writing Python, laying out a package,
building a container — is work a competent run does correctly without help.

## The crux

**The crux is Step 11: the product of many documents, many selectors and many
option sets is executed against both implementations and compared on all three
observables.**

It has three failure surfaces, and all three are separately fatal:

1. **Testing the port only against the migrated suite.** The source suite is
   small enough to mirror in an afternoon, and mirroring it is the default
   route: it is producible, it is fast, and it is green on a port that disagrees
   with the original on the majority of real inputs. The source suite covers a
   fraction of the surface the program exposes.
2. **Curating the comparison corpus by hand and stopping there.** A hand-written
   corpus tests what its author thought of. The behaviours that decide this
   migration are the ones nobody thinks of — they are consequences of library
   internals and of an ownership model, not of the feature list. The corpus is
   necessary as regression, and it is not sufficient as discovery.
3. **Fixing a divergence without a model that predicted it.** A sweep produces
   a failing case; the tempting move is to adjust code until it passes. That
   converges on a port that agrees on the swept inputs and diverges just outside
   them. Each divergence has a cause that generalises, and the cause is what has
   to be found.

The first is the nearest real competitor. It produces a package that installs,
runs, tests green, and is wrong in the dimension the task is about.

---

## Step 0 — Take the base commit and inventory the source

Record the base commit, the licence and the declared dependency **versions**
before anything else. The versions matter: the behaviour being preserved is that
of the pinned versions, not of the crates' current heads, and at least one of
them will differ from what a reader remembers of the current release.

Read all three source files and the test file end to end. The crate is small
enough that this is cheap, and everything that follows depends on knowing which
dependency APIs are reachable from the binary and — more importantly — which of
them the source reaches *into* rather than merely calls.

Record the line counts, split into production and test. They are the honest
denominator for "did I port all of it".

## Step 1 — Establish the source's build and test baseline in a container

Write a container definition that builds the crate and compiles its test binaries
at image-build time, so a run only executes them. Keep it outside the source tree
so the specimen stays pristine.

Choose the base image from the crate's declared edition, not from habit; the
edition sets a toolchain floor that the obvious base image may not clear. Expect
one missing-toolchain failure on a minimal image and fix it in the definition
rather than by changing the base.

Run the suite. Record the per-target results.

## Step 2 — Decide the dependency boundary, and decide it explicitly

This is the decision the whole migration turns on.

For each declared dependency ask: **is this library's behaviour observable
through the program's output?** If yes, it must be ported. If no, use the
target language's equivalent.

Work through them one at a time and name, for each, the specific observable that
decides it. Several look like obvious library swaps and are not, because the
source reaches into the library rather than calling it: one module *subclasses*
the serialiser and writes into its output buffer between callbacks, which makes
both the byte output and the callback protocol part of the contract.

One dependency is a genuine swap — the target language has an implementation of
the same published algorithm. Take it, and be precise about which half of that
library's job you are taking and which half you are still on the hook for.

Write the resulting table into the README. A reader who disagrees with a row
should be able to see what evidence would change it.

## Step 3 — Read the ownership model, not just the API

Read the DOM crate's own source — it is in the build image from Step 1 — and
find which link directions own their targets and which do not.

Then work out what happens to a node the program detaches *while an iterator is
walking it*. Then find the traversal cursor's exact termination condition and the
exact order in which it consults each link, and plan to reproduce that rather
than a preorder walk that happens to agree on unmutated trees.

This is not a detail; it changes which nodes the program emits. A
transliteration cannot find it. Only this reading, or Step 11, will.

## Step 4 — Capture the original's behaviour before writing Python

Write down a corpus of named cases, each an argument vector and a standard
input, covering: the source's own tests; defaults and the whole-document path;
every serialiser edge; every selector form the engine accepts and a good number
it must reject; every option and every meaningful option combination; the
link-rewriting paths including the one that bypasses joining; and the whole
command-line surface including help, version, unknown arguments, missing values
and repeated arguments.

Run the corpus through the original binary **in one container invocation**,
encoding standard input and both output streams so bytes survive, and store the
recordings as a fixture. A container start per case is slow enough that it will
tempt you to shrink the corpus, which is the one thing that must not happen.

Read the recordings before writing any Python. Several will contradict what you
expected from reading the dependencies. That is why this step is here and not
later.

## Step 5 — Port bottom-up, deepest dependency first

Order: the URL type, then the serialiser, then the selector engine, then the
DOM, then the three program modules, then the argument parser.

**The URL type.** Port the specification's parser state machine. Do not reach for
the standard library's joiner; it implements a different specification, and the
places the two disagree are exactly the inputs the base-URL option exists for.
Smoke-test it against the operations the program actually performs before moving
on.

**The serialiser.** Reproduce the escaping rules, the element classifications and
the callback protocol exactly, and keep the output buffer reachable as a public
attribute, because the pretty-printer writes into it directly.

**The selector engine.** The pinned version is the contract: which pseudo-classes
parse, which parse but can never match, how many simple selectors negation
accepts, which flags an attribute selector takes, and which attributes get
case-insensitive value comparison and against which elements. Read the crate
source in the build image rather than trusting recollection.

**The DOM.** Implement the Step 3 ownership asymmetry with weak references for
the back-edges. Write the iterators as explicit iterator classes, not generator
functions: a generator frame keeps its locals alive across a yield, which holds a
node past the point the original releases it.

## Step 6 — Port the three program modules line for line

Keep the shape of the original: same function names, same order, same control
flow, same quirks. Where the source does something that looks like a bug, port
the bug and say so in a comment. Where a source-language idiom has no target
spelling, keep the call sites recognisable — a borrow that cannot fail becomes a
method returning self, a mutable-value accessor becomes a handle with a mutable
field.

Preserve the laziness of the iterator chain. The original inspects, transforms
and consumes one item at a time, and the mutations it makes while consuming
affect what is still to come. A port that collects first and processes after is a
different program.

Release each handle at the point the original drops it. The loop construct that
reads most naturally holds the previous item while it asks for the next one, and
so does the filtering iterator underneath it; both keep a released-in-the-original
node reachable for one step longer, and that is enough to change the output.

## Step 7 — Reproduce the command-line surface, not an equivalent one

The argument parser's help text, error wording and exit codes are user-visible
and were captured in Step 4. Reimplement them. The target language's standard
argument parser differs in every particular and cannot be configured into
agreement.

Cover the argument-syntax forms the original accepts, the two distinct exits it
uses for different failure kinds, and the rule about which arguments may repeat
and which may not.

## Step 8 — Reproduce the failure paths

The original aborts on specific conditions with a fixed message shape and an
exit status distinct from the usage-error one. Reproduce the shape, the status
and the operating-system error rendering embedded in it.

Where a message embeds a source location from the original, keep it verbatim and
say in a comment why: the goal is byte-identical output, and every such line has
a counterpart in the port.

## Step 9 — Write out the platform hazards

Two bite here.

The original writes one line terminator on every platform; the target's text
streams do not. Route every output path — normal output, help, version, usage
errors, abort messages — through the binary buffers.

The original's string trimming follows one Unicode property; the target's
built-in trims a wider set. Implement the former and use it everywhere the
original trims.

## Step 10 — Migrate the test suite one for one

The source's integration test declares its cases through a macro that expands
each tuple into a test function. Parameterisation is the target's spelling of
that macro: carry the same tuples, the same argument vectors, the same expected
output, the same names.

Do the same for the unit-test module that lives inside a source file, moving it
to the tests directory — idiomatic in the target, and it changes nothing about
what is asserted.

Then add the parity suite: replay the Step 4 corpus and assert all three
observables against the recordings. Run it as a real subprocess, so exit status
and stream handling are exercised the way a user meets them.

## Step 11 — Differential execution against the original  *(crux)*

Write a sweep that runs the product of many documents, many selectors and many
option sets through both implementations and reports every divergence on all
three observables.

Include documents that are malformed, that use foreign namespaces, that nest
formatting elements badly, that carry comments and processing instructions, that
hold deeply nested structure, and that carry the constructs the parser treats
specially. Include selectors that must fail to parse. Include base URLs of
several schemes and shapes. Include option sets that combine the tree-mutating
options with each output mode.

Run the original side batched in one container and the port in-process. A
subprocess per combination makes the sweep too slow to run more than once, and
it will be run several times.

Expect divergences. Each is a fact about the original that was not available
from reading it. For each one: predict the behaviour from the model of Step 3,
check the prediction against the binary on small hand-built inputs, and only then
change code. A fix that makes the sweep pass without a model that predicted it is
a guess, and it will not generalise.

Fold every confirmed divergence into the Step 4 corpus so it becomes a regression
test. Iterate until the sweep is clean.

## Step 12 — Cover what the parity harness cannot observe

The parity harness compares two streams and a status. At least one option writes
its result somewhere else. Cover that path separately, taking its expected values
the same way — by running the original.

## Step 13 — Package, containerise, and run the port where the oracle ran

Write the packaging metadata with the real dependencies and a console entry
point. Write a container definition that installs the port and runs its suite
with no network and no source-language toolchain, since the parity fixture is a
recording.

Run the suite in that container. Developing on one operating system and recording
the oracle on another leaves room for a line-ending or path assumption to hide;
running the port where the oracle ran closes it.

## Step 14 — Produce the patch, and verify it away from the tree that made it

Generate a single diff that turns a pristine checkout of the source at the base
commit into the migrated tree, rooted so that it applies one level above the
repository directory. Split it into the test-file portion and the remainder.

Verify by applying to a **fresh** pristine checkout in a scratch directory, never
in the working tree. A patch verified against the tree it was generated from has
been verified against nothing.

## Step 15 — Final gate

Lint for dead imports. Compile every module. Re-run the migrated suite, the
container suite and the full sweep, in that order, after the last edit — not
before it. Then write the README a reviewer can argue with: the module mapping,
the dependency table with the reason for each row, the behaviours that took
deliberate work, and the known differences stated plainly rather than omitted.

Carry the licence over unchanged and attribute the original author.

---

## The test cases

Both repositories must be brought to a state where their tests run and pass, and
the two must be shown to agree. The cases below are the required set. Each says
what it covers and where its expected value comes from — **not what that value
is**; every golden is obtained by executing the source in Step 4.

### Source-side cases (the Rust repository)

| # | Case | How it is satisfied |
|---|---|---|
| S0 | The crate builds from its own manifest with the lockfile honoured, cold cache, on a toolchain that clears the declared edition | Step 1 |
| S1 | The project's test command runs and reports per target — the binary's unit tests and each integration test file | Step 1 |
| S2–S4 | Each integration case executes and passes | Step 1; macro-expanded cases counted as cases |
| S5–S10 | Each unit case in the source module executes and passes | Step 1; both macro groups |
| S11 | Behaviour is captured for every case in the Step 4 corpus, all three observables, bytes preserved | Step 4, batched harness |
| S12 | The command-line surface is captured: both help spellings, both version spellings, and every usage-error kind | Step 4 |
| S13 | The abort paths are captured: message shape, embedded location, embedded system error, exit status | Step 4 |
| S14 | The dependency versions actually resolved are read from the lockfile, and the pinned selector engine's accepted grammar is read from its source in the image | Steps 0 and 5 |
| S15 | The DOM's link ownership and its traversal termination rule are read from the crate source | Step 3 |
| S16 | The option that writes elsewhere than the compared streams is exercised and its result captured | Step 12 |

S2–S10 are the source's whole suite. It exercises a small fraction of what the
program exposes, which is the property that makes E1–E4 necessary rather than
optional.

### Migrated-side cases (the Python repository)

| # | Case | Relationship to the source |
|---|---|---|
| M0 | The package installs and imports; the image builds cold | mirrors S0 |
| M1–M3 | One test per source integration case: same name, same tuple, same argument vector, same expected output | one-for-one mirror of S2–S4 |
| M4–M9 | One test per source unit case, same inputs and expected values, relocated to the tests directory | one-for-one mirror of S5–S10 |
| M10… | One test per case in the Step 4 corpus, asserting all three observables against the recording | no source counterpart; Steps 4 and 10 |
| M11 | The corpus and the recording are checked against each other, so a case added without re-recording fails loudly | no source counterpart |
| M12… | The option of S16 covered directly, including truncation of an existing destination and one non-default output mode | no source counterpart; Step 12 |
| M13 | The suite runs unchanged inside the container of Step 13 | mirrors S0's platform |

The migrated suite must have **at least** as many cases as the source's and must
not drop, merge or weaken any of M1–M9.

### Equivalence cases (cross-repository)

| # | Case | Passing condition |
|---|---|---|
| E1 | Curated corpus × both implementations | Standard output, standard error and exit status agree byte for byte on every case |
| E2 | Swept product × both implementations | Zero divergences across the full product of documents, selectors and option sets |
| E3 | Tree-mutating options across the sweep | The emitted node sequence agrees, including the cases where the original's walk ends earlier than the tree structure suggests |
| E4 | Failure paths across the sweep | Every rejected selector, unparseable base, unknown argument, missing value and repeated argument agrees on message and status |
| E5 | Predicted-then-checked divergences | For each divergence found in Step 11, a prediction was made from the ownership model and confirmed against the binary on a minimal input *before* code changed |
| E6 | Patch round trip | The patch applies to a fresh pristine checkout at the base commit, and that tree tests green; the two split patches apply in either order and together reproduce the whole |

**E1–E5 are the migration's actual acceptance criteria.** M1–M9 passing is
necessary and not sufficient, because the ported suite — like the source's —
covers a fraction of the surface.

### Meta cases

| # | Case | Passing condition |
|---|---|---|
| X1 | Every divergence found by the sweep is present in the curated corpus as a named regression case | A divergence fixed but not recorded will return |
| X2 | Known differences are stated | Any behaviour the port cannot reproduce is named in the README with its cause, rather than omitted |

### Running the cases

Every case above is executed by one of the commands below. They are given so the
workflow is reproducible without guessing at invocations; **none of them states
what its output should be.** The expected values remain the Step 4 capture.

`SRC` is `scraped_repos/Rust/mgdm_htmlq` and `MIG` is
`migrated_repo_python/mgdm_htmlq`.

**Source side.**

| Case | Command | What to record |
|---|---|---|
| S0 | `docker build --no-cache -f docker/htmlq-rust-test.Dockerfile -t htmlq-rust-test $SRC` | that it builds cold, and the toolchain the stage pins |
| S1–S10 | `docker run --rm --network none htmlq-rust-test` | the per-target results and the per-case lines, verbatim |
| S11–S13 | `python tools/capture_oracle.py` from `$MIG`, against the image above | the recording, one entry per case, streams kept separate and byte-encoded |
| S14 | read the lockfile; read the pinned selector engine's parser inside the image | the resolved versions and the accepted grammar |
| S15 | read the DOM crate's node and iterator modules inside the image | which links own, and the cursor's termination rule |
| S16 | run the original with the option, against a fresh and an existing destination | the destination's bytes |

**Migrated side.** Run from `$MIG`.

| Case | Command | What to record |
|---|---|---|
| M0 | `make install`, then `make image` | that both succeed cold |
| M1–M12 | `make test` | the per-case results and the counted summary |
| M13 | `make docker-test` | that the same count passes on the oracle's platform |

**Equivalence and meta.**

| Case | Command | Passing condition |
|---|---|---|
| E1 | `python -m pytest tests/test_parity.py` | every case agrees on all three observables |
| E2–E4 | `make sweep` | the divergence count is zero |
| E5 | for each divergence: hand-built minimal input, run against the original, compare with the prediction | the prediction matched before any code changed |
| E6 | apply the patch to a fresh pristine checkout in a scratch directory, then test *that* tree | applies clean and passes there |
| X1 | diff the set of swept divergences ever seen against the corpus case names | every one is present |

E2 is run repeatedly during Step 11 and once more after the last edit. E6 is run
in a fresh directory, never in the working tree.

---

## Where runs break

- **The port is only ever tested against the migrated suite.** The default route.
  The source suite is small, mirroring it is quick, and the result is green on a
  port that disagrees with the original on most inputs.
- **The nearest library is adopted for a behavioural dependency.** A URL joiner
  implementing a different specification, a CSS engine with a different accepted
  grammar, a serialiser with different escaping, a standard argument parser with
  different messages. The program keeps its shape and loses its contract, and
  nothing in the port looks wrong.
- **The ownership asymmetry is missed.** Ordinary references everywhere, a
  preorder walk that looks obviously correct, and more output than the original
  produces on exactly the option the feature exists for.
- **Iterators are written as generators.** Correct-looking, and they hold a node
  across a yield past the point the original releases it. This survives the
  curated corpus and only shows up under the sweep.
- **A handle is held one step too long.** The natural loop construct keeps the
  previous item bound while fetching the next. Same symptom as above, one layer
  further out, and it reappears independently in each filtering stage.
- **The pinned version is assumed to be the current version.** A grammar feature
  that the current release supports and the pinned one does not, or the reverse.
  Recollection is not evidence; the crate source is in the image.
- **Line endings are left to the target's text streams.** Every line of output
  differs on one platform and no test written on that platform notices, because
  both sides of the comparison are produced there.
- **A divergence is fixed by adjusting code until the sweep passes.** Converges
  on agreement inside the swept space and divergence just outside it.
- **The sweep is run once, early, and not again after the last edit.**
- **The patch is generated and verified against the same tree.** It applies on
  the machine that produced it and nowhere else.
- **A known difference is quietly omitted from the README** because it has no
  clean fix.

## What cannot be done

The behaviour is not recoverable from the source text with confidence. What a
void element round-trips to, which characters are escaped in which position,
which selectors parse, which pseudo-classes parse but never match, what a base
URL joins to, what the argument parser prints on each failure kind — all are
decisions made inside libraries that are not in this repository, at the versions
this commit pins. Reading the source tells you which library is called; it does
not tell you what it emits. Only executing the original does.

Neither can the emitted node sequence under tree mutation be derived from the
tree structure. It is a consequence of the DOM's link ownership, and it has to
be read out of the dependency's source and then confirmed by execution.

Neither is a passing suite evidence. The source's suite covers a fraction of the
surface, and a migrated suite built to mirror it inherits exactly that coverage.
Judge on E1–E5, not on a green result.

And a port that installs and runs tells you nothing at all. Every wrong route
here produces a program that accepts a selector, prints plausible HTML and exits
zero, so nothing about its appearance separates a byte-compatible migration from
a confident invention.

## Sources

- **The program being migrated** — `scraped_repos/Rust/mgdm_htmlq` at base commit
  `bfcb1d1d11a80fdd92c0dace1e7e559fbdb225cb`, upstream
  `https://github.com/mgdm/htmlq`. Licensed **MIT**, © 2019 Michael Maclean; the
  licence file is carried into the migrated tree unchanged and the notice is
  preserved, which is the licence's condition.
- **The build and test baseline of the source** —
  `docker/htmlq-rust-test.Dockerfile`, kept outside the source tree so the
  specimen stays pristine, and which compiles the test binaries at image-build
  time so a run only executes them.
- **The behaviour of the pinned dependencies** — read from their own sources
  inside that image, at the versions the lockfile resolves, then confirmed
  against the Step 4 capture; where the reading and the capture disagree, the
  capture governs.
- **The layout, naming and top-level file set the migrated repository follows** —
  the sibling migrations in this workspace, in particular their separation of
  entry point from ported dependencies, their README structure and their
  `golden.patch` convention.
- **Every golden value referenced but not printed here** — obtained by executing
  the source per Step 4, and preserved in the migrated repository as the recorded
  fixture and the sweep tool, so that each one is re-derivable rather than
  asserted.
