/*
 * reg10_gallium.h - Re:G 1010 Gallium (Mesa) driver interface.
 *
 * This is the bridge between Mesa's Gallium3D framework and the Re:G
 * usermode driver. Mesa state trackers (OpenGL, OpenGL ES, Vulkan via
 * lavapipe-style) call into these entry points to submit draw commands
 * that get translated into Re:G ISA kernels.
 */

#ifndef REG10_GALLIUM_H
#define REG10_GALLIUM_H

#include "reg10_driver.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Create a Gallium pipe_context-like object backed by the Re:G driver.
 * Returns NULL on failure. */
reg10_context_t *reg10_gallium_context_create(reg10_device_t *dev);

/* Destroy a Gallium context. */
void reg10_gallium_context_destroy(reg10_context_t *ctx);

/* Draw: submit a vertex/fragment shader pair as Re:G kernels.
 * vertices: host pointer to vertex data (stride bytes per vertex).
 * num_vertices: vertex count.
 * framebuffer: width/height for the render target.
 */
void reg10_gallium_draw(reg10_context_t *ctx,
                        const void *vertices, uint32_t stride,
                        uint32_t num_vertices,
                        uint32_t fb_width, uint32_t fb_height);

/* Get the rendered framebuffer pointer (BGRA8). */
uint8_t *reg10_gallium_get_framebuffer(reg10_context_t *ctx,
                                       size_t *out_size);

#ifdef __cplusplus
}
#endif

#endif /* REG10_GALLIUM_H */