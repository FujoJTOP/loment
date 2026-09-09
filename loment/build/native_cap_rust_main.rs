// loment/build/native_cap_rust_main.rs — P4 的 Rust 路径对照 (docs/145)
// 期望: 3 2

include!("native_cap.rs");

fn main() {
    println!("{}", ok());
    println!("{}", literal_ok());
}
