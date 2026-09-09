// 由 tools/potato_assert.py 从 loment/build/*.potato.json 生成 —— 请勿手改。
// 断言绑定: A1 域封闭 / A2 审计站点 / A3 模型缺席 / A4 可撤销 (docs/147 §4)。

#[derive(Clone, Copy)]
pub struct CapAssert {
    pub unit: &'static str,
    pub name: &'static str,
    pub space: &'static str,
    pub lo: u64,
    pub hi: u64,
    pub revocable: bool,
    pub guards: u64,
    pub a1: bool,
    pub a2: bool,
    pub a3: bool,
    pub a4: bool,
}

pub static CAP_ASSERTS: &[CapAssert] = &[  // 2 条
    CapAssert { unit: "demo", name: "blk_write", space: "disk", lo: 0, hi: 4, revocable: true, guards: 0, a1: true, a2: true, a3: true, a4: true },
    CapAssert { unit: "native_cap", name: "blk_write", space: "disk", lo: 0, hi: 4, revocable: true, guards: 2, a1: true, a2: true, a3: true, a4: true },
];

/// 内核启动自检: 返回 (检查数, 失败数); 断言 failed == 0 (P7 接入)。
pub fn assert_a1_a4() -> (u64, u64) {
    let mut checked = 0u64;
    let mut failed = 0u64;
    for c in CAP_ASSERTS {
        checked += 1;
        if !(c.a1 && c.a2 && c.a3 && c.a4) {
            failed += 1;
        }
    }
    (checked, failed)
}
