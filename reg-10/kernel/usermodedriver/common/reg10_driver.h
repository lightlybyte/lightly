/*
 * reg10_driver.h - Public usermode driver API for Re:G 1010 GPU VM.
 *
 * Provides device enumeration, kernel submission, command ring,
 * framebuffer access, and fences. Pure usermode library - no kernel
 * driver required; the GPU is emulated in-process on the host CPU.
 */

#ifndef REG10_DRIVER_H
#define REG10_DRIVER_H

#include "reg10_isa.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct reg10_device  reg10_device_t;
typedef struct reg10_context reg10_context_t;
typedef struct reg10_queue   reg10_queue_t;
typedef struct reg10_mem     reg10_mem_t;
typedef struct reg10_shader  reg10_shader_t;
typedef struct reg10_fence   reg10_fence_t;

typedef enum reg10_result {
    REG10_SUCCESS          = 0,
    REG10_ERROR_NO_DEVICE  = -1,
    REG10_ERROR_NO_MEMORY  = -2,
    REG10_ERROR_INVALID    = -3,
    REG10_ERROR_UNSUPPORTED = -4,
    REG10_ERROR_DEVICE_LOST = -5,
} reg10_result_t;

typedef struct reg10_device_info {
    uint32_t vendor_id;
    uint32_t device_id;
    char     name[64];
    uint32_t num_cores;
    uint32_t warps_per_core;
    uint32_t lanes_per_warp;
    uint64_t global_ram_size;
    uint32_t scratchpad_size;
    uint32_t fb_width;
    uint32_t fb_height;
} reg10_device_info_t;

uint32_t reg10_enumerate_devices(reg10_device_info_t *infos,
                                 uint32_t max_infos);
reg10_device_t *reg10_device_open(uint32_t index);
void reg10_device_close(reg10_device_t *dev);
reg10_result_t reg10_device_get_info(reg10_device_t *dev,
                                     reg10_device_info_t *info);

reg10_context_t *reg10_context_create(reg10_device_t *dev);
void             reg10_context_destroy(reg10_context_t *ctx);
reg10_queue_t   *reg10_context_get_queue(reg10_context_t *ctx);

reg10_mem_t *reg10_mem_alloc(reg10_context_t *ctx, size_t size,
                             uint32_t flags);
void         reg10_mem_free(reg10_mem_t *mem);
void *reg10_mem_map(reg10_mem_t *mem);
void  reg10_mem_unmap(reg10_mem_t *mem);
uint64_t reg10_mem_get_gpu_addr(reg10_mem_t *mem);
reg10_result_t reg10_mem_flush(reg10_mem_t *mem);

reg10_shader_t *reg10_shader_create(reg10_context_t *ctx,
                                    const uint64_t *code, size_t num_words);
void reg10_shader_destroy(reg10_shader_t *shader);

reg10_fence_t *reg10_fence_create(reg10_context_t *ctx);
void           reg10_fence_destroy(reg10_fence_t *fence);
reg10_result_t reg10_fence_wait(reg10_fence_t *fence, uint64_t timeout_ms);

reg10_result_t reg10_queue_dispatch(reg10_queue_t   *queue,
                                    reg10_shader_t  *shader,
                                    uint32_t         grid_x,
                                    uint32_t         grid_y,
                                    uint32_t         block_x,
                                    uint32_t         block_y,
                                    uint32_t         warps_per_block,
                                    uint64_t         args_addr,
                                    reg10_fence_t   *fence);
reg10_result_t reg10_queue_copy(reg10_queue_t *queue,
                                uint64_t       src_addr,
                                uint64_t       dst_addr,
                                size_t         length,
                                reg10_fence_t *fence);
reg10_result_t reg10_queue_present(reg10_queue_t *queue);
reg10_result_t reg10_queue_submit(reg10_queue_t *queue);

reg10_result_t reg10_device_set_framebuffer(reg10_device_t *dev,
                                            uint32_t width, uint32_t height);
uint8_t *reg10_device_get_fb(reg10_device_t *dev, size_t *out_size);
reg10_result_t reg10_device_run(reg10_device_t *dev, uint64_t max_cycles);

#ifdef __cplusplus
}
#endif

#endif /* REG10_DRIVER_H */