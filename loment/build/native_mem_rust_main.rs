// loment/build/native_mem_rust_main.rs — M15/M18 的 Rust 路径对照 (docs/145)
// 期望: 42 0 0 5

include!("native_mem.rs");

fn main() {
    println!("{}", heap_roundtrip());
    println!("{}", wrap_add(4294967295u32, 1u32));
    println!("{}", safe_div(10, 0));
    println!("{}", safe_div(10, 2));
    println!("{}", atomic_roundtrip());
}
