/* loment/build/native_bits_driver.c — M22 的 C 驱动 (docs/145)
 * 期望: 13 5 1
 */
#include <stdio.h>

extern unsigned char pack(unsigned char);
extern unsigned int roundtrip(unsigned char);
extern unsigned int top_bits(unsigned char);

int main(void) {
    printf("%u\n", (unsigned)pack(5));
    printf("%u\n", roundtrip(5));
    printf("%u\n", top_bits(13));
    return 0;
}
