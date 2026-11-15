#ifndef REG10_PLATFORM_H
#define REG10_PLATFORM_H

/* Platform-specific hooks (display, threading). Minimal for usermode VM. */

#ifdef __cplusplus
extern "C" {
#endif

/* Present the framebuffer to the screen (platform-specific). */
void reg10_platform_present(const uint8_t *fb, uint32_t width,
                            uint32_t height, uint32_t stride);

/* Sleep for a number of milliseconds. */
void reg10_platform_sleep_ms(uint32_t ms);

#ifdef __cplusplus
}
#endif

#endif