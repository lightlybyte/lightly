/*
 * platform/windows/reg10_platform.c - Windows-specific hooks.
 * Minimal: present does nothing, sleep uses Sleep().
 */

#include "reg10_platform.h"
#include <windows.h>

void reg10_platform_present(const uint8_t *fb, uint32_t width,
                            uint32_t height, uint32_t stride)
{
    (void)fb; (void)width; (void)height; (void)stride;
    /* No GUI in usermode driver; FB remains in RAM for host inspection. */
}

void reg10_platform_sleep_ms(uint32_t ms)
{
    Sleep(ms);
}
