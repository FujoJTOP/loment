![Loment — Programming Language, Program by Fujo](editors/loment-banner.png)

# Loment

Loment is a systems programming language for writing software that runs without a
runtime. Its syntax is a strict subset of Rust, extended with **capability domains** —
a first-class way to state which part of a program may touch which resource.

The compiler emits native x86-64 executables (Linux ELF and Windows PE) directly.
Programs link against no runtime and no libc. The toolchain is itself written in Loment
and rebuilds from a seed committed to this repository, so compiling a program requires
neither Python nor a C compiler.

Loment is the systems language of the FujoOS project.

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

## Why Loment?

- **Nothing sits between the program and the machine.** No runtime, no libc, no garbage
  collector. The entry point is `_start`, the interface to the operating system is a
  system call, and the built-in surface is a fixed table you can read in one sitting —
  there is no `printf`, no `String`, no `Vec`.
- **Capability domains are part of the language, not a library.** A domain names a
  resource space and the range of indices a program may use in it; a `guard` enforces
  that range, and the compiler rejects indices it can already see to be outside it.
- **Self-hosted, down to the assembler.** The compiler is written in Loment and the
  build chain begins at an assembler committed to this repository. The bootstrap needs
  no Python, no interpreter and no C compiler, and it checks that the compiler
  reproduces itself byte for byte and that later stages reach a fixed point.
- **The deviations from Rust are written down.** Rust's syntax and type system carry you
  most of the way; where Loment differs, the differences are enumerated as a short list
  with minimal reproductions, so you do not have to find them by failing to compile.

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

## Capability domains

This is the one thing Loment adds to Rust. A capability domain declares a resource space,
the range of indices a program may use inside it, and whether it can be revoked:

```rust
module blk

capability blk_write : disk[0..4] revocable

fn write_slot(slot: u32) -> u32 {
    guard blk_write(slot);
    return slot;
}
```

`guard blk_write(slot);` evaluates the index: outside `[0..4]` the program traps, with no
other side effect; inside, one audit entry is counted. An index the compiler can see to be
out of range — `guard blk_write(7)` — is rejected at compile time as `E4` instead.

**The boundary is stated, not glossed** (`docs/146-loment-capability-semantics.md`): a
guard constrains the *index*, not the *subject*. It does not ask whether the caller is
entitled to the capability; binding a subject to a capability is the kernel's job.
`revocable` is, on the language side, a declaration and a flag in the domain table.

## Documentation

| Document | Contents |
|---|---|
| `.claude/skills/loment/SKILL.md` | Language guide: syntax, built-in functions, error codes, commands, and the deviations from Rust. Self-contained, and shipped inside the package (`loment skill --print`). The fastest way in for both people and coding agents. |
| `loment/examples/tour.lomt` | The whole language in one file, with commentary. It compiles and runs; print it with `loment example tour`. |
| `docs/manual/` | Generated manual: the specifications, plus a page per example. |
| `docs/143-l1-loment-v0.md` | Language specification. |
| `docs/146-loment-capability-semantics.md` | Capability domains: semantics, what they guarantee, and what they do not cover. |
| `docs/158-loment-freeze.md` | What is frozen, what is not, and what changing each part costs. |
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
| `loment/lib/` | Core library modules: `mem`, `num`, `json`, `sha256`, `proc`. |
| `loment/examples/` | 28 example programs. |
| `loment/bootstrap.sh` | From-source build driver: seed, `stage1`, `stage2`, `stage3`. |
| `lom/` | Interface layer: one declaration source that generates constants and decoders for other languages. |
| `lompi/` | The package manager, written in Loment. Published separately as [FujoJTOP/lompi](https://github.com/FujoJTOP/lompi). |
| `editors/` | Editor support: syntax highlighting, completion and navigation for VS Code and Vim. |
| `tools/` | Build, packaging and verification tools. |
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
