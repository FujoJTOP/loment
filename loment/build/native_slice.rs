// 由 tools/lomentc.py 从 .lomt 转译 —— 请勿手改。
// module native_slice (loment v0 -> rust)

// ==== 本模块 native_slice ====
pub fn sum(xs: &[u32]) -> u32 {
    let mut s: u32 = 0;
    for i in 0..(xs.len() as u32) {
        s = (s + xs[(i) as usize]);
    }
    return s;
}

pub fn call_sum() -> u32 {
    let mut a: [u32; 4] = [1, 2, 3, 4];
    return sum((&a));
}

pub fn first_of(xs: &[u32]) -> u32 {
    return xs[(0) as usize];
}

pub fn call_first() -> u32 {
    let mut a: [u32; 3] = [7, 8, 9];
    return first_of((&a));
}
