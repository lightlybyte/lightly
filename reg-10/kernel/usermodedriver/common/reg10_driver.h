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
