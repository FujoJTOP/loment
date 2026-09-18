![Loment — Programming Language, Program by Fujo](editors/loment-banner.png)

# Loment

Loment is a **systems programming language**: it compiles to native x86-64 executables —
Linux ELF and Windows PE, or a freestanding object for bare metal — with no runtime and no
libc. Its syntax is a strict subset of Rust, and the toolchain is itself written in Loment.

Two things were added in the latest update, and they are why this repository is worth a
look now:

- **It calls libraries written in other languages** — ten of them, end to end, from C and
  Rust to Python and Java.
- **It reads source written in other languages' syntax** — C, Rust, Go, Java, Python —
  and turns a library's source into a Loment interface unit.

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

## Reading other languages' source

Any source syntax → a Potato form object → a L1 unit (`.lomt`) → the frozen core:

```
$ python tools/potato_from.py lib.rs --json lib.potato.json
$ python tools/lomt_from.py  lib.potato.json --out lib.lomt

$ python tools/lomt_from.py  lib.rs --lang rust --out lib.lomt    # both steps at once
```

The language is decided by extension first (`.c`/`.h`, `.rs`, `.go`, `.java`, `.py`) and,
for a `.lomt` file holding foreign syntax, by its content. When it cannot tell, it asks for
`--lang` instead of guessing — a wrong guess produces a wrong interface rather than an
error.

**What you get** are declarations: types, constants, capabilities and function signatures.
Every function the front end can represent becomes `pub extern fn`, so the unit can be
`use`d and linked against an object built from the same library. Five syntaxes, ten
criteria each (`loment_multisyntax_test`).

**What you do not get** are function bodies. The representation layer records what exists,
not what it computes; carrying implementations would be a structural extension of it, not
an extra field. Whatever cannot be represented — overloads, generics, managed runtimes —
is reported with a name and a reason, and nothing is dropped silently.

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
