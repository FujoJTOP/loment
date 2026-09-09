/* 由 tools/lomc.py 生成 —— 请勿手改；改 lom/*.lom 后重新生成。 */
/* module fujr */
#ifndef LOM_GEN_H
#define LOM_GEN_H

#include <stdint.h>

#define MAGIC ((uint32_t)0x524A5546)
#define VERSION ((uint32_t)0x00000001)
#define SECTION_ALIGN ((uint32_t)0x00001000)

/* enum Tag: u32 (3 项) */
#define TAG_MANIFEST ((uint32_t)0x00000001)
#define TAG_EMBED ((uint32_t)0x00000004)
#define TAG_DATA ((uint32_t)0x00000005)
#define TAG_COUNT (3u)

/* record Header: size=64 endian=little packed=true */
#define HEADER_SIZE (64u)
#define HEADER_MAGIC_OFF (0u)
#define HEADER_VERSION_OFF (4u)
#define HEADER_COUNT_OFF (8u)

/* record Section: size=32 endian=little packed=true */
#define SECTION_SIZE (32u)
#define SECTION_TAG_OFF (0u)
#define SECTION_OFF_OFF (8u)
#define SECTION_SIZE_OFF (16u)
#define SECTION_FNV1A_OFF (24u)

#endif /* LOM_GEN_H */
