// 由 tools/lomc.py 生成 —— 请勿手改；改 lom/*.lom 后重新生成。
// module fujr

pub const MAGIC: u32 = 0x524A5546;
pub const VERSION: u32 = 0x00000001;
pub const SECTION_ALIGN: u32 = 0x00001000;

// enum Tag: u32 (3 项)
pub const TAG_MANIFEST: u32 = 0x00000001;
pub const TAG_EMBED: u32 = 0x00000004;
pub const TAG_DATA: u32 = 0x00000005;
pub const TAG_COUNT: usize = 3;

// record Header: size=64 endian=little packed=true
pub const HEADER_SIZE: usize = 64;
pub const HEADER_MAGIC_OFF: usize = 0;
pub const HEADER_VERSION_OFF: usize = 4;
pub const HEADER_COUNT_OFF: usize = 8;

// record Section: size=32 endian=little packed=true
pub const SECTION_SIZE: usize = 32;
pub const SECTION_TAG_OFF: usize = 0;
pub const SECTION_OFF_OFF: usize = 8;
pub const SECTION_SIZE_OFF: usize = 16;
pub const SECTION_FNV1A_OFF: usize = 24;
