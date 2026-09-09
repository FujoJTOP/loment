/* loment/build/native_mem_driver.c — M15/M18 的 C 驱动 (docs/145)
 * 期望: 42 0 0 5
 */
#include <stdio.h>

extern unsigned int heap_roundtrip(void);
extern unsigned int wrap_add(unsigned int, unsigned int);
extern unsigned int safe_div(unsigned int, unsigned int);
extern unsigned int atomic_roundtrip(void);

int main(void) {
    printf("%u\n", heap_roundtrip());
    printf("%u\n", wrap_add(4294967295u, 1u));
    printf("%u\n", safe_div(10, 0));
    printf("%u\n", safe_div(10, 2));
    printf("%u\n", atomic_roundtrip());
    return 0;
}
