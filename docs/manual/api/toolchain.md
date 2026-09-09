# API: `toolchain`

> 源: `D:\Dev\FujoOS\loment\examples\toolchain.lomt` · 由 tools/lomdoc.py 生成

## 枚举

### `enum Option`



- `Option::Some` (T)
- `Option::None`

### `enum Result`



- `Result::Ok` (T)
- `Result::Err` (E)

## 函数

### `fn fib(n: u32) -> u32`

斐波那契 (迭代)。

### `fn gcd(a: u32, b: u32) -> u32`

最大公约数。

### `fn popcount(x: u32) -> u32`

位计数。

### `fn test_fib() -> bool`



### `fn test_gcd() -> bool`



### `fn test_popcount() -> bool`



### `fn test_fail_case() -> bool`



### `fn bench_fib() -> u32`



### `fn bench_popcount() -> u32`



### `fn cov_main() -> u32`
