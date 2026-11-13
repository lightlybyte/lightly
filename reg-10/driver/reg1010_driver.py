"""reg1010_driver.py - Re:G 1010 Windows user-mode driver library.

Host-side driver for the Re:G 1010 GPU. Talks to hardware through the
command ring in global RAM and the DMA engine. Matches the ISA model in
reg-10/kernel/ISA/.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List
import struct

# ---------------------------------------------------------------------------
# Command ring opcodes (must match scheduler.py CmdOp)
# ---------------------------------------------------------------------------
class CmdOp(IntEnum):
    NOP      = 0
    DISPATCH = 1
    COPY     = 2
    FENCE    = 3
    SET_FB   = 4
    PRESENT  = 5

# ---------------------------------------------------------------------------
# DMA channels (must match dma.py DMAChannel)
# ---------------------------------------------------------------------------
class DMAChannel(IntEnum):
    G2S = 0   # global RAM -> scratchpad
    S2G = 1   # scratchpad -> global RAM
    G2F = 2   # global RAM -> framebuffer
    F2G = 3   # framebuffer -> global RAM
    S2F = 4   # scratchpad -> framebuffer

# ---------------------------------------------------------------------------
# Re:G 1010 hardware constants
# ---------------------------------------------------------------------------
RING_BASE      = 0x100      # command ring base in global RAM
FB_BASE        = 0x10000    # framebuffer base in global RAM
GLOBAL_RAM_SIZE = 1 << 26   # 64 MiB

REGL_1010_VENDOR_ID  = 0x1DE5   # "Re:G Labs"
REGL_1010_DEVICE_ID  = 0x1010
REGL_1010_REVISION   = 0x01

_pack_u32 = struct.Struct("<I").pack
_pack_u64 = struct.Struct("<Q").pack
_unpack_u64 = struct.Struct("<Q").unpack_from


# ===========================================================================
# RESOURCE / MEMORY MANAGEMENT
# ===========================================================================

@dataclass
class GpuBuffer:
    """A buffer allocated in Re:G 1010 global RAM."""
    addr: int
    size: int
    mem: bytearray  # backing store mirrored to the emulator's global RAM


class GpuAllocator:
    """Simple bump allocator for Re:G 1010 global RAM."""
    def __init__(self, base: int = FB_BASE + (1920 * 1080 * 4)):
        self.base = base
        self.ptr = base
        self.end = GLOBAL_RAM_SIZE
        self.allocations: List[GpuBuffer] = []

    def alloc(self, size: int, align: int = 256) -> GpuBuffer:
        size = (size + align - 1) & ~(align - 1)
        addr = self.ptr
        if addr + size > self.end:
            raise MemoryError(
                f"Re:G 1010: out of global RAM (need {size} at {addr:#x})")
        mem = bytearray(size)
        buf = GpuBuffer(addr=addr, size=size, mem=mem)
        self.allocations.append(buf)
        self.ptr = addr + size
        return buf

    def free(self, buf: GpuBuffer) -> None:
        if buf in self.allocations:
            self.allocations.remove(buf)

    def upload(self, buf: GpuBuffer, data: bytes, offset: int = 0) -> None:
        if offset + len(data) > buf.size:
            raise ValueError("upload exceeds buffer size")
        buf.mem[offset:offset + len(data)] = data


# ===========================================================================
# COMMAND RING
# ===========================================================================

class CommandRing:
    """Writes commands into the Re:G 1010 command ring in global RAM."""
    def __init__(self, mem: bytearray, base: int = RING_BASE, size: int = 4096):
        self.mem = mem
        self.base = base
        self.size = size
        self.head = base
        self.tail = base
        self._fence_counter = 0

    def _write_u32(self, addr: int, val: int) -> None:
        self.mem[addr:addr + 4] = _pack_u32(val & 0xFFFFFFFF)

    def new_fence(self) -> int:
        fid = self._fence_counter
        self._fence_counter += 1
        return fid

    def dispatch(self, grid_x: int, grid_y: int, block_x: int, block_y: int,
                 warps: int, kernel_pc: int, args_addr: int, fence_id: int) -> None:
        """Emit a DISPATCH command (28 bytes = 7 u32s)."""
        words = [
            CmdOp.DISPATCH,
            (grid_x & 0xFFFF) | ((grid_y & 0xFFFF) << 16),
            (block_x & 0xFFFF) | ((block_y & 0xFFFF) << 16),
            warps & 0xFFFFFFFF,
            kernel_pc & 0xFFFFFFFF,
            args_addr & 0xFFFFFFFF,
            fence_id & 0xFFFFFFFF,
        ]
        for i, w in enumerate(words):
            self._write_u32(self.tail + i * 4, w)
        self.tail += 28

    def copy(self, channel: DMAChannel, src: int, dst: int, length: int,
             fence_id: int = -1) -> None:
        """Emit a COPY command (24 bytes = 6 u32s)."""
        words = [
            CmdOp.COPY,
            src & 0xFFFFFFFF,
            dst & 0xFFFFFFFF,
            length & 0xFFFFFFFF,
            int(channel) & 0xFFFFFFFF,
            fence_id & 0xFFFFFFFF,
        ]
        for i, w in enumerate(words):
            self._write_u32(self.tail + i * 4, w)
        self.tail += 24

    def fence(self, fence_id: int) -> None:
        self._write_u32(self.tail, CmdOp.FENCE)
        self._write_u32(self.tail + 4, fence_id & 0xFFFFFFFF)
        self.tail += 8

    def present(self) -> None:
        self._write_u32(self.tail, CmdOp.PRESENT)
        self.tail += 4

    def nop(self) -> None:
        self._write_u32(self.tail, CmdOp.NOP)
        self.tail += 4

    def flush_to_device(self) -> None:
        """In a real driver this rings the doorbell. Here the emulator polls
        ring_head < ring_tail directly, so flushing is a no-op."""
        pass


# ===========================================================================
# RE:G 1010 DEVICE / DRIVER CONTEXT
# ===========================================================================

@dataclass
class ReG1010Device:
    """Handle to a Re:G 1010 GPU (real or emulated)."""
    vendor_id: int = REGL_1010_VENDOR_ID
    device_id: int = REGL_1010_DEVICE_ID
    revision: int = REGL_1010_REVISION
    fb_width: int = 1920
    fb_height: int = 1080
    num_cores: int = 4
    warps_per_core: int = 32
    global_ram: bytearray = field(default_factory=lambda: bytearray(GLOBAL_RAM_SIZE))
    ring: CommandRing = field(init=False)
    allocator: GpuAllocator = field(init=False)
    fences: dict = field(default_factory=dict)
    fence_counter: int = 0
    fb_addr: int = FB_BASE

    def __post_init__(self):
        self.ring = CommandRing(self.global_ram)
        fb_size = self.fb_width * self.fb_height * 4
        self.fb_addr = FB_BASE
        self.allocator = GpuAllocator(base=FB_BASE + fb_size)

    def new_fence(self) -> int:
        fid = self.fence_counter
        self.fence_counter += 1
        self.fences[fid] = False
        return fid

    def signal_fence(self, fid: int) -> None:
        self.fences[fid] = True

    def is_fence_signalled(self, fid: int) -> bool:
        return self.fences.get(fid, False)

    def fb_size(self) -> int:
        return self.fb_width * self.fb_height * 4

    # ------------------------------------------------------------------
    # Emulator integration: process the command ring like the hardware.
    # This ties the host driver to the ISA emulator (cpu/scheduler/ram).
    # ------------------------------------------------------------------
    def execute(self, max_cycles: int = 10_000_000) -> int:
        """Process the command ring and run dispatched kernels.

        Mirrors scheduler.py behavior: DISPATCH launches warps on cores,
        COPY performs DMA, FENCE signals, PRESENT is a no-op here.
        Returns total cycles executed.
        """
        # Lazy import to avoid a hard dependency cycle at import time.
        import sys

        isa_dir = "c:\\lightly\\reg-10\\kernel\\ISA"
        if isa_dir not in sys.path:
            sys.path.insert(0, isa_dir)

        from ram import MemorySystem
        from cpu import SIMTCore
        from dma import DMAEngine
        from scheduler import Scheduler

        # Build the emulator memory system around our global_ram.
        fb_w, fb_h = self.fb_width, self.fb_height
        mem = MemorySystem(
            num_cores=self.num_cores,
            fb_width=fb_w,
            fb_height=fb_h,
            global_size=GLOBAL_RAM_SIZE,
            fb_base=FB_BASE,
        )
        # Copy our global RAM contents (kernel + args) into the emulator.
        mem.global_ram.mem[:] = self.global_ram[:]

        cores = [SIMTCore(core_id=i, num_warps=self.warps_per_core,
                          memory=mem.global_ram) for i in range(self.num_cores)]
        dma = DMAEngine(mem.global_ram, mem.scratchpads, mem.framebuffer)
        sched = Scheduler(cores, mem, dma, ring_base=RING_BASE)

        # Mirror ring state.
        sched.ring_head = self.ring.head
        sched.ring_tail = self.ring.tail
        sched.fences = dict(self.fences)

        cycles = sched.run(max_cycles=max_cycles)

        # Write results back to the host-visible global RAM.
        self.global_ram[:] = mem.global_ram.mem[:]
        self.fences = dict(sched.fences)
        self.ring.head = sched.ring_head
        self.ring.tail = sched.ring_tail

        # Signal any fences that completed.
        for fid, signalled in sched.fences.items():
            if signalled:
                self.signal_fence(fid)

        return cycles


def enumerate_devices() -> List[ReG1010Device]:
    """Enumerate Re:G 1010 GPUs. Returns one emulated device in this build.

    A real Windows driver would scan PCIe here using the vendor/device ID.
    """
    return [ReG1010Device()]


def open_device(index: int = 0) -> ReG1010Device:
    devs = enumerate_devices()
    if index >= len(devs):
        raise RuntimeError(f"No Re:G 1010 at index {index}")
    return devs[index]
    def fb_size(self) -> int:
        return self.fb_width * self.fb_height * 4

    # ------------------------------------------------------------------
    # Emulator integration: process the command ring like the hardware.
    # This ties the host driver to the ISA emulator (cpu/scheduler/ram).
    # ------------------------------------------------------------------
    def execute(self, max_cycles: int = 10_000_000) -> int:
        """Process the command ring and run dispatched kernels.

        Mirrors scheduler.py behavior: DISPATCH launches warps on cores,
        COPY performs DMA, FENCE signals, PRESENT is a no-op here.
        Returns total cycles executed.
        """
        # Lazy import to avoid a hard dependency cycle at import time.
        import sys
        isa_dir = "c:\\lightly\\reg-10\\kernel\\ISA"
        if isa_dir not in sys.path:
            sys.path.insert(0, isa_dir)

        from ram import MemorySystem
        from cpu import SIMTCore
        from dma import DMAEngine
        from scheduler import Scheduler, CmdOp as _CmdOp

        # Build the emulator memory system around our global_ram.
        fb_w, fb_h = self.fb_width, self.fb_height
        mem = MemorySystem(
            num_cores=self.num_cores,
            fb_width=fb_w,
            fb_height=fb_h,
            global_size=GLOBAL_RAM_SIZE,
            fb_base=FB_BASE,
        )
        # Copy our global RAM contents (kernel + args) into the emulator.
        mem.global_ram.mem[:] = self.global_ram[:]

        cores = [SIMTCore(core_id=i, num_warps=self.warps_per_core,
                          memory=mem.global_ram) for i in range(self.num_cores)]
        dma = DMAEngine(mem.global_ram, mem.scratchpads, mem.framebuffer)
        sched = Scheduler(cores, mem, dma, ring_base=RING_BASE)

        # Mirror ring state.
        sched.ring_head = self.ring.head
        sched.ring_tail = self.ring.tail
        sched.fences = dict(self.fences)

        cycles = sched.run(max_cycles=max_cycles)

        # Write results back to the host-visible global RAM.
        self.global_ram[:] = mem.global_ram.mem[:]
        self.fences = dict(sched.fences)
        self.ring.head = sched.ring_head
        self.ring.tail = sched.ring_tail

        # Signal any fences that completed.
        for fid, signalled in sched.fences.items():
            if signalled:
                self.signal_fence(fid)

        return cycles
_unpack_u32 = struct.Struct("<I").unpack_from