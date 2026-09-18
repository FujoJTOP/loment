# Quick start

Loment is a systems programming language: a strict-subset-of-Rust syntax, native x86-64
executables (Linux ELF, Windows PE), no runtime and no libc. The
[README](README.md) says what it is; this page is only about getting a program to run.

**Nothing is published yet** — 0.1.4 is not finished, and there are no packages in
[Releases](https://github.com/FujoJTOP/loment/releases) — so this starts from a checkout.

```
$ git clone https://github.com/FujoJTOP/loment.git
$ cd loment
```

## 1. Write a program

`hello.lomt`:

```rust
module hello

fn _start() {
    let s: str = "hello from Loment\n";
    syscall4(1, 1, str_ptr(s) as u64, str_len(s) as u64);
    syscall4(60, 0, 0, 0);
}
```

`_start` is the entry point, because there is no runtime to call one for you. There is no
`printf` either: output goes through the `write` system call, whose arguments are the file
descriptor, the string's pointer and its length. The second call is `exit`.

## 2. Compile and run it

### With the repository's tools

The tools in `tools/` are Python, so this path needs Python 3 and nothing else:

```
$ python3 tools/loment.py ir hello.lomt > hello.ll     # source -> LLVM IR
$ python3 tools/lomelf.py hello.ll -o hello            # IR -> executable
$ ./hello
hello from Loment
```

On Windows the target is PE, so name the output `.exe` — the linker picks the format from
that name:

```
> python tools\lomelf.py hello.ll --target pe -o hello.exe
> hello.exe
hello from Loment
```

### Or build the toolchain itself

The compiler is written in Loment and rebuilds from a seed committed to this repository.
POSIX `sh` on x86-64 Linux — natively or under WSL — is the only requirement: no Python,
no interpreter and no C compiler.

```
$ sh loment/bootstrap.sh
...
SEED BOOTSTRAP OK: ...
```

It performs four checks: the seed builds a `stage1`, that `stage1` reproduces the seed byte
for byte, `stage2 -> stage3` reaches a fixed point, and `stage1` agrees with `stage2` on a
foreign entry file. Hand it an entry file and it prints that file's IR instead of running
the checks:

```
$ sh loment/bootstrap.sh hello.lomt > hello.ll
```

To get the packaged toolchain (the `loment` command, `lompi`, the language server), build
the distribution and install it. Python is needed for this one step and for nothing after
it — the package contains no Python:

```
$ python tools/loment_dist.py --emit
$ cd loment/dist && tar xzf loment-*-linux-x64.tar.gz
$ cd loment-*-linux-x64 && sh install.sh          # Windows: powershell -File install.ps1
```

Details, prefixes and checksums: `docs/162-loment-distribution.md`.

## 3. With `loment` on your PATH

```
$ loment run hello.lomt          # compile, link, run
$ loment build hello.lomt -o hello
$ loment check hello.lomt        # check only, nothing written
$ loment ir hello.lomt           # the LLVM IR it generates
```

## 4. Look around the language

```
$ loment example tour      # the whole language in one file — const, capability, struct,
                           # enum, match, generics, slices, arrays, syscalls
$ loment cheat             # the traps, ordered by how easily they bite
$ loment builtins          # every built-in function there is
$ loment syntax            # syntax summary
$ loment help              # all 38 commands
```

The full guide is `.claude/skills/loment/SKILL.md`, and `loment skill --print` prints it.
It is written to be read start to finish before you write much code, and it is the same
document that ships inside the package.

## 5. Call a function written in C

Declare it with `extern fn` — a signature and a semicolon, no body — and the call site uses
the platform C ABI.

`add.c`:

```c
int c_add(int a, int b) {
    return a + b;
}
```

`app.lomt`:

```rust
module app

extern fn c_add(a: i32, b: i32) -> i32;

fn _start() {
    let n: i32 = c_add(20, 22);
    syscall4(60, n as u64, 0, 0);     // exit with the result
}
```

```
$ cc -c add.c -o add.o
$ python3 tools/loment.py ir app.lomt > app.ll
$ python3 tools/lomelf.py app.ll --link add.o -o app
$ ./app; echo $?
42
```

With an installed toolchain the middle two steps are one command:

```
$ loment build app.lomt --link add.o -o app
```

The object has to be self-contained: no undefined symbols, so no libc. C++ (`extern "C"`),
Rust (`#[no_mangle] pub extern "C"`) and Zig (`export fn`) work the same way.

**This leg is Linux/ELF only today.** A Windows build with a foreign object is refused
rather than producing something that does not link. Languages whose runtime is not a C
library — Python, JavaScript, Java, Perl, Lua, Go — are reached through a subprocess
instead (`loment/lib/proc.lomt`); that is Linux/ELF only as well. What is *not* supported:
dynamic libraries and embedding a runtime. The honest ledger is `docs/173-loment-ffi.md`.

## 6. Turn a C file into a Loment interface

You do not have to write those `extern fn` declarations by hand. Point the front ends at a
source file; the language is taken from the extension (`.c`/`.h`, `.rs`, `.go`, `.java`,
`.py`) or, for a `.lomt` file holding foreign syntax, from the content.

```
$ python3 tools/potato_from.py add.c --json add.potato.json
$ python3 tools/lomt_from.py add.potato.json --out add.lomt
```

`add.lomt` now holds the interface, and you can `use` it:

```rust
module add

// ---- 外部函数 (docs/173: 声明在此, 实现在源语言那一侧, C ABI)
pub extern fn c_add(a: i32, b: i32) -> i32;
```

(The generator writes its comments in Chinese, like most of this repository's prose. The
declarations are the part that matters.)

Three things are worth knowing before you rely on it:

- **Declarations only, no function bodies.** The representation layer records what exists,
  not what it computes, so the implementation stays in the source language and is linked in
  as an object. Carrying bodies would be a structural change, not an extra field.
- **When it cannot tell the language, it asks** (`--lang`) instead of guessing, because a
  wrong guess produces a wrong interface rather than an error.
- **Whatever it cannot represent is reported**, with a name and a reason — overloads,
  generics, managed runtimes. Nothing is dropped silently.

Details and the five per-syntax criteria: `docs/179-multisyntax-frontends.md`.

## 7. When it does not compile

```
$ loment check hello.lomt      # diagnostics on stderr, nothing written
$ loment explain E4            # one error code, in detail
$ loment codes                 # the whole table
```

Two traps are worth knowing before your first real program, because neither is a compile
error: a non-void function that falls off its end crashes at run time (the last statement
must be a `return`), and every `match` arm body must be a block — `E::A => { return 0; }`,
not `E::A => 0,`. `loment cheat` lists these first, in the order they bite.

## Where to go next

| | |
|---|---|
| [README](README.md) | What the language is, and the repository layout. |
| `.claude/skills/loment/SKILL.md` | The language guide. Read this one. |
| `docs/manual/` | The specifications, plus a page per example. |
| `loment/examples/` | 28 example programs; `loment example NAME` prints one. |
| `docs/158-loment-freeze.md` | What is frozen, and what changing it costs. |
| [FujoJTOP/lompi](https://github.com/FujoJTOP/lompi) | The package manager. |
