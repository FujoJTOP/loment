/* 由 tools/lomc.py 生成 —— 请勿手改；改 lom/*.lom 后重新生成。 */
/* module fuc */
#ifndef LOM_GEN_H
#define LOM_GEN_H

#include <stdint.h>

#define MAGIC ((uint32_t)0x43495546)
#define VERSION ((uint16_t)0x0001)

/* record Header: size=48 endian=little packed=true */
#define HEADER_SIZE (48u)
#define HEADER_MAGIC_OFF (0u)
#define HEADER_VERSION_OFF (4u)
#define HEADER_FLAGS_OFF (6u)
#define HEADER_NODE_COUNT_OFF (8u)
#define HEADER_NODE_OFF_OFF (12u)
#define HEADER_STR_OFF_OFF (16u)
#define HEADER_STR_LEN_OFF (20u)
#define HEADER_TOK_OFF_OFF (24u)
#define HEADER_TOK_COUNT_OFF (28u)
#define HEADER_ANIM_OFF_OFF (32u)
#define HEADER_ANIM_COUNT_OFF (36u)
#define HEADER_DOC_W_OFF (40u)
#define HEADER_DOC_H_OFF (42u)
#define HEADER_ROOT_OFF (44u)
#define HEADER_RESERVED_OFF (46u)

/* record Node: size=64 endian=little packed=true */
#define NODE_SIZE (64u)
#define NODE_KIND_OFF (0u)
#define NODE_ID_OFF (2u)
#define NODE_FLAGS_OFF (4u)
#define NODE_CHILDREN_OFF (6u)
#define NODE_X_OFF (8u)
#define NODE_Y_OFF (10u)
#define NODE_W_MODE_OFF (12u)
#define NODE_W_VAL_OFF (14u)
#define NODE_H_MODE_OFF (16u)
#define NODE_H_VAL_OFF (18u)
#define NODE_PAD_L_OFF (20u)
#define NODE_PAD_T_OFF (22u)
#define NODE_PAD_R_OFF (24u)
#define NODE_PAD_B_OFF (26u)
#define NODE_GAP_OFF (28u)
#define NODE_RADIUS_OFF (30u)
#define NODE_ELEV_OFF (32u)
#define NODE_ALIGN_OFF (34u)
#define NODE_JUSTIFY_OFF (36u)
#define NODE_SIZE_OFF (38u)
#define NODE_BG_OFF (40u)
#define NODE_FG_OFF (44u)
#define NODE_TEXT_ID_OFF (48u)
#define NODE_TONE_OFF (52u)
#define NODE_OPACITY_OFF (54u)
#define NODE_FIRST_CHILD_OFF (56u)
#define NODE_EXTRA_OFF (60u)

#endif /* LOM_GEN_H */
