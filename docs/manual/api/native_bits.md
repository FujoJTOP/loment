# API: `native_bits`

> 源: `loment/examples/native_bits.lomt` · 由 tools/lomdoc.py 生成

## 枚举

### `enum Option`



- `Option::Some` (T)
- `Option::None`

### `enum Result`



- `Result::Ok` (T)
- `Result::Err` (E)

## 函数

### `fn pack(v: u8) -> u8`



### `fn unpack(b: u8) -> u32`



### `fn roundtrip(v: u8) -> u32`



### `fn top_bits(b: u8) -> u32`
