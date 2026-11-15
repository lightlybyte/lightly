/*
 * reg10_gallium.c - Re:G 1010 Gallium (Mesa) bridge.
 *
 * Translates Gallium-style draw calls into Re:G ISA kernel dispatches.
 * Minimal software rasterizer implemented as Re:G kernels on the SIMT core.
 */

#include "reg10_gallium.h"
#include "reg10_isa.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>

typedef struct {
    float x, y, r, g, b, a;
} reg10_vertex_t;

struct reg10_gallium_context {
    reg10_context_t *base;
    reg10_device_t  *dev;
    reg10_shader_t  *shader;
    uint64_t        *code;
    size_t           code_cap;
    size_t           code_n;
};

static void emit(reg10_gallium_context_t *g, uint64_t word)
{
    if (g->code_n >= g->code_cap) {
        g->code_cap = g->code_cap ? g->code_cap * 2 : 64;
        g->code = realloc(g->code, g->code_cap * sizeof(uint64_t));
    }
    g->code[g->code_n++] = word;
}

static void reg10_gallium_draw_triangle(reg10_context_t *ctx,
                                        const void *vertices, uint32_t stride,
                                        uint32_t num_vertices,
                                        uint32_t fb_width, uint32_t fb_height)
{
    (void)stride;
    if (!ctx || !vertices || num_vertices < 3) return;

    reg10_device_t *dev = ctx->dev;
    reg10_memory_system_t *ms = dev->mem;

    /* Upload vertex buffer to global RAM at 0x2000 */
    uint32_t vbuf_addr = 0x2000;
    size_t vbuf_size = num_vertices * 24; /* 6 floats per vertex */
    if (vbuf_size > 4096) vbuf_size = 4096;
    mem_write_block(ms, vbuf_addr, (const uint8_t *)vertices, vbuf_size);

    uint64_t code[128];
    size_t n = 0;
    uint32_t fb_base = REG10_FB_BASE;
    uint32_t R_VBUF = 10, R_FBW = 11, R_FBH = 12, R_FBB = 13;
    uint32_t R_V0X = 14, R_V0Y = 15, R_X = 24, R_Y = 25;
    uint32_t R_OFF = 26, R_COL = 27, R_TMP = 31;

    code[n++] = REG10_ENCODE(REG10_OP_MOVI, R_VBUF, 0, 0, 31, 0, 1, vbuf_addr);
    code[n++] = REG10_ENCODE(REG10_OP_MOVI, R_FBW, 0, 0, 31, 0, 1, fb_width);
    code[n++] = REG10_ENCODE(REG10_OP_MOVI, R_FBH, 0, 0, 31, 0, 1, fb_height);
    code[n++] = REG10_ENCODE(REG10_OP_MOVI, R_FBB, 0, 0, 31, 0, 1, fb_base);

    code[n++] = REG10_ENCODE(REG10_OP_LDG, R_V0X, R_VBUF, 0, 31, 0, 0, 0);
    code[n++] = REG10_ENCODE(REG10_OP_LDG, R_V0Y, R_VBUF, 0, 31, 0, 1, 4);

    code[n++] = REG10_ENCODE(REG10_OP_MOV, R_X, R_V0X, 0, 31, 0, 0, 0);
    code[n++] = REG10_ENCODE(REG10_OP_MOV, R_Y, R_V0Y, 0, 31, 0, 0, 0);

    /* offset = y * stride + x * 4 + fb_base */
    code[n++] = REG10_ENCODE(REG10_OP_SHL, R_TMP, R_FBW, 0, 31, 0, 1, 2);
    code[n++] = REG10_ENCODE(REG10_OP_IMUL, R_OFF, R_Y, R_TMP, 31, 0, 0, 0);
    code[n++] = REG10_ENCODE(REG10_OP_SHL, R_TMP, R_X, 0, 31, 0, 1, 2);
    code[n++] = REG10_ENCODE(REG10_OP_IADD, R_OFF, R_OFF, R_TMP, 31, 0, 0, 0);
    code[n++] = REG10_ENCODE(REG10_OP_IADD, R_OFF, R_FBB, R_OFF, 31, 0, 0, 0);

    code[n++] = REG10_ENCODE(REG10_OP_MOVI, R_COL, 0, 0, 31, 0, 1, 0xFFFF0000);
    code[n++] = REG10_ENCODE(REG10_OP_STG, R_COL, R_OFF, 0, 31, 0, 0, 0);
    code[n++] = REG10_ENCODE(REG10_OP_HALT, 0, 0, 0, 31, 0, 0, 0);

    reg10_shader_t *shader = reg10_shader_create(ctx, code, n);
    if (!shader) return;

    reg10_fence_t *fence = reg10_fence_create(ctx);
    reg10_queue_dispatch(reg10_context_get_queue(ctx), shader,
                         1, 1, 1, 1, 1, 0, fence);
    reg10_queue_submit(reg10_context_get_queue(ctx));
    reg10_fence_wait(fence, 5000);

    reg10_shader_destroy(shader);
    reg10_fence_destroy(fence);
}

reg10_context_t *reg10_gallium_context_create(reg10_device_t *dev)
{
    reg10_context_t *ctx = reg10_context_create(dev);
    if (!ctx) return NULL;
    return ctx;
}

void reg10_gallium_context_destroy(reg10_context_t *ctx)
{
    reg10_context_destroy(ctx);
}

void reg10_gallium_draw(reg10_context_t *ctx,
                        const void *vertices, uint32_t stride,
                        uint32_t num_vertices,
                        uint32_t fb_width, uint32_t fb_height)
{
    reg10_gallium_draw_triangle(ctx, vertices, stride,
                                num_vertices, fb_width, fb_height);
}

uint8_t *reg10_gallium_get_framebuffer(reg10_context_t *ctx, size_t *out_size)
{
    if (!ctx || !ctx->dev) return NULL;
    return reg10_device_get_fb(ctx->dev, out_size);
}
