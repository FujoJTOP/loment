// loment/build/native_str_rust_main.rs — M1/M2 的 Rust 路径对照 (docs/145)
// 编译: rustc -O -o native_str_rust_exe.exe native_str_rust_main.rs
// 期望: 5 1 0 1 0 90 99

include!("native_str.rs");

fn main() {
    println!("{}", hello_len());
    println!("{}", if same_lit() { 1 } else { 0 });
    println!("{}", if same_var() { 1 } else { 0 });
    println!("{}", if eq_var() { 1 } else { 0 });
    println!("{}", if diff_len() { 1 } else { 0 });
    println!("{}", first_byte());
    println!("{}", third_byte());
}
