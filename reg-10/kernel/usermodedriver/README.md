# Re:G 1010 Usermode Driver

A usermode GPU driver for the **Re:G 1010** GPU VM, compatible with the
Re:G ISA v2 documented in `reg-10/kernel/ISA/doc.txt`. The driver is
implemented entirely in userspace C — no kernel driver required — and
emulates the SIMT execution engine on the host CPU.

## Architecture

```
reg-10/kernel/usermodedriver/
├── common/               # Core driver (platform-independent)
│   ├── reg10_isa.h       # ISA v2 constants, encoding, opcodes
│   ├── reg10_driver.h    # Public usermode driver API
│   ├── reg10_mem.h / .c  # Global RAM, scratchpads, framebuffer
│   ├── reg10_mem_inl.h   # Inline memory accessors
│   ├── reg10_cpu.h / .c  # SIMT core execution engine (warp scheduler)
│   └── reg10_driver.c    # Device/context/mem/shader/fence/queue
├── platform/             # OS-specific hooks
│   ├── reg10_platform.h
│   ├── linux/reg10_platform.c
│   └── windows/reg10_platform.c
├── gallium/              # Mesa Gallium bridge
│   ├── reg10_gallium.h
│   └── reg10_gallium.c
├── vulkan/               # Minimal Vulkan ICD
│   ├── reg10_vulkan.h
│   └── reg10_vulkan.c
├── test/                 # Smoke test
│   └── reg10_test.c
└── Makefile              # Build system (Linux/mingw)
```

## Design

### Machine model (matches ISA doc exactly)

| Feature | Implementation |
|---------|---------------|
| Architecture | SIMT, 32 lanes per warp |
| Encoding | 64-bit fixed width, little-endian |
| Registers | r0-r31 (GP, per-lane), f0-f31 (FP, per-lane), p0-p7 (predicate) |
| PC / call stack / reconvergence | Per-warp |
| Memory | Global RAM (64 MB), scratchpad (64 KB/core), framebuffer in RAM |
| Opcodes | All 43 ISA v2 opcodes implemented |
| Tensor units | 2 units, 4-cycle latency, async FMA |
| Divergence | BRA / BRA_DIV with reconvergence stack |

### Driver API (`reg10_driver.h`)

```
reg10_enumerate_devices()       - Discover Re:G 1010 devices
reg10_device_open()/close()     - Device lifecycle
reg10_context_create()/destroy() - Command submission scope
reg10_mem_alloc()/free()/map()/flush() - Host-visible GPU memory
reg10_shader_create()/destroy() - Upload 64-bit instruction binaries
reg10_fence_create()/destroy()/wait() - Synchronization
reg10_queue_dispatch()/copy()/present()/submit() - Command submission
reg10_device_set_framebuffer()/get_fb()/run() - Framebuffer control
```

### Mesa/Gallium bridge (`gallium/`)

`reg10_gallium_draw()` translates a Gallium-style triangle draw into a
Re:G ISA kernel that runs on the SIMT core and writes BGRA32 pixels to
the framebuffer. A Mesa state tracker would call into these functions
instead of a hardware GPU.

### Minimal Vulkan ICD (`vulkan/`)

Exposes the subset of Vulkan ICD entry points needed for a software
driver:

- `vkGetInstanceProcAddr`
- `vkEnumerateInstanceExtensionProperties`
- `vkCreateInstance` / `vkDestroyInstance`
- `vkEnumeratePhysicalDevices` / `vkGetPhysicalDeviceProperties`
- `vkCreateDevice` / `vkDestroyDevice`
- `vkCreateSwapchainKHR` / `vkDestroySwapchainKHR`
- `vkAcquireNextImageKHR`
- `vkQueueSubmit` / `vkQueuePresentKHR` / `vkQueueWaitIdle`

## Building

No compiler was detected in this environment, but the Makefile targets
both Linux and Windows (mingw):

```bash
# Linux
make
make test

# Windows (mingw32-make)
mingw32-make
mingw32-make test
```

The test builds a gradient-fill kernel (ported from `kernel.s` in the
ISA doc), dispatches it on the driver, and reads back pixels from the
framebuffer.

## Usage example

```c
#include "reg10_driver.h"

// 1. Find and open the device
reg10_device_info_t info;
reg10_enumerate_devices(&info, 1);
reg10_device_t *dev = reg10_device_open(0);

// 2. Create a context
reg10_context_t *ctx = reg10_context_create(dev);

// 3. Build a shader (array of 64-bit instructions)
reg10_shader_t *shader = reg10_shader_create(ctx, code, num_words);

// 4. Set framebuffer size
reg10_device_set_framebuffer(dev, 320, 240);

// 5. Dispatch a kernel grid
reg10_fence_t *fence = reg10_fence_create(ctx);
reg10_queue_dispatch(reg10_context_get_queue(ctx), shader,
                     1, 1, 1, 1, 1, 0, fence);
reg10_queue_submit(reg10_context_get_queue(ctx));
reg10_fence_wait(fence, 5000);

// 6. Read framebuffer
size_t fb_size;
uint8_t *fb = reg10_device_get_fb(dev, &fb_size);
```

## Compatibility notes

- **Mesa/Gallium**: The `gallium/` layer provides a `pipe_context`-like
  interface. A full Mesa state tracker (e.g. `softpipe`-style) would be
  built on top of `reg10_gallium_draw()`.
- **Vulkan**: The `vulkan/` layer is a minimal software ICD. It presents
  `Re:G 1010 (usermode VM)` as a `VK_PHYSICAL_DEVICE_TYPE_CPU` device
  with vendor ID `0x1234`, device ID `0x1010`.
- **Platform**: `platform/linux/` and `platform/windows/` provide OS
  hooks (present, sleep). Currently these are stubs since the driver is
  pure userspace — the framebuffer lives in host memory and can be
  inspected directly.

## Limitations

- Single-core dispatch (all warps launch on core 0; multi-core
  scheduling is a TODO).
- Memory allocated via `reg10_mem_alloc()` uses a fixed GPU address
  (`0x1000`); a real bump allocator is a TODO.
- No actual GPU hardware — this is a VM emulator running on the host CPU.

## Assembler (`reg10_asm.h` / `reg10_asm.c`)

A two-pass assembler for the full Re:G ISA v2 (including graphics extensions).

### Supported directives
- `.org ADDR` - Set assembly origin (pads output to ADDR)
- `.label NAME` - Define a label at the current PC
- `.word VALUE` - Emit a raw 64-bit word
- `.string "TEXT"` - Emit a length-prefixed UTF-8 string
- `.fill N` - Emit N zero 64-bit words

### Syntax
```
[@P[!]N]  MNEMONIC   operands...   ; comment
```

Examples:
```
@P0   IADD r3, r1, r2      ; predicated on P0 true
@!P0  STG r3, r4, 0        ; predicated on P0 false
MOVI  r10, 0x10000         ; load immediate
LDG   r5, [r10+0]          ; global load
STG   r5, [r10+4]          ; global store
TEX   r20, r10, r11, 1     ; texture sample (LINEAR)
BLEND r12, r13, 1          ; alpha blend
DEPTH r14, r15             ; depth test
KILL  r16                  ; fragment discard
```

### Building the assembler tool
```bash
make asm
./reg10_as kernels/gradient.s -o gradient.bin
```

## Example Kernels

### `kernels/gradient.s`
Gradient fill kernel ported from the ISA doc example. Renders a horizontal/vertical
gradient where red varies with X, green with Y, blue fixed at 0x80.

### `kernels/fragment.s`
Fragment shader demonstrating graphics extensions: texture sampling (TEX),
alpha blending (BLEND), depth testing (DEPTH), and fragment discard (KILL).