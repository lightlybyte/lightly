"""
ram.py - Memory subsystem for Re:G.

Framebuffer is a memoryview into GlobalRAM at a fixed base address,
so kernel writes to global memory are immediately visible to screen.py.
"""

from __future__ import annotations
from dataclasses import dataclass
import struct

GLOBAL_RAM_SIZE   = 1 << 26
SCRATCHPAD_SIZE   = 64 * 1024
FB_MAX_W          = 3840
FB_MAX_H          = 2160
FB_BPP            = 4
CACHE_LINE        = 64
DEFAULT_FB_BASE   = 0x10000

_pack_u32 = struct.Struct("<I").pack
_unpack_u32 = struct.Struct("<I").unpack_from
_pack_f32 = struct.Struct("<f").pack
_unpack_f32 = struct.Struct("<f").unpack_from
_pack_u64 = struct.Struct("<Q").pack
_unpack_u64 = struct.Struct("<Q").unpack_from


class GlobalRAM:
    __slots__ = ("size", "mem", "mv", "_mask")

    def __init__(self, size: int = GLOBAL_RAM_SIZE):
        self.size = size
        self.mem = bytearray(size)
        self.mv = memoryview(self.mem)
        self._mask = size - 1

    def read_u8(self, addr): return self.mem[addr]
    def write_u8(self, addr, val): self.mem[addr] = val & 0xFF
    def read_u16(self, addr): return self.mem[addr] | (self.mem[addr+1] << 8)
    def write_u16(self, addr, val):
        self.mem[addr] = val & 0xFF
        self.mem[addr+1] = (val >> 8) & 0xFF
    def read_u32(self, addr): return _unpack_u32(self.mv, addr)[0]
    def write_u32(self, addr, val): self.mem[addr:addr+4] = _pack_u32(val & 0xFFFFFFFF)
    def read_u64(self, addr): return _unpack_u64(self.mv, addr)[0]
    def write_u64(self, addr, val): self.mem[addr:addr+8] = _pack_u64(val & 0xFFFFFFFFFFFFFFFF)
    def read_f32(self, addr): return _unpack_f32(self.mv, addr)[0]
    def write_f32(self, addr, val): self.mem[addr:addr+4] = _pack_f32(val)

    def load(self, data: bytes, addr: int = 0) -> None:
        self.mem[addr:addr+len(data)] = data

    def read_block(self, addr: int, length: int) -> bytes:
        return bytes(self.mv[addr:addr+length])

    def write_block(self, addr: int, data: bytes) -> None:
        self.mem[addr:addr+len(data)] = data

    def zero(self, addr: int, length: int) -> None:
        self.mem[addr:addr+length] = b"\x00" * length


class Framebuffer:
    """A BGRA8 framebuffer that lives inside GlobalRAM at a fixed base."""

    def __init__(self, global_ram: GlobalRAM, base: int, width: int, height: int):
        if width > FB_MAX_W or height > FB_MAX_H:
            raise ValueError(f"framebuffer too large: {width}x{height}")
        self.global_ram = global_ram
        self.base = base
        self.width = width
        self.height = height
        self.stride = width * FB_BPP
        self.size = self.stride * height
        if base + self.size > global_ram.size:
            raise ValueError(
                f"framebuffer at {base:#x} ({self.size} bytes) "
                f"exceeds global RAM ({global_ram.size} bytes)")
        self._mv = global_ram.mv[base:base + self.size]
        self._buf = global_ram.mem  # direct alias for hot writes

    @property
    def buffer(self):
        """The underlying bytearray (whole RAM). Use with base+size."""
        return self._buf

    def view(self):
        return self._mv

    def _off(self, x: int, y: int) -> int:
        return self.base + y * self.stride + x * FB_BPP

    def write_pixel(self, x: int, y: int, bgra: int) -> None:
        off = self._off(x, y)
        self._buf[off:off + 4] = _pack_u32(bgra & 0xFFFFFFFF)

    def read_pixel(self, x: int, y: int) -> int:
        return _unpack_u32(self._buf, self._off(x, y))[0]

    def clear(self, bgra: int = 0xFF000000) -> None:
        pixel = _pack_u32(bgra & 0xFFFFFFFF)
        row = pixel * self.width
        for y in range(self.height):
            off = self.base + y * self.stride
            self._buf[off:off + self.stride] = row

    def present(self) -> bytes:
        return bytes(self._mv)


class Scratchpad:
    __slots__ = ("size", "mem", "mv", "_mask")

    def __init__(self, size: int = SCRATCHPAD_SIZE):
        self.size = size
        self.mem = bytearray(size)
        self.mv = memoryview(self.mem)
        self._mask = size - 1

    def read_u32(self, addr):
        return _unpack_u32(self.mv, addr & self._mask)[0]

    def write_u32(self, addr, val):
        addr &= self._mask
        self.mem[addr:addr+4] = _pack_u32(val & 0xFFFFFFFF)

    def read_f32(self, addr):
        return _unpack_f32(self.mv, addr & self._mask)[0]

    def write_f32(self, addr, val):
        addr &= self._mask
        self.mem[addr:addr+4] = _pack_f32(val)

    def zero(self):
        self.mem[:] = b"\x00" * self.size


class ScratchpadPool:
    __slots__ = ("num_cores", "size", "pads")

    def __init__(self, num_cores, size=SCRATCHPAD_SIZE):
        self.num_cores = num_cores
        self.size = size
        self.pads = [Scratchpad(size) for _ in range(num_cores)]

    def get(self, core_id):
        return self.pads[core_id]

    def zero_all(self):
        for p in self.pads:
            p.zero()


class MemorySystem:
    __slots__ = ("global_ram", "framebuffer", "scratchpads", "fb_base")

    def __init__(self, num_cores, fb_width, fb_height,
                 global_size=GLOBAL_RAM_SIZE, scratchpad_size=SCRATCHPAD_SIZE,
                 fb_base=DEFAULT_FB_BASE):
        self.global_ram = GlobalRAM(global_size)
        self.scratchpads = ScratchpadPool(num_cores, scratchpad_size)
        self.fb_base = fb_base
        self.framebuffer = Framebuffer(self.global_ram, fb_base,
                                       fb_width, fb_height)

    def clear_framebuffer(self, bgra=0xFF000000):
        self.framebuffer.clear(bgra)

    def present(self):
        return self.framebuffer.present()