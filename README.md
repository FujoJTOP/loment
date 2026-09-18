![Loment — Programming Language, Program by Fujo](editors/loment-banner.png)

# Loment

Loment is a **systems programming language**: it compiles to native x86-64 executables —
Linux ELF and Windows PE, or a freestanding object for bare metal — with no runtime and no
libc, and the toolchain is itself written in Loment. Its own syntax is Rust-flavored, and the
same program can be written in six more: C, C++, Java, C#, Go or Python.

**[Quick start](QUICKSTART.md)** ·
[Project site](https://fujojtop.github.io/FujoOSwebsite/loment/) ·
[Manual](docs/manual/index.md) ·
[Language guide](.claude/skills/loment/SKILL.md) ·
[Examples](loment/examples/) ·
[Issues](https://github.com/FujoJTOP/loment/issues)

## Status

`0.1.4-pre2`, a pre-release; prebuilt toolchains are not published yet. The language surface
is frozen — `docs/158-loment-freeze.md` says what changing it costs — but the implementation
is not.

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

`_start` is the entry point, because there is no runtime to call one for you, and output goes
through the `write` system call, because there is no `printf`. [QUICKSTART.md](QUICKSTART.md)
takes it from here — sources, toolchain, and the first four rows below.

## What it does that others do not

| | |
|---|---|
| **Calls libraries written in other languages** | Ten of them, end to end: C, C++, Rust and Zig by static linking behind the C ABI, then Go, Python, Java, JavaScript, Perl and Lua over a process bridge. [docs/173](docs/173-loment-ffi.md) is the honest ledger — dynamic libraries and embedding a runtime are not started. |
| **Can be written in six other syntaxes** | C, C++, Java, C#, Go, Python. Only the spelling changes; the semantics are always Loment's. Declaring it in the file (`choose write grammar`) is not wired into the compiler yet — the translators in `tools/` are the path. [docs/188](docs/188-grammar-declaration.md) |
| **Everything is customizable** | The source suffix, the command surface (`loment-<name>` on `PATH`; official commands always win), the libraries (a directory whose identity is a content hash), and the toolchain itself. No registry — the extension points are files on disk. |
| **Reports errors out of a separate program** | The compiler emits structured diagnostics; `lomenterr` adds the title, the location and how to fix it, so the compiler carries no message table of its own. [docs/182](docs/182-lomenterr-and-choose-switches.md) |
| **Capability domains** | The one thing the language adds to Rust: `capability blk : disk[0..4]` and `guard blk(i);`. A guard bounds the *index*, not the *subject* — [docs/146](docs/146-loment-capability-semantics.md) states that boundary rather than glossing it. |

## Documentation

| | |
|---|---|
| `.claude/skills/loment/SKILL.md` | The language guide: syntax, built-ins, error codes, and the deviations from Rust. Self-contained, shipped in the package (`loment skill --print`), and the fastest way in. |
| `loment/examples/tour.lomt` | The whole language in one file, with commentary (`loment example tour`). |
| `docs/manual/` | The generated manual: specifications, plus a page per example. |
| `docs/143-l1-loment-v0.md` · `docs/146` · `docs/158` | The specification, capability semantics, and what is frozen. |
| `docs/173` · `docs/188` | The two capabilities in the table above. |
| `docs/` | Design and measurement records, numbered by document. Most are written in Chinese. |
| [FujoJTOP/lompi](https://github.com/FujoJTOP/lompi) | The package manager. |

## Repository layout

| | |
|---|---|
| `loment/selfhost/` | The compiler. It is written in Loment. |
| `loment/tools/` | CLI, formatter, documentation generator, language server, linker, error reporter. |
| `loment/lib/` | Core library modules: `mem`, `num`, `json`, `sha256`, `proc`. |
| `loment/examples/` | Example programs. |
| `lom/` | Interface layer: one declaration source that generates constants and decoders for other languages. |
| `lompi/` | The package manager, written in Loment. |
| `editors/` | Editor support: VS Code and Vim. |
| `tools/` · `docs/` | Build and verification tools; design records. |

## Getting help and contributing

Ask in an [issue](https://github.com/FujoJTOP/loment/issues) — questions are as welcome as
bug reports. Loment is developed in this repository, and the working conventions are in
`AGENTS.md`.

## License

MIT. See [LICENSE](LICENSE).
