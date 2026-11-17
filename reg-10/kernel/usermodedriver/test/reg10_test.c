/*
 * test/reg10_test.c - Smoke test for the Re:G 1010 usermode driver.
 *
 * Builds and runs a small gradient-fill kernel directly through the
 * driver C API.
 */

#include "reg10_driver.h"
#include "reg10_isa.h"
#include <stdio.h>
#include <stdlib.h>

static uint32_t read_px(const uint8_t *fb, uint32_t w, uint32_t h,
                        uint32_t x, uint32_t y)
{
    if (x >= w) x = w - 1;
    if (y >= h) y = h - 1;
    uint32_t off = y * (w * 4) + x * 4;
    return (uint32_t)fb[off] | ((uint32_t)fb[off + 1] << 8) |
           ((uint32_t)fb[off + 2] << 16) | ((uint32_t)fb[off + 3] << 24);
}

int main(void)
{
    reg10_device_info_t info;
    uint32_t ndev = reg10_enumerate_devices(&info, 1);
    printf("Re:G 1010 usermode driver smoke test\n");
    printf("devices found: %u\n", ndev);
    if (ndev == 0) {
        printf("no Re:G device found\n");
        return 1;
    }
    printf("  name:     %s\n", info.name);
    printf("  vendor:   0x%04x device: 0x%04x\n", info.vendor_id, info.device_id);
    printf("  cores:    %u warps/core: %u lanes/warp: %u\n",
           info.num_cores, info.warps_per_core, info.lanes_per_warp);
    printf("  RAM:      %llu bytes  scratchpad: %u bytes\n",
           (unsigned long long)info.global_ram_size, info.scratchpad_size);
    printf("  FB:       %ux%u\n", info.fb_width, info.fb_height);

    reg10_device_t *dev = reg10_device_open(0);
    if (!dev) {
        printf("failed to open device\n");
        return 1;
    }

    reg10_context_t *ctx = reg10_context_create(dev);
    if (!ctx) {
        printf("failed to create context\n");
        return 1;
    }
    /* ------------------------------------------------------------------
     * Build a gradient kernel in memory (ported from ISA doc example).
     * ------------------------------------------------------------------ */
    uint64_t code[64];
    size_t n = 0;

    /* MOVI r10, 0x10000        ; fb base */
    code[n++] = REG10_ENCODE(REG10_OP_MOVI, 10, 0, 0, 31, 0, 1, 0x10000);
    /* MOVI r13, 320            ; width */
    code[n++] = REG10_ENCODE(REG10_OP_MOVI, 13, 0, 0, 31, 0, 1, 320);
    /* MOVI r14, 240            ; height */
    code[n++] = REG10_ENCODE(REG10_OP_MOVI, 14, 0, 0, 31, 0, 1, 240);
    /* MOVI r2, 0               ; y = 0 */
    code[n++] = REG10_ENCODE(REG10_OP_MOVI, 2, 0, 0, 31, 0, 1, 0);
    size_t yloop = n;
    /* MOVI r1, 0               ; x_base = 0 */
    code[n++] = REG10_ENCODE(REG10_OP_MOVI, 1, 0, 0, 31, 0, 1, 0);
    size_t xloop = n;
    /* LANEID r11 */
    code[n++] = REG10_ENCODE(REG10_OP_LANEID, 11, 0, 0, 31, 0, 0, 0);
    /* IADD r15, r1, r11        ; x = x_base + lane */
    code[n++] = REG10_ENCODE(REG10_OP_IADD, 15, 1, 11, 31, 0, 0, 0);
    /* MOVI r19, 0xFF */
    code[n++] = REG10_ENCODE(REG10_OP_MOVI, 19, 0, 0, 31, 0, 1, 0xFF);
    /* IAND r16, r15, r19       ; r = x & 0xFF */
    code[n++] = REG10_ENCODE(REG10_OP_IAND, 16, 15, 19, 31, 0, 0, 0);
    /* IAND r17, r2, r19       ; g = y & 0xFF */
    code[n++] = REG10_ENCODE(REG10_OP_IAND, 17, 2, 19, 31, 0, 0, 0);
    /* SHLI r17, r17, 16 */
    code[n++] = REG10_ENCODE(REG10_OP_SHL, 17, 17, 0, 31, 0, 1, 16);
    /* IOR r16, r16, r17 */
    code[n++] = REG10_ENCODE(REG10_OP_IOR, 16, 16, 17, 31, 0, 0, 0);
    /* MOVI r17, 0xFF8080 */
    code[n++] = REG10_ENCODE(REG10_OP_MOVI, 17, 0, 0, 31, 0, 1, 0xFF8080);
    /* IOR r16, r16, r17        ; color */
    code[n++] = REG10_ENCODE(REG10_OP_IOR, 16, 16, 17, 31, 0, 0, 0);
    /* MOVI r19, 1280 */
    code[n++] = REG10_ENCODE(REG10_OP_MOVI, 19, 0, 0, 31, 0, 1, 1280);
    /* IMUL r17, r2, r19        ; y * 1280 */
    code[n++] = REG10_ENCODE(REG10_OP_IMUL, 17, 2, 19, 31, 0, 0, 0);
    /* SHLI r19, r15, 2         ; x * 4 */
    code[n++] = REG10_ENCODE(REG10_OP_SHL, 19, 15, 0, 31, 0, 1, 2);
    /* IADD r17, r17, r19 */
    code[n++] = REG10_ENCODE(REG10_OP_IADD, 17, 17, 19, 31, 0, 0, 0);
    /* IADD r17, r10, r17       ; fb_base + offset */
    code[n++] = REG10_ENCODE(REG10_OP_IADD, 17, 10, 17, 31, 0, 0, 0);
    /* STG r16, r17, 0          ; store pixel */
    code[n++] = REG10_ENCODE(REG10_OP_STG, 16, 17, 0, 31, 0, 0, 0);
    /* ADDI r1, r1, 32          ; x_base += 32 */
    code[n++] = REG10_ENCODE(REG10_OP_IADD, 1, 1, 0, 31, 0, 1, 32);
    /* SETP p0, r1, r13, 0      ; p0 = (r1 < 320) */
    code[n++] = REG10_ENCODE(REG10_OP_SETP, 0, 1, 13, 31, 0, 0, REG10_SETP_LT);
    /* @P0  BRA xloop           ; branch back */
    {
        /* imm = target_addr - (current_addr + 8) */
        int32_t imm = (int32_t)((xloop - n) * 8 - 8);
        code[n++] = REG10_ENCODE(REG10_OP_BRA, 0, 0, 0, 0, 0, 1, imm);
    }
    /* ADDI r2, r2, 1           ; y += 1 */
    code[n++] = REG10_ENCODE(REG10_OP_IADD, 2, 2, 0, 31, 0, 1, 1);
    /* SETP p0, r2, r14, 0      ; p0 = (r2 < 240) */
    code[n++] = REG10_ENCODE(REG10_OP_SETP, 0, 2, 14, 31, 0, 0, REG10_SETP_LT);
    /* @P0  BRA yloop */
    {
        /* imm = target_addr - (current_addr + 8) */
        int32_t imm = (int32_t)((yloop - n) * 8 - 8);
        code[n++] = REG10_ENCODE(REG10_OP_BRA, 0, 0, 0, 0, 0, 1, imm);
    }
    /* HALT */
    code[n++] = REG10_ENCODE(REG10_OP_HALT, 0, 0, 0, 31, 0, 0, 0);

    printf("kernel: %zu instructions (%zu bytes)\n", n, n * 8);

    /* Create shader */
    reg10_shader_t *shader = reg10_shader_create(ctx, code, n);
    if (!shader) {
        printf("failed to create shader\n");
        return 1;
    }

    /* Set framebuffer size */
    reg10_device_set_framebuffer(dev, info.fb_width, info.fb_height);

    /* Create fence */
    reg10_fence_t *fence = reg10_fence_create(ctx);

    /* Dispatch kernel */
    reg10_result_t r = reg10_queue_dispatch(reg10_context_get_queue(ctx),
                                            shader,
                                            1, 1,   /* grid */
                                            1, 1,   /* block */
                                            1,      /* warps per block */
                                            0,      /* args_addr */
                                            fence);
    if (r != REG10_SUCCESS) {
        printf("dispatch failed: %d\n", (int)r);
        return 1;
    }

    /* Submit and run */
    r = reg10_queue_submit(reg10_context_get_queue(ctx));
    if (r != REG10_SUCCESS) {
        printf("submit/run failed: %d\n", (int)r);
        return 1;
    }

    /* Wait for fence */
    r = reg10_fence_wait(fence, 5000);
    printf("fence wait: %d\n", (int)r);

    /* Read back some pixels */
    size_t fb_size = 0;
    uint8_t *fb = reg10_device_get_fb(dev, &fb_size);
    if (fb) {
        uint32_t w = info.fb_width, h = info.fb_height;
        printf("pixel(0,0)   = 0x%08X\n", read_px(fb, w, h, 0, 0));
        printf("pixel(160,120)= 0x%08X\n", read_px(fb, w, h, 160, 120));
        printf("pixel(319,239)= 0x%08X\n", read_px(fb, w, h, 319, 239));
    }

    /* Cleanup */
    reg10_fence_destroy(fence);
    reg10_shader_destroy(shader);
    reg10_context_destroy(ctx);
    reg10_device_close(dev);

    printf("smoke test complete\n");
    return 0;
}


