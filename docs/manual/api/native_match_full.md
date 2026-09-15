# API: `native_match_full`

> 源: `loment/examples/native_match_full.lomt` · 由 lomdoc 生成

## 枚举

### `enum Kind`



- `Kind::Red`
- `Kind::Sized` (u32)

### `enum Option`



- `Option::Some` (T)
- `Option::None`

### `enum Result`



- `Result::Ok` (T)
- `Result::Err` (E)

## 函数

### `fn write_str(fd: u64, s: str) -> i64`



### `fn write_dec(fd: u64, v: u32) -> ()`



### `fn full(k: Kind) -> u32`

全覆盖、**不写** `_` —— 默认目标必须是 `mend`。

### `fn with_wild(k: Kind) -> u32`

带 `_` —— 默认目标必须是 `mwild`。两种形态都要留在语料里。

### `fn _start() -> ()`
