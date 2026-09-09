// loment/build/native_gen_rust_main.rs — M6/M7 的 Rust 路径对照 (docs/145)
// 编译: rustc -O -o native_gen_rust_exe.exe native_gen_rust_main.rs
// 期望: 7 3 44

include!("native_gen.rs");

fn main() {
    println!("{}", call_pair_max());
    println!("{}", call_max_i32());
    println!("{}", call_opt());
}
