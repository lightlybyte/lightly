/*
 * platform/linux/reg10_platform.c - Linux-specific hooks.
 * Minimal: present does nothing (no X11/Wayland dependency), sleep uses nanosleep.
 */

#include "reg10_platform.h"
#include <time.h>

void reg10_platform_present(const uint8_t *fb, uint32_t width,
                            uint32_t height, uint32_t stride)
{
    (void)fb; (void)width; (void)height; (void)stride;
    /* No GUI in usermode driver; FB remains in RAM for host inspection. */
}

void reg10_platform_sleep_ms(uint32_t ms)
{
    struct timespec ts;
    ts.tv_sec = ms / 1000;
    ts.tv_nsec = (ms % 1000) * 1000000L;
    nanosleep(&ts, NULL);
}
