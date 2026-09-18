![Loment — Programming Language, Program by Fujo](editors/loment-banner.png)

# Loment

Loment is a **systems programming language**: it compiles to native x86-64 executables —
Linux ELF and Windows PE, or a freestanding object for bare metal — with no runtime and no
libc. Its own syntax is Rust-flavored, and the toolchain is itself written in Loment.

Three things are worth knowing about it, and they are why this repository is worth a look
now:

- **It calls libraries written in other languages** — ten of them, end to end, from C and
  Rust to Python and Java.
- **It can be written in six other syntaxes** — C, C++, Java, C#, Go and Python. Only the
  spelling changes: a surface grammar decides how a file is *read*, never what it *means*.
- **Everything is customizable** — the source suffix, the commands, the libraries, and the
  toolchain itself.

**[Quick start](QUICKSTART.md)** ·
[Project site](https://fujojtop.github.io/FujoOSwebsite/loment/) ·
[Manual](docs/manual/index.md) ·
[Language guide](.claude/skills/loment/SKILL.md) ·
[Examples](loment/examples/) ·
[Issues](https://github.com/FujoJTOP/loment/issues)

## Status

Current version: `0.1.4-pre2`. This is a pre-release; prebuilt toolchains are not
published yet.

The **language surface is frozen** — syntax, type rules, diagnostics, unit loading and
capability semantics are specified in `docs/158-loment-freeze.md`, which also states what
a change to them costs. The **implementation is not frozen**.

## Getting the toolchain

### Prebuilt packages

Not published yet. Linux and Windows packages will appear in this repository's
[Releases](https://github.com/FujoJTOP/loment/releases).

### Building from source

The bootstrap needs POSIX `sh` on an x86-64 Linux host — natively, or under WSL on
Windows — and nothing else:

```
$ sh loment/bootstrap.sh
...
SEED BOOTSTRAP OK: ...
```

It puts the build chain through four checks: the seed builds a `stage1`, that `stage1`
reproduces the seed byte for byte, `stage2 -> stage3` reaches a fixed point, and `stage1`
agrees with `stage2` on a foreign entry file. Given an entry file, it also prints that
file's compiler output:

```
$ sh loment/bootstrap.sh hi.lomt > hi.ll
```

## A first program

```rust
module hello

fn _start() {
    let s: str = "hello from Loment\n";
    syscall4(1, 1, str_ptr(s) as u64, str_len(s) as u64);
    syscall4(60, 0, 0, 0);
}
```

```
$ loment run hello.lomt
hello from Loment
```

Source files are `.lomt`. `_start` is the entry point, and output goes through the `write`
system call. `loment build hello.lomt` produces a single self-contained executable.

Getting from here to a running binary — from a checkout today, since nothing is published
yet — is [QUICKSTART.md](QUICKSTART.md).

## Calling other languages' libraries

`extern fn` declares a function that lives somewhere else; the call site uses the platform
C ABI. There are two legs, and which one a language takes depends only on how it can be
reached.

### Static linking — anything that exports C symbols

```rust
extern fn c_add(a: i32, b: i32) -> i32;
```

```
$ cc -c lib.c -o lib.o
$ loment build app.lomt --link lib.o -o app
```

Several objects can be passed at once and symbols resolve across them. The linker in this
repository also takes a static archive (`libfoo.a`): it applies relocations and pulls in
only the members that define the symbols it needs. That is the difference between being
able to call one hand-picked function and being able to use a real C library.

C, C++, Rust and Zig all run end to end on this leg — C++ behind `extern "C"`, Rust behind
`#[no_mangle] pub extern "C"`, Zig behind `export fn`. The mechanism is the same for
anything else that exports C symbols.

### Process bridge — languages whose runtime is not a C library

Python, JavaScript, Java, Perl, Lua and Go are reached through `loment/lib/proc.lomt`:
start the interpreter, hand it the code, read the answer back, and let it `import` its own
libraries the way it always does. The price is a process boundary — bytes cross it, not
pointers, so a struct cannot be passed — and it is Linux/ELF only, because the Windows
shim has no pipe.

**Ten languages, each with a criterion that runs the result** (`loment_ffi_test`, 18/18,
none skipped): C / C++ / Rust / Zig, then Go / Python / Java / JavaScript / Perl / Lua.

### What it does not do yet

- Dynamic libraries (`.so` / `.dll`) and embedding a runtime (CPython, the JVM) — both are
  later stages, and neither is started.
- No `str` across the boundary. `str` is a pointer plus a length, not a C string, so a
  signature carrying one is rejected rather than miscompiled.
- No aggregates by value, no variadic functions, no callbacks.
- No FFI on the PE target: a Windows build with a foreign object is refused rather than
  producing something that does not link.

## Six surface syntaxes, one language

A file's syntax is a surface. Loment's own is Rust-flavored, and the same program can also be
written the way you already write C, C++, Java, C#, Go or Python — six in all. Declaring
which one is meant to be a line at the top of the file, before `module`:

```
choose write grammar python
```

Only the spelling changes. The surface grammar says how a file is *read*; the meaning is
always Loment's. So you do not have to learn a new language to write Loment — you write the
one you already know, and the semantics are the ones the rest of this repository specifies.

Today the six are reached through their translators, and each one has a criterion that
settles the question the only way it can be settled: **run the original, run the
translation, compare the numbers.**

```
$ python tools/ctrans.py sample.c --out sample.lomt        # C  -> Loment
$ python tools/potato_from.py Sample.go --json s.json      # Go -> form object
$ python tools/lomt_from.py s.json --impl --out s.lomt     #    -> Loment, with bodies
```

```
module Sample

pub fn level(n: i64) -> i64 {
    if (n < 10) { return 0; }
    ...
```

One translator per language — `ctrans.py`, `pytrans.py`, `jtrans.py`, `cstrans.py`,
`cpptrans.py`, `gotrans.py` — with one shared parser for the brace-and-semicolon family
(`trans_core.py`) and one dialect table each, because what really differs between C, C++,
Java and C# is small and worth writing down: C's `&&` yields an `int` and its conditions
accept one, while Java's and C#'s yield a `boolean`; C# has an unsigned `byte` where Java
has a signed one; Go has no implicit numeric conversion at all, so its `int(b)` is already
the `as` Loment wants. What cannot be represented is reported with a name and a reason —
nothing is dropped silently.

**Not wired yet:** the compiler does not accept `choose write grammar` in a file today. The
declaration is recorded in the form object (`docs/188-grammar-declaration.md`) and going
through `tools/` is the path that works; what is built and tested is the translation, not
the in-file switch.

## Loment · everything is customizable

The language fixes as little as it can get away with. What is a matter of taste or of local
convention is left to the project, and changing it never means forking the toolchain.

| What | How |
|---|---|
| The source file's **suffix** | `.lomt` is only a habit. Any suffix is a L1 source — `.lom` is the one exception, because it is the L0 interface contract and is routed by suffix. A project settles the rest in `<project root>/loment.conf`: `pub fn source_ext() -> str { return ".foo"; }` |
| The **command surface** | Put an executable named `loment-<name>` anywhere on `PATH`, and `loment <name> ...` forwards to it with the arguments and the exit code untouched. Official commands always win, so a stray `loment-version` cannot lie about the version. The same shape as `git`. |
| **Libraries** | A library is a directory, its identity is a content hash, and its dependencies are the `use` lines in its source rather than a manifest. So two versions can coexist, and nothing is identified by a version number you have to trust. |
| The **toolchain itself** | The compiler is written in Loment and rebuilds from a seed committed to this repository, so you can read it, change it, and check that it still reproduces itself byte for byte. |

None of this needs a registry or a schema: the extension points are files on disk, so you
can list them, diff them and put them in version control. A project's own conventions stay
in the project.

## The language itself

- **Syntax**: Rust's, minus the parts that need a runtime. `module`, `use`, `fn`, `let`,
  `if`, `while`, `for`, `match`, `enum`, `struct`, `trait`, generics, slices, `Result`.
  Where it deviates, the deviations are enumerated with minimal reproductions in the
  language guide, so you do not have to find them by failing to compile.
- **Built-ins**: a short fixed table. There is no `printf`, no `String`, no `Vec`; the
  interface to the operating system is a system call.
- **Capability domains**: the one thing Loment adds to Rust. A domain declares a resource
  space and the range of indices a program may use in it, and a `guard` enforces that
  range:

```rust
module blk

capability blk_write : disk[0..4] revocable

fn write_slot(slot: u32) -> u32 {
    guard blk_write(slot);
    return slot;
}
```

  Outside `[0..4]` the program traps, with no other side effect; inside, one audit entry is
  counted. An index the compiler can see to be out of range — `guard blk_write(7)` — is
  rejected at compile time as `E4` instead. The boundary is stated rather than glossed
  (`docs/146-loment-capability-semantics.md`): a guard constrains the *index*, not the
  *subject*, and binding a subject to a capability is the kernel's job.

## Diagnostics

The compiler reports *what* and *where* — a code, a file, a line, a column — as structured
data. A separate program, `lomenterr`, renders it:

```
error[E2]: 符号未声明
  --> z.lomt:4:12
  |
4 |     return z;
  |            ^
  | 消息: 使用未声明的变量 z
  | 怎么改:
  |   1. 补类型标注：`let x: u32 = 1;`
```

It is an independent command, like `lompi` and unlike a `loment` subcommand, so `loment
help` does not list it. The split is deliberate: the reporter adds the title, what went
wrong, why it is wrong and how to fix it, which means the self-hosted compiler carries no
message table at all. The two implementations stay comparable on the part that matters —
the code and the shape — instead of on wording.

The prose is Chinese today, like most of this repository's writing. The English-facing
listing is `loment codes`, one ASCII line per code. Colour is on by default; `-C` or
`--no-color` turns it off, and `lomenterr diag.jsonl` renders a diagnostics file directly.

Not yet: the same source rendered at **run time**. Rendering a trap — a division by zero, a
capability guard going out of range — from a copy of the reporter linked into your program
is designed but not implemented; `docs/182-lomenterr-and-choose-switches.md` §10.4 records
what it is waiting on.

## Documentation

| Document | Contents |
|---|---|
| `.claude/skills/loment/SKILL.md` | Language guide: syntax, built-in functions, error codes, commands, and the deviations from Rust. Self-contained, and shipped inside the package (`loment skill --print`). The fastest way in for both people and coding agents. |
| `loment/examples/tour.lomt` | The whole language in one file, with commentary. It compiles and runs; print it with `loment example tour`. |
| `docs/manual/` | Generated manual: the specifications, plus a page per example. |
| `docs/143-l1-loment-v0.md` | Language specification. |
| `docs/146-loment-capability-semantics.md` | Capability domains: semantics, what they guarantee, and what they do not cover. |
| `docs/158-loment-freeze.md` | What is frozen, what is not, and what changing each part costs. |
| `docs/173-loment-ffi.md` | Calling other languages' libraries: the four stages, and an honest ledger of what runs today. |
| `docs/179-multisyntax-frontends.md` | Reading other languages' syntax into L1. |
| `docs/188-grammar-declaration.md` | Surface grammars: the six syntaxes, what only the spelling changes, and `choose write grammar`. |
| `docs/` | Design and measurement records, numbered by document. |
| [FujoJTOP/lompi](https://github.com/FujoJTOP/lompi) | The package manager: a library is a directory, its identity is a content hash, and its dependencies are the `use` lines in the source. |

The command line has 38 commands. `loment help` lists them, `loment commands` prints the
bare names, and `loment cheat` is a one-page summary. Most design documents under `docs/`
are written in Chinese.

## Repository layout

| Path | Contents |
|---|---|
| `loment/selfhost/` | The compiler. It is written in Loment. |
| `loment/tools/` | Command-line front end, formatter, documentation generator, language server, linker. |
| `loment/lib/` | Core library modules: `mem`, `num`, `json`, `sha256`, `proc` (the last one is the process bridge). |
| `loment/examples/` | 28 example programs. |
| `loment/bootstrap.sh` | From-source build driver: seed, `stage1`, `stage2`, `stage3`. |
| `lom/` | Interface layer: one declaration source that generates constants and decoders for other languages. |
| `lompi/` | The package manager, written in Loment. Published separately as [FujoJTOP/lompi](https://github.com/FujoJTOP/lompi). |
| `editors/` | Editor support: syntax highlighting, completion and navigation for VS Code and Vim. |
| `tools/` | Build, packaging and verification tools, including the multi-syntax front ends. |
| `docs/` | Design and measurement records. |

## Getting help

Ask in an [issue](https://github.com/FujoJTOP/loment/issues) — questions are as welcome
as bug reports. There is no chat channel or forum yet.

## Contributing

Loment is developed in this repository, so ordinary development commits land here. Bug
reports, questions and patches are welcome as issues and pull requests; the working
conventions for this repository are in `AGENTS.md`.

## License

MIT. See [LICENSE](LICENSE).
