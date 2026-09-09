/* loment/build/native_cap_driver.c — P4 的 C 驱动 (docs/145)
 * 期望: 3 2
 */
#include <stdio.h>

extern unsigned int ok(void);
extern unsigned int literal_ok(void);

int main(void) {
    printf("%u\n", ok());
    printf("%u\n", literal_ok());
    return 0;
}
