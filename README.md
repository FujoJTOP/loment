<p align="center">
  <img src="editors/loment-banner.png" width="720" alt="Loment — Programming Language, Program by Fujo">
</p>

<p align="center">
  <a href="https://github.com/FujoJTOP/loment/actions/workflows/gate.yml"><img src="https://github.com/FujoJTOP/loment/actions/workflows/gate.yml/badge.svg" alt="gate"></a>
  <a href="https://github.com/FujoJTOP/loment/releases/latest"><img src="https://img.shields.io/github/v/release/FujoJTOP/loment?label=release&amp;color=4c6ef5" alt="latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Loment--Open-4c6ef5" alt="license: Loment-Open"></a>
  <a href="https://discord.gg/rGw7NRNU"><img src="https://img.shields.io/badge/Discord-join%20chat-5865F2?logo=discord&amp;logoColor=white" alt="Discord"></a>
</p>

<p align="center">
  <b>A systems programming language.</b><br>
  Native x86-64 — Linux ELF, Windows PE, or bare metal.<br>
  <b>Sealed</b> by default. <b>Hosted</b> when you say so.
</p>

<p align="center">
  <a href="https://github.com/FujoJTOP/loment/releases/tag/v0.1.4.5-quickfix"><b>Download 0.1.4.5 QuickFix</b></a> ·
  <a href="https://fujojtop.github.io/loment-site/">Site</a> ·
  <a href="https://loment.fandom.com/">Wiki</a> ·
  <a href="https://mastodon.social/@Loment">Mastodon</a> ·
  <a href="https://github.com/FujoJTOP/loment/issues">Issues</a> ·
  <a href="https://discord.gg/rGw7NRNU">Discord</a>
</p>

Loment compiles to native x86-64 executables: Linux ELF, Windows PE, or a freestanding object
for bare metal. `choose sealed`, the default, carries no runtime and no libc; `choose hosted`
would reach real libraries, and a release build refuses it. The toolchain is written in Loment,
and installing needs no Python, no clang and no WSL. Write in its Rust-flavored syntax or in C,
C++, Java, C#, Go, Python.

## A first program

```rust
module hello

fn _start() {
    let s: str = "hello from Loment\n";
    syscall4(1, 1, str_ptr(s) as u64, str_len(s) as u64);
    syscall4(60, 0, 0, 0);
}
```

```console
$ loment run hello.lomt
hello from Loment
```

`_start` is the entry point; a sealed artifact has no runtime. Output goes through `write`;
there is no `printf`.

![How a program is built: hello.lomt goes to loment-driver, which checks it and emits LLVM IR; loment-lomelf, the repository's own linker, turns that into an 8 KB static executable](editors/loment-pipeline.png)

## What it is for

Loment asks: who can run what, in what scope, and can that be settled without reading the
source?

| | |
|---|---|
| **Capability domains** | `capability blk : disk[0..4]` declares the range a unit may reach. `guard blk(i);` checks an index against it at compile time for a literal, otherwise at run time, trapping out of range. |
| **Potato form objects** | Every unit exports a machine-readable summary of what it holds and reaches, judged by two independent validators. |
| **A compat layer** | C, C++, Rust and Zig behind the C ABI; Go, Python, Java, JavaScript, Perl and Lua over a process bridge. |

Diagnostics come from a separate program: `lomenterr` supplies what the compiler does not.

![loment check rendering an E002 undeclared-name diagnostic: the code, the source line with a caret, then message, what went wrong, why, four numbered fixes, and what is and is not supported](editors/loment-diagnostic.png)

## Status

`0.1.4.5 QuickFix` ships as a package for Linux and Windows. The toolchain is self-hosted at run
time; the development side is not yet.

![The bootstrap: the committed seed is cooked into a stage1 by the genesis assembler; stage1 must reproduce the seed byte for byte; stage2 to stage3 reaches a fixed point; the two stages agree on a foreign entry file](editors/loment-bootstrap.png)

## How this is built

AI coding agents work on Loment alongside people, and the repository says so: each change is
checked by a program.

## License

**Loment-Open License**: MIT plus two conditions — derived source must credit Loment, and the
name is reserved. See [LICENSE](LICENSE).
