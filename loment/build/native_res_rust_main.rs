// loment/build/native_res_rust_main.rs — M9/M10 的 Rust 路径对照 (docs/145)
// 期望: 6 99

include!("native_res.rs");

fn main() {
    println!("{}", call_ok());
    println!("{}", call_err());
}
