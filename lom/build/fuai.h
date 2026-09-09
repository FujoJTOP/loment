/* 由 tools/lomc.py 生成 —— 请勿手改；改 lom/*.lom 后重新生成。 */
/* module fuai */
#ifndef LOM_GEN_H
#define LOM_GEN_H

#include <stdint.h>

#define TAU_HIGH_DEFAULT_FUJO (45)
#define TAU_HIGH_DEFAULT_LINUX (46)

#define TAU_LOW_DEFAULT_FUJO (35)
#define TAU_LOW_DEFAULT_LINUX (35)

/* enum Opcode: u16 (46 项) */
#define OPCODE_CLASSIFY ((uint16_t)0x5101)
#define OPCODE_FETCH ((uint16_t)0x5102)
#define OPCODE_INFO ((uint16_t)0x5104)
#define OPCODE_CTX_COMPRESS ((uint16_t)0x8001)
#define OPCODE_CTX_SUBSCRIBE ((uint16_t)0x8002)
#define OPCODE_CTX_EVENTS ((uint16_t)0x8003)
#define OPCODE_CTX_INJECT ((uint16_t)0x8004)
#define OPCODE_CTX_STRUCT ((uint16_t)0x8005)
#define OPCODE_CAP_GRANT ((uint16_t)0x8101)
#define OPCODE_CAP_CHECK ((uint16_t)0x8102)
#define OPCODE_AUD_LOG ((uint16_t)0x8103)
#define OPCODE_AUD_READ ((uint16_t)0x8104)
#define OPCODE_CAP_EXEC ((uint16_t)0x8105)
#define OPCODE_CFG_GET ((uint16_t)0x8106)
#define OPCODE_DOM_CREATE ((uint16_t)0x8107)
#define OPCODE_DOM_BIND ((uint16_t)0x8108)
#define OPCODE_DOM_REVOKE ((uint16_t)0x8109)
#define OPCODE_DOM_INFO ((uint16_t)0x810A)
#define OPCODE_ROUTE_SET ((uint16_t)0x8201)
#define OPCODE_ROUTE_CLASSIFY ((uint16_t)0x8202)
#define OPCODE_ROUTE_TABLE ((uint16_t)0x8203)
#define OPCODE_INFER_RUN ((uint16_t)0x8301)
#define OPCODE_INFER_SLOT ((uint16_t)0x8302)
#define OPCODE_INFER_SET ((uint16_t)0x8303)
#define OPCODE_ANOM_RUN ((uint16_t)0x8304)
#define OPCODE_PLAN_RUN ((uint16_t)0x8305)
#define OPCODE_IO_PREDICT ((uint16_t)0x8306)
#define OPCODE_NLC_SET ((uint16_t)0x8307)
#define OPCODE_ENV_SCAN ((uint16_t)0x8308)
#define OPCODE_R3_PROBE ((uint16_t)0x8309)
#define OPCODE_INV_RUN ((uint16_t)0x830A)
#define OPCODE_RULES_LOAD ((uint16_t)0x830B)
#define OPCODE_AI_STATS ((uint16_t)0x830C)
#define OPCODE_AI_AUDIT ((uint16_t)0x830D)
#define OPCODE_CR3_PROBE ((uint16_t)0x830E)
#define OPCODE_EVL_MODE ((uint16_t)0x830F)
#define OPCODE_EV_DIGEST ((uint16_t)0x8312)
#define OPCODE_DOM_ADMIT ((uint16_t)0x8313)
#define OPCODE_QUAL_FEED ((uint16_t)0x8314)
#define OPCODE_QUAL_SEQ ((uint16_t)0x8315)
#define OPCODE_BOX_RUN ((uint16_t)0x8316)
#define OPCODE_BOX_STAT ((uint16_t)0x8317)
#define OPCODE_BOX_RESULT ((uint16_t)0x8318)
#define OPCODE_BOX_FB_INFO ((uint16_t)0x8319)
#define OPCODE_BOX_CONTRACT ((uint16_t)0x8320)
#define OPCODE_UNIFIED_AUD ((uint16_t)0x8C01)
#define OPCODE_COUNT (46u)

#endif /* LOM_GEN_H */
