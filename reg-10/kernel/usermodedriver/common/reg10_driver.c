/*
 * reg10_driver.c - Usermode driver implementation for Re:G 1010 GPU VM.
 */

#include "reg10_driver.h"
#include "reg10_mem.h"
#include "reg10_cpu.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#define REG10_VENDOR_ID  0x1234u
#define REG10_DEVICE_ID  0x1010u
#define REG10_DEVICE_NAME "Re:G 1010"
#define REG10_NUM_CORES  4

struct reg10_device {
    uint32_t index;
    reg10_memory_system_t *mem;
    reg10_core_t *cores;
    uint32_t num_cores;
    uint32_t fb_width;
    uint32_t fb_height;
    uint32_t fb_base;
    int      fb_configured;
};

struct reg10_context {
    reg10_device_t *dev;
    uint32_t next_mem_id;
    uint32_t next_shader_id;
    uint32_t next_fence_id;
    reg10_mem_t    *mems[1024];
    reg10_shader_t *shaders[1024];
    reg10_fence_t  *fences[1024];
    reg10_fence_t  *pending_fence;
    uint32_t num_mems;
    uint32_t num_shaders;
    uint32_t num_fences;
};

struct reg10_queue {
    reg10_context_t *ctx;
};

struct reg10_mem {
    reg10_context_t *ctx;
    size_t           size;
    uint8_t         *host_ptr;
    uint32_t         gpu_addr;
    int              mapped;
};

struct reg10_shader {
    reg10_context_t *ctx;
    uint64_t        *code;
    size_t           num_words;
};

struct reg10_fence {
    reg10_context_t *ctx;
    uint32_t         id;
    int              signalled;
};

static reg10_device_t g_the_device;
static int            g_device_inited = 0;

uint32_t reg10_enumerate_devices(reg10_device_info_t *infos,
                                 uint32_t max_infos)
{
    if (!g_device_inited) {
        memset(&g_the_device, 0, sizeof(g_the_device));
        g_the_device.index = 0;
        g_the_device.num_cores = REG10_NUM_CORES;
        g_the_device.fb_width = 320;
        g_the_device.fb_height = 240;
        g_the_device.fb_base = REG10_FB_BASE;
        g_the_device.fb_configured = 1;
        g_the_device.mem = mem_system_create(REG10_NUM_CORES,
                                             g_the_device.fb_width,
                                             g_the_device.fb_height,
                                             g_the_device.fb_base);
        g_the_device.cores = calloc(REG10_NUM_CORES, sizeof(*g_the_device.cores));
        for (uint32_t i = 0; i < REG10_NUM_CORES; i++) {
            g_the_device.cores[i] = *reg10_core_create(i, g_the_device.mem);
        }
        g_device_inited = 1;
    }

    uint32_t count = (max_infos > 0) ? 1 : 0;
    if (infos && max_infos > 0) {
        reg10_device_info_t *info = &infos[0];
        info->vendor_id = REG10_VENDOR_ID;
        info->device_id = REG10_DEVICE_ID;
        snprintf(info->name, sizeof(info->name), "%s", REG10_DEVICE_NAME);
        info->num_cores = REG10_NUM_CORES;
        info->warps_per_core = REG10_NUM_WARPS_PER_CORE;
        info->lanes_per_warp = REG10_LANES;
        info->global_ram_size = REG10_GLOBAL_RAM_SIZE;
        info->scratchpad_size = REG10_SCRATCHPAD_SIZE;
        info->fb_width = g_the_device.fb_width;
        info->fb_height = g_the_device.fb_height;
    }
    return count;
}

reg10_device_t *reg10_device_open(uint32_t index)
{
    if (index != 0 || !g_device_inited)
        return NULL;
    return &g_the_device;
}

void reg10_device_close(reg10_device_t *dev)
{
    (void)dev;
}

reg10_result_t reg10_device_get_info(reg10_device_t *dev,
                                     reg10_device_info_t *info)
{
    if (!dev || !info) return REG10_ERROR_INVALID;
    info->vendor_id = REG10_VENDOR_ID;
    info->device_id = REG10_DEVICE_ID;
    snprintf(info->name, sizeof(info->name), "%s", REG10_DEVICE_NAME);
    info->num_cores = dev->num_cores;
    info->warps_per_core = REG10_NUM_WARPS_PER_CORE;
    info->lanes_per_warp = REG10_LANES;
    info->global_ram_size = REG10_GLOBAL_RAM_SIZE;
    info->scratchpad_size = REG10_SCRATCHPAD_SIZE;
    info->fb_width = dev->fb_width;
    info->fb_height = dev->fb_height;
    return REG10_SUCCESS;
}
reg10_result_t reg10_device_set_framebuffer(reg10_device_t *dev,
                                            uint32_t width, uint32_t height)
{
    if (!dev) return REG10_ERROR_INVALID;
    dev->fb_width = width;
    dev->fb_height = height;
    dev->fb_configured = 1;
    if (dev->mem) mem_system_destroy(dev->mem);
    dev->mem = mem_system_create(dev->num_cores, width, height, dev->fb_base);
    return dev->mem ? REG10_SUCCESS : REG10_ERROR_NO_MEMORY;
}

uint8_t *reg10_device_get_fb(reg10_device_t *dev, size_t *out_size)
{
    if (!dev || !dev->mem) return NULL;
    reg10_framebuffer_t *fb = mem_fb(dev->mem);
    if (out_size) *out_size = fb->size;
    return fb->mem + fb->base;
}

reg10_result_t reg10_device_run(reg10_device_t *dev, uint64_t max_cycles)
{
    (void)max_cycles;
    if (!dev) return REG10_ERROR_INVALID;
    for (uint32_t c = 0; c < dev->num_cores; c++) {
        while (!reg10_core_all_done(&dev->cores[c]))
            reg10_core_step(&dev->cores[c]);
    }
    return REG10_SUCCESS;
}



reg10_context_t *reg10_context_create(reg10_device_t *dev)
{
    if (!dev) return NULL;
    reg10_context_t *ctx = calloc(1, sizeof(*ctx));
    if (!ctx) return NULL;
    ctx->dev = dev;
    ctx->pending_fence = NULL;
    return ctx;
}

void reg10_context_destroy(reg10_context_t *ctx)
{
    if (!ctx) return;
    for (uint32_t i = 0; i < ctx->num_mems; i++)
        reg10_mem_free(ctx->mems[i]);
    for (uint32_t i = 0; i < ctx->num_shaders; i++)
        reg10_shader_destroy(ctx->shaders[i]);
    for (uint32_t i = 0; i < ctx->num_fences; i++)
        reg10_fence_destroy(ctx->fences[i]);
    free(ctx);
}

reg10_queue_t *reg10_context_get_queue(reg10_context_t *ctx)
{
    if (!ctx || !ctx->dev) return NULL;
    static reg10_queue_t q;
    q.ctx = ctx;
    return &q;
}


reg10_mem_t *reg10_mem_alloc(reg10_context_t *ctx, size_t size,
                             uint32_t flags)
{
    (void)flags;
    if (!ctx || size == 0) return NULL;
    reg10_mem_t *mem = calloc(1, sizeof(*mem));
    if (!mem) return NULL;
    mem->ctx = ctx;
    mem->size = size;
    mem->gpu_addr = 0x1000;
    mem->host_ptr = calloc(1, size);
    if (!mem->host_ptr) { free(mem); return NULL; }
    if (ctx->num_mems < 1024)
        ctx->mems[ctx->num_mems++] = mem;
    return mem;
}

void reg10_mem_free(reg10_mem_t *mem)
{
    if (!mem) return;
    if (mem->host_ptr) free(mem->host_ptr);
    free(mem);
}

void *reg10_mem_map(reg10_mem_t *mem)
{
    if (!mem) return NULL;
    mem->mapped = 1;
    return mem->host_ptr;
}

void reg10_mem_unmap(reg10_mem_t *mem)
{
    if (!mem) return;
    mem->mapped = 0;
}

uint64_t reg10_mem_get_gpu_addr(reg10_mem_t *mem)
{
    return mem ? mem->gpu_addr : 0;
}

reg10_result_t reg10_mem_flush(reg10_mem_t *mem)
{
    if (!mem || !mem->host_ptr || !mem->ctx || !mem->ctx->dev)
        return REG10_ERROR_INVALID;
    reg10_memory_system_t *ms = mem->ctx->dev->mem;
    mem_write_block(ms, mem->gpu_addr, mem->host_ptr, mem->size);
    return REG10_SUCCESS;
}


reg10_shader_t *reg10_shader_create(reg10_context_t *ctx,
                                    const uint64_t *code, size_t num_words)
{
    if (!ctx || !code || num_words == 0) return NULL;
    reg10_shader_t *sh = calloc(1, sizeof(*sh));
    if (!sh) return NULL;
    sh->ctx = ctx;
    sh->num_words = num_words;
    sh->code = malloc(num_words * sizeof(uint64_t));
    if (!sh->code) { free(sh); return NULL; }
    memcpy(sh->code, code, num_words * sizeof(uint64_t));
    if (ctx->num_shaders < 1024)
        ctx->shaders[ctx->num_shaders++] = sh;
    return sh;
}

void reg10_shader_destroy(reg10_shader_t *shader)
{
    if (!shader) return;
    free(shader->code);
    free(shader);
}

reg10_fence_t *reg10_fence_create(reg10_context_t *ctx)
{
    if (!ctx) return NULL;
    reg10_fence_t *f = calloc(1, sizeof(*f));
    if (!f) return NULL;
    f->ctx = ctx;
    f->id = ctx->next_fence_id++;
    f->signalled = 0;
    if (ctx->num_fences < 1024)
        ctx->fences[ctx->num_fences++] = f;
    return f;
}

void reg10_fence_destroy(reg10_fence_t *fence)
{
    free(fence);
}

reg10_result_t reg10_fence_wait(reg10_fence_t *fence, uint64_t timeout_ms)
{
    (void)timeout_ms;
    if (!fence) return REG10_ERROR_INVALID;
    while (!fence->signalled) {
        if (fence->ctx && fence->ctx->dev)
            reg10_device_run(fence->ctx->dev, 100000);
        else
            break;
    }
    return REG10_SUCCESS;
}


reg10_result_t reg10_queue_dispatch(reg10_queue_t *queue,
                                    reg10_shader_t  *shader,
                                    uint32_t         grid_x,
                                    uint32_t         grid_y,
                                    uint32_t         block_x,
                                    uint32_t         block_y,
                                    uint32_t         warps_per_block,
                                    uint64_t         args_addr,
                                    reg10_fence_t   *fence)
{
    (void)args_addr;
    if (!queue || !queue->ctx || !queue->ctx->dev || !shader)
        return REG10_ERROR_INVALID;

    reg10_device_t *dev = queue->ctx->dev;
    reg10_memory_system_t *ms = dev->mem;
    uint32_t origin = 0x1000;
    for (size_t i = 0; i < shader->num_words; i++) {
        mem_write_u64(ms, origin + (uint32_t)(i * 8), shader->code[i]);
    }

    reg10_core_t *core = &dev->cores[0];
    uint32_t total_warps = grid_x * grid_y * block_x * block_y * warps_per_block;
    if (total_warps == 0) total_warps = 1;
    if (total_warps > REG10_NUM_WARPS_PER_CORE)
        total_warps = REG10_NUM_WARPS_PER_CORE;

    for (uint32_t i = 0; i < total_warps; i++) {
        reg10_core_launch_warp(core, (int)i, origin);
    }

    /* Store fence to be signalled after reg10_device_run() completes */
    reg10_context_t *ctx = queue->ctx;
    ctx->pending_fence = fence;
    return REG10_SUCCESS;
}

reg10_result_t reg10_queue_copy(reg10_queue_t *queue,
                                uint64_t       src_addr,
                                uint64_t       dst_addr,
                                size_t         length,
                                reg10_fence_t *fence)
{
    if (!queue || !queue->ctx || !queue->ctx->dev)
        return REG10_ERROR_INVALID;
    reg10_memory_system_t *ms = queue->ctx->dev->mem;
    uint8_t tmp[4096];
    size_t off = 0;
    while (off < length) {
        size_t chunk = length - off;
        if (chunk > sizeof(tmp)) chunk = sizeof(tmp);
        mem_read_block(ms, (uint32_t)(src_addr + off), tmp, chunk);
        mem_write_block(ms, (uint32_t)(dst_addr + off), tmp, chunk);
        off += chunk;
    }
    if (fence) fence->signalled = 1;
    return REG10_SUCCESS;
}

reg10_result_t reg10_queue_present(reg10_queue_t *queue)
{
    (void)queue;
    return REG10_SUCCESS;
}

reg10_result_t reg10_queue_submit(reg10_queue_t *queue)
{
    if (!queue || !queue->ctx || !queue->ctx->dev)
        return REG10_ERROR_INVALID;
    reg10_result_t r = reg10_device_run(queue->ctx->dev, 10000000);
    /* Signal any pending fence now that all warps have completed */
    if (queue->ctx->pending_fence) {
        queue->ctx->pending_fence->signalled = 1;
        queue->ctx->pending_fence = NULL;
    }
    return r;
}

