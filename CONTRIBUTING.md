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

**Say that a pull request came from an agent.** If an agent wrote the patch — or wrote it and you
only skimmed it — mark the pull request as such: an `ai-generated` label, or a line at the top of
the description naming the tool. This is not a stigma and it does not change whether the patch is
accepted; it changes what review can rely on. A person answers a question in the thread. A batch of
agent pull requests may have nobody behind it who will read a follow-up, and a reviewer who assumes
otherwise waits for an answer that is not coming.

Two failures are worth expecting from a robot in particular, and neither looks like a wrong line: a
criterion widened until it passes (already above), and its twin — **a check that stops running while
its suite still reports green**. Both read as success, so say what wrote the patch, and read its
diff on that assumption.

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

**LLVM 19 or newer is required.** The IR the toolchain emits uses the `#dbg_declare` record form,
which older clangs do not know; on clang 18 the `--debug` criteria fail with
`error: expected instruction opcode`, and nothing is wrong with your change. (Ubuntu 24.04 ships
clang 18, which is why the CI workflow installs LLVM 19 explicitly.)

Before writing much Loment, read `.claude/skills/loment/SKILL.md`. It is the language guide —
syntax, builtins, the E001–E023 codes, and the traps that cost the most time — and it ships with
the toolchain (`loment skill --print`).

## Run the gate before you open a pull request

The gate is one command, and it is the same one for everyone:

```
python tools/ci.py --static-only      # the static gate, no QEMU
```

It takes several minutes — 6–8 on a four-core machine, longer under load — because five of the
criteria are heavy and are only trustworthy when they run **alone**. On a Windows checkout those
five go through WSL; on Linux they run natively.

The full gate adds the bare-metal targets and needs QEMU:

```
python tools/ci.py
```

**And the same gate now runs on the pull request itself.** `.github/workflows/gate.yml` runs
exactly the static gate on every pull request and on every push to `main`. So a submission that
arrives with no gate result is incomplete: say in the description **what you ran** (CI, the command
above, or a single suite) and **what came out** — green N / red M — and for each red say whether it
is one of the known ones below or something you introduced. A change whose gate result nobody can
state is a change nobody can merge.

**This applies to a person's submission and an agent's alike.** There is no lighter track for
either: the point of a gate is that the answer does not depend on who is asked.

**Where the gate stands right now (2026-09-22).** The workflow is new and the gate is **not
all-green on a clean checkout**. The first measured run on a GitHub runner was 62 criteria, 43
green and 19 red — and the reds were not missing tools. Four of them need the companion repository
`LinuxFUAI/`, a private checkout that is not part of this one; the rest are Linux portability
problems being worked through one at a time, and the set is platform-dependent (a Windows checkout
is red in different places). So **today the bar is not "green". It is "you did not add a red, and
you said which reds you saw."** When the red set reaches zero this paragraph goes away and green
becomes the bar.

If a suite fails, run that suite on its own before believing it — a criterion that fails only in
a parallel run is usually another checkout writing to the same tree.

And when the thing under test is a binary you just produced, suspect the **file mode** before the
logic. This repository tracks 688 files and every one of them is mode `100644`, so a produced ELF
has no exec bit — and a checkout on Windows will not tell you, because the local artifact there is
a PE and PE execution ignores that bit entirely. That is not hypothetical: it is what the first red
on the first CI run turned out to be.

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

## One free thing we would ask for

**Star the repository.** It costs nothing and takes one click, and for a project with no marketing
that number is what decides whether the next person ever finds it. If Loment is useful to you —
or you only want it to keep going — that is the whole ask. Thank you.

## Licence

MIT ([LICENSE](LICENSE)). Contributions are accepted under it.
