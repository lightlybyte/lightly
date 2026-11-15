/*
 * reg10_mem.c - Memory subsystem for Re:G 1010 usermode driver.
 * Mirrors ram.py from the reference ISA implementation.
 */

#include "reg10_mem_inl.h"
#include <stdlib.h>
#include <string.h>

reg10_memory_system_t *mem_system_create(uint32_t num_cores,
                                         uint32_t fb_w, uint32_t fb_h,
                                         uint32_t fb_base)
{
    reg10_memory_system_t *ms = calloc(1, sizeof(*ms));
    if (!ms) return NULL;

    ms->global_ram = calloc(1, sizeof(*ms->global_ram));
    if (!ms->global_ram) { free(ms); return NULL; }
    ms->global_ram->mem = calloc(1, REG10_GLOBAL_RAM_SIZE);
    if (!ms->global_ram->mem) {
        free(ms->global_ram);
        free(ms);
        return NULL;
    }
    ms->global_ram->size = REG10_GLOBAL_RAM_SIZE;

    ms->scratchpads = calloc(num_cores, sizeof(*ms->scratchpads));
    if (!ms->scratchpads) {
        free(ms->global_ram->mem);
        free(ms->global_ram);
        free(ms);
        return NULL;
    }
    ms->num_cores = num_cores;
    for (uint32_t i = 0; i < num_cores; i++) {
        ms->scratchpads[i].mem = calloc(1, REG10_SCRATCHPAD_SIZE);
        if (!ms->scratchpads[i].mem) {
            for (uint32_t j = 0; j < i; j++)
                free(ms->scratchpads[j].mem);
            free(ms->scratchpads);
            free(ms->global_ram->mem);
            free(ms->global_ram);
            free(ms);
            return NULL;
        }
        ms->scratchpads[i].size = REG10_SCRATCHPAD_SIZE;
        ms->scratchpads[i].mask = REG10_SCRATCHPAD_SIZE - 1;
    }

    ms->framebuffer.mem   = ms->global_ram->mem;
    ms->framebuffer.base  = fb_base;
    ms->framebuffer.width = fb_w;
    ms->framebuffer.height= fb_h;
    ms->framebuffer.stride= fb_w * 4;
    ms->framebuffer.size  = ms->framebuffer.stride * fb_h;

    return ms;
}

void mem_system_destroy(reg10_memory_system_t *ms)
{
    if (!ms) return;
    for (uint32_t i = 0; i < ms->num_cores; i++)
        free(ms->scratchpads[i].mem);
    free(ms->scratchpads);
    free(ms->global_ram->mem);
    free(ms->global_ram);
    free(ms);
}

void mem_system_clear_fb(reg10_memory_system_t *ms, uint32_t bgra)
{
    reg10_framebuffer_t *fb = &ms->framebuffer;
    for (uint32_t y = 0; y < fb->height; y++) {
        uint32_t off = fb->base + y * fb->stride;
        for (uint32_t x = 0; x < fb->width; x++) {
            write_u32(fb->mem + off + x * 4, bgra);
        }
    }
}

