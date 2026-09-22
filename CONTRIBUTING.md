# Contributing to Loment

Loment is early. `0.1.4` is not published as a package — you build the toolchain from a
checkout ([QUICKSTART.md](QUICKSTART.md)) — and the implementation changes quickly on purpose
([README](README.md#status) says which parts are frozen and which are not).

Three things are worth knowing before you spend time here: who writes this, one closed door, and
one wide open one.

## People and AI agents build this together

Loment is written by humans and by AI agents in the same repositories, often in the same commit.
That is deliberate, not something tolerated — this repository ships agent instructions
([CLAUDE.md](CLAUDE.md), [AGENTS.md](AGENTS.md)) and the language guide is written for an agent to
read first. Three things follow.

**Your patch may be reviewed by an AI, and that review counts.** Automated review is part of the
process here rather than a second-class opinion: it can close a pull request on its own, and it is
what watches the closed door below. "A person would have let it through" is therefore not a
defence, because a person may never look.

**You are responsible for what your tools produce.** An agent editing this tree is your agent, and
what it commits is your patch. The accidents this document warns about — a rewrite that spans
`lom/`, a generator's output edited by hand, a criterion widened to turn green — are exactly the
ones an agent makes quickly, confidently and in bulk. Read the diff before you send it; the check
for the door below is one command and it takes a second.

**Do not send code you have no right to send.** Nothing copied from a source whose licence you
cannot comply with, nothing confidential or leaked, nothing malicious. This is a legal
requirement, not a matter of taste, and it is a live risk with AI tools in particular: they
reproduce material from their training data without marking it, so a patch can carry someone
else's terms with no sign that it does. If you cannot say where a piece came from and under what
licence, do not send it. If you are unsure, say so in the pull request rather than staying quiet —
an open question is workable, a hidden one is not.

**A patch that breaks this document is not accepted, whoever or whatever wrote it.** The rules
below apply the same way to a change typed by hand and one produced by an agent: the closed door,
the generated files, the criteria.

## Do not change anything under `lom/`

`lom/` holds the **L0 interface** — `fuai.lom`, `fuc.lom`, `fujr.lom`, and the files emitted
from them under `lom/build/`. It is not an internal header directory. It is the contract the
rest of the project is built against, and **its other side lives in a different repository**: the
kernel side registers those primitives and checks them back with its own gate. A change here is a
change to something outside this repository, and this repository cannot tell you whether you
broke it.

So:

- **A pull request that modifies anything under `lom/` is detected automatically and closed
  without review.** Not personal — there is no way to evaluate the change from inside this
  repository alone, because the half it would break is not here.
- **Three of those, and you will no longer be able to open pull requests against any Loment
  repository.** The detection does not need a human to agree with it.

It is easy to hit by accident: a search-and-replace across the tree, a formatter pointed at the
repository root, a script that rewrites every `.lom` file. Both of those suffixes are in use
here. Before you push:

```
git diff --name-only origin/main -- lom/
```

If that prints anything, you have edited the interface.

Wanting the interface to change is a fine thing to want — that conversation is welcome, just not
as a patch. Open an issue. Interface changes are made deliberately, in a commit of their own,
with the downstream impact written down.

## Everything else is open — criticism included

This is early development, and right now the most useful contribution is usually a report rather
than a patch. **Anyone may submit a critical pull request from any angle.** "This construct is
wrong" counts: a design objection, a security objection, a documented feature that does not work,
a criterion that tests the wrong thing, an argument that this file is itself a mistake. Sketches,
half-finished proposals, and patches that delete something are all welcome.

What makes a report land is evidence:

- **the exact command, and the exact output** — pasted, not paraphrased;
- **the commit**, because the toolchain moves: `loment version` prints it, and an installed
  package can be dozens of commits behind a checkout;
- **the smallest source that shows it** — a twelve-line file beats a paragraph;
- **whether you think it is a bug or a thing not built yet.** Both are useful. You do not have to
  be right about which.

## Getting a toolchain

Either path in [QUICKSTART.md](QUICKSTART.md) works: the Python tools in `tools/`, or
`sh loment/bootstrap.sh` to build the self-hosted compiler from the committed seed. Once you have
the packaged toolchain on `PATH`, `loment version` tells you which commit you are on — quote that
in anything you report.

Before writing much Loment, read `.claude/skills/loment/SKILL.md`. It is the language guide —
syntax, builtins, the E001–E023 codes, and the traps that cost the most time — and it ships with
the toolchain (`loment skill --print`).

## Run the gate before you open a pull request

**There is no CI in this repository.** No workflow runs on your pull request, so nothing checks
your change for you except you:

```
python tools/ci.py --static-only      # about three minutes, no QEMU
```

The full gate adds the bare-metal targets and needs QEMU:

```
python tools/ci.py
```

**A few criteria are red when this repository is checked out on its own.** They need a companion
checkout that is not part of it (`LinuxFUAI/`) or a copy of the library store that lives outside
the repository. Those are expected. What matters is not adding to them.

If a suite fails, run that suite on its own before believing it — a criterion that fails only in
a parallel run is usually another checkout writing to the same tree.

## House rules for a change

### The language surface exists twice, and the two must agree byte for byte

`tools/lomentc.py` (the reference implementation) and `loment/selfhost/*.lomt` (the self-hosted
one) have to produce identical results. A change to the surface — lexer, parser, checker, codegen,
the diagnostic codes, the builtin table — changes both, in the same submission, or the parity
criteria fail. Read [docs/158](docs/158-loment-freeze.md) first: the surface is frozen, and
changing it means changing the specification, the conformance suites and both implementations
together.

### Generated files get their own commit

Several tracked files are produced from others: the self-host seed
(`loment/build/selfhost_driver.ll`), `loment/build/release-manifest.json`,
`loment/build/SHA256SUMS`, `docs/manual/`. Put them in a **separate commit whose message says it
is only generated output**, so a reviewer can look at the hand-written change by itself. The seed
has been 93% of a diff's lines — which is precisely the problem that separation solves.

If you touch a file the release manifest covers, regenerate it. The list is
`loment_release.GLOBS` and includes `tools/*.py`, `loment/tools/*.lomt`, `loment/lib/*.lomt` and
`docs/*.md`:

```
python tools/loment_release.py --emit
python tools/loment_release.py --checksums loment/build/SHA256SUMS
```

The seed is regenerated, never hand-merged:

```
python tools/loment_seed.py --emit
```

### Do not widen a criterion to make it pass

If a criterion turns red because of what you changed, that is the criterion working. A limit, a
budget or an expected count that has to move is a decision — make it explicitly and say so. It is
never a line to relax quietly.

### Notes live in `docs/`

Reasoning that should outlive the thread goes in `docs/NNN-kebab-name.md`, using the next free
number. `docs/manual/` is generated: change the generator, not the output.

### Displaying Loment

Loment has no syntax highlighter anywhere — not on GitHub, not in either editor's built-in
viewer. In docs, issues and comments, tag Loment code as `rust`: the overlap is large enough that
the colouring comes out right, and nothing else colours it at all. The file itself is always
`.lomt` (or `.lom`), never `.rs`.

## Reporting a bug

The shape that gets acted on fastest:

```
### Environment
- loment version -> (paste the line, it carries the commit)
- OS, and how you invoked it

### Summary
one paragraph

### Repro
the smallest file, and the exact commands

### Expected / Actual
what you expected, and what happened instead
```

Issues are in English. A report with a runnable reproduction is worth more than a well-argued one
without.

## Licence

MIT ([LICENSE](LICENSE)). Contributions are accepted under it.
