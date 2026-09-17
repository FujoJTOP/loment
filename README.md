# Loment

Loment is a systems programming language for writing software that runs without a
runtime. Its syntax is a strict subset of Rust, extended with **capability domains** —
a first-class way to state which part of a program may touch which resource.

The compiler emits native x86-64 executables (Linux ELF and Windows PE) directly.
Programs link against no runtime and no libc; building a program requires neither
Python nor a C compiler.

Project site: <https://fujojtop.github.io/FujoOSwebsite/loment/>

## Status

Current version: `0.1.4-pre2`.

The language surface is frozen — syntax, type rules, diagnostics, unit loading and the
capability semantics are documented in `docs/158-loment-freeze.md`, and any change to
them has to go through the process described there. The implementation is not frozen.

Prebuilt toolchains are not published yet; see [Getting the toolchain](#getting-the-toolchain).

## Getting the toolchain

### Prebuilt packages

Linux and Windows packages will be published in this repository's
[Releases](https://github.com/FujoJTOP/loment/releases). They are not available yet.

### Building from source

The compiler rebuilds itself from sources kept in this repository. No Python, no
interpreter and no C compiler are required — the repository ships an assembler that
serves as the starting point of the build chain:

```
$ sh loment/bootstrap.sh
...
SEED BOOTSTRAP OK
```

The script rebuilds the compiler from the committed seed and verifies that the rebuilt
result is byte-identical to the seed, then checks that the second and third stages reach
a fixed point. Given an entry file, it also prints the compiler output for that file:

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

The entry point is `_start`; there is no `printf`, and output goes through a system call.
The output is a single self-contained executable.

## Documentation

| Document | Contents |
|---|---|
| `.claude/skills/loment/SKILL.md` | Language guide: syntax, built-in functions, error codes, commands. Self-contained; the fastest way in for both people and coding agents. |
| `loment/examples/tour.lomt` | The whole language in one file, with commentary. |
| `docs/143-l1-loment-v0.md` | Language specification. |
| `docs/146-loment-capability-semantics.md` | Capability domains. |
| `docs/` | Design and measurement records, numbered by document. |

The command line has 38 commands; `loment help` lists them, `loment cheat` is a one-page
summary. Most design documents under `docs/` are written in Chinese.

## Repository layout

| Path | Contents |
|---|---|
| `loment/selfhost/` | The compiler. It is written in Loment. |
| `loment/tools/` | Command-line front end, formatter, documentation generator, language server, linker. |
| `loment/examples/` | Example programs. |
| `lom/` | Interface layer: one declaration source that generates constants and decoders for other languages. |
| `editors/` | Editor support: syntax highlighting, completion and navigation for VS Code and Vim. |
| `tools/` | Build, packaging and verification tools. |
| `docs/` | Design and measurement records. |

## Contributing

Development takes place in a private monorepo, and this repository is generated from a
subset of it — commits made here are overwritten by the next publication. Bug reports and
questions are welcome as issues; patches should be discussed there first.

## License

MIT. See [LICENSE](LICENSE).
