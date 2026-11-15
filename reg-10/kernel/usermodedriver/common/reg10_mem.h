#ifndef REG10_MEM_H
#define REG10_MEM_H

#include "reg10_isa.h"

/* Forward declarations */
typedef struct reg10_global_ram    reg10_global_ram_t;
typedef struct reg10_scratchpad    reg10_scratchpad_t;
typedef struct reg10_framebuffer   reg10_framebuffer_t;
typedef struct reg10_memory_system reg10_memory_system_t;

/* ------------------------------------------------------------------ */
/* Scratchpad (inline accessors so the CPU core can use them)        */
/* ------------------------------------------------------------------ */

struct reg10_scratchpad {
    uint8_t *mem;
    size_t   size;
    size_t   mask;
};

static inline uint32_t sp_read_u32(reg10_scratchpad_t *p, uint32_t addr)
{
    addr &= (uint32_t)p->mask;
    return (uint32_t)p->mem[addr] | ((uint32_t)p->mem[addr+1] << 8) |
           ((uint32_t)p->mem[addr+2] << 16) | ((uint32_t)p->mem[addr+3] << 24);
}

static inline void sp_write_u32(reg10_scratchpad_t *p, uint32_t addr,
                                uint32_t v)
{
    addr &= (uint32_t)p->mask;
    p->mem[addr]   = (uint8_t)(v & 0xFF);
    p->mem[addr+1] = (uint8_t)((v >> 8) & 0xFF);
    p->mem[addr+2] = (uint8_t)((v >> 16) & 0xFF);
    p->mem[addr+3] = (uint8_t)((v >> 24) & 0xFF);
}

/* ------------------------------------------------------------------ */
/* Full type definitions needed by struct reg10_memory_system         */
/* ------------------------------------------------------------------ */

struct reg10_global_ram {
    uint8_t *mem;
    size_t   size;
};

struct reg10_framebuffer {
    uint8_t *mem;
    uint32_t base;
    uint32_t width;
    uint32_t height;
    uint32_t stride;
    uint32_t size;
};

struct reg10_memory_system {
    reg10_global_ram_t   *global_ram;
    reg10_scratchpad_t   *scratchpads;
    uint32_t              num_cores;
    reg10_framebuffer_t   framebuffer;
};

reg10_memory_system_t *mem_system_create(uint32_t num_cores,
                                         uint32_t fb_w, uint32_t fb_h,
                                         uint32_t fb_base);
void mem_system_destroy(reg10_memory_system_t *ms);
void mem_system_clear_fb(reg10_memory_system_t *ms, uint32_t bgra);

/* ------------------------------------------------------------------ */
/* Inline accessors (defined in reg10_mem_inl.h)                     */
/* ------------------------------------------------------------------ */

#include "reg10_mem_inl.h"

#endif /* REG10_MEM_H */

