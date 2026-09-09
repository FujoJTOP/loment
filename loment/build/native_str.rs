// 由 tools/lomentc.py 从 .lomt 转译 —— 请勿手改。
// module native_str (loment v0 -> rust)

// ==== 本模块 native_str ====
pub fn hello_len() -> u32 {
    return ("hello".len() as u32);
}

pub fn same_lit() -> bool {
    return ("abc" == "abc");
}

pub fn same_var() -> bool {
    let mut a: &'static str = "abc";
    let mut b: &'static str = "abd";
    return (a == b);
}

pub fn eq_var() -> bool {
    let mut a: &'static str = "fujo";
    let mut b: &'static str = "fujo";
    return (a == b);
}

pub fn diff_len() -> bool {
    let mut a: &'static str = "abc";
    let mut b: &'static str = "abcd";
    return (a == b);
}

pub fn first_byte() -> u32 {
    return ("Z".as_bytes()[(0) as usize] as u32);
}

pub fn third_byte() -> u32 {
    let mut s: &'static str = "abc";
    return (s.as_bytes()[(2) as usize] as u32);
}
