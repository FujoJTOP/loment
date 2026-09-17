//! fujr.rs — M17 FUJR `.run` 容器装载器 v0 (内核侧)
//!
//! 解析 FUJR v0.1 (64B 头 + 32B×n 节表 + 4096 对齐 payload, FNV-1a 校验):
//!   - EMBED 段 → 可执行体 (交给格式嗅探 ELF/PE/Mach-O 装载器)
//!   - DATA 段 → 资源: 拷贝进内核静态, VFS `/runres/<name>` 可读
//!   - manifest 中的权限声明 (`"perms": [...]`) → 审计日志 (M91 护栏前哨)
//!
//! 约束 v0: ≤8 资源, 每资源 ≤16KiB; 名称 ≤15 字符。

use crate::serial;

pub const MAX_RES: usize = 8;
pub const RES_MAX: usize = 16384;

static mut RES_NAMES: [[u8; 16]; MAX_RES] = [[0; 16]; MAX_RES];
#[allow(static_mut_refs)]
static mut RES_DATA: [[u8; RES_MAX]; MAX_RES] = [[0; RES_MAX]; MAX_RES];
static mut RES_LEN: [usize; MAX_RES] = [0; MAX_RES];
static mut RES_COUNT: usize = 0;

fn le16(b: &[u8], off: usize) -> u16 {
    u16::from_le_bytes([b[off], b[off + 1]])
}
fn le32(b: &[u8], off: usize) -> u32 {
    u32::from_le_bytes([b[off], b[off + 1], b[off + 2], b[off + 3]])
}
fn le64(b: &[u8], off: usize) -> u64 {
    u64::from_le_bytes([
        b[off],
        b[off + 1],
        b[off + 2],
        b[off + 3],
        b[off + 4],
        b[off + 5],
        b[off + 6],
        b[off + 7],
    ])
}

fn fnv1a(data: *const u8, len: usize) -> u32 {
    let mut h: u32 = 0x811C_9DC5;
    unsafe {
        for i in 0..len {
            h ^= data.add(i).read() as u32;
            h = h.wrapping_mul(0x0100_0193);
        }
    }
    h
}

fn print_dec(v: u64) {
    let mut buf = [0u8; 24];
    let mut i = 24;
    let mut x = v;
    if x == 0 {
        serial::write_str("0");
        return;
    }
    while x > 0 {
        i -= 1;
        buf[i] = b'0' + (x % 10) as u8;
        x /= 10;
    }
    serial::write_str(core::str::from_utf8(&buf[i..]).unwrap());
}

fn print_hex(v: u64) {
    const HX: &[u8; 16] = b"0123456789abcdef";
    let mut buf = [0u8; 18];
    buf[0] = b'0';
    buf[1] = b'x';
    for i in 0..16 {
        let d = ((v >> (4 * (15 - i))) & 0xF) as u8;
        buf[2 + i] = HX[d as usize];
    }
    serial::write_str(core::str::from_utf8(&buf).unwrap());
}

/// 装载容器: 校验全部 section (FNV), 提取 EMBED, 拷贝 DATA 资源。
/// 返回 EMBED 段地址/长度 (位于模块区内)。
pub fn load(module: u64, mlen: u64) -> Option<(u64, u64)> {
    unsafe {
        if mlen < 64 {
            return None;
        }
        let b = module as *const u8;
        // 检查魔数
        let magic_ok = b.read() == b'F'
            && b.add(1).read() == b'U'
            && b.add(2).read() == b'J'
            && b.add(3).read() == b'R';
        if !magic_ok {
            return None;
        }
        let count = u32::from_le_bytes([
            b.add(8).read(),
            b.add(9).read(),
            b.add(10).read(),
            b.add(11).read(),
        ]) as usize;
        if mlen < 64 + (32 * count) as u64 {
            return None;
        }
        let mut exec: Option<(u64, u64)> = None;
        let mut manifest: (u64, u64) = (0, 0);
        let mut data_sections: [(u64, u64); MAX_RES] = [(0, 0); MAX_RES];
        let mut data_n = 0usize;

        for i in 0..count {
            let base = 64 + i * 32;
            let tag = u32::from_le_bytes([
                b.add(base).read(),
                b.add(base + 1).read(),
                b.add(base + 2).read(),
                b.add(base + 3).read(),
            ]);
            let off = u64::from_le_bytes([
                b.add(base + 8).read(),
                b.add(base + 9).read(),
                b.add(base + 10).read(),
                b.add(base + 11).read(),
                b.add(base + 12).read(),
                b.add(base + 13).read(),
                b.add(base + 14).read(),
                b.add(base + 15).read(),
            ]);
            let size = u64::from_le_bytes([
                b.add(base + 16).read(),
                b.add(base + 17).read(),
                b.add(base + 18).read(),
                b.add(base + 19).read(),
                b.add(base + 20).read(),
                b.add(base + 21).read(),
                b.add(base + 22).read(),
                b.add(base + 23).read(),
            ]);
            let hash = u32::from_le_bytes([
                b.add(base + 24).read(),
                b.add(base + 25).read(),
                b.add(base + 26).read(),
                b.add(base + 27).read(),
            ]);
            if off as u64 + size > mlen {
                serial::write_line("run  : section out of bounds");
                return None;
            }
            let h = fnv1a(b.add(off as usize), size as usize);
            if h != hash {
                serial::write_str("run  : section hash mismatch (");
                print_dec(i as u64);
                serial::write_line(")");
                return None;
            }
            match tag {
                4 => exec = Some((module + off, size)),        // EMBED
                1 => manifest = (module + off, size),          // MANIFEST
                5 if data_n < MAX_RES => {
                    data_sections[data_n] = (module + off, size);
                    data_n += 1;
                }
                _ => {}
            }
        }
        let exec = match exec {
            Some(e) => e,
            None => {
                serial::write_line("run  : no EMBED section");
                return None;
            }
        };
        serial::write_str("run  : FUJR container ok (sections=");
        print_dec(count as u64);
        serial::write_str(", exec=");
        print_hex(exec.0);
        serial::write_line(")");

        // --- manifest: 资源名/sec 映射 + 权限声明 (v0 行扫描) ---
        let mut res_map: [(u64, usize); MAX_RES] = [(0, 0); MAX_RES]; // (name数组未用: 按 DATA 顺序)
        if manifest.1 > 0 {
            let m = manifest.0 as *const u8;
            let mlen_u = manifest.1 as usize;
            // 权限: 找 "perms": ["...
            let mut i = 0usize;
            while i + 9 <= mlen_u {
                if m.add(i).read() == b'"'
                    && m.add(i + 1).read() == b'p'
                    && m.add(i + 2).read() == b'e'
                    && m.add(i + 3).read() == b'r'
                {
                    serial::write_str("run  : perm claim: ");
                    let mut j = i;
                    while j < mlen_u && m.add(j).read() != b']' {
                        if m.add(j).read() == b'"' {
                            let mut k = j + 1;
                            let mut buf = [0u8; 40];
                            let mut n = 0usize;
                            while k < mlen_u && m.add(k).read() != b'"' && n < 39 {
                                buf[n] = m.add(k).read();
                                n += 1;
                                k += 1;
                            }
                            serial::write_str(core::str::from_utf8(&buf[..n]).unwrap_or("?"));
                            serial::write_str(" ");
                            j = k;
                        }
                        j += 1;
                    }
                    serial::write_line("(audited)");
                    break;
                }
                i += 1;
            }
            // 资源名: 只在 "resources": [ ... ] 段内扫描 "name":"X" (应用名同名键跳过)
            let mut names: [[u8; 16]; MAX_RES] = [[0; 16]; MAX_RES];
            let mut name_i = 0usize;
            // 找到开始标记
            let mut start = 0usize;
            let mut i0 = 0usize;
            while i0 + 13 <= mlen_u {
                if m.add(i0).read() == b'"'
                    && m.add(i0 + 1).read() == b'r'
                    && m.add(i0 + 2).read() == b'e'
                    && m.add(i0 + 3).read() == b's'
                    && m.add(i0 + 4).read() == b'o'
                    && m.add(i0 + 5).read() == b'u'
                    && m.add(i0 + 6).read() == b'r'
                    && m.add(i0 + 7).read() == b'c'
                    && m.add(i0 + 8).read() == b'e'
                    && m.add(i0 + 9).read() == b's'
                    && m.add(i0 + 10).read() == b'"'
                {
                    start = i0;
                    break;
                }
                i0 += 1;
            }
            let mut i2 = start + 10;
            while i2 + 8 <= mlen_u && name_i < MAX_RES {
                if m.add(i2).read() == b'"'
                    && m.add(i2 + 1).read() == b'n'
                    && m.add(i2 + 2).read() == b'a'
                    && m.add(i2 + 3).read() == b'm'
                    && m.add(i2 + 4).read() == b'e'
                {
                    // "name":"X" — i2 指向开引号; i2+5 为键闭引号, i2+6 为 ':'
                    // 跳过键闭引号, 再前进到值开引号
                    let mut j = i2 + 6;
                    while j < mlen_u && m.add(j).read() != b'"' {
                        j += 1;
                    }
                    if j >= mlen_u {
                        break;
                    }
                    j += 1;
                    let mut n = 0usize;
                    while j < mlen_u && m.add(j).read() != b'"' && n < 15 {
                        names[name_i][n] = m.add(j).read();
                        n += 1;
                        j += 1;
                    }
                    name_i += 1;
                    i2 = j + 1;
                    continue;
                }
                i2 += 1;
            }
            res_map = [(0, 0); MAX_RES];
            for k in 0..MAX_RES {
                res_map[k] = (0, k);
                let _ = &mut res_map;
            }
            // 拷贝 DATA 资源
            RES_COUNT = data_n.min(name_i).min(MAX_RES);
            for k in 0..RES_COUNT {
                let (src, slen) = data_sections[k];
                let copy_len = (slen as usize).min(RES_MAX);
                for x in 0..copy_len {
                    RES_DATA[k][x] = (src as *const u8).add(x).read_volatile();
                }
                RES_LEN[k] = copy_len;
                // 名称
                let mut nn = 0usize;
                while nn < 15 {
                    RES_NAMES[k][nn] = names[k][nn];
                    nn += 1;
                }
                serial::write_str("run  : resource #");
                print_dec(k as u64);
                serial::write_str(" name=");
                let mut n_end = 0usize;
                while n_end < 16 && RES_NAMES[k][n_end] != 0 {
                    n_end += 1;
                }
                serial::write_str(core::str::from_utf8(&RES_NAMES[k][..n_end]).unwrap_or(""));
                serial::write_str(" size=");
                print_dec(copy_len as u64);
                serial::write_line("");
            }
            serial::write_str("run  : resources mounted at /runres (");
            print_dec(RES_COUNT as u64);
            serial::write_line(")");
        }
        Some(exec)
    }
}

/// /runres/<name> 资源查询。
pub fn resource(name: &[u8]) -> Option<(*const u8, usize)> {
    unsafe {
        let n = name.len().min(15);
        for k in 0..RES_COUNT {
            let mut same = true;
            for i in 0..n {
                if RES_NAMES[k][i] != name[i] {
                    same = false;
                    break;
                }
            }
            if same {
                for i in n..15 {
                    if RES_NAMES[k][i] != 0 {
                        same = false;
                        break;
                    }
                }
            }
            if same && RES_LEN[k] > 0 {
                return Some((core::ptr::addr_of!(RES_DATA[k][0]) as *const u8, RES_LEN[k]));
            }
        }
    }
    None
}
