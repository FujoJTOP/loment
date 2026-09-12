# API: `native_brk`

> 源: `loment/examples/native_brk.lomt` · 由 lomdoc 生成

## 枚举

### `enum Option`



- `Option::Some` (T)
- `Option::None`

### `enum Result`



- `Result::Ok` (T)
- `Result::Err` (E)

## 函数

### `fn ptr_to_int(p: ptr) -> u64`

指针 -> 整数 (M67)。

### `fn int_to_ptr(v: u64) -> ptr`

整数 -> 指针 (M83)。往返一次必须回到原值。

### `fn roundtrip(p: ptr) -> bool`



### `fn first_byte(p: ptr, v: u8) -> u32`



### `fn scratch_probe() -> u32`
