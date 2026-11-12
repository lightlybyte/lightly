"""
ram.py - Memory subsystem for Re:G.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import struct

GLOBAL_RAM_SIZE   = 1 << 26
SCRATCHPAD_SIZE   = 64 * 1024
FB_MAX_W          = 3840
FB_MAX_H          = 2160
FB_BPP            = 4
CACHE_LINE        = 64

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


@dataclass(slots=True)
class Framebuffer:
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

    @property
    def stride(self): return self._stride
    @property
    def size(self): return len(self._buf)
    @property
    def buffer(self): return self._buf
    def view(self): return self._mv

    def write_pixel(self, x, y, bgra):
        off = y * self._stride + x * FB_BPP
        self._buf[off:off+4] = _pack_u32(bgra & 0xFFFFFFFF)

    def read_pixel(self, x, y):
        return _unpack_u32(self._mv, y * self._stride + x * FB_BPP)[0]

    def clear(self, bgra: int = 0xFF000000) -> None:
        pixel = _pack_u32(bgra & 0xFFFFFFFF)
        row = pixel * self.width
        for y in range(self.height):
            off = y * self._stride
            self._buf[off:off+self._stride] = row

    def blit_scanline(self, y, src_row):
        if len(src_row) != self._stride:
            raise ValueError(f"scanline length {len(src_row)} != stride {self._stride}")
        off = y * self._stride
        self._buf[off:off+self._stride] = src_row

    def present(self) -> bytes:
        return bytes(self._buf)


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
    __slots__ = ("global_ram", "framebuffer", "scratchpads")

    def __init__(self, num_cores, fb_width, fb_height,
                 global_size=GLOBAL_RAM_SIZE, scratchpad_size=SCRATCHPAD_SIZE):
        self.global_ram = GlobalRAM(global_size)
        self.framebuffer = Framebuffer(fb_width, fb_height)
        self.scratchpads = ScratchpadPool(num_cores, scratchpad_size)

    def clear_framebuffer(self, bgra=0xFF000000):
        self.framebuffer.clear(bgra)

    def present(self):
        return self.framebuffer.present()