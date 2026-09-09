# API: `mathutil`

> 源: `D:\Dev\FujoOS\loment\examples\mathutil.lomt` · 由 tools/lomdoc.py 生成

## 常量

| 常量 | 类型 | 值 | 说明 |
|---|---|---|---|
| `TWO` | `u32` | 2 |  |

## 类型

### `struct Pair`



| 字段 | 类型 |
|---|---|
| `a` | `u32` |
| `b` | `u32` |

## 枚举

### `enum Option`



- `Option::Some` (T)
- `Option::None`

### `enum Result`



- `Result::Ok` (T)
- `Result::Err` (E)

## 函数

### `fn double(x: u32) -> u32`



### `fn pair_sum(p: Pair) -> u32`
