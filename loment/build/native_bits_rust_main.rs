// loment/build/native_bits_rust_main.rs — M22 的 Rust 路径对照 (docs/145)
// 期望: 13 5 1

include!("native_bits.rs");

fn main() {
    println!("{}", pack(5));
    println!("{}", roundtrip(5));
    println!("{}", top_bits(13));
}
