// loment/build/native_mut_rust_main.rs — M4 的 Rust 路径对照 (docs/145)
// 编译: rustc -O -o native_mut_rust_exe.exe native_mut_rust_main.rs
// 期望: 27 10

include!("native_mut.rs");

fn main() {
    println!("{}", call_fill());
    println!("{}", call_read_only());
}
