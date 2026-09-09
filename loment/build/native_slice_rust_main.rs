// loment/build/native_slice_rust_main.rs — M3 的 Rust 路径对照 (docs/145)
// 编译: rustc -O -o native_slice_rust_exe.exe native_slice_rust_main.rs
// 期望: 10 7

include!("native_slice.rs");

fn main() {
    println!("{}", call_sum());
    println!("{}", call_first());
}
