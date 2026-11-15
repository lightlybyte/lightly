#ifndef REG10_MEM_INL_H
#define REG10_MEM_INL_H

#include "reg10_mem.h"
#include <string.h>

/* ------------------------------------------------------------------ */
/* Helpers                                                            */
/* ------------------------------------------------------------------ */

static inline uint32_t read_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static inline void write_u32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v & 0xFF);
    p[1] = (uint8_t)((v >> 8) & 0xFF);
    p[2] = (uint8_t)((v >> 16) & 0xFF);
    p[3] = (uint8_t)((v >> 24) & 0xFF);
}
static inline uint64_t read_u64(const uint8_t *p)
{
    uint64_t v = 0;
    for (int i = 0; i < 8; i++)
        v |= ((uint64_t)p[i]) << (8 * i);
    return v;
}
static inline void write_u64(uint8_t *p, uint64_t v)
{
    for (int i = 0; i < 8; i++)
        p[i] = (uint8_t)((v >> (8 * i)) & 0xFF);
}
static inline uint8_t read_u8(const uint8_t *p) { return p[0]; }
static inline uint16_t read_u16(const uint8_t *p)
{
    return (uint16_t)(p[0] | (p[1] << 8));
}

static inline uint8_t  *mem_ram_base(reg10_memory_system_t *ms)
{ return ms->global_ram->mem; }
static inline size_t    mem_ram_size(reg10_memory_system_t *ms)
{ return ms->global_ram->size; }
static inline uint32_t  mem_read_u32(reg10_memory_system_t *ms, uint32_t a)
{ return read_u32(ms->global_ram->mem + (a & (uint32_t)(ms->global_ram->size - 1))); }
static inline void      mem_write_u32(reg10_memory_system_t *ms, uint32_t a,
                                      uint32_t v)
{ write_u32(ms->global_ram->mem + (a & (uint32_t)(ms->global_ram->size - 1)), v); }
static inline uint64_t  mem_read_u64(reg10_memory_system_t *ms, uint32_t a)
{ return read_u64(ms->global_ram->mem + (a & (uint32_t)(ms->global_ram->size - 1))); }
static inline void      mem_write_u64(reg10_memory_system_t *ms, uint32_t a,
                                      uint64_t v)
{ write_u64(ms->global_ram->mem + (a & (uint32_t)(ms->global_ram->size - 1)), v); }
static inline uint8_t   mem_read_u8(reg10_memory_system_t *ms, uint32_t a)
{ return ms->global_ram->mem[a & (uint32_t)(ms->global_ram->size - 1)]; }
static inline void      mem_write_u8(reg10_memory_system_t *ms, uint32_t a,
                                     uint8_t v)
{ ms->global_ram->mem[a & (uint32_t)(ms->global_ram->size - 1)] = v; }
static inline uint16_t  mem_read_u16(reg10_memory_system_t *ms, uint32_t a)
{ return read_u16(ms->global_ram->mem + (a & (uint32_t)(ms->global_ram->size - 1))); }
static inline void      mem_write_block(reg10_memory_system_t *ms, uint32_t a,
                                        const uint8_t *d, size_t n)
{
    a &= (uint32_t)(ms->global_ram->size - 1);
    if (a + n > ms->global_ram->size) n = ms->global_ram->size - a;
    memcpy(ms->global_ram->mem + a, d, n);
}
static inline void      mem_read_block(reg10_memory_system_t *ms, uint32_t a,
                                       uint8_t *d, size_t n)
{
    a &= (uint32_t)(ms->global_ram->size - 1);
    if (a + n > ms->global_ram->size) n = ms->global_ram->size - a;
    memcpy(d, ms->global_ram->mem + a, n);
}
static inline reg10_scratchpad_t *mem_scratchpad(reg10_memory_system_t *ms,
                                                 uint32_t core)
{ return &ms->scratchpads[core]; }
static inline reg10_framebuffer_t *mem_fb(reg10_memory_system_t *ms)
{ return &ms->framebuffer; }

#endif