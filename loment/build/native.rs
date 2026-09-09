// 由 tools/lomentc.py 从 .lomt 转译 —— 请勿手改。
// module native (loment v0 -> rust)

// ==== 本模块 native ====
pub const SCALE: u32 = 3;

pub fn fib(n: u32) -> u32 {
    let mut a: u32 = 0;
    let mut b: u32 = 1;
    let mut i: u32 = 0;
    while (i < n) {
        let mut t: u32 = (a + b);
        a = b;
        b = t;
        i = (i + 1);
    }
    return a;
}

pub fn gcd(a: u32, b: u32) -> u32 {
    let mut x: u32 = a;
    let mut y: u32 = b;
    while (y != 0) {
        let mut t: u32 = (x % y);
        x = y;
        y = t;
    }
    return x;
}

pub fn popcount(x: u32) -> u32 {
    let mut v: u32 = x;
    let mut c: u32 = 0;
    while (v != 0) {
        if ((v % 2) == 1) {
            c = (c + 1);
        }
        v = (v / 2);
    }
    return c;
}

pub fn sum_range(n: u32) -> u32 {
    let mut s: u32 = 0;
    for i in 0..n {
        s = (s + i);
    }
    return s;
}

pub fn mask_low(x: u32, n: u32) -> u32 {
    return (x & ((1 << n) - 1));
}

pub fn in_domain(off: u32) -> bool {
    return ((off >= 0) && (off <= 4));
}

pub fn scaled(x: u32) -> u32 {
    return (x * SCALE);
}
