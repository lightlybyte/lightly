"""
ram.py - Blazing-fast memory subsystem for Re:G.

Layout:
  ┌────────────────────────────────────────────┐
  │ GLOBAL RAM   (unified, shared by all cores)│
  ├────────────────────────────────────────────┤
  │ FRAMEBUFFER  (pinned, cache-line aligned)  │
  ├────────────────────────────────────────────┤
  │ SCRATCHPAD POOL (per-core, software-managed│
  └────────────────────────────────────────────┘

Design goals:
  - minimal Python overhead in the hot path
  - bytearray + memoryview backing (no per-access allocation)
  - unaligned and aligned fast paths
  - zero-copy views for the rasterizer and display endpoint
  - SIMD-friendly row blit for the framebuffer
"""

from __future__ import annotations
import struct
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# CONSTANTS
# ============================================================

GLOBAL_RAM_SIZE   = 1 << 26          # 64 MiB
SCRATCHPAD_SIZE   = 64 * 1024        # 64 KiB per core
FB_MAX_W          = 3840
FB_MAX_H          = 2160
FB_BPP            = 4                # BGRA8
CACHE_LINE        = 64

# Precompiled struct packers — reused, no per-call format parsing.
_pack_u32 = struct.Struct("<I").pack
_unpack_u32 = struct.Struct("<I").unpack_from
_pack_f32 = struct.Struct("<f").pack
_unpack_f32 = struct.Struct("<f").unpack_from
_pack_u64 = struct.Struct("<Q").pack
_unpack_u64 = struct.Struct("<Q").unpack_from


# ============================================================
# GLOBAL RAM
# ============================================================

class GlobalRAM:
    """
    Unified global memory. All cores share one instance.
    Hot-path reads/writes use memoryview + struct.unpack_from,
    which is roughly 3-5x faster than slicing bytearray in CPython.
    """
    __slots__ = ("size", "mem", "mv", "_mask")

    def __init__(self, size: int = GLOBAL_RAM_SIZE):
        self.size = size
        self.mem = bytearray(size)
        self.mv = memoryview(self.mem)
        self._mask = size - 1

    # ---- raw byte access (no masking, caller guarantees in-range) ----

    def read_u8(self, addr: int) -> int:
        return self.mem[addr]

    def write_u8(self, addr: int, val: int) -> None:
        self.mem[addr] = val & 0xFF

    def read_u16(self, addr: int) -> int:
        return self.mem[addr] | (self.mem[addr + 1] << 8)

    def write_u16(self, addr: int, val: int) -> None:
        self.mem[addr] = val & 0xFF
        self.mem[addr + 1] = (val >> 8) & 0xFF

    def read_u32(self, addr: int) -> int:
        return _unpack_u32(self.mv, addr)[0]

    def write_u32(self, addr: int, val: int) -> None:
        self.mem[addr:addr + 4] = _pack_u32(val & 0xFFFFFFFF)

    def read_u64(self, addr: int) -> int:
        return _unpack_u64(self.mv, addr)[0]

    def write_u64(self, addr: int, val: int) -> None:
        self.mem[addr:addr + 8] = _pack_u64(val & 0xFFFFFFFFFFFFFFFF)

    def read_f32(self, addr: int) -> float:
        return _unpack_f32(self.mv, addr)[0]

    def write_f32(self, addr: int, val: float) -> None:
        self.mem[addr:addr + 4] = _pack_f32(val)

    # ---- bulk ----

    def load(self, data: bytes, addr: int = 0) -> None:
        self.mem[addr:addr + len(data)] = data

    def read_block(self, addr: int, length: int) -> bytes:
        return bytes(self.mv[addr:addr + length])

    def write_block(self, addr: int, data: bytes) -> None:
        self.mem[addr:addr + len(data)] = data

    def zero(self, addr: int, length: int) -> None:
        self.mem[addr:addr + length] = b"\x00" * length


# ============================================================
# FRAMEBUFFER
# ============================================================

@dataclass(slots=True)
class Framebuffer:
    """
    A BGRA8 framebuffer with a fast per-scanline blit path.
    Stored as a single contiguous bytearray; each pixel is 4 bytes.
    """
    width: int
    height: int
    _buf: bytearray = field(init=False, repr=False)
    _mv: memoryview = field(init=False, repr=False)
    _stride: int = field(init=False)

    def __post_init__(self):
        if self.width > FB_MAX_W or self.height > FB_MAX_H:
            raise ValueError(f"framebuffer too large: {self.width}x{self.height}")
        self._stride = self.width * FB_BPP
        self._buf = bytearray(self._stride * self.height)
        self._mv = memoryview(self._buf)

    # ---- properties ----

    @property
    def stride(self) -> int:
        return self._stride

    @property
    def size(self) -> int:
        return len(self._buf)

    @property
    def buffer(self) -> bytearray:
        return self._buf

    def view(self) -> memoryview:
        """Zero-copy view for the display endpoint."""
        return self._mv

    # ---- per-pixel ----

    def write_pixel(self, x: int, y: int, bgra: int) -> None:
        off = y * self._stride + x * FB_BPP
        self._buf[off:off + 4] = _pack_u32(bgra & 0xFFFFFFFF)

    def read_pixel(self, x: int, y: int) -> int:
        return _unpack_u32(self._mv, y * self._stride + x * FB_BPP)[0]

    # ---- fast paths ----

    def clear(self, bgra: int = 0xFF000000) -> None:
        """Fill the entire framebuffer with one color."""
        # 4-byte value repeated across a scanline, then repeated by scanline.
        pixel = _pack_u32(bgra & 0xFFFFFFFF)
        row = pixel * self.width
        for y in range(self.height):
            off = y * self._stride
            self._buf[off:off + self._stride] = row

    def blit_scanline(self, y: int, src_row: bytes) -> None:
        """Copy one precomputed scanline (stride*4 bytes) into row y."""
        if len(src_row) != self._stride:
            raise ValueError(f"scanline length {len(src_row)} != stride {self._stride}")
        off = y * self._stride
        self._buf[off:off + self._stride] = src_row

    def blit_block(self, x: int, y: int, w: int, h: int, src: bytes) -> None:
        """Copy a rectangular block of BGRA pixels."""
        for row in range(h):
            dst = (y + row) * self._stride + x * FB_BPP
            s = row * w * FB_BPP
            self._buf[dst:dst + w * FB_BPP] = src[s:s + w * FB_BPP]

    def row_view(self, y: int) -> memoryview:
        off = y * self._stride
        return self._mv[off:off + self._stride]

    def present(self) -> bytes:
        """Snapshot the framebuffer for the display endpoint."""
        return bytes(self._buf)


# ============================================================
# SCRATCHPAD POOL
# ============================================================

class Scratchpad:
    """
    Per-core software-managed scratchpad. No cache coherence — the kernel
    is responsible for coherency via explicit fences.
    """
    __slots__ = ("size", "mem", "mv", "_mask")

    def __init__(self, size: int = SCRATCHPAD_SIZE):
        self.size = size
        self.mem = bytearray(size)
        self.mv = memoryview(self.mem)
        self._mask = size - 1

    def read_u32(self, addr: int) -> int:
        return _unpack_u32(self.mv, addr & self._mask)[0]

    def write_u32(self, addr: int, val: int) -> None:
        addr &= self._mask
        self.mem[addr:addr + 4] = _pack_u32(val & 0xFFFFFFFF)

    def read_f32(self, addr: int) -> float:
        return _unpack_f32(self.mv, addr & self._mask)[0]

    def write_f32(self, addr: int, val: float) -> None:
        addr &= self._mask
        self.mem[addr:addr + 4] = _pack_f32(val)

    def zero(self) -> None:
        self.mem[:] = b"\x00" * self.size


class ScratchpadPool:
    """Allocates and tracks per-core scratchpads."""
    __slots__ = ("num_cores", "size", "pads")

    def __init__(self, num_cores: int, size: int = SCRATCHPAD_SIZE):
        self.num_cores = num_cores
        self.size = size
        self.pads = [Scratchpad(size) for _ in range(num_cores)]

    def get(self, core_id: int) -> Scratchpad:
        return self.pads[core_id]

    def zero_all(self) -> None:
        for p in self.pads:
            p.zero()


# ============================================================
# MEMORY SYSTEM (facade)
# ============================================================

class MemorySystem:
    """
    Bundles GlobalRAM + Framebuffer + ScratchpadPool.
    This is what the SIMT core, rasterizer, and endpoint talk to.
    """
    __slots__ = ("global_ram", "framebuffer", "scratchpads")

    def __init__(self,
                 num_cores: int,
                 fb_width: int,
                 fb_height: int,
                 global_size: int = GLOBAL_RAM_SIZE,
                 scratchpad_size: int = SCRATCHPAD_SIZE):
        self.global_ram = GlobalRAM(global_size)
        self.framebuffer = Framebuffer(fb_width, fb_height)
        self.scratchpads = ScratchpadPool(num_cores, scratchpad_size)

    def clear_framebuffer(self, bgra: int = 0xFF000000) -> None:
        self.framebuffer.clear(bgra)

    def present(self) -> bytes:
        return self.framebuffer.present()


# ============================================================
# SMOKE TEST
# ============================================================

if __name__ == "__main__":
    import time

    print("Memory system smoke test")

    mem = MemorySystem(num_cores=4, fb_width=1920, fb_height=1080)
    fb = mem.framebuffer

    # Per-pixel write
    fb.write_pixel(10, 10, 0xFFFF0000)
    assert fb.read_pixel(10, 10) == 0xFFFF0000

    # Clear + timing
    t0 = time.perf_counter()
    for _ in range(10):
        fb.clear(0xFF336699)
    t1 = time.perf_counter()
    print(f"10x 1080p clear: {(t1 - t0)*1000:.2f} ms "
          f"({(t1 - t0)/10*1000:.2f} ms/frame)")

    # Scanline blit
    row = b"\xAA\xBB\xCC\xDD" * fb.width
    t0 = time.perf_counter()
    for y in range(fb.height):
        fb.blit_scanline(y, row)
    t1 = time.perf_counter()
    print(f"1080p scanline blit: {(t1 - t0)*1000:.2f} ms")

    # Scratchpad
    sp = mem.scratchpads.get(0)
    sp.write_u32(0, 0xDEADBEEF)
    assert sp.read_u32(0) == 0xDEADBEEF

    # Global
    mem.global_ram.write_u32(0x1000, 0x12345678)
    assert mem.global_ram.read_u32(0x1000) == 0x12345678

    print("all tests passed")