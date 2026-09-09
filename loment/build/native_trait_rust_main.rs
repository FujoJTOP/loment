// loment/build/native_trait_rust_main.rs — M8 的 Rust 路径对照 (docs/145)
// 期望: 7 70

include!("native_trait.rs");

fn main() {
    println!("{}", call_small());
    println!("{}", call_big());
}
