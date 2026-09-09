// loment/build/native_rust_main.rs — M34 差分对照: Rust 转译路径 (docs/145)
//
// 与 native_driver.c 打印同一组值, 用于与 LLVM IR 路径逐值比对。
// 编译: rustc -O -o native_rust_exe.exe native_rust_main.rs
// 期望: 55 21 8 45 205 1 21

include!("native.rs");

fn main() {
    println!("{}", fib(10));
    println!("{}", gcd(1071, 462));
    println!("{}", popcount(0xF0F0));
    println!("{}", sum_range(10));
    println!("{}", mask_low(0xABCD, 8));
    println!("{}", if in_domain(4) { 1 } else { 0 });
    println!("{}", scaled(7));
}
