// 由 tools/lomc.py 生成 —— 请勿手改；改 lom/*.lom 后重新生成。
// module fuc

pub const MAGIC: u32 = 0x43495546;
pub const VERSION: u16 = 0x0001;

// record Header: size=48 endian=little packed=true
pub const HEADER_SIZE: usize = 48;
pub const HEADER_MAGIC_OFF: usize = 0;
pub const HEADER_VERSION_OFF: usize = 4;
pub const HEADER_FLAGS_OFF: usize = 6;
pub const HEADER_NODE_COUNT_OFF: usize = 8;
pub const HEADER_NODE_OFF_OFF: usize = 12;
pub const HEADER_STR_OFF_OFF: usize = 16;
pub const HEADER_STR_LEN_OFF: usize = 20;
pub const HEADER_TOK_OFF_OFF: usize = 24;
pub const HEADER_TOK_COUNT_OFF: usize = 28;
pub const HEADER_ANIM_OFF_OFF: usize = 32;
pub const HEADER_ANIM_COUNT_OFF: usize = 36;
pub const HEADER_DOC_W_OFF: usize = 40;
pub const HEADER_DOC_H_OFF: usize = 42;
pub const HEADER_ROOT_OFF: usize = 44;
pub const HEADER_RESERVED_OFF: usize = 46;

// record Node: size=64 endian=little packed=true
pub const NODE_SIZE: usize = 64;
pub const NODE_KIND_OFF: usize = 0;
pub const NODE_ID_OFF: usize = 2;
pub const NODE_FLAGS_OFF: usize = 4;
pub const NODE_CHILDREN_OFF: usize = 6;
pub const NODE_X_OFF: usize = 8;
pub const NODE_Y_OFF: usize = 10;
pub const NODE_W_MODE_OFF: usize = 12;
pub const NODE_W_VAL_OFF: usize = 14;
pub const NODE_H_MODE_OFF: usize = 16;
pub const NODE_H_VAL_OFF: usize = 18;
pub const NODE_PAD_L_OFF: usize = 20;
pub const NODE_PAD_T_OFF: usize = 22;
pub const NODE_PAD_R_OFF: usize = 24;
pub const NODE_PAD_B_OFF: usize = 26;
pub const NODE_GAP_OFF: usize = 28;
pub const NODE_RADIUS_OFF: usize = 30;
pub const NODE_ELEV_OFF: usize = 32;
pub const NODE_ALIGN_OFF: usize = 34;
pub const NODE_JUSTIFY_OFF: usize = 36;
pub const NODE_SIZE_OFF: usize = 38;
pub const NODE_BG_OFF: usize = 40;
pub const NODE_FG_OFF: usize = 44;
pub const NODE_TEXT_ID_OFF: usize = 48;
pub const NODE_TONE_OFF: usize = 52;
pub const NODE_OPACITY_OFF: usize = 54;
pub const NODE_FIRST_CHILD_OFF: usize = 56;
pub const NODE_EXTRA_OFF: usize = 60;
