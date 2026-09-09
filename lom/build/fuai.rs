// 由 tools/lomc.py 生成 —— 请勿手改；改 lom/*.lom 后重新生成。
// module fuai

pub const TAU_HIGH_DEFAULT_FUJO: i64 = 45;
pub const TAU_HIGH_DEFAULT_LINUX: i64 = 46;

pub const TAU_LOW_DEFAULT_FUJO: i64 = 35;
pub const TAU_LOW_DEFAULT_LINUX: i64 = 35;

// enum Opcode: u16 (46 项)
pub const OPCODE_CLASSIFY: u16 = 0x5101;
pub const OPCODE_FETCH: u16 = 0x5102;
pub const OPCODE_INFO: u16 = 0x5104;
pub const OPCODE_CTX_COMPRESS: u16 = 0x8001;
pub const OPCODE_CTX_SUBSCRIBE: u16 = 0x8002;
pub const OPCODE_CTX_EVENTS: u16 = 0x8003;
pub const OPCODE_CTX_INJECT: u16 = 0x8004;
pub const OPCODE_CTX_STRUCT: u16 = 0x8005;
pub const OPCODE_CAP_GRANT: u16 = 0x8101;
pub const OPCODE_CAP_CHECK: u16 = 0x8102;
pub const OPCODE_AUD_LOG: u16 = 0x8103;
pub const OPCODE_AUD_READ: u16 = 0x8104;
pub const OPCODE_CAP_EXEC: u16 = 0x8105;
pub const OPCODE_CFG_GET: u16 = 0x8106;
pub const OPCODE_DOM_CREATE: u16 = 0x8107;
pub const OPCODE_DOM_BIND: u16 = 0x8108;
pub const OPCODE_DOM_REVOKE: u16 = 0x8109;
pub const OPCODE_DOM_INFO: u16 = 0x810A;
pub const OPCODE_ROUTE_SET: u16 = 0x8201;
pub const OPCODE_ROUTE_CLASSIFY: u16 = 0x8202;
pub const OPCODE_ROUTE_TABLE: u16 = 0x8203;
pub const OPCODE_INFER_RUN: u16 = 0x8301;
pub const OPCODE_INFER_SLOT: u16 = 0x8302;
pub const OPCODE_INFER_SET: u16 = 0x8303;
pub const OPCODE_ANOM_RUN: u16 = 0x8304;
pub const OPCODE_PLAN_RUN: u16 = 0x8305;
pub const OPCODE_IO_PREDICT: u16 = 0x8306;
pub const OPCODE_NLC_SET: u16 = 0x8307;
pub const OPCODE_ENV_SCAN: u16 = 0x8308;
pub const OPCODE_R3_PROBE: u16 = 0x8309;
pub const OPCODE_INV_RUN: u16 = 0x830A;
pub const OPCODE_RULES_LOAD: u16 = 0x830B;
pub const OPCODE_AI_STATS: u16 = 0x830C;
pub const OPCODE_AI_AUDIT: u16 = 0x830D;
pub const OPCODE_CR3_PROBE: u16 = 0x830E;
pub const OPCODE_EVL_MODE: u16 = 0x830F;
pub const OPCODE_EV_DIGEST: u16 = 0x8312;
pub const OPCODE_DOM_ADMIT: u16 = 0x8313;
pub const OPCODE_QUAL_FEED: u16 = 0x8314;
pub const OPCODE_QUAL_SEQ: u16 = 0x8315;
pub const OPCODE_BOX_RUN: u16 = 0x8316;
pub const OPCODE_BOX_STAT: u16 = 0x8317;
pub const OPCODE_BOX_RESULT: u16 = 0x8318;
pub const OPCODE_BOX_FB_INFO: u16 = 0x8319;
pub const OPCODE_BOX_CONTRACT: u16 = 0x8320;
pub const OPCODE_UNIFIED_AUD: u16 = 0x8C01;
pub const OPCODE_COUNT: usize = 46;
